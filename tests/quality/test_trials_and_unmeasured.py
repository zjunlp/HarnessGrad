"""两次测量的差别,和"根本没测到"——一个样本说不出这两件事里的任何一件。

为什么要单独钉这两条:

  * **一次评估就是一个样本。** 实测:同一份**没改过**的 harness、同一个模型、同一批题,
    一次拿到 0.333,下一次拿到 0.000 —— 因为模型偶尔吐出 harness 解析不了的工具调用方言,
    harness 就把这道题结束了。不记方差的曲线,撑不起"这次编辑有用"这个平台存在的理由。
  * **检查没跑成不是 0。** 实测 83/89 道 TB 题的检查要靠网络装自己的 pytest;装不上时
    reward 文件是个 0,而平台以前把这个 0 记在 harness 头上。现在平台能证明"报告没写出来"
    时,这道题归 `invalid`(不写进 per_task),和"平台测不出这道题"是同一类事实。
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

import eval.run.runner as runner  # noqa: E402

# Scored doubles below model an observed command verifier. w-obs requires that
# fact explicitly; unmeasured/invalid doubles and saved legacy evidence stay as-is.


# --------------------------------------------------------------- 方差 ---

def test_the_spread_is_a_population_standard_deviation():
    """全体标准差,不是样本标准差:n 次试验就是全部,不是从一个更大的总体里抽的。"""
    assert runner._spread([1.0]) == 0.0
    assert runner._spread([0.5, 0.5, 0.5]) == 0.0
    assert runner._spread([0.0, 1.0]) == pytest.approx(0.5)
    # mean 2/3, deviations -2/3, 1/3, 1/3 -> variance 2/9
    assert runner._spread([0.0, 1.0, 1.0]) == pytest.approx((2 / 9) ** 0.5)


def test_trials_average_and_record_their_spread(tmp_path, monkeypatch):
    """`--trials 2`:任务分数是两次的均值,并且两次都留在记录里。"""
    seq = iter([0.0, 1.0])

    def fake(*_a, **_k):
        return {"verdict": {"kind": "command", "passed": True, "score": next(seq),
                            "scorer_isolation_class": "actor-workdir",
                            "detail": "d"},
                "trace": "x", "exit_code": 0, "stderr": ""}

    monkeypatch.setattr(runner, "run_one", fake)
    res = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=2)
    assert res["per_task"]["t1"] == 0.5
    assert res["per_task_trials"]["t1"] == [0.0, 1.0]
    assert res["per_task_std"]["t1"] == pytest.approx(0.5)
    assert res["score_std"] == pytest.approx(0.5)
    assert res["trials"] == 2


def test_one_trial_records_no_spread_at_all(tmp_path, monkeypatch):
    """"缺字段"必须读成"只测了一次",不能读成"方差为零"。"""
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: {
        "verdict": {"kind": "command", "passed": True, "score": 1.0, "detail": "d",
                    "scorer_isolation_class": "actor-workdir"},
        "trace": "x", "exit_code": 0, "stderr": ""})
    res = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=1)
    assert res["per_task_std"] == {} and res["per_task_trials"] == {}
    assert res["score_std"] == 0.0


def test_trials_reach_the_curve_point_end_to_end(tmp_path):
    """端到端:`--trials` 要真的走到曲线点上,而不是只在 runner 里存在。"""
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "0", "--trials", "2", "--run-id", "trials-check", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work")],
        cwd=ROOT, capture_output=True, text=True, timeout=600, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-500:]}\n{proc.stderr[-500:]}"
    point = json.loads((runs / "trials-check" / "curve.jsonl").read_text().splitlines()[0])
    assert point["n_trials"] == 2
    assert "score_std" in point and "per_task_std" in point
    # 每道题一个方差(float),以及**两次试验各自的分数** —— 后者原来只在 runner 的结果里,
    # 运行结束后就没了(见下面那条测试)。
    assert point["per_task_std"] and all(v >= 0 for v in point["per_task_std"].values())
    assert point["per_task_trials"], point.keys()
    assert all(len(v) == 2 for v in point["per_task_trials"].values()), point["per_task_trials"]


def test_one_trial_puts_no_trial_list_on_the_point(tmp_path):
    """反向的一半:只测一次时不留 `per_task_trials`。

    一次试验的"列表"就是分数本身。写出来会让"我只测了一次"和"我测了三次都是同一个数"
    在记录里长得一样 —— 而后者是重复测量,前者不是。
    """
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "0", "--trials", "1", "--run-id", "one-trial", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work")],
        cwd=ROOT, capture_output=True, text=True, timeout=600, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-500:]}\n{proc.stderr[-500:]}"
    point = json.loads((runs / "one-trial" / "curve.jsonl").read_text().splitlines()[0])
    assert "per_task_trials" not in point and "per_task_std" not in point


def test_the_runner_keeps_every_trial_s_trace_and_verdict(tmp_path, monkeypatch):
    """**每题每次试验的轨迹与判据都要留下,不只最后一次。**

    实测(`B2-noop`,`cobol-modernization`):分数 0.667 —— 三次里过了两次;存下来的判据
    是**最后一次**的,而它passed。于是记录一边写 `passed: true`,一边写 `score: 0.667`,
    失败那次的轨迹和理由是空的。`--trials` 存在的全部意义就是回答"第三次发生了什么",
    而这个问题当时没有任何地方能回答。
    """
    seq = iter([1.0, 0.0, 1.0])

    def fake(*_a, **_k):
        score = next(seq)
        return {"verdict": {"kind": "command", "passed": score == 1.0, "score": score,
                            "scorer_isolation_class": "actor-workdir",
                            "detail": f"trial score {score}"},
                "trace": f"trace for {score}", "exit_code": 0, "stderr": ""}

    monkeypatch.setattr(runner, "run_one", fake)
    res = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=3)
    assert res["per_task_trials"]["t1"] == [1.0, 0.0, 1.0]
    assert res["trial_traces"]["t1"] == ["trace for 1.0", "trace for 0.0", "trace for 1.0"]
    assert [v["passed"] for v in res["trial_verdicts"]["t1"]] == [True, False, True]
    # 兼容:`verdicts` 仍然是最后一次,老读者照旧
    assert res["verdicts"]["t1"]["passed"] is True
    assert res["traces"]["t1"] == "trace for 1.0"


def test_a_named_trial_that_failed_is_readable_on_disk(tmp_path):
    """端到端:失败那次的轨迹和理由要能在磁盘上按名字找到。

    这条测试是拿真实缺口写的:一个 0.667 旁边的 `passed: true`。修复之后,同一个目录里
    有 `t1.trial2.jsonl`(失败那次),而判据 JSON 里有 `trial_scores: [1, 0, 1]`。
    """
    from harnessgrad.records import _save_traces, _save_verdicts

    res = {"per_task": {"t1": 2 / 3}, "per_task_std": {"t1": 0.4714},
           "per_task_trials": {"t1": [1.0, 0.0, 1.0]},
           "traces": {"t1": "LAST TRIAL TRACE"},
           "trial_traces": {"t1": ["FIRST", "SECOND (failed)", "THIRD"]},
           "verdicts": {"t1": {"kind": "command", "passed": True, "detail": "third passed"}},
           "trial_verdicts": {"t1": [
               {"kind": "command", "passed": True, "score": 1.0, "detail": "first passed"},
               {"kind": "command", "passed": False, "score": 0.0, "detail": "second FAILED here"},
               {"kind": "command", "passed": True, "score": 1.0, "detail": "third passed"}]}}
    _save_traces(tmp_path, 0, res["traces"], ["t1"], trial_traces=res["trial_traces"])
    _save_verdicts(tmp_path, 0, res, ["t1"])

    traces = tmp_path / "round0_traces"
    assert (traces / "t1.jsonl").read_text() == "LAST TRIAL TRACE"
    assert (traces / "t1.trial1.jsonl").read_text() == "FIRST"
    assert (traces / "t1.trial2.jsonl").read_text() == "SECOND (failed)"
    assert (traces / "t1.trial3.jsonl").read_text() == "THIRD"

    entry = json.loads((tmp_path / "verdicts" / "round-0" / "t1.json").read_text())
    assert entry["score"] == pytest.approx(2 / 3)
    assert entry["trial_scores"] == [1.0, 0.0, 1.0], entry
    assert [t["passed"] for t in entry["trials"]] == [True, False, True], entry
    assert "second FAILED here" in json.dumps(entry), entry


# ------------------------------------------------- 没测到 ≠ 测出来是 0 ---

def test_a_check_that_never_ran_is_invalid_not_zero(tmp_path, monkeypatch):
    """平台能证明"检查没跑"时,这道题是 `invalid`:不进 per_task,也不缓存。"""
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: {
        "verdict": {"kind": "command", "passed": False, "score": 0.0,
                    "unmeasured": "the check wrote no /logs/verifier/ctrf.json: "
                                  "its tests did not run, so this task was not measured",
                    "detail": "reward 0 from /logs/verifier/reward.txt (exit 0)"},
        "trace": "", "exit_code": 0, "stderr": ""})
    res = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=1)
    assert res["per_task"] == {}, "没测到的题不能写进 per_task"
    assert res["invalid"]["t1"]["stage"] == "check"
    assert "ctrf.json" in res["invalid"]["t1"]["detail"]


def test_a_measured_zero_is_still_a_zero(tmp_path, monkeypatch):
    """没有 `unmeasured` 标记的 0 仍然是 0——这条不能因为上一条而变宽。"""
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: {
        "verdict": {"kind": "command", "passed": False, "score": 0.0,
                    "scorer_isolation_class": "actor-workdir",
                    "detail": "reward 0 from /logs/verifier/reward.txt (exit 0)"},
        "trace": "x", "exit_code": 0, "stderr": ""})
    res = runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=1)
    assert res["per_task"] == {"t1": 0.0}
    assert res["invalid"] == {}


# ------------------------------------------------- 重测与复用,不能同形 ---

def test_an_unchanged_tree_is_measured_once_and_says_so(tmp_path):
    """**同一棵树只测一次;复用的那次要在记录里说明白。**

    实测(`B2-noop`, 2026-10-04):round 0 与 round 1 的 `harness_sha` 完全相同(对照方法
    什么都没改),可 round 1 把五道题**重测了一遍**,0.267 → 0.467。原因是 H0 的缓存键
    是字面量 `"H0"`,而后面每一轮用内容哈希 —— 于是"内容相同的候选"永远命不中缓存。
    谁都能看出这是缺陷(白花钱),但真正的代价是另一面:一个**没动过**的对照曲线因为一次
    缓存不命中而"上涨了 0.200",而另一条曲线会不会上涨,取决于缓存键恰好对不对得上。

    修法:缓存按内容寻址。代价是噪声不再从"重复测量"里冒出来 —— 那是 `--trials` 的活,
    而且它现在是显式的;收益是"复用的测量"变成一个可读的事实(`n_cached`)。
    """
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "2", "--trials", "2", "--run-id", "cache-content", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=900, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    points = [json.loads(l) for l in
              (runs / "cache-content" / "curve.jsonl").read_text().splitlines()
              if l.strip()]
    r0, r1 = points[0], points[-1]
    # 前置条件:方法什么都没改,所以两轮测的是同一棵树
    assert r1["identity"]["harness_sha"] == r0["identity"]["harness_sha"], points
    assert r1.get("n_cached") == r1["n_scored"], (
        "同一棵树的第二轮没有复用第一轮的测量 —— 它又跑了一遍:"
        f"n_cached={r1.get('n_cached')}, n_scored={r1['n_scored']}")
    assert r1["per_task"] == r0["per_task"]
    assert r1["per_task_trials"] == r0["per_task_trials"]
    # 复用是记下来的,不是靠分数相同去猜的
    assert sorted(r1["cached_tasks"]) == sorted(r0["per_task"]), r1["cached_tasks"]


def test_a_changed_tree_is_not_a_cache_hit(tmp_path):
    """反向的一半:树真的变了就必须重新测,`n_cached` 不能把它吃掉。

    否则这条修法会把"方法改好了"也一起缓存掉 —— 那比白花钱严重得多。
    """
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop", "--mode", "A",
         "--rounds", "1", "--trials", "1", "--run-id", "cache-changed", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/echo_base/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=900, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    points = [json.loads(l) for l in
              (runs / "cache-changed" / "curve.jsonl").read_text().splitlines()
              if l.strip()]
    r0, r1 = points[0], points[-1]
    # 前置条件必须自己先成立,否则这条测试会"通过"但什么都没测
    assert r1["identity"]["harness_sha"] != r0["identity"]["harness_sha"], (
        "echo_base 没有改动这棵树,这条测试就没有测到缓存")
    assert not r1.get("n_cached"), (
        "树变了却复用了缓存:" + str(r1.get("cached_tasks")))


# ---------------------------------------- 被杀掉的跑道不能丢掉已经测到的 ---

def test_a_task_is_written_down_the_moment_it_is_measured(tmp_path, monkeypatch):
    """**一轮的结果不能只在整轮结束时落盘。**

    实测三次同一个设计缺陷的代价:`local-floor-5/6` 和 `loop-terminal_bench-41133` 被防篡改
    中止时丢掉了已测的题;`screen-all-1`(51 题 × 3 次重复,2 小时 05 分,五个容器还在跑)在
    22:42 被杀掉,`runs/screen-all-1/` 只剩下 `run_meta.json` 和一行表头。

    所以每道题最后一次试验一结束就写自己:分数、每次试验的分数、判据、判据自带的测试计数、
    墙钟,以及每次试验的轨迹。写在 `progress/` 下而不是最终路径 —— 未跑完的一轮不能看起来
    像跑完的。
    """
    seq = iter([1.0, 0.0, 1.0])

    def fake(*_a, **_k):
        score = next(seq)
        return {"verdict": {"kind": "command", "passed": score == 1.0, "score": score,
                            "scorer_isolation_class": "actor-workdir",
                            "detail": "d", "tests": {"passed": 2, "failed": 1}},
                "trace": '{"wall_clock_s": {"model": 1.0, "commands": 2.0, "total": 3.5}}\n',
                "exit_code": 0, "stderr": ""}

    monkeypatch.setattr(runner, "run_one", fake)
    runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=3,
                     progress_dir=tmp_path, round_no=0)

    entry = json.loads((tmp_path / "progress" / "round-0" / "t1.json").read_text())
    assert entry["trial_scores"] == [1.0, 0.0, 1.0], entry
    assert entry["score"] == pytest.approx(2 / 3)
    assert entry["n_trials"] == 3 and entry["complete"] is True
    assert entry["tests"] == {"passed": 2, "failed": 1}
    assert entry["wall_clock_s"] == 3.5
    for i in (1, 2, 3):
        assert (tmp_path / "progress" / "round-0" / "traces" / f"t1.trial{i}.jsonl").is_file()


def test_an_unmeasured_task_is_written_down_too(tmp_path, monkeypatch):
    """测不出来的题也要立刻落盘,否则被杀之后连"这道题平台没测到"都无从得知。"""
    monkeypatch.setattr(runner, "run_one", lambda *_a, **_k: {
        "verdict": {"kind": "command", "passed": False, "score": 0.0, "detail": "d"},
        "trace": "x", "exit_code": 0, "stderr": "",
        "invalid": {"stage": "prepare", "detail": "the image was not there"}})
    runner._evaluate(tmp_path, [{"task_id": "t1"}], {}, trials=1,
                     progress_dir=tmp_path, round_no=0)
    entry = json.loads((tmp_path / "progress" / "round-0" / "t1.json").read_text())
    assert entry["invalid"]["stage"] == "prepare", entry


def test_what_survives_a_kill_is_what_was_already_measured(tmp_path, monkeypatch):
    """端到端的性质:第二道题炸掉时,第一道题的数据必须已经在磁盘上。

    这就是 `screen-all-1` 那一夜缺的东西 —— 被杀之后能拿到的是"已经测完的题",不是零。
    """
    from pathlib import Path

    def fake(_repo, task, **_kwargs):
        if task["task_id"] == "t2":
            raise RuntimeError("simulated hard kill of this task")
        return {"verdict": {"kind": "command", "passed": True, "score": 1.0, "detail": "ok",
                            "scorer_isolation_class": "actor-workdir"},
                "trace": "TRACE-OF-T1\n", "exit_code": 0, "stderr": ""}

    monkeypatch.setattr(runner, "run_one", fake)
    with pytest.raises(RuntimeError):
        runner._evaluate(tmp_path, [{"task_id": "t1"}, {"task_id": "t2"}], {},
                         trials=1, jobs=1, progress_dir=tmp_path, round_no=0)
    survived = tmp_path / "progress" / "round-0" / "t1.json"
    assert survived.is_file(), "第一道题已经测完了,却没有留下任何东西"
    assert json.loads(survived.read_text())["score"] == 1.0
    assert (tmp_path / "progress" / "round-0" / "traces" / "t1.trial1.jsonl").read_text() == "TRACE-OF-T1\n"


def test_a_real_run_leaves_a_progress_record(tmp_path):
    """端到端:`driver.py` 真的把 `run_dir` 传下去了(不然上面两条都测不到接线)。"""
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "0", "--trials", "1", "--run-id", "progress-record", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work")],
        cwd=ROOT, capture_output=True, text=True, timeout=600, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-600:]}\n{proc.stderr[-600:]}"
    dest = runs / "progress-record" / "progress" / "round-0"
    files = sorted(p.name for p in dest.glob("*.json"))
    assert files, f"没有 progress 记录:{sorted(dest.parent.iterdir())}"
    assert all(json.loads((dest / f).read_text()).get("complete") for f in files)
