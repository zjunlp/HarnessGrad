"""把 agent CLI 当作 base harness —— 这条链路能不能通,以及靠什么通。

这一族 harness(Claude Code / Codex / OpenCode 一类)有四个和我们的 harness 不一样的地方,
每一个都足以让它们**跑都跑不起来**,而且失败的样子都像"这个 harness 很弱":

  1. 它们把会话状态写在 `$HOME` 底下,而沙箱是从**空 root** 起的 —— 宿主真实的 `$HOME`
     在那里根本不存在,`mkdir` 直接 ENOENT。
  2. 它们不收 `--task`/`--workdir`,也不写 `answer.txt`;需要一个翻译层。
  3. 它们的模型来自**自己的配置**,不是 `HG_AGENT_*`,所以平台以为自己知道的模型是错的。
  4. 它们的可执行文件不一定在 `/usr` 下(venv、自建目录),而沙箱只绑了固定几个树。

四条各有一个测试,外加一条端到端的:用一个假 CLI 把 `verify_demo` 全做对,由平台的
验证器判定。整套东西的意义就在这里 —— 不是"能启动",是**能被公平地测量**。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.run.runner import run_one                                      # noqa: E402


@pytest.fixture
def work_root(tmp_path):
    """Per-test work tree: one test's cleanup must never delete another's run."""
    root = tmp_path / "work"
    root.mkdir()
    return root


#: Where the fake CLI is put for these tests: **outside the platform, outside the work
#: root, and outside `/tmp`**. All three matter, and the first version of this fixture
#: got it wrong by putting the CLI under the work root -- which the sandbox already
#: binds read-only, so the "unreachable without a declaration" test passed the wrong
#: way (the CLI was reachable with no declaration at all).
#:
#: It must also be unique per test. The previous version used one fixed directory
#: (`ROOT.parent / "harnessgrad_cli"`) shared by every process in the suite, so two
#: test files running at once overwrote each other's CLI and cleaned up each other's
#: files.
#:
#: The location is chosen rather than demanded. `--basetemp` outside `/tmp` (what the
#: runner passes, and what the project's guidance recommends) puts `tmp_path` in a
#: good place and pytest cleans it up. A plain `pytest tests/quality/test_cli_harness.py`
#: uses pytest's default under `/tmp`, which the sandbox tmpfs-masks, so the CLI would
#: be invisible -- the fixture then falls back to a unique directory beside the
#: platform, which is outside both the platform and `/tmp`, and removes it afterwards.
@pytest.fixture
def cli_dir(tmp_path) -> Path:
    """A unique fake CLI, visible only when its runtime path is declared."""
    root = tmp_path / "cli"
    temporary = False
    if Path("/tmp") in root.resolve().parents:
        root = Path(tempfile.mkdtemp(dir=ROOT.parent, prefix="harnessgrad_cli-"))
        temporary = True
    else:
        root.mkdir()
    shutil.copy2(ROOT / "tools" / "fake_cli.py", root / "fake_cli.py")
    yield root
    if temporary:
        shutil.rmtree(root, ignore_errors=True)


def _harness(work_root: Path, body: str, *,
             runtime_paths: list[str] | None = None,
             env_kinds: list[str] | None = None) -> Path:
    repo = work_root / "run" / "workspace"
    repo.mkdir(parents=True, exist_ok=True)
    man = {"name": "probe", "version": "1", "entrypoint": "agent.py", "backend": "cli"}
    if runtime_paths:
        man["runtime_paths"] = runtime_paths
    if env_kinds is not None:
        man["env_kinds"] = env_kinds
    (repo / "harness.json").write_text(json.dumps(man))
    (repo / "agent.py").write_text(
        "import json, os, pathlib, sys\n"
        "ap = __import__('argparse').ArgumentParser()\n"
        "ap.add_argument('--task'); ap.add_argument('--workdir')\n"
        "a = ap.parse_args()\n"
        "work = pathlib.Path(a.workdir)\n"
        f"{body}\n")
    return repo


# ------------------------------------------------ 1) 每任务可写的 HOME ---

def test_every_task_gets_a_writable_home_and_not_the_real_one(work_root, monkeypatch):
    """CLI 活不活得下来,就看这一条。

    沙箱从空 root 起,里面只有 /usr /bin /sbin /lib* /etc 和平台显式绑的那几棵树 ——
    **`/home` 不存在**。而每一个 agent CLI 都会往 `$HOME` 底下写会话状态。
    """
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work_root))
    host_home = os.environ["HOME"]
    body = (
        "home = pathlib.Path(os.environ['HOME'])\n"
        "s = home / '.fakecli' / 'sessions'; s.mkdir(parents=True, exist_ok=True)\n"
        "(s / 'x.json').write_text('{}')\n"
        "work.joinpath('answer.txt').write_text(json.dumps({\n"
        "  'home': str(home),\n"
        "  'writable': (s / 'x.json').exists(),\n"
        f"  'host_home_visible': pathlib.Path({host_home!r}).exists(),\n"
        "  'tmpdir': os.environ.get('TMPDIR'),\n"
        "}))\n"
    )
    repo = _harness(work_root, body)
    r = run_one(repo, {"task_id": "t", "goal": "g"}, sandbox=True,
                verifier={"kind": "answer", "expected": "never"})
    got = json.loads(r["answer"])

    assert got["writable"] is True, got
    assert got["home"].startswith(str(work_root / "run")) or "hg-task" in got["home"], got
    assert got["host_home_visible"] is False, (
        "宿主真实的 $HOME 在沙箱里可见 —— 那说明 CLI 会去写一个共享的、跨任务的状态目录")
    assert got["tmpdir"], got


def test_the_home_is_per_task_not_shared(work_root, monkeypatch):
    """两个任务不能共用会话状态。

    同一个论证已经在 `.state/` 上做过一次(tmpfs 而不是 bind):任务 N 留下的东西不能
    影响任务 N+1 的分数。
    """
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work_root))
    body = ("work.joinpath('answer.txt').write_text(os.environ['HOME'])\n")
    repo = _harness(work_root, body)
    a = run_one(repo, {"task_id": "a", "goal": "g"}, sandbox=True,
                verifier={"kind": "answer", "expected": "x"})
    b = run_one(repo, {"task_id": "b", "goal": "g"}, sandbox=True,
                verifier={"kind": "answer", "expected": "x"})
    assert a["answer"] != b["answer"], "两个任务拿到了同一个 HOME"


# ------------------------------------------------ 2) 身份与成本的自报 ---

def test_the_harness_declaration_beats_the_platform_environment(work_root, monkeypatch):
    """平台只知道自己那套 `HG_AGENT_*`;CLI 读的是它自己的配置。

    所以默认行为会让记录**说谎**,而这个项目已经吃过一次(`agent_model` 写着 `mock`
    而实际是真实模型在答题)。harness 自报,平台采信,不一致时**两个值都记**。
    """
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work_root))
    monkeypatch.setenv("HG_AGENT_MODEL", "platform-env-model")
    body = (
        "work.joinpath('trace.jsonl').write_text(\n"
        "  json.dumps({'harness_identity': {'agent_model': 'declared-model',\n"
        "                                    'agent_backend': 'cli',\n"
        "                                    'harness': 'some-cli 1.2'}}) + '\\n')\n"
    )
    repo = _harness(work_root, body)
    r = run_one(repo, {"task_id": "t", "goal": "g"}, sandbox=True,
                verifier={"kind": "answer", "expected": "x"})
    assert r["identity"] == {"agent_model": "declared-model", "agent_backend": "cli",
                             "harness": "some-cli 1.2"}


def test_the_curve_point_prefers_the_declaration_and_records_the_conflict(
        work_root, cli_dir, monkeypatch, tmp_path):
    """端到端:曲线点上是声明值,而且不一致这件事本身要留在记录里。"""
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work_root))
    monkeypatch.setenv("HG_AGENT_MODEL", "platform-env-model")
    monkeypatch.setenv("HG_CLI", f"{sys.executable} {cli_dir / 'fake_cli.py'} -p")
    monkeypatch.setenv("HG_CLI_MODEL", "fake-model-1")
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")

    # Declare this test's CLI rather than relying on the example manifest's
    # machine-specific installation path. Leave the reference harness untouched.
    repo = tmp_path / "cli-agent"
    shutil.copytree(ROOT / "base_harness" / "cli_agent", repo)
    manifest = repo / "harness.json"
    spec = json.loads(manifest.read_text())
    spec["runtime_paths"] = [str(cli_dir)]
    manifest.write_text(json.dumps(spec))

    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", str(repo), "--mode", "A",
         "--rounds", "1", "--run-id", "ident", "--dataset", "verify_demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(work_root),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=600, env=os.environ.copy())
    assert proc.returncode == 0, f"{proc.stdout[-500:]}\n{proc.stderr[-500:]}"

    point = json.loads((runs / "ident" / "curve.jsonl").read_text().splitlines()[0])
    ident = point["identity"]
    assert ident["agent_model"] == "fake-model-1"
    assert ident["agent_model_source"] == "harness"
    assert ident["agent_model_conflict"]["platform_env"] == "platform-env-model", ident
    # 成本必须被翻译过来。读到 0 不是"规则弱",是"规则没在跑"。
    assert point["cost"]["harness_tokens"] > 0, point["cost"]


# ------------------------------------------------ 4) 声明式的运行时路径 ---

def test_a_cli_outside_usr_is_unreachable_until_the_manifest_declares_it(
        work_root, cli_dir, monkeypatch):
    """CLI 装在 `/usr` 之外时,harness 清单必须说出来。

    这和解释器前缀是同一件事:平台没法知道某个人的 agent CLI 装在哪,所以 harness 声明,
    平台照绑(只读、且在隐藏之前绑,所以声明平台自己的路径也还是会被藏掉)。
    """
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work_root))
    body = (
        "import subprocess\n"
        "p = subprocess.run([sys.executable, " + repr(str(cli_dir / "fake_cli.py")) +
        ", '--version'], capture_output=True, text=True)\n"
        "work.joinpath('answer.txt').write_text(p.stdout.strip() or 'UNREACHABLE')\n"
    )
    # 没声明:不可达
    repo = _harness(work_root, body)
    r = run_one(repo, {"task_id": "t", "goal": "g"}, sandbox=True,
                verifier={"kind": "answer", "expected": "x"})
    assert r["answer"].strip() == "UNREACHABLE", r["answer"]

    # 声明之后就可达
    repo2 = _harness(work_root, body, runtime_paths=[str(cli_dir)])
    r2 = run_one(repo2, {"task_id": "t", "goal": "g"}, sandbox=True,
                 verifier={"kind": "answer", "expected": "x"})
    assert r2["answer"].strip().startswith("fake-cli"), r2["answer"]


# ------------------------------------------------ 3) env_kinds 的拒绝 ---

def test_a_harness_that_cannot_operate_on_this_environment_is_refused(
        work_root, monkeypatch, tmp_path):
    """不能跑的环境要**当面拒绝**,而不是跑出 0 分让人去猜。

    声明 `env_kinds: ["exec"]`(不支持 files)的 harness 遇到一个 files 数据集,必须在
    任何一次评估之前停下来并说明原因。
    """
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work_root))
    repo = _harness(work_root, "pass\n", env_kinds=["exec"])
    shutil.copytree(ROOT / "base_harness" / "loop_plain", repo / "loop_home",
                    dirs_exist_ok=True)
    # 用 driver 直跑,harness 指向上面的 repo:用 --harness 的绝对路径形式
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", str(repo), "--mode", "A",
         "--rounds", "1", "--run-id", "kinds", "--dataset", "verify_demo",
         "--sampling", "all", "--runs-root", str(tmp_path / "runs"),
         "--work-root", str(work_root),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300, env=os.environ.copy())
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined[-400:]
    # 查实质而不是字面量:拒绝理由必须说清"这个数据集要什么"和"这个 harness 声明了什么",
    # 否则读的人只知道被拒了,不知道为什么。
    assert "declares only" in combined and "exec" in combined, combined[-400:]
