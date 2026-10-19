"""全局测量预算（`harnessgrad/slots.py`）：跨进程容量、崩溃安全、向后兼容。

这一层存在的理由是一条实测：九个方法各自 `--jobs 6` 时，**每轮九个一起进入 improver**，
GPU 在整段 improver 时间里空转（模拟：9 个同时跑 3.36 h，其中 100 min 空转）。把"每次运行
自己的预留"换成"全机一个容量预算 + 每个任务租一个槽"后是 2.21 h。所以这里要钉的正是
"`--jobs` 是上限、预算才是全局容量"这件事。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad import slots                                           # noqa: E402

CHILD = textwrap.dedent('''
    import os, sys, time
    sys.path.insert(0, {root!r})
    from harnessgrad import slots
    slots.configure({capacity}, {directory!r})

    def mark(kind):
        fd = os.open({trace!r}, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.write(fd, (kind + "\\n").encode())
        os.close(fd)

    with slots.slot():
        mark("in")
        time.sleep({hold})
        mark("out")
    {crash}
''')


def _run_children(tmp_path: Path, *, capacity: int, children: int, hold: float,
                  crash_first: bool = False) -> list[subprocess.Popen]:
    directory = tmp_path / "slots"
    trace = tmp_path / "trace.txt"
    procs = []
    for index in range(children):
        body = CHILD.format(root=str(ROOT), capacity=capacity, directory=str(directory),
                            trace=str(trace), hold=hold,
                            crash="" if not (crash_first and index == 0)
                            else "os._exit(7)  # 拿到槽位后直接死掉，不释放")
        procs.append(subprocess.Popen([sys.executable, "-c", body]))
    return procs


def _max_concurrent(trace: Path) -> int:
    inside, peak = 0, 0
    for line in trace.read_text().splitlines():
        inside += 1 if line.strip() == "in" else -1
        peak = max(peak, inside)
    return peak


def test_capacity_is_enforced_across_processes(tmp_path):
    """容量是**跨进程**的：六个进程同时要槽位，容量 2，则同时在里面的从不超过 2。

    这正是 `--jobs` 做不到的：六个 `--jobs 2` 的跑道会让端点同时看到 12 个任务。
    """
    procs = _run_children(tmp_path, capacity=2, children=6, hold=0.4)
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    assert _max_concurrent(tmp_path / "trace.txt") <= 2


def test_a_crashed_holder_does_not_leak_capacity(tmp_path):
    """进程带着槽位死掉，锁由内核释放 —— 不然一次 kill 会永久吃掉一格容量。"""
    procs = _run_children(tmp_path, capacity=2, children=3, hold=0.2, crash_first=True)
    for proc in procs:
        proc.wait(timeout=120)
    budget = slots.SlotBudget(tmp_path / "slots", 2)
    # 两个槽位都还能拿到，说明死掉的那个没有泄漏。
    with budget.hold():
        with budget.hold():
            pass


def test_two_runs_disagreeing_about_capacity_is_an_error(tmp_path):
    """容量是端点的事实，不是某个跑道的事实：两个不同的数不能悄悄共存。"""
    slots.SlotBudget(tmp_path / "slots", 4)
    with pytest.raises(slots.SlotBudgetError, match="already has capacity 4"):
        slots.SlotBudget(tmp_path / "slots", 9)


def test_the_same_capacity_may_be_configured_twice(tmp_path):
    slots.SlotBudget(tmp_path / "slots", 4)
    slots.SlotBudget(tmp_path / "slots", 4)          # 九条跑道都会走这一条


def test_no_budget_means_the_old_behaviour():
    """没有配置预算时 `slot()` 是空操作 —— 平台默认记录不能因为加了这层而变。"""
    slots.configure(None)
    with slots.slot():
        pass
    assert slots.current() is None


def test_configure_then_clear_restores_the_old_behaviour(tmp_path):
    slots.configure(3, tmp_path / "slots")
    assert slots.current() is not None and slots.current().capacity == 3
    slots.configure(None)
    assert slots.current() is None


def test_the_pool_never_exceeds_the_budget(tmp_path):
    """跑真实的任务池：`--jobs 8` 加预算 2，实际并发不得过 2。"""
    slots.configure(2, tmp_path / "slots")
    try:
        from eval.run import runner
        peak = 0
        inside = 0
        lock = __import__("threading").Lock()

        def measure(index, task):
            nonlocal peak, inside
            with lock:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.05)
            with lock:
                inside -= 1
            return {"task_id": task["task_id"], "per_task": {}, "score": 0.0}

        tasks = [{"task_id": f"t{i}"} for i in range(12)]
        runner._in_parallel(tasks, measure, 8)
        assert peak <= 2, f"jobs 是上限，预算才是全局容量；观测到 {peak}"
    finally:
        slots.configure(None)


def test_a_point_records_the_budget_it_was_measured_under(tmp_path):
    """曲线点必须能说出它是在多大的全局容量下产生的。"""
    from harnessgrad.identity import _identity
    man = {"name": "loop", "version": "0.2.0",
           "improve_entrypoint": None, "accept_entrypoint": None}
    with_budget = _identity(man, "abc", "A", jobs=6, trials=1, gpu_budget=40)
    assert with_budget["jobs"] == 6 and with_budget["gpu_budget"] == 40
    without = _identity(man, "abc", "A", jobs=6, trials=1)
    assert "gpu_budget" not in without, "没有预算就不要写一个假的数字"


def test_the_driver_arms_the_budget_without_a_budget_profile(tmp_path):
    """**实测过的回归**：`slots.configure()` 曾被放在 `if budget_profile_source:` 里，
    所以没传 `--budget-profile` 的 run —— 也就是我们每一个 run —— 收到了 `--gpu-budget`
    却把它执行到空处：槽位目录根本没被创建，九方法批次会给端点 216 路并发而不是 40。"""
    slots_dir = tmp_path / "slots"
    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--dataset", "demo", "--sampling", "all", "--rounds", "1", "--jobs", "2",
            "--gpu-budget", "3", "--gpu-budget-dir", str(slots_dir),
            "--run-id", "budget-armed", "--runs-root", str(tmp_path / "runs"),
            "--work-root", str(tmp_path / "work"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    assert (slots_dir / "capacity").read_text().strip() == "3", (
        "驱动没有配置预算：槽位目录不存在，`--gpu-budget` 等于没写")


def test_a_point_records_no_budget_when_none_was_armed(tmp_path):
    """没有预算的 run 不能在点上写一个假的容量。"""
    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--dataset", "demo", "--sampling", "all", "--rounds", "1", "--jobs", "1",
            "--run-id", "no-budget", "--runs-root", str(tmp_path / "runs"),
            "--work-root", str(tmp_path / "work"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, proc.stderr[-600:]
    points = [json.loads(l) for l in
              (tmp_path / "runs" / "no-budget" / "curve.jsonl").read_text().splitlines()
              if l.strip()]
    assert points and all("gpu_budget" not in p["identity"] for p in points)
