"""父代选择规则:方法必须能挑一个**旧状态**来改,而不是只能改当前这个。

这一族(DGM、HyperAgents)的贡献都是"从历史里挑哪个状态继续"。平台以前只把当前
harness 的文件交给方法,`history` 里只有分数 —— 于是这些规则全都退化成同一条爬山,
八个方法跑起来一模一样却各自声称是自己。`_harnessgrad/states/` 就是为了让这件事
真的可表达而加的,这里钉住它确实被用上了。

另外钉住一条对别人方法的可核对断言:HyperAgents 的 `score_prop` 按 sigmoid 权重
采样，归一化后仍然按分数排序，并不均匀。真正均匀的是
`select_next_parent.py` 直接丢弃权重后调用 `random.choice` 的分支。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))


def _load(name: str):
    path = ROOT / "methods" / name / "run.py"
    spec = importlib.util.spec_from_file_location(f"m_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _channel(tmp_path: Path, points: list[dict], states: list[int],
             traces: dict[str, str] | None = None) -> Path:
    """A base harness with a staged channel: history, states, traces."""
    base = tmp_path / "base"
    base.mkdir(parents=True)
    (base / "harness.json").write_text(json.dumps({"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    (base / "agent.py").write_text("print('v0')\n")

    hg = base / "_harnessgrad"
    (hg / "traces").mkdir(parents=True)
    (hg / "history").mkdir(parents=True)
    (hg / "states").mkdir(parents=True)
    for p in points:
        (hg / "history" / f"round-{p['round']}.json").write_text(json.dumps(p))
    (hg / "round.json").write_text(json.dumps(points[-1]))

    index: dict = {"kept": [], "window": 12, "available": len(states)}
    for r in states:
        d = hg / "states" / f"round-{r}"
        d.mkdir(parents=True)
        (d / "harness.json").write_text((base / "harness.json").read_text())
        (d / "agent.py").write_text(f"print('v{r}')\n")
        index["kept"].append(f"round-{r}")
        index[f"round-{r}"] = {"sha": f"sha{r}", "files": 2, "score": 0.0,
                               "harness_dir": str(d)}
    (hg / "states" / "index.json").write_text(json.dumps(index))

    for tid, body in (traces or {"t1": json.dumps({"command": "ls"}) + "\n"}).items():
        (hg / "traces" / f"{tid}.jsonl").write_text(body)
    return base


def _point(r: int, score: float, *, parent: int | None = None,
           rejected: bool = False, per_task: dict | None = None) -> dict:
    reported = {}
    if parent is not None:
        reported["dgm_parent"] = parent
        reported["hyper_parent"] = parent
    if rejected:
        reported["rejected"] = "x"
    return {"round": r, "score": score, "train_score": score,
            "train_per_task": per_task or {"t1": score},
            "per_task": {"t1": score}, "score_ci95": [score, score],
            "measured_by_platform": True, "identity": {"harness_sha": f"sha{r}"},
            "method_reported": reported}


def _invoke(mod, request, monkeypatch, reply=None, raises=None) -> dict:
    import editor

    captured: dict = {}

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        captured["prompt"] = prompt
        if raises is not None:
            raise raises
        return reply if reply is not None else {"no_change": "stub"}

    monkeypatch.setattr(editor, "ask", fake_ask)
    monkeypatch.setattr(mod.editor, "ask", fake_ask, raising=False)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys, "stdout", out)
    try:
        mod.main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
    return {"stdout": json.loads(out.getvalue() or "{}"), **captured}


def _request(base: Path, tmp_path: Path) -> dict:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return {"platform_api_version": "0.1.0", "mode": "A", "base_harness": str(base),
            "workspace": str(ws), "round_index": 3, "incumbent_score": 0.0,
            "task_ids": ["t1"], "train_task_ids": ["t1"],
            "trajectory_out": str(tmp_path / "traj.json")}


# --------------------------------------------------------------- DGM ---

def test_dgm_weights_follow_its_own_formula():
    """权重必须是 sigmoid(10*(s-0.5)) / (1+子代数),不是别的什么。"""
    dgm = _load("dgm")
    history = [_point(0, 0.2), _point(1, 0.9), _point(2, 0.5)]
    _, report = dgm.select_parent(history, round_index=3)
    w = report["weights"]
    for point in history:
        expect = dgm._sigmoid(10 * (point["score"] - 0.5)) / (
            1 + report["child_counts"].get(str(point["round"]), 0))
        assert abs(w[str(point["round"])] - expect) < 1e-6, (w, point)


def test_dgm_penalises_a_parent_that_already_has_children():
    """这正是 DGM 与"贪心选最高分"的区别:生过的父代要被压下去。"""
    dgm = _load("dgm")
    # 两个同分候选,其中一个已经有一个子代
    history = [_point(0, 0.8, parent=None), _point(1, 0.8, parent=0), _point(2, 0.8)]
    _, report = dgm.select_parent(history, round_index=1)
    counts = report["child_counts"]
    assert counts.get("0") == 1, counts
    assert report["weights"]["0"] < report["weights"]["2"], report["weights"]


def test_dgm_keep_better_compares_against_the_initial_score(monkeypatch):
    """DGM 的保留门槛比的是**最初那一轮**,不是归档里最好的那一轮。"""
    dgm = _load("dgm")
    monkeypatch.setattr(dgm, "RETAIN", "keep_better")
    monkeypatch.setattr(dgm, "NOISE_LEEWAY", 0.1)
    history = [_point(0, 0.5), _point(1, 0.95), _point(2, 0.3)]
    kept = [p["round"] for p in dgm._archive(history)]
    # 0.3 < 0.5-0.1,被挡掉;0.95 留下 —— 即使它远高于初始分
    assert kept == [0, 1], kept


def test_dgm_keep_all_is_the_default_the_adapter_got_wrong():
    """`--update_archive` 出厂默认是 keep_all,不是 keep_better。

    我们自己的适配器写反了(legacy/adapters/dgm.py)。这里钉住的是**代码里的默认值**,
    因为一个默认跑 keep_all 却声称自己是 keep_better 的运行,是对别人方法的一句假话。
    """
    dgm = _load("dgm")
    assert dgm.RETAIN == "keep_all", (
        f"默认应当是 DGM_outer.py:232 的 keep_all,拿到 {dgm.RETAIN}")
    history = [_point(0, 0.9), _point(1, 0.0)]
    assert len(dgm._archive(history)) == 2, "keep_all 不该挡掉任何有分数的轮次"


def test_dgm_never_uses_exam_score_for_parent_selection():
    dgm = _load("dgm")
    history = [
        {"round": 0, "score_kind": "training", "score": 0.2, "measured_by_platform": True},
        {"round": 1, "score_kind": "exam", "score": 1.0, "train_score": 0.0,
         "measured_by_platform": True},
    ]
    assert [p["round"] for p in dgm._archive(history)] == [0, 1]
    _, report = dgm.select_parent(history, round_index=2)
    assert report["weights"]["1"] == pytest.approx(dgm._sigmoid(-5.0), abs=1e-6)


def test_dgm_no_eligible_parent_is_explicit():
    dgm = _load("dgm")
    chosen, report = dgm.select_parent([
        {"round": 4, "score_kind": "exam", "score": 1.0,
         "train_score": None, "measured_by_platform": True,
         "method_reported": {"rejected": True}},
    ], round_index=1)
    assert chosen == {} and report["reason"] == "no eligible parent"


# -------------------------------------------------------- HyperAgents ---

def test_hyperagents_score_prop_is_not_uniform(monkeypatch):
    """`score_prop` 不是均匀的 —— 这一点我一开始写反了,所以留成测试。

    第一版基于"每个候选的权重就是它自己的 sigmoid,归一化会约掉单调变换"写成
    "score_prop 恒等于均匀"。对照源码,那是错的:`gl_utils.py:556-568` 是
    `random.choices(..., weights=归一化 sigmoid)`,采样**正比于**权重。
    真正均匀的是另一个分支 —— `select_next_parent.py:56` 把刚算出来的子代数和分数
    扔掉,直接 `random.choice`。

    区别很重要:前者说"HyperAgents 设计上就是随机",后者说"它两个选择器里有一个
    忽略了自己算出来的统计量"。
    """
    hyper = _load("hyperagents")
    history = [_point(0, 0.1), _point(1, 0.9), _point(2, 0.5), _point(3, 0.75)]
    scores = {p["round"]: p["score"] for p in history}
    w = hyper.weights_for(history, scores, {}, mid=0.5, kind="score_prop")

    assert w[1] > w[3] > w[2] > w[0], f"权重必须随分数单调:{w}"
    assert len(set(round(v, 9) for v in w.values())) > 1, "score_prop 不可能是均匀的"

    _, report = hyper.select_parent(history, round_index=1)
    assert report["is_uniform"] is False, report
    assert report["deviation_from_uniform"] > 0


def test_hyperagents_the_uniform_setting_is_the_one_that_is_actually_uniform():
    """只有把权重整个丢掉的那个分支才是均匀的。"""
    hyper = _load("hyperagents")
    scores = {0: 0.1, 1: 0.9, 2: 0.5}
    w = hyper.weights_for([{"round": r} for r in scores], scores, {}, 0.5, "uniform")
    assert set(w.values()) == {1.0}, w


def test_hyperagents_best_setting_is_greedy():
    hyper = _load("hyperagents")
    scores = {0: 0.1, 1: 0.9, 2: 0.5}
    w = hyper.weights_for([{"round": r} for r in scores], scores, {}, 0.5, "best")
    assert w == {0: 0.0, 1: 1.0, 2: 0.0}, w


def test_hyperagents_child_penalty_only_bites_past_eight_children():
    """惩罚是 (子代数/8)^3,所以在 8 之前几乎不起作用 —— 与 DGM 的 1/(1+c) 不同。"""
    hyper = _load("hyperagents")
    near = hyper.math.exp(-((2 / 8.0) ** 3))
    far = hyper.math.exp(-((16 / 8.0) ** 3))
    assert near > 0.98, near
    assert far < 0.01, far


def test_hyperagents_child_count_pulls_a_high_scorer_down():
    """子代惩罚必须真的改变排序,否则它和 score_prop 没区别。"""
    hyper = _load("hyperagents")
    scores = {0: 0.9, 1: 0.9}
    no_children = hyper.weights_for([{"round": 0}, {"round": 1}], scores, {},
                                    0.9, "score_child_prop")
    assert no_children[0] == no_children[1]
    with_children = hyper.weights_for([{"round": 0}, {"round": 1}], scores,
                                      {0: 30}, 0.9, "score_child_prop")
    assert with_children[0] < with_children[1], with_children


def test_hyperagents_validity_gate_ignores_score():
    """它的门只看"这次运行有没有产出可用结果",从不看分数。"""
    hyper = _load("hyperagents")
    assert hyper._valid(_point(0, 0.0)) is True, "低分不是淘汰理由"
    assert hyper._valid(_point(1, 0.99, rejected=True)) is False
    assert hyper._valid({"round": 2, "score": 1.0,
                         "measured_by_platform": False}) is False
    assert hyper._valid({"round": 3, "score_kind": "training", "score": None,
                         "measured_by_platform": True}) is False
    assert hyper._valid({"round": 4, "score_kind": "training", "score": 0.5,
                         "measured_by_platform": True, "valid_parent": False}) is False
    assert hyper._valid({"round": 5, "score_kind": "training", "score": 0.5,
                         "measured_by_platform": True,
                         "method_reported": {"meta_rollback": {"success": False}}}) is False


def test_hyperagents_no_parent_does_not_fallback_to_latest_failed_state():
    hyper = _load("hyperagents")
    chosen, report = hyper.select_parent([
        {"round": 0, "score_kind": "training", "score": None,
         "measured_by_platform": True},
        {"round": 1, "score_kind": "training", "score": 0.9,
         "measured_by_platform": True, "valid_parent": False},
    ], round_index=1)
    assert chosen == {} and not report["candidates"]


# ------------------------------------------- 真的用了旧状态,不是当前这个 ---

@pytest.mark.parametrize("name", ["dgm", "hyperagents"])
def test_the_method_builds_on_the_selected_old_state_not_the_incumbent(
        name, tmp_path, monkeypatch):
    """这一条才是这一族存在的前提:候选必须来自被选中的旧状态。

    如果它总是拿当前 harness 当底,那"父代选择"就只是装饰 —— 规则算得再漂亮,
    产出的候选和一条无条件爬山的链子没有任何区别。
    """
    mod = _load(name)
    base = _channel(tmp_path, [_point(0, 0.1), _point(1, 0.9), _point(2, 0.2)],
                    states=[0, 1, 2])
    # 强制选中 round-0,这样它的 agent.py 与当前 base 的明显不同
    monkeypatch.setattr(mod, "select_parent",
                        lambda history, round_index: (
                            next(p for p in history if p["round"] == 0),
                            {"best_round": 1, "weights": {}, "child_counts": {},
                             "rule": "forced", "predicted_affected": []}))
    got = _invoke(mod, _request(base, tmp_path), monkeypatch,
                  reply={"files": [{"path": "agent.py", "content": "print('edited')\n"}],
                         "hypothesis": "h"})

    # **问轨迹要候选,不写死文件名。** `dgm` 现在一次交一批(`child1..k`),`hyperagents`
    # 仍然交一个 —— 这一族的不变量是"交出来的那棵树来自被选中的旧状态",不是目录叫什么。
    traj = json.loads(Path(_request(base, tmp_path)["trajectory_out"]).read_text())
    dest = Path(traj["steps"][0]["harness_dir"])
    assert (dest / "agent.py").read_text() == "print('edited')\n"
    # harness.json 来自被选中的状态,不是凭空造出来的
    assert (dest / "harness.json").is_file()
    assert got["stdout"]["changed"] is True


@pytest.mark.parametrize("name", ["dgm", "hyperagents"])
def test_the_method_records_which_state_it_built_on(name, tmp_path, monkeypatch):
    """父代声明必须进 `method_reported` —— 下一轮的 lineage 只能从这里读。

    平台记的是"这一轮的 harness 是什么",不是"它从哪来"。方法不写下来,子代数就
    永远是 1,`1/(1+c)` 和 `exp(-(c/8)^3)` 两项就都成了死代码。
    """
    mod = _load(name)
    base = _channel(tmp_path, [_point(0, 0.1), _point(1, 0.9)], states=[0, 1])
    monkeypatch.setattr(mod, "select_parent",
                        lambda history, round_index: (
                            history[0], {"best_round": 1, "weights": {},
                                         "child_counts": {}, "rule": "forced"}))
    got = _invoke(mod, _request(base, tmp_path), monkeypatch,
                  reply={"no_change": "nothing to do"})

    key = "dgm_parent" if name == "dgm" else "hyper_parent"
    traj = json.loads(Path(_request(base, tmp_path)["trajectory_out"]).read_text())
    reported = traj["steps"][0]["method_reported"]
    assert reported[key] == 0, traj["steps"][0]
    if name == "dgm":
        # A batch draws k parents, so the lineage is a list; `dgm_parent` stays the first
        # one because that is the key the child-count term has always read.
        assert set(reported["dgm_parents"]) == {0}, reported
    assert got["stdout"]["changed"] is False


@pytest.mark.parametrize("name", ["dgm", "hyperagents"])
def test_a_missing_state_is_reported_rather_than_guessed(name, tmp_path, monkeypatch):
    """索引里没有的状态不能猜路径:猜了会找到空目录,然后报一个平台造成的失败。"""
    mod = _load(name)
    base = _channel(tmp_path, [_point(0, 0.1)], states=[])   # 状态没被 materialize
    monkeypatch.setattr(mod, "select_parent",
                        lambda history, round_index: (history[0], {"best_round": 0}))
    traj = tmp_path / "traj.json"
    got = _invoke(mod, _request(base, tmp_path), monkeypatch, reply={"no_change": "x"})

    assert traj.exists(), "没有可用状态时也必须写出 trajectory"
    assert got["stdout"]["changed"] is False
    assert "not staged" in got["stdout"]["hypothesis"]
