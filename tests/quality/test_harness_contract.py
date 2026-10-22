"""What the platform needs from a harness: the contract, not the content.

Which behaviours a harness has, and how they work, belongs to whoever writes it.
The platform needs exactly three things from any harness, and this file checks
those three against the reference one:

  1. it runs a task and writes an answer, so it can be measured at all;
  2. it reports what the run cost, so a harness cannot look free while spending;
  3. an edit a method makes to it survives into the run, so a method's improvement
     is not silently dropped.

Anything about *how* the harness behaves -- its context policy, its truncation
rules, its prompts -- is not the platform's business and is not tested here. An
earlier version of this file did test those, for a mechanism layer that came from
a different project's experiment design; it has been removed, and this is what
replaced the part that was ever ours.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "base_harness" / "loop"


def _run(candidate: Path, goal: str = "Answer with the word alpha.") -> dict:
    """Run a harness on one task; return the last line of its trace."""
    workdir = Path(tempfile.mkdtemp(prefix="hg-contract-"))
    try:
        (workdir / "task.json").write_text(json.dumps(
            {"task_id": "t", "goal": goal}))
        proc = subprocess.run(
            [sys.executable, str(candidate / "agent.py"),
             "--task", str(workdir / "task.json"), "--workdir", str(workdir)],
            capture_output=True, text=True, timeout=180, cwd=workdir,
        )
        assert proc.returncode == 0, proc.stderr[-500:]
        lines = [json.loads(l) for l in (workdir / "trace.jsonl").read_text().splitlines()
                 if l.strip()]
        return lines[-1]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_the_reference_harness_runs_a_task_and_writes_an_answer():
    """Contract point 1. Without this the harness cannot be measured, and every
    later question about it is unanswerable."""
    last = _run(HARNESS)
    assert last is not None


def test_the_harness_reports_what_the_run_cost():
    """Contract point 2. A harness that cannot report its own spending cannot be
    compared with a cheaper one at the same score, and a method's edit could make
    it arbitrarily expensive without the record showing it."""
    last = _run(HARNESS)
    assert "usage" in last, (
        "the trace must carry a usage block; it is the only place model calls are "
        "counted"
    )
    usage = last["usage"]
    for field in ("calls", "input_tokens", "output_tokens", "retries"):
        assert field in usage, f"usage must report {field!r}"
        assert isinstance(usage[field], int), (
            f"usage.{field} must be an int; a missing provider value stays absent "
            "rather than becoming a zero that reads as a real measurement"
        )


def test_a_methods_edit_survives_into_the_run(tmp_path):
    """Contract point 3, and the one that fails silently.

    A method's improvement is dropped somewhere between its candidate directory
    and the harness process more easily than it looks: an environment variable
    that is not forwarded, a file the adapter forgets to copy, a config the
    harness no longer reads. In every case the curve shows no change, which is
    indistinguishable from "the improvement did not help".
    """
    candidate = tmp_path / "candidate"
    shutil.copytree(HARNESS, candidate)

    before = _run(candidate)
    marker = "# MARKER-ADDED-BY-A-METHOD"
    agent = candidate / "agent.py"
    agent.write_text(marker + "\n" + agent.read_text())
    after = _run(candidate)

    assert after["usage"]["calls"] >= before["usage"]["calls"] - 1, (
        "the edited harness must still run; an edit that breaks the harness is a "
        "different failure, and the platform reports it rather than hiding it"
    )
    assert agent.read_text().startswith(marker), (
        "the edit must still be present after a run: the harness is copied per "
        "task, and a copy that loses the edit measures the wrong harness"
    )


# ------------------------------- harness 的 import 必须由它自己的依赖满足
#
# 这不是理论风险。实测(2026-10-02):为了让 harness 不复用空闲的 HTTP 连接,我在 `loop`
# 里写了 `import httpx` 去构造一个客户端 —— 而它声明的 `openai` 3.22.1 依赖的是
# **`httpx2`**(这个平台给 harness 装依赖用的是覆盖层,里面没有 `httpx`)。结果每次模型
# 调用都 `ModuleNotFoundError`,五道题各 0.9 秒就崩,而平台把这一轮记成了"五道题 0 分"。
#
# 一两行 import 就能把一次几小时的运行变成一堆 0,所以它值得一条不变量:
# **harness 里出现的非标准库 import,必须能在它自己的覆盖层里找到。**
def _harness_imports(entry: Path) -> set[str]:
    import ast
    tree = ast.parse(entry.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names - set(sys.stdlib_module_names)


def _overlay_importables(harness_dir: Path) -> set[str] | None:
    """这个 harness 的覆盖层里能 import 到的顶层名字。没有覆盖层就返回 None。"""
    from eval.containers import harness_runtime
    spec = harness_runtime.install_spec(harness_dir)
    if spec is None:
        return None
    root = harness_runtime.overlays_root() / spec[0][:16]
    if not root.is_dir():
        return None
    names: set[str] = set()
    for version_dir in root.iterdir():
        if not version_dir.is_dir():
            continue
        for child in version_dir.iterdir():
            if child.is_dir() and not child.name.endswith((".dist-info", ".data")):
                names.add(child.name)
    return names or None


def test_each_harness_imports_only_what_its_overlay_provides():
    """没有覆盖层时跳过并说明 —— 但一旦构建过,这条就必须成立。"""
    checked = 0
    for harness_dir in sorted((ROOT / "base_harness").iterdir()):
        entry = harness_dir / "harness.json"
        if not entry.is_file():
            continue
        names = _harness_imports(harness_dir / "agent.py")
        if not names:
            continue
        provided = _overlay_importables(harness_dir)
        if provided is None:
            continue
        checked += 1
        missing = sorted(names - provided)
        assert not missing, (
            f"{harness_dir.name}: import 了 {missing},而它的覆盖层里只有 "
            f"{sorted(provided)} —— 这会让每次模型调用都 ModuleNotFoundError,"
            f"而一次运行会把这种崩溃记成'这道题 0 分'")
    if checked == 0:
        pytest.skip("这台机器上没有构建过 harness 覆盖层;先跑 tools/configure_harness.py")
