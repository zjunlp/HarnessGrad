"""进度必须是记录的一部分,不是打印的副产品。INTERFACE.md §4.46。

这个模块存在的理由是一次实测:`loop-terminal_bench-26452` 的 round 0 跑了五道题、
约 100 分钟,driver 全程在工作,而 `events.jsonl` 里只有 **11 条**事件 —— 六条 note、
一个 header、一条 warn、一个 round。面板每 1.5 秒轮询一次、只在事件变化时重画,于是
那一小时里它显示的是同一屏。从外面看,「在跑」和「卡死」没有区别。

所以这里检查的不是"有没有打印进度",而是四件可以被证伪的事:

  * 一个 task 的开始、结束、耗时和分数真的进了事件流;任务失败时也进,且带原因。
  * 一个 phase 的开始、结束、耗时进了;抛异常的 phase 也有结束记录。
  * 在一个会阻塞很久的命令上,心跳按间隔出现 —— 这是"看起来卡住"那一小时的解药。
  * 真实 driver 跑一遍 `demo`,轮内事件真的在(而不是只有单元测试里在)。

最后一条是这里的重点。第一版把进度事件只放在 context 里,而 driver 自己的
`evaluate` 调用没有设 context:结果是真实 run 报了方法调用的 phase,却对那四个真正
花掉一小时的 task 一个字都不写 —— 一个只讲最短等待、藏起最长等待的日志,比不报还糟。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import eval.observe.progress as progress                                    # noqa: E402
from eval.observe.console import Console                                    # noqa: E402


def _events(tmp_path: Path):
    """(console, 读事件流的函数)。事件是 Console 写的,和真实 run 同一条路径。"""
    path = tmp_path / "events.jsonl"
    console = Console(path)

    def read():
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    return console, read


# ------------------------------------------------------------ Reporter ---

def test_a_phase_records_its_start_end_and_elapsed_time(tmp_path):
    console, read = _events(tmp_path)
    with progress.reporting(console.reporter):
        with console.reporter.phase("verify", round_no=3, task_id="t1"):
            pass
    phases = [e for e in read() if e["kind"] == "phase"]
    assert [e["status"] for e in phases] == ["start", "done"], phases
    assert all(e["phase"] == "verify" for e in phases)
    assert all((e["round"], e["task"]) == (3, "t1") for e in phases)
    assert "elapsed_s" in phases[1] and phases[1]["elapsed_s"] >= 0


def test_a_phase_that_raises_still_says_it_ended(tmp_path):
    """没有这条,一个把任务跑挂的阶段会在记录里永远是 `start`。

    那正好是读日志的人最需要区分的情况:一个停在 start 的记录,和一个压根没写的
    阶段,在面板上是两种东西。
    """
    console, read = _events(tmp_path)
    reporter = console.reporter
    with pytest.raises(RuntimeError):
        with progress.reporting(reporter):
            with reporter.phase("agent", round_no=1, task_id="t1"):
                raise RuntimeError("harness blew up")
    done = [e for e in read() if e["kind"] == "phase" and e["status"] == "failed"]
    assert done, read()
    assert "harness blew up" in done[0]["detail"]


def test_a_task_records_start_end_score_and_status(tmp_path):
    console, read = _events(tmp_path)
    with progress.reporting(console.reporter):
        with progress.task_progress("t9", round_no=2, index=3, total=5) as held:
            held.score = 0.5
    tasks = [e for e in read() if e["kind"] == "task"]
    assert [e["status"] for e in tasks] == ["start", "done"], tasks
    assert all((e["index"], e["total"]) == (3, 5) for e in tasks)
    assert tasks[1]["score"] == 0.5
    assert tasks[1]["elapsed_s"] >= 0


def test_a_failed_task_is_recorded_as_failed_with_its_reason(tmp_path):
    console, read = _events(tmp_path)
    with progress.reporting(console.reporter):
        with progress.task_progress("t9") as held:
            held.failed("no such image")
    last = [e for e in read() if e["kind"] == "task"][-1]
    assert last["status"] == "failed"
    assert "no such image" in last["detail"]


def test_beats_appear_while_a_phase_is_still_running(tmp_path, monkeypatch):
    monkeypatch.setenv(progress.ENV_BEAT_S, "0.05")
    console, read = _events(tmp_path)
    with progress.reporting(console.reporter):
        with console.reporter.phase("verify", round_no=1, task_id="t1"):
            import time
            time.sleep(0.25)
    beats = [e for e in read() if e["kind"] == "beat"]
    assert beats, "一个跑了 250ms 的阶段、间隔 50ms 的心跳,一条 beat 都没有"
    assert all(e["phase"] == "verify" for e in beats)
    assert all(e["task"] == "t1" for e in beats)


def test_an_invented_phase_name_is_refused(tmp_path):
    """面板按名字分组、按名字写中文。一个随手起的名字,面板解释不了。"""
    console, _ = _events(tmp_path)
    with pytest.raises(KeyError):
        with console.reporter.phase("almost_done"):
            pass


def test_progress_works_with_no_run_at_all():
    """单元测试和直接调用没有 reporter,进度必须变成 no-op 而不是抛异常。"""
    with progress.reporting(None):
        with progress.task_progress("t1") as held:
            held.score = 1.0
        reporter = progress.current()
        assert reporter is None
        assert progress.task_of() is None


# -------------------------------------------------- 阻塞命令上的心跳 ---

class _TimingOutPopen:
    """一次 `wait` 超时、一次返回的假进程。用它证明心跳是**在等待中**写的。"""

    def __init__(self, argv, **kw):
        self.argv = argv
        self.returncode = 0
        self.stdout = io.StringIO("the subject's output\n")
        self.stderr = io.StringIO("")
        self.calls = 0
        self.killed = False

    def wait(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return 0

    def kill(self):
        self.killed = True


def test_a_long_docker_call_beats_while_it_waits(tmp_path, monkeypatch):
    """`docker exec` 一个小时的 harness:等在里面的那段时间必须有事件。

    这是这一整套东西存在的那个场景本身 —— 11:52 到 12:13 之间平台一条事件都没写,
    而它当时正在等一个容器把题做完。

    心跳由**阶段**负责(`eval/observe/progress.py` 里那条线程),不是由 stream 循环负责:
    第一版两处都在 beat,于是同一个间隔里出现两条记录 —— 一份自相矛盾的日志。
    这条测试因此分两半:stream 循环把输出读回来、按超时抛异常(下一条测试);
    阶段在**一个很长的等待**期间持续 beat(这一条)。
    """
    import eval.containers.container as container
    import threading
    monkeypatch.setenv(progress.ENV_BEAT_S, "0.05")
    console, read = _events(tmp_path)
    monkeypatch.setattr(container.subprocess, "Popen", _TimingOutPopen)
    reporter = console.reporter
    with progress.reporting(reporter):
        with reporter.phase("agent", round_no=4, task_id="t1"):
            # 半个心跳间隔,等第一条 beat 真的写出来。以前只是调一次
            # `_run_streaming`,它在毫秒内返回,于是根本没有 beat 可断言 ——
            # 一条永远为空的断言。
            threading.Event().wait(0.3)
            proc = container._run_streaming(["docker", "exec", "c", "true"], 30,
                                            "docker exec")
    assert proc.returncode == 0
    assert proc.stdout == "the subject's output\n"
    beats = [e for e in read() if e["kind"] == "beat"]
    assert beats, "等待中的那段时间没有写出任何 beat"
    assert {e["phase"] for e in beats} == {"agent"}
    assert {e["task"] for e in beats} == {"t1"}
    # 时间不倒退,而且不是同一条被写了两遍。
    times = [e["elapsed_s"] for e in beats]
    assert times == sorted(times), times
    assert any(t > 0 for t in times), times


def test_the_streaming_runner_still_raises_on_timeout(monkeypatch):
    """心跳不能把超时吃掉。超时是 enforcer 的一部分,不是可以顺带丢掉的细节。"""
    import eval.containers.container as container

    class _NeverFinishes(_TimingOutPopen):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(self.argv, timeout)

    monkeypatch.setattr(container.subprocess, "Popen", _NeverFinishes)
    with pytest.raises(subprocess.TimeoutExpired):
        with progress.reporting(None):
            container._run_streaming(["docker", "exec", "c", "sleep"], 0.01,
                                     "docker exec")


# ------------------------------------------- 真实 driver 的事件流 ---

def test_a_real_run_narrates_its_tasks_inside_the_round(tmp_path):
    """端到端:一次真的 driver 运行,轮**内**就有事件。

    这条是回归线。上面每一条单元测试都可以在 driver 不接 context 的情况下通过 ——
    实测就是这样:方法调用的 phase 进了事件流,四道真题一条 task 事件都没有。
    """
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock", progress.ENV_BEAT_S: "0.2"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop", "--mode", "A",
         "--rounds", "1", "--run-id", "progress-check", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         # `echo_base` 交回一棵**真的变了**的树,所以第一轮候选会被评估一次 —— 用 `noop`
         # 不行:它不改动,候选与 H0 内容相同,于是命中的是缓存,一条 task 事件都不会有
         # (那正是下面 `test_a_reused_measurement_narrates_nothing` 钉住的行为)。
         #
         # 用 `loop` 而不是 `loop_plain`:`echo_base` 的锚点(`if "answer.txt exists"`)只在
         # `loop` 里存在,在 `loop_plain` 里它每轮都报"没有改动"。这条测试以前用
         # `loop_plain` 也能通过,靠的是 H0 缓存键那个缺陷带来的重测 —— 见
         # `test_each_rounds_task_phases_carry_their_own_round_number` 里的说明。
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/echo_base/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"

    events = [json.loads(l) for l in
              (runs / "progress-check" / "events.jsonl").read_text().splitlines()
              if l.strip()]
    kinds = [e["kind"] for e in events]
    assert kinds.count("task") >= 8, f"四道题两轮,应该有 8 条 task 事件:{kinds}"
    assert kinds.count("phase") >= 8, kinds

    # 一轮之内的顺序必须是 task -> setup -> agent -> verify -> task,不能是"轮结束
    # 时一把补上"。补上的事件证明不了"当时在跑"。
    first_round = events.index(next(e for e in events if e["kind"] == "round"))
    after = [e for e in events[first_round:] if e["kind"] in ("task", "phase")]
    assert any(e["kind"] == "phase" and e["phase"] == "agent" for e in after), after[:5]
    assert any(e["kind"] == "task" and e["status"] == "done" for e in after)


def test_progress_lines_do_not_flood_the_terminal(tmp_path):
    """心跳只进事件流,不进 stdout:一小时 360 行会把轮次表顶到屏幕外。"""
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock", progress.ENV_BEAT_S: "0.05"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "progress-quiet", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, proc.stderr[-500:]
    events = [json.loads(l) for l in
              (runs / "progress-quiet" / "events.jsonl").read_text().splitlines()
              if l.strip()]
    assert any(e["kind"] == "beat" for e in events), "这次 run 没产生心跳,测试没意义"
    # 心跳在事件流里,但 stdout 里一条都没有。
    assert "beat" not in proc.stdout
    assert proc.stdout.count("阶段") == 0 or "超过它自己声明的" not in proc.stdout


def test_each_rounds_task_phases_carry_their_own_round_number(tmp_path):
    """事件流里的 `round` 必须是真的轮次,否则日志分不清一条 task 属于哪一轮。

    实测:每一轮候选评估的 create/prepare/agent/snapshot/verify 全被标成 `round: 0`
    —— traces 和 verdicts 一直是按轮存的,只有这份**实时记录**把轮号丢了。方法诊断时
    读的就是这份记录。
    """
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock", progress.ENV_BEAT_S: "30"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop", "--mode", "A",
         "--rounds", "2", "--run-id", "round-labels", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         # `echo_base` 每轮真的改一个文件,所以第一轮候选会被**评估** —— 用 noop 不行:
         # 它报 `changed: false`,平台就停了,没有候选轮的 task 事件可查。
         #
         # **而且必须用 `loop`,不能用 `loop_plain`。** `echo_base` 靠一个锚点字符串定位
         # 它要改的那段(`if "answer.txt exists" in task_text`),那个锚点在 `loop_plain`
         # 里**不存在**,于是它每轮都返回"没有改动"。这条测试以前用 `loop_plain` 却能通过,
         # 靠的正是本轮修掉的那个缓存缺陷:H0 的键是字面量 `"H0"`、候选的键是内容哈希,
         # 内容相同的候选因此永远命不中缓存、被**重测**一遍 —— 于是"候选轮"看起来发生了。
         # 缓存按内容寻址之后,这个假象消失,测试当场失败:它一直在测一件没发生的事。
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/echo_base/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=600, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    events = [json.loads(l) for l in
              (runs / "round-labels" / "events.jsonl").read_text().splitlines()
              if l.strip()]
    # 只看 `verify`:方法调用那一轮本来就叫 `agent` 且轮号是对的,用它当断言等于什么都没测。
    task_rounds = {e.get("round") for e in events
                   if e["kind"] == "phase" and e["phase"] == "verify"}
    # H0 加两轮候选:`--rounds 2` 的曲线点是 0、1、2,不是 0、1(断言旧值 `{0, 1}` 之所以
    # 通过,是因为 `echo_base` 在 `loop_plain` 上什么都没改、平台因此停在第一轮)。
    assert task_rounds == {0, 1, 2}, sorted(task_rounds)


def test_a_reused_measurement_narrates_nothing(tmp_path):
    """缓存命中的一轮**不该**有 task 事件 —— 因为那四道题这一次没有跑。

    实测(`B2-noop`, 2026-10-04):对照方法不改动任何东西,而 round 1 把五道题重测了一遍,
    于是事件流看起来像"第二轮做了很多工作"。缓存按内容寻址之后,这一轮什么也没跑,所以
    也没有可报的进度 —— "复用的测量"和"新做的测量"在事件流里也必须不同形。
    """
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock", progress.ENV_BEAT_S: "30"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "2", "--run-id", "progress-cached", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    point = [json.loads(l) for l in
             (runs / "progress-cached" / "curve.jsonl").read_text().splitlines()
             if l.strip()][-1]
    assert point.get("n_cached"), "前置条件:这一轮必须全是缓存命中"
    events = [json.loads(l) for l in
              (runs / "progress-cached" / "events.jsonl").read_text().splitlines()
              if l.strip()]
    # 真实题目的 task/phase 事件只能属于 round 0(H0 那次测量)。缓存命中的那一轮没有
    # 跑任何题,所以一条都不该有 —— 它和自己的 round 号绑在一起,不会被误读成"跑了两次"。
    rounds_with_tasks = {e.get("round") for e in events
                         if e["kind"] in ("task", "phase")
                         and (e.get("task") or "").startswith("t")}
    assert rounds_with_tasks == {0}, sorted(rounds_with_tasks, key=str)
