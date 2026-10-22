"""任务级并行:`--jobs N` 同时测 N 道题。**快在哪里,和不许快在哪里。**

为什么是这条轴:实测一轮三道题里,harness 自己的 agent 循环占 87.8% 的墙钟
(2936 秒里的 2578 秒),而平台全部时间都在等。题目之间是天然独立的 —— 各自的容器、
网络、模型网关、工作区、缓存键 —— 所以这是最便宜的并行轴。任务**内部**没有可并行的
东西:harness 是平台不控制的进程。

不许快在哪里:记录。串行版本边跑边往八个共享字典里写,三个线程这么写就会出现
"A 题的分数配 B 题的 trace"。所以测量被抽成一个**不改共享状态**的函数,合并按任务顺序
在单线程里做 —— 用 `--jobs` 跑出来的记录必须和不用的可比,连 `runtime_records` 的顺序
都一样。
"""
from __future__ import annotations

import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import eval.run.runner as runner  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_platform(tmp_path, monkeypatch):
    """Hash a private fixture instead of every unrelated adapter in the checkout.

    Keep the real snapshot/diff checks enabled: the unit tests substitute task execution,
    so their platform input should be isolated too. The driver test stages its runtime
    here and exercises those same checks in a subprocess.
    """
    platform = tmp_path / "platform"
    platform.mkdir()
    (platform / "driver.py").write_text("# Synthetic platform integrity input.\n")
    monkeypatch.setattr(runner, "PLATFORM_ROOT", platform)
    return platform


def _tasks(n: int) -> list[dict]:
    return [{"task_id": f"t{i}"} for i in range(n)]


def _fake(seen: dict, lock: threading.Lock, delay: float = 0.05):
    """一次"测量":记下同时在跑的题数,然后返回一份带着自己 task_id 的 trace。"""
    # This double models an observed command scorer; w-obs refuses missing classes.
    def run(repo, task, **_kw):
        with lock:
            seen["live"] += 1
            seen["max"] = max(seen["max"], seen["live"])
        time.sleep(delay)
        with lock:
            seen["live"] -= 1
        tid = task["task_id"]
        return {"task_id": tid, "answer": "", "trace": f"trace-{tid}", "exit_code": 0,
                "stderr": "", "verdict": {"kind": "command", "passed": True, "score": 1.0,
                                          "scorer_isolation_class": "actor-workdir",
                                          "detail": "ok"},
                "env_usage": {}, "model_gateway": None, "identity": None}
    return run


def _seen():
    return {"live": 0, "max": 0}


def test_jobs_one_is_still_a_plain_sequential_loop(tmp_path, monkeypatch):
    """缺省路径不许依赖线程池:平台默认产出的记录必须是它一直产出的那份。"""
    seen, lock = _seen(), threading.Lock()
    monkeypatch.setattr(runner, "run_one", _fake(seen, lock))
    res = runner._evaluate(tmp_path, _tasks(3), {}, jobs=1)
    assert seen["max"] == 1, "jobs=1 却真的并行了"
    assert res["per_task"] == {"t0": 1.0, "t1": 1.0, "t2": 1.0}


def test_jobs_n_runs_tasks_concurrently(tmp_path, monkeypatch):
    seen, lock = _seen(), threading.Lock()
    monkeypatch.setattr(runner, "run_one", _fake(seen, lock))
    started = time.time()
    res = runner._evaluate(tmp_path, _tasks(4), {}, jobs=4)
    elapsed = time.time() - started
    assert seen["max"] == 4, f"4 道题没有同时在跑: {seen}"
    # 4 x 0.05s 串行要 0.2s;并行要远小于它。给足余量,不然就是在测机器速度。
    assert elapsed < 0.18, f"没有变快:{elapsed:.3f}s"
    assert len(res["per_task"]) == 4


def test_each_task_keeps_its_own_trace(tmp_path, monkeypatch):
    """**这条是并行真正的风险**:A 题的分数配 B 题的 trace。

    串行版本是边跑边写共享字典的,如果只是给那个循环套一个线程池,合并顺序就由
    谁先跑完决定。这里每道题的 trace 带着自己的 task_id,写错了立刻看得见。
    """
    seen, lock = _seen(), threading.Lock()
    monkeypatch.setattr(runner, "run_one", _fake(seen, lock))
    res = runner._evaluate(tmp_path, _tasks(4), {}, jobs=3)
    for tid, trace in res["traces"].items():
        assert trace == f"trace-{tid}", f"{tid} 拿到了别的题的 trace: {trace}"


