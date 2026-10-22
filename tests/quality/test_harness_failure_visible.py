"""harness 跑不起来这件事,必须在运行记录里说出来。

那只 harness 死了,而运行什么都没说
------------------------------------
一条实测的故障链,`loop` + `terminal_bench` + `rrsi`:

    base_harness/loop/agent.py:105  from openai import OpenAI
    ModuleNotFoundError: No module named 'openai'      exit code 1
    trace: 0 字节        score: 0.000        运行记录里的解释: 无

`exit_code` 和 `stderr` 在 `eval/run/runner.py` 里被算出来、放进返回值,然后**没有任何
东西持久化它们**。于是曲线上的读数和「方法决定不改」完全一样:

    RRSI rule: no edit (b_t=4)      train 0.000

用户看到的是后者。这不是「分数算错了」——那个 0.000 是真的,校验器读了产物、判它不合格
(§2.2:harness 崩了但留下了能用的产物,仍然算完成了任务)。缺的是**归因**。

所以 `harness_failed` 是一个独立的类别,不是 `invalid` 的别名:

    invalid         平台测不了 → 不写进 per_task(§2.5.7)
    harness_failed  平台测了   → 分数保留,但说明 harness 没留下任何证据

这里钉住三件事:
  * 空 trace 被识别成 harness 故障,带 exit code 和 stderr
  * 有 trace 时不误报(崩溃但留下产物的 harness 不该被当成没跑)
  * 端到端:这样的 harness 会让运行**印出一条带名字的警告**,而分数照旧
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.run.runner import _harness_failure                         # noqa: E402


# ------------------------------------------------------------ 判定的边界 ---

def test_an_empty_trace_with_a_crash_is_a_failure():
    got = _harness_failure({"trace": "", "exit_code": 1,
                            "stderr": "ModuleNotFoundError: No module named 'openai'"})
    assert got is not None
    assert got["exit_code"] == 1
    assert "openai" in got["stderr"]


def test_a_harness_that_worked_and_then_crashed_is_not_a_failure():
    """干了活之后才崩的 harness **不**在此列:§2.2 说那个任务算完成了,而 trace 能解释它。

    把这条判宽会让每一次「最后一条命令失败」的 harness 都被报成故障,警告就没人看了。
    """
    worked = '{"step": 0, "reply": "{\\"answer\\": \\"x\\"}"}\n{"usage": {"calls": 1}}\n'
    assert _harness_failure({"trace": worked, "exit_code": 1, "stderr": "boom"}) is None


def test_a_nonzero_exit_without_a_model_call_is_a_failure():
    """**第二条触发条件,来自一次实测。** 非空 trace 不等于 harness 跑起来了。

    2026-10-04 在当前平台上复现:`--harness cli_agent` 在没有 CLI 时只写一行
    `{"error": "HG_CLI is unset: no CLI to run"}`,一次模型都没调,退出码 2 —— 而曲线说的是
    `score 0.0, harness_failed: null`。控制台警告了、verdict 文件里也有退出码,事实存在两处,
    唯独不在读者比较跑道的地方(曲线点)。

    判据用 `usage` 块,因为它是 harness 契约要求的(`INTERFACE.md` §1.2),也是唯一与 harness
    内部结构无关的"它调用过模型"的证据。
    """
    unset = '{"error": "HG_CLI is unset: no CLI to run"}\n'
    got = _harness_failure({"trace": unset, "exit_code": 2, "stderr": "no CLI\n"})
    assert got is not None, "一个从没跑起来的 harness 不该读成干净的 0 分"
    # 只说平台知道的事实:`usage` 块是 harness 跑完之后才写的,所以"没有 usage 块"是事实,
    # "没有调用过模型"是错的推论(实测那三道题各有 15-23 次调用)。
    assert got["exit_code"] == 2
    assert "no `usage` block" in got["reason"] and "did not reach its end" in got["reason"]
    assert "model call" not in got["reason"]


def test_a_scripted_harness_that_makes_no_calls_and_exits_zero_is_fine():
    """`base_harness/oracle` 不调模型(它跑参考解),退出码 0 —— 不该被报成故障。"""
    assert _harness_failure({"trace": '{"step": 0}\n', "exit_code": 0,
                             "stderr": ""}) is None


def test_a_clean_exit_with_no_trace_is_still_reported():
    """没崩却不写 trace:分数照旧,但「方法没有任何东西可读」是个事实。

    平台自带的五只 harness 全都写 `trace.jsonl`,所以空 trace 永远是异常。
    """
    got = _harness_failure({"trace": "", "exit_code": 0, "stderr": ""})
    assert got is not None and got["exit_code"] == 0


def test_whitespace_is_not_a_trace():
    assert _harness_failure({"trace": "\n  \n", "exit_code": 2}) is not None


# ------------------------------------------------------- 端到端:要看得见 ---

def _dying_harness(root: Path) -> Path:
    """一只启动就死、不写 trace 的 harness。形状取自真实那一次。"""
    repo = root / "dies"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "harness.json").write_text(json.dumps({
        "name": "dies", "version": "0.0.1", "path": ".",
        "entrypoint": "agent.py", "backend": "cli", "env_kinds": ["files"],
    }))
    (repo / "agent.py").write_text(
        "import sys\n"
        "sys.stderr.write(\"ModuleNotFoundError: No module named 'openai'\\n\")\n"
        "sys.exit(1)\n")
    return repo


def test_a_harness_that_dies_is_named_in_the_run(tmp_path):
    """端到端:运行必须说出来,而不是只留下一个 0.000。

    这是那次运行的直接回归 —— 修复前这条断言拿到的是一份没有任何相关内容的输出。
    """
    repo = _dying_harness(tmp_path)
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", str(repo), "--mode", "A",
         "--rounds", "1", "--run-id", "dies", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(tmp_path / "runs"),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    combined = proc.stdout + proc.stderr
    assert "left no trace" in combined, combined[-1200:]
    assert "exit code 1" in combined, combined[-1200:]
    # 断言的是**异常名**而不是 "openai":后端名也叫 openai(`model ... · openai`),
    # 拿它当证据等于断言 `openai` 这个字符串出现过 —— 修不修都会通过。
    assert "ModuleNotFoundError" in combined, \
        "stderr 的内容没被带出来,读的人还是不知道为什么"
    assert "t01" in combined, "没说是哪道题"

    # 分数仍然记下来了 —— 这是归因,不是拒绝测量
    run_dir = tmp_path / "runs" / "dies"
    point = json.loads((run_dir / "curve.jsonl").read_text().splitlines()[0])
    assert point["score"] == 0.0
    assert point["n_harness_failed"] == 4, point.get("harness_failed")
    assert "t01" in point["harness_failed"]
    assert point["harness_failed"]["t01"]["exit_code"] == 1
    assert point["n_invalid"] == 0, "harness 故障不是 invalid:任务是被测过的"

    # 面板读的是事件流,所以警告也必须在那里
    events = [json.loads(l) for l in
              (run_dir / "events.jsonl").read_text().splitlines() if l.strip()]
    notes = [e for e in events if e.get("kind") == "note"
             and "left no trace" in json.dumps(e, ensure_ascii=False)]
    assert notes, "事件流里没有这条警告,面板就看不到它"


def test_a_healthy_harness_produces_no_such_warning(tmp_path):
    """反向:正常跑完的运行不该出现这条警告,否则它就成了噪音。"""
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "healthy", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(tmp_path / "runs"),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, proc.stdout[-600:]
    assert "left no trace" not in (proc.stdout + proc.stderr)
    point = json.loads((tmp_path / "runs" / "healthy" / "curve.jsonl")
                       .read_text().splitlines()[0])
    assert point["n_harness_failed"] == 0


if __name__ == "__main__":                                       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))


# ------------------------- 有 trace 但**非零退出**:归因要看得见,分类不许放宽 ---

def _said(monkeypatch) -> list[str]:
    from harnessgrad import environments
    out: list[str] = []
    monkeypatch.setattr(environments.console, "note",
                        lambda msg, **kw: out.append(msg))
    return out


def test_a_harness_that_died_after_it_started_is_named(monkeypatch):
    """`harness_failed` 回答"它启动了吗",回答不了"它为什么死在第 4 步"。

    实测:`headless-terminal` 跑了 4 步、退出码 1、`loop.ended = crashed`,而
    `RuntimeError: 3 attempts failed, last: Connection error` 这句平台**已经记下来了**
    (在 `harness_output` 里),却没有任何人读到 —— 日志没有,方法那一侧也没有。读的人
    只能自己猜是"模型写的代码不行"还是"harness 死了"。
    """
    from harnessgrad import environments
    said = _said(monkeypatch)
    environments._report_harness_failed({
        "harness_failed": {},
        "harness_output": {"t01": {
            "exit_code": 1,
            "stderr": "Traceback...\nRuntimeError: 3 attempts failed, last: "
                      "Connection error.\n"}}}, "terminal_bench")
    assert len(said) == 1, said
    assert "exited 1" in said[0] and "Connection error" in said[0], said[0]


def test_a_healthy_harness_is_not_warned_about(monkeypatch):
    """退出码 0 但 stderr 里有日志,不是故障 —— 否则每一次正常运行都带一条警告。"""
    from harnessgrad import environments
    said = _said(monkeypatch)
    environments._report_harness_failed({
        "harness_failed": {},
        "harness_output": {"t01": {"exit_code": 0, "stderr": "step 1: ls\n"}}},
        "terminal_bench")
    assert said == [], said


def test_the_empty_trace_case_is_reported_once_not_twice(monkeypatch):
    """两条分支都在,但同一道题只能响一次。"""
    from harnessgrad import environments
    said = _said(monkeypatch)
    environments._report_harness_failed({
        "harness_failed": {"t01": {"exit_code": 1, "stderr": "no trace",
                                   "reason": "the harness left no trace at all"}},
        "harness_output": {"t01": {"exit_code": 1, "stderr": "no trace"}}},
        "terminal_bench")
    assert len(said) == 1 and "left no trace" in said[0], said


def test_an_old_record_without_a_reason_does_not_get_one_invented(monkeypatch):
    """早于 `reason` 字段的记录里没有它。那时**不能编**一个原因出来 —— 说"没记录原因"
    比说错一个原因好,这和整个平台"只说知道的事"是同一条规矩。"""
    from harnessgrad import environments
    said = _said(monkeypatch)
    environments._report_harness_failed({
        "harness_failed": {"t01": {"exit_code": 1, "stderr": "boom"}}},
        "terminal_bench")
    assert len(said) == 1
    assert "no reason recorded" in said[0] and "left no trace" not in said[0], said
