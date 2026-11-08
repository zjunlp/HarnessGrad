"""一个 authored bundle 变成数据集：材料进 SETUP，检查器进 VERIFY。

这一层不做任何执行（执行在 run 里），所以测试只看两件事：翻译对不对，以及**不该被编译
的东西会不会被悄悄编译**。后者才是重点——一个被吃掉的初始状态会让 oracle 也失败，读的人
只会以为题出错了。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import authored                                       # noqa: E402

EXAMPLE = ROOT / "data" / "authored" / "example-fix-bug"


def test_the_built_in_example_compiles_to_tasks_setup_and_verify():
    tasks, setup, verify = authored.load("example-fix-bug")
    assert [t["task_id"] for t in tasks] == ["t01"]
    assert "test_broken.py" in tasks[0]["goal"]
    assert {f["path"] for f in setup["t01"]["files"]} == {"broken.py", "test_broken.py"}
    assert verify["t01"]["kind"] == "command"
    # 作者写的命令不再直接当 argv：它被一个包装脚本调用，判决写进 `reward_file`。
    # 数据集契约里 `command` 检查器的分数来源是文件而不是退出码（退出码还要带着"检查
    # 器自己崩了"这类信息，混进分数就分不清"题没做对"和"检查器没跑起来"）。
    assert verify["t01"]["argv"] == ["python3", "_hg_exit0.py"]
    assert verify["t01"]["reward_file"] == "/verifier/reward.txt"
    spec = json.loads(next(i["content"] for i in verify["t01"]["inputs"]
                           if i["path"] == "_hg_exit0.json"))
    assert spec["argv"] == ["python3", "test_broken.py"]


def test_the_checker_file_travels_with_verify_so_the_agent_cannot_edit_it():
    """`VERIFY.inputs` 在 agent 停下之后重新物化；只把测试放材料里，agent 改掉就没人管了。

    包装脚本自带 `reward_file` 的写法，所以它必须进 `inputs`：agent 能改到材料里的
    `test_broken.py` 也没用，检查器用的永远是平台重新物化的那一份。
    """
    _tasks, _setup, verify = authored.load("example-fix-bug")
    assert [i["path"] for i in verify["t01"]["inputs"]] == [
        "_hg_exit0.py", "_hg_exit0.json", "test_broken.py"]


def test_a_published_dataset_is_loadable_through_the_registry():
    from data.scaffolds import registry
    name = "authored_example_fix_bug"
    module = registry.module_name(name)
    assert module == "data.datasets.authored_example_fix_bug"
    loaded = registry.load_tasks(name)
    tasks = loaded[0]
    assert [t["task_id"] for t in tasks] == ["t01"]
    assert loaded[1]["t01"]["files"]


def _materialize(workdir: Path, files):
    for entry in files:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry["content"], encoding="utf-8")


def test_the_compiled_checker_scores_through_the_reward_file(tmp_path):
    """把 SETUP/VERIFY 物化出来跑一遍：空手 0、参考解 1，分数从 `reward_file` 读。

    这是"翻译对不对"之外唯一能证明编译结果真的可判定的测试：`--gates` 跑的是 bundle 里
    作者那条命令，不是编译出来的 VERIFY，两者走的是不同的路。
    """
    import subprocess
    from eval.verification import verifier

    tasks, setup, verify = authored.load("example-fix-bug")
    spec = verify["t01"]
    _materialize(tmp_path, setup["t01"]["files"])
    _materialize(tmp_path, spec["inputs"])

    empty = verifier._verify(tmp_path, spec, "", sandbox=False, timeout_s=60)
    assert (empty["score"], empty["passed"]) == (0.0, False), empty
    assert (tmp_path / "verifier" / "reward.txt").read_text().strip() == "0"

    oracle = (EXAMPLE / "oracle" / "t01.sh").read_text(encoding="utf-8")
    ran = subprocess.run(["bash", "-c", oracle], cwd=tmp_path, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    solved = verifier._verify(tmp_path, spec, "", sandbox=False, timeout_s=60)
    assert (solved["score"], solved["passed"]) == (1.0, True), solved
    assert (tmp_path / "verifier" / "reward.txt").read_text().strip() == "1"


def test_the_generated_file_checker_also_writes_a_numeric_reward(tmp_path):
    """模板生成的检查器（`file_equals` 等）走同一条文件通道，分数不是退出码。"""
    from eval.verification import verifier

    bundle = tmp_path / "bundle"
    (bundle / "materials" / "t01").mkdir(parents=True)
    (bundle / "materials" / "t01" / "out.txt").write_text("wrong\n", encoding="utf-8")
    (bundle / "oracle").mkdir()
    (bundle / "oracle" / "t01.sh").write_text("printf 'right\\n' > out.txt\n", encoding="utf-8")
    (bundle / "bundle.json").write_text(json.dumps({
        "schema_version": 1, "slug": "bundle", "title": "t", "env": {"kind": "files"},
        "split": {"by": "sha256(task_id)", "eval_fraction": 0.2}, "eval_visible_to_authors": True,
        "tasks": [{"task_id": "t01", "goal": "写出 right", "materials": "materials/t01",
                   "oracle": "oracle/t01.sh",
                   "verifier": {"template": "file_equals",
                                "params": {"path": "out.txt", "expected": "right"}}}]},
        ensure_ascii=False), encoding="utf-8")

    tasks, setup, verify = authored.load("bundle", authored_root=tmp_path)
    spec = verify["t01"]
    _materialize(tmp_path, setup["t01"]["files"])
    _materialize(tmp_path, spec["inputs"])
    assert spec["reward_file"] == "/verifier/reward.txt"
    failed = verifier._verify(tmp_path, spec, "", sandbox=False, timeout_s=60)
    assert (failed["score"], failed["passed"]) == (0.0, False), failed

    (tmp_path / "out.txt").write_text("right\n", encoding="utf-8")
    solved = verifier._verify(tmp_path, spec, "", sandbox=False, timeout_s=60)
    assert (solved["score"], solved["passed"]) == (1.0, True), solved


@pytest.fixture
def bundle(tmp_path):
    target = tmp_path / "example-fix-bug"
    shutil.copytree(EXAMPLE, target)
    return target


def test_a_binary_material_is_refused_by_name(bundle):
    (bundle / "materials" / "t01" / "photo.png").write_bytes(b"\x89PNG\r\n")
    with pytest.raises(authored.BundleError) as excinfo:
        authored.load(bundle.name, authored_root=bundle.parent)
    assert "photo.png" in str(excinfo.value) and "文本" in str(excinfo.value)


def test_an_unimplemented_checker_template_is_refused(bundle):
    path = bundle / "bundle.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tasks"][0]["verifier"] = {"template": "tests_pass_ratio",
                                    "params": {"cmd": "pytest -q", "reward_file": "r.json"}}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(authored.BundleError) as excinfo:
        authored.load(bundle.name, authored_root=bundle.parent)
    assert "tests_pass_ratio" in str(excinfo.value)


def test_an_exec_bundle_is_refused_rather_than_half_compiled(bundle):
    path = bundle / "bundle.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["env"] = {"kind": "exec", "image": "python:3.11-slim"}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(authored.BundleError):
        authored.load(bundle.name, authored_root=bundle.parent)


def test_generated_adapter_matches_generator():
    assert (ROOT / 'data/datasets/authored_example_fix_bug.py').read_text() == authored.module_source('example-fix-bug')


def test_installer_bytecode_does_not_change_fixture_metadata_or_tasks(tmp_path):
    import py_compile
    import shutil
    copied = tmp_path / 'example-fix-bug'
    shutil.copytree(EXAMPLE, copied)
    before = authored.load(copied.name, authored_root=tmp_path)
    metadata = authored.download_entry(copied.name, authored_root=tmp_path)
    assert metadata['availability'] == 'ready'
    for source in copied.rglob('*.py'):
        py_compile.compile(str(source), doraise=True)
    assert list(copied.rglob('*.pyc'))
    assert authored.load(copied.name, authored_root=tmp_path) == before
    assert authored.download_entry(copied.name, authored_root=tmp_path) == metadata
    assert not any('__pycache__' in row['path'] or row['path'].endswith(('.pyc', '.pyo'))
                   for setup in before[1].values() for row in setup['files'])
    (copied / 'materials/t01/unexpected.bin').write_bytes(b'\x00\xff')
    assert authored.download_entry(copied.name, authored_root=tmp_path)['availability'] == 'invalid'
    with pytest.raises(authored.BundleError, match='不是文本文件'):
        authored.load(copied.name, authored_root=tmp_path)


@pytest.mark.parametrize('malformed', [[], {'env': ['files']}, {'env': {'kind': 'files'}, 'tasks': ['bad task']}])
def test_authored_metadata_marks_malformed_bundle_invalid(tmp_path, malformed):
    target = tmp_path / 'broken'
    target.mkdir()
    (target / 'bundle.json').write_text(json.dumps(malformed))
    entry = authored.download_entry('broken', authored_root=tmp_path)
    assert entry['availability'] == 'invalid'
    assert entry['detail']