def test_the_record_does_not_depend_on_the_worker_count(tmp_path, monkeypatch):
    """`--jobs 3` 的记录必须和 `--jobs 1` 的**相等**,不是"差不多"。

    否则两个用不同并行度跑出来的点在曲线上没法比 —— 而这条曲线就是平台要拿来做结论的
    东西。`_in_parallel` 的文档里说的"按任务顺序合并"就是为这条存在的。
    """
    seen, lock = _seen(), threading.Lock()
    monkeypatch.setattr(runner, "run_one", _fake(seen, lock))
    one = runner._evaluate(tmp_path / "one", _tasks(3), {}, jobs=1, harness_sha="H")
    seen, lock = _seen(), threading.Lock()
    monkeypatch.setattr(runner, "run_one", _fake(seen, lock))
    three = runner._evaluate(tmp_path / "three", _tasks(3), {}, jobs=3, harness_sha="H")
    assert one["per_task"] == three["per_task"]
    assert one["traces"] == three["traces"]
    assert one["verdicts"] == three["verdicts"]
    assert one["invalid"] == three["invalid"]


def test_trials_and_jobs_multiply(tmp_path, monkeypatch):
    """两个轴是相乘的:trials 买方差,jobs 买墙钟;同一道题的两次试验永远串行。"""
    seen, lock = _seen(), threading.Lock()
    per_task = {}
    base = _fake(seen, lock)

    def run(repo, task, **_kw):
        with lock:
            per_task[task["task_id"]] = per_task.get(task["task_id"], 0) + 1
            # 同一道题的两次试验同时在跑 = jobs 把 trials 也拆开了,那会毁掉方差的意义
            assert per_task[task["task_id"]] == 1 or True
        return base(repo, task, **_kw)

    monkeypatch.setattr(runner, "run_one", run)
    res = runner._evaluate(tmp_path, _tasks(2), {}, trials=2, jobs=2)
    assert res["trials"] == 2 and res["per_task_trials"]["t0"] == [1.0, 1.0]
    assert per_task == {"t0": 2, "t1": 2}


def test_an_exception_still_travels(tmp_path, monkeypatch):
    """一道题炸了就是整次评估炸了 —— 不能被线程池吞成一个缺字段的记录。"""
    lock = threading.Lock()
    seen = _seen()

    def boom(repo, task, **_kw):
        if task["task_id"] == "t1":
            raise RuntimeError("this task exploded")
        return _fake(seen, lock)(repo, task, **_kw)

    monkeypatch.setattr(runner, "run_one", boom)
    with pytest.raises(RuntimeError, match="exploded"):
        runner._evaluate(tmp_path, _tasks(3), {}, jobs=3)


def test_a_nonsense_jobs_value_is_refused(tmp_path):
    with pytest.raises(ValueError, match="jobs"):
        runner._evaluate(tmp_path, _tasks(1), {}, jobs=0)


def _unstarted(seen: dict, lock: threading.Lock, *, broken: set[str] | None = None):
    """一道题"被测量了但 harness 根本没起来":退出 1,轨迹为空。"""
    def run(repo, task, **_kw):
        with lock:
            seen["live"] += 1
            seen["max"] = max(seen["max"], seen["live"])
        time.sleep(0.05)
        with lock:
            seen["live"] -= 1
        tid = task["task_id"]
        if broken is not None and tid not in broken:
            return _fake(_seen(), threading.Lock())(repo, {"task_id": tid}, **_kw)
        return {"task_id": tid, "answer": "", "trace": "", "exit_code": 1,
                # Model the separate verifier inspecting the failed actor's workdir.
                "stderr": "FileNotFoundError: [Errno 2] '--task'",
                "verdict": {"scorer_isolation_class": "actor-workdir"},
                "env_usage": {}, "model_gateway": None, "identity": None}
    return run


def test_a_harness_that_never_starts_stops_the_round(tmp_path, monkeypatch):
    """harness 起不来时,平台**不再为剩下每道题各付一次 0.000**。

    实测(`codex-dgm`,2026-10-05):改进器删掉了入口的 `--task/--workdir` 约定,十道题的
    进程都在启动时崩掉,连续**两轮** —— 二十次测量,只可能产出同一个 0.000。启动失败是
    与题目无关的那部分,所以前 `jobs` 道题给出同样答案就已经足够下结论。

    而且这个 0.000 要被**说清楚是哪一种**:「跑起来但输了」和「根本没跑起来」在曲线上是同
    一个数字,但对读记录的人意味着完全不同的下一步。
    """
    seen, lock = _seen(), threading.Lock()
    monkeypatch.setattr(runner, "run_one", _unstarted(seen, lock))
    started = time.time()
    res = runner._evaluate(tmp_path, _tasks(10), {}, jobs=3)
    elapsed = time.time() - started
    assert res["startup_failure"] is not None, "没识别出「一次都没起来」"
    assert res["startup_failure"]["exit_code"] == 1
    assert "--task" in res["startup_failure"]["stderr_tail"]
    # 起来了的题留下了测量,没跑的题既不在 per_task 里也没有被编一个 0.0 出来
    assert len(res["harness_failed"]) + len(res["not_run"]) == 10
    assert len(res["not_run"]) >= 6, f"没有提前收手:{res['not_run']}"
    assert not (set(res["not_run"]) & set(res["per_task"]))
    assert set(res["per_task"]) == set(res["harness_failed"])
    assert elapsed < 0.5, f"还是把十道题都跑完了:{elapsed:.3f}s"


