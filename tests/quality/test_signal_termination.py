"""平台自己停掉的任务不是 0 分;harness 自己崩的**是** 0 分。

这两句必须同时成立,而它们很容易被做反。`INTERFACE.md` §2.2:

> **Exit code and score are independent.** The harness's exit code says whether the
> *harness* finished; the verifier says whether the *task* was completed. A harness
> that crashed but left working artifacts still scores -- the platform grades the
> result, not the process.

所以第一版把"harness 崩溃"整体改判成 `unmeasured` 的方案是**错的**:它会把 134 个
合法的 0 分从均值里丢掉,而那些 0 分是真实的测量结果(verifier 真的跑过、真的判了 0)。

真正的缺陷有两个,都比那个小:

  * **`exit_code` 被丢掉了。** runner 在 `runner.py:536` 读到 `proc.returncode`,却从不
    写进 per-task 记录。于是"harness 崩溃"和"跑完了但没做出来"产生**逐字节相同**的记录。
    实测(AgentDojo train,601 题):479 个 0 分里有 134 个来自崩溃,而记录里一个字都没有 ——
    要靠读轨迹和 `events.jsonl` 才能查出来。
  * **被信号杀死的任务被记成了分。** `subprocess` 对信号杀死给负返回码,平台的
    `agent_timeout_s` 超时走另一条路(显式 `-1` + `timeout after Ns`)。没人检查符号,所以
    两者都掉给 verifier 记成普通 0 分。而 `_cancel` 自己写着:"a cancelled task is not a
    task that scored nothing"。

四种收尾,只有一种不该记分:

| `exit_code` | 收尾 | 该不该记分 |
| --- | --- | --- |
| `0` | harness 跑完 | 记分 |
| `> 0` | harness 自己崩 | **记分**(§2.2) |
| `-1` + timeout 标记 | 平台的 `agent_timeout_s` 用尽 | **记分**(预算是声明过的) |
| 其它 `< 0` | 被外部信号杀死(操作者看门狗、OOM) | **不记分** |

最后一种正是我们自己那批数据:train 侧 156,157,159,160 与 294,295,296,297,301,302,
303,307,eval 侧 257,259 —— 它们在集合里是**唯一真正错的 0 分**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import eval.observe.tracing as tracing  # noqa: E402
import eval.run.runner as runner  # noqa: E402


# --------------------------------------------------------------- 判据本身 ---

def test_process_outcome_separates_the_four_endings():
    """四值必须互不混淆,尤其 `-1` 的两种来源。"""
    assert tracing.process_outcome(0, "") == "completed"
    assert tracing.process_outcome(1, "Traceback ...\nUnicodeDecodeError: ...") == "harness_crash"
    assert tracing.process_outcome(2, "") == "harness_crash"
    assert tracing.process_outcome(-1, "timeout after 3600s") == "platform_timeout"
    # A bare -1 with no timeout marker is a signal (SIGHUP is signal 1), and the
    # platform's own marker is what tells the two apart.
    assert tracing.process_outcome(-1, "") == "stopped_by_signal"
    assert tracing.process_outcome(-9, "") == "stopped_by_signal"
    assert tracing.process_outcome(None, "") is None


def test_the_timeout_marker_is_the_only_thing_that_makes_a_negative_code_a_budget():
    """`runner.py` 超时那条路写的是 `timeout after Ns`;没有它就必须按信号算。"""
    assert tracing.process_outcome(-1, "  timeout after 900s\n") == "platform_timeout"
    assert tracing.process_outcome(-15, "timeout after 900s") == "platform_timeout"
    assert tracing.process_outcome(-15, "Killed") == "stopped_by_signal"


# ------------------------------------------- 判据接进真实评估路径(接线本身) ---

def _result(**over) -> dict:
    """与 `run_one` 返回同名字段的一次结果。"""
    result = {"task_id": "t1", "answer": "", "trace": "",
              "exit_code": 0, "stderr": "",
              "verdict": {"kind": "command", "passed": False, "score": 0.0,
                          "scorer_isolation_class": "actor-workdir",
                          "detail": "reward 0 from /logs/verifier/reward.txt"},
              "env_usage": {}, "model_gateway": None, "identity": None}
    result.update(over)
    return result


def _evaluate(tmp_path, monkeypatch, **over):
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: _result(**over))
    return runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=1)


def test_a_harness_that_crashed_on_its_own_is_still_a_score(tmp_path, monkeypatch):
    """**这条是本文件最重要的一条。**

    §2.2:verifier 判的是 artifact,不是进程。一个没留下 artifact 的崩溃 harness 就是
    没完成这道题,0 分是真实的测量结果。把它改成 `unmeasured` 会丢掉真实数据 —— 那正是
    这个文件第一版犯的错。
    """
    res = _evaluate(tmp_path, monkeypatch, exit_code=1,
                    stderr="Traceback (most recent call last):\nUnicodeDecodeError")
    assert res["invalid"] == {}, "harness 自己崩了不是 unmeasured,是 0 分"
    assert res["per_task"] == {"t1": 0.0}


def test_a_budget_timeout_is_still_a_score(tmp_path, monkeypatch):
    """`agent_timeout_s` 是**声明过的**预算,用尽就是没在预算内完成。"""
    res = _evaluate(tmp_path, monkeypatch, exit_code=-1,
                    stderr="timeout after 900s")
    assert res["invalid"] == {}
    assert res["per_task"] == {"t1": 0.0}


def test_a_signal_kill_is_not_a_score(tmp_path, monkeypatch):
    """平台没有按自己的条款停它、也没有把它当预算失败评过分 —— 没人测过它。"""
    res = _evaluate(tmp_path, monkeypatch, exit_code=-9, stderr="")
    assert res["per_task"] == {}, "被信号杀死不能变成 0 分"
    bad = res["invalid"]["t1"]
    assert bad["outcome"] == "stopped_by_signal"
    assert bad["stage"] == "harness"
    assert "signal 9" in bad["detail"] and "not a score" in bad["detail"]


def test_a_passing_artifact_survives_a_signal_kill(tmp_path, monkeypatch):
    """正证据不被推翻 —— 与 provider 规则同一个例外(§2.2)。"""
    res = _evaluate(tmp_path, monkeypatch, exit_code=-9, stderr="",
                    verdict={"kind": "command", "passed": True, "score": 1.0,
                             "detail": "ok",
                             "scorer_isolation_class": "actor-workdir"})
    assert res["invalid"] == {}
    assert res["per_task"] == {"t1": 1.0}


def test_a_completed_harness_that_scored_zero_is_untouched(tmp_path, monkeypatch):
    """回归:正常跑完的 0 分不能被新判据碰到。"""
    res = _evaluate(tmp_path, monkeypatch, exit_code=0, stderr="")
    assert res["invalid"] == {}
    assert res["per_task"] == {"t1": 0.0}


# --------------------------------------------------- 记录里必须留下过程 ---

def _record(tmp_path, measured, unmeasured=None):
    tracing._write_progress(tmp_path, 0, "t1", measured, unmeasured)
    path = Path(tmp_path) / "progress" / "round-0" / "t1.json"
    return json.loads(path.read_text())


def test_a_crash_zero_is_distinguishable_from_a_completion_zero(tmp_path):
    """**这就是断掉的那环。** 两种 0 分以前生成逐字节相同的记录。"""
    crash = _record(tmp_path, [_result(exit_code=1, stderr="Traceback: boom")])
    done = _record(tmp_path, [_result(exit_code=0)])
    assert crash["score"] == done["score"] == 0.0
    assert crash["exit_code"] == 1 and crash["process_outcome"] == "harness_crash"
    assert done["exit_code"] == 0 and done["process_outcome"] == "completed"


def test_the_record_names_a_budget_timeout_and_a_signal_apart(tmp_path):
    t = _record(tmp_path, [_result(exit_code=-1, stderr="timeout after 900s")])
    assert t["process_outcome"] == "platform_timeout"
    s = _record(tmp_path, [_result(exit_code=-9, stderr="")])
    assert s["process_outcome"] == "stopped_by_signal"


def test_an_unmeasured_record_carries_the_reason_as_its_outcome(tmp_path):
    rec = _record(tmp_path, [], {"stage": "harness",
                                 "outcome": "stopped_by_signal",
                                 "detail": "killed by signal 9"})
    assert "score" not in rec, "未测量的记录没有分数"
    assert rec["process_outcome"] == "stopped_by_signal"
    assert rec["invalid"]["stage"] == "harness"
