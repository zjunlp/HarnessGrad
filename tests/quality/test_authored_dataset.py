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
    assert verify["t01"]["argv"] == ["python3", "test_broken.py"]


def test_the_checker_file_travels_with_verify_so_the_agent_cannot_edit_it():
    """`VERIFY.inputs` 在 agent 停下之后重新物化；只把测试放材料里，agent 改掉就没人管了。"""
    _tasks, _setup, verify = authored.load("example-fix-bug")
    assert [i["path"] for i in verify["t01"]["inputs"]] == ["test_broken.py"]


def test_a_published_dataset_is_loadable_through_the_registry():
    from data.scaffolds import registry
    name = "authored_example_fix_bug"
    module = registry.module_name(name)
    assert module == "data.datasets.authored_example_fix_bug"
    loaded = registry.load_tasks(name)
    tasks = loaded[0]
    assert [t["task_id"] for t in tasks] == ["t01"]
    assert loaded[1]["t01"]["files"]


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