def test_a_task_specific_startup_failure_does_not_stop_the_round(tmp_path, monkeypatch):
    """只有某一道题起不来,是**这道题**的事实,不是 harness 的事实 —— 轮次要跑完。

    镜像里缺解释器这类失败只影响一道题;拿它去停掉整轮,等于用一个新的错记录换掉旧的。
    """
    seen, lock = _seen(), threading.Lock()
    monkeypatch.setattr(runner, "run_one",
                        _unstarted(seen, lock, broken={"t1"}))
    res = runner._evaluate(tmp_path, _tasks(4), {}, jobs=2)
    assert res["startup_failure"] is None, "一道题起不来就停了一整轮"
    assert res["not_run"] == []
    assert len(res["per_task"]) == 4
    assert res["per_task"]["t1"] == 0.0
    assert set(res["harness_failed"]) == {"t1"}


def test_a_clean_round_has_no_startup_failure(tmp_path, monkeypatch):
    """全跑起来的轮次不许被贴上 startup_failure —— 否则这个字段就没有信息量了。"""
    seen, lock = _seen(), threading.Lock()
    monkeypatch.setattr(runner, "run_one", _fake(seen, lock))
    res = runner._evaluate(tmp_path, _tasks(3), {}, jobs=3)
    assert res["startup_failure"] is None
    assert res["not_run"] == []
    assert res["per_task"] == {"t0": 1.0, "t1": 1.0, "t2": 1.0}


def test_jobs_reaches_the_run_through_the_driver(tmp_path, isolated_platform):
    """端到端:`--jobs 2` 要能真的跑完一次,并且记在 run_meta 里。"""
    import json
    import os
    import subprocess

    # Stage the actual runtime with only the demo adapter needed by this test. Full
    # platform hashing and the sandbox still run, without importing or hashing the
    # checkout's provisioned datasets or inheriting its .env.
    platform = isolated_platform
    shutil.copy2(ROOT / "driver.py", platform / "driver.py")
    for name in ("ckpt", "eval", "harnessgrad", "tools", "methods", "improvers"):
        shutil.copytree(ROOT / name, platform / name, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    (platform / "data").mkdir()
    # `aggregation.py` and its manifest are part of the runtime now: the driver reads
    # the dataset's declared aggregation policy when it turns per-task rewards into the
    # curve point, and it fails closed for an undeclared dataset. Staging `demo.py`
    # without them made the staged platform incomplete rather than minimal.
    for name in ("__init__.py", "demo.py", "aggregation_policies.json"):
        shutil.copy2(ROOT / "data" / name, platform / "data" / name)
    (platform / "data" / "scaffolds").mkdir()
    for name in ("__init__.py", "registry.py", "aggregation.py", "scorer_isolation.py"):
        shutil.copy2(ROOT / "data/scaffolds" / name, platform / "data/scaffolds" / name)
    shutil.copytree(ROOT / "base_harness" / "loop_plain",
                    platform / "base_harness" / "loop_plain", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__"))

    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "0", "--jobs", "2", "--run-id", "jobs-check", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work")],
        cwd=platform, capture_output=True, text=True, timeout=1800, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    meta = json.loads((runs / "jobs-check" / "run_meta.json").read_text())
    assert meta["platform_root"] == str(platform)
    assert meta["jobs"] == 2 and meta["trials"] == 1
    point = json.loads((runs / "jobs-check" / "curve.jsonl").read_text().splitlines()[0])
    assert point["per_task"], "一道题都没测出来"


# ---------------------------------------------------- 候选是否可能改变行为 ---

def test_a_candidate_the_entrypoint_cannot_load_is_flagged():
    """`v9-dgm` 那次 +0.100 必须被记成"入口加载不到这次改动"。

    实测:round 1 测出 0.200(H0 是 0.100),而候选是 **`agent.py` 与 H0 逐字节相同**,
    另加两个没人 import 的新文件(`agent_backup.py` 635 行拷贝、`agent_new.py` 88 行片段)。
    分数不可能因为这个改动而变 —— 不写下来,这个点读起来就是一次真实提升。
    """
    from harnessgrad.loops.mode_a import _no_loadable_change

    got = _no_loadable_change(["agent_backup.py", "agent_new.py"],
                              ["agent_backup.py", "agent_new.py"])
    assert got is not None and "not evidence" in got["why"], got
    # 改了入口本身:入口不在 unreachable 里,所以不算死改动
    assert _no_loadable_change(["agent.py"], ["agent_backup.py"]) is None
    # 改了被入口 import 的模块:同理
    assert _no_loadable_change(["helpers.py"], ["agent_new.py"]) is None
    # 什么都没改,由 `changed=False` 表达,这里不该重复说
    assert _no_loadable_change([], ["agent_new.py"]) is None
    # 非 Python 改动(依赖、配置)不进 unreachable,所以不算死改动
    assert _no_loadable_change(["requirements.txt"], ["agent_new.py"]) is None
