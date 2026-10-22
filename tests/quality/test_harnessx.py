"""HarnessX 的验收门:两种条件、历史最佳、以及模式 A 表达不了的那半步。

这一族方法(DGM、HyperAgents)的贡献是"从历史里挑状态";HarnessX 的贡献是另一件事 ——
**决定刚测完的那一轮算不算数**。它读的是一条很具体的算术:

    cost_delta_ratio = (round_cost - best_cost) / max(best_cost, 1e-3)
    score            = pass_rate - cost_weight * max(cost_delta_ratio, 0)
    回退 当且仅当 score < best_rate - tolerance
              且 |round_passed - best_passed| >= pass_count_noise_threshold
                                    -- recipe/gaia_evolver/run.py:1210-1257

这里钉三条最容易写错、写错了曲线就假的地方:

  1. **两个条件都要满足才回退。** 只看分数阈值会让"小任务集上一两个任务抖动"
     触发假回退(HarnessX 的注释 run.py:1191-1199 就是为这件事写的)。
  2. **噪声级跌破算接受,但不更新历史最佳。** 写成"接受并更新"会让基线慢慢被拖低;
     写成"回退"则比源码更严。源码 run.py:1227-1236 明确不更新 best。
  3. **比较对象是历史最佳,不是上一轮接受的候选。** 这是 run.py:1186-1189 的原话,
     也是 tolerance 不能把基线漂下去的唯一原因。

另外钉住模式 A 的**弱化**:平台没有拒绝这一步,所以"回退"只能表达成
`method_reported` 里的一条记录 + 一个指名 `states/round-<n>/` 的指令,并且本轮候选
就是把那个状态原样交回去。测试里会检查 `editor.ask` 在回退路径上根本不被调用 ——
既然拿的是历史状态,再花一次模型调用去"改"它就是把回退做成了另一件事。

最后一条:回退/停止路径也必须写 trajectory。这是 `editor` 那一层立下的规矩,但
HarnessX 是第一个"主动选择不动"会走到这一层的规则,值得单独钉一下。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))

TUN = {"tolerance": 0.03, "cost_weight": 0.0,
       "pass_count_noise_threshold": 3, "cost_field": "harness_tokens"}


def _load(name: str):
    path = ROOT / "methods" / name / "run.py"
    spec = importlib.util.spec_from_file_location(f"m_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _point(r: int, score: float, passed: int, *, total: int = 10, cost: float = 0.0,
           method_reported: dict | None = None) -> dict:
    """一个曲线点:per_task 里恰好有 `passed` 个满分任务,所以通过数可断言。"""
    per_task = {f"t{i}": (1.0 if i < passed else 0.0) for i in range(total)}
    return {
        "round": r, "score": score, "train_score": score,
        # 平台现在把这两个聚合值发给方法:eval 逐题身份被收走,但"考了几题、几题满分"
        # 留着 —— 计数不含身份,而它正是 HarnessX 的门要用的东西。
        "n_scored": total, "n_passed": passed,
        "sampling": {"policy": "all", "task_ids": [f"t{i}" for i in range(total)]},
        "per_task": per_task, "train_per_task": dict(per_task),
        "score_ci95": [score, score],
        "cost": {"harness_tokens": cost, "method_generation_tokens": 0,
                 "evaluation_trials": total, "wall_clock_s": 1.0},
        "measured_by_platform": True,
        "identity": {"harness_sha": f"sha{r}"},
        "method_reported": method_reported or {},
    }


def _channel(tmp_path: Path, points: list[dict], states: list[int]) -> Path:
    """一个 staged 好的 base harness:history + states/index.json。"""
    base = tmp_path / "base"
    base.mkdir(parents=True)
    (base / "harness.json").write_text(json.dumps({"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    (base / "agent.py").write_text("print('base')\n")

    hg = base / "_harnessgrad"
    (hg / "traces").mkdir(parents=True)
    (hg / "history").mkdir(parents=True)
    (hg / "states").mkdir(parents=True)
    for p in points:
        (hg / "history" / f"round-{p['round']}.json").write_text(json.dumps(p))
    (hg / "round.json").write_text(json.dumps(points[-1]))
    (hg / "traces" / "t01.jsonl").write_text(json.dumps({"command": "ls"}) + "\n")

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
    return base


def _request(base: Path, tmp_path: Path, *, round_index: int = 2,
             traj: Path | None = None) -> dict:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return {"platform_api_version": "0.1.0", "mode": "A", "base_harness": str(base),
            "workspace": str(ws), "round_index": round_index, "incumbent_score": 0.0,
            "task_ids": ["t01"], "train_task_ids": ["t01"],
            "trajectory_out": str(traj or (tmp_path / "traj.json"))}


def _invoke(mod, request, monkeypatch, reply=None, raises=None) -> dict:
    import editor

    captured: dict = {}

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        captured["prompt"] = prompt
        captured["ask_calls"] = captured.get("ask_calls", 0) + 1
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


# ------------------------------------------------ 两个条件,不是只看分数 ---

def test_the_gate_reverts_only_when_both_conditions_hold():
    """分数跌破 + 通过数变化达到阈值 = 回退;缺任何一个都不回退。

    HarnessX 的注释把第二个条件写成"小任务集上一两个任务的抖动是 eval 随机性,
    不是回归"(`run.py:1191-1199`)。只看分数阈值是这类移植最常见的错法。
    """
    hx = _load("harnessx")
    best = {"rate": 0.90, "cost": 0.0, "round": 0, "passed": 9}

    # 分数跌破 AND |Δpassed| = 3 >= 3 -> REVERTED
    both = hx._gate_decision(_point(1, 0.80, 6), best, **TUN)
    assert both["decision"] == "REVERTED", both
    assert both["reverted_to_round"] == 0 and both["revert_to"] == 0

    # 分数跌破,但只差 1 个任务 -> 噪声,接受
    noise = hx._gate_decision(_point(1, 0.80, 8), best, **TUN)
    assert noise["decision"] == "ACCEPTED" and noise["noise_level"] is True, noise

    # 通过数差 3,但分数没跌破 tolerance -> 接受,不适用噪声规则
    within = hx._gate_decision(_point(1, 0.88, 6), best, **TUN)
    assert within["decision"] == "ACCEPTED" and within["noise_level"] is False, within


def test_a_noise_level_breach_is_accepted_without_updating_best():
    """噪声级跌破:接受,但 `best` 必须原样留着 —— 否则基线会被一轮轮拖低。

    源码在 `run.py:1234-1236` 直接 `return ("ACCEPTED", reason, best, None)`。
    """
    hx = _load("harnessx")
    best = {"rate": 0.90, "cost": 0.0, "round": 0, "passed": 9}
    rec = hx._gate_decision(_point(1, 0.80, 8), best, **TUN)

    assert rec["decision"] == "ACCEPTED"
    assert rec["noise_level"] is True
    assert rec["updated_best_round"] == 0, "噪声级跌破不该产生新的 best"
    assert rec["best"] is best, "返回的 best 必须是同一个对象,不是按分数重建的"
    assert rec["count_delta"] == 1


def test_the_gate_compares_against_the_historical_best_not_the_last_accepted():
    """比较对象必须是历史最佳(R0),不是上一轮接受但没夺魁的 R1。

    构造:R0=0.90(历史最佳),R1=0.89(在容差内被接受,分数没超过 R0),R2=0.865。
    对 R0:0.865 < 0.90-0.03,是跌破 -> 通过数没动,判噪声,`best` 仍是 0。
    对"上一轮接受的 R1":0.865 >= 0.89-0.03,根本不跌破,`best` 会变成 1。
    所以断言 `best_round == 0` 就是这条规则本身。
    """
    hx = _load("harnessx")
    history = [_point(0, 0.90, 9), _point(1, 0.89, 9), _point(2, 0.865, 9)]
    gate = hx._grade(history, TUN)

    assert gate["graded_round"] == 2
    assert gate["best_round"] == 0, "对照的是历史最佳 R0,不是上一轮 R1"
    assert gate["updated_best_round"] == 0
    assert gate["noise_level"] is True
    # 回放也说明 R1 接受了但没有夺魁
    assert gate["history_steps"][1]["decision"] == "ACCEPTED"
    assert gate["history_steps"][1]["updated_best_round"] == 0


def test_only_strictly_better_rounds_displace_the_best():
    """同分不夺魁:源码 run.py:1207-1208 —— equal-score rounds keep the earliest holder。"""
    hx = _load("harnessx")
    best = {"rate": 0.80, "cost": 0.0, "round": 0, "passed": 8}
    tie = hx._gate_decision(_point(1, 0.80, 8), best, **TUN)
    assert tie["decision"] == "ACCEPTED" and tie["updated_best_round"] == 0, tie

    up = hx._gate_decision(_point(2, 0.90, 9), best, **TUN)
    assert up["updated_best_round"] == 2, up


def test_the_cost_term_is_the_published_arithmetic():
    """`score = pass_rate - cost_weight * max(cost_delta_ratio, 0)`,比值分母取 1e-3 下限。"""
    hx = _load("harnessx")
    tun = dict(TUN, cost_weight=0.5)
    best = {"rate": 0.90, "cost": 100.0, "round": 0, "passed": 9}
    rec = hx._gate_decision(_point(1, 0.90, 9, cost=200.0), best, **tun)

    assert abs(rec["cost_delta_ratio"] - 1.0) < 1e-9, rec
    assert abs(rec["adjusted_score"] - 0.40) < 1e-9, rec


def test_the_pass_count_comes_from_the_platforms_own_count():
    """HarnessX 的门要的是一个**整数**通过数,平台现在直接给。

    它以前从 eval 的逐题分数里数。逐题身份被收走后(§2.3),平台改发一个聚合计数
    `n_passed` —— 计数不含身份,所以可以完整地还回来,方法的保真度没有损失。

    下面第二行是这条设计存在的理由:0.5 分的题不算通过,所以计数和均值对不上。
    从均值重建 (`score * n` = 3) 会数错。
    """
    hx = _load("harnessx")
    assert hx._passed({"round": 1, "score": 0.5, "n_passed": 2, "n_scored": 4}) == 2
    assert hx._passed({"round": 1, "score": 0.75, "n_passed": 1, "n_scored": 4}) == 1
    assert hx._passed({"round": 1, "score": 0.0, "n_passed": 0, "n_scored": 4}) == 0


def test_the_pass_count_is_rebuilt_only_for_points_predating_the_field():
    """老曲线点没有 `n_passed`,只能从均值重建 —— 对 0/1 分数精确,对部分给分只是近似。"""
    hx = _load("harnessx")
    old = {"round": 1, "score": 0.5, "sampling": {"task_ids": ["a", "b", "c", "d"]}}
    assert hx._passed(old) == 2
    # "零个通过"和"没有计数"必须分得开 —— 噪声护栏不能把后者读成前者
    assert hx._passed({"round": 1, "score": 0.0}) is None
    assert hx._passed({"round": 1, "score": 0.0,
                       "measured_by_platform": False}) is None


# --------------------------------------------------- 模式 A 的回退指令 ---

def test_a_revert_hands_back_the_best_state_and_names_it(tmp_path, monkeypatch):
    """模式 A 没有拒绝这一步,回退只能表达成:交回最佳状态 + 指名 `states/round-<n>/`。

    R1 分数 0.60、通过数 6,对 R0 的 0.90/9 同时满足两个条件,所以门判回退;本轮候选
    必须逐字来自 `states/round-0/`,而且 `editor.ask` 一次都不能被调用 —— 拿历史状态
    再让模型"改一下"就不是回退了。
    """
    hx = _load("harnessx")
    base = _channel(tmp_path, [_point(0, 0.90, 9), _point(1, 0.60, 6)], states=[0, 1])
    traj = tmp_path / "traj.json"

    got = _invoke(hx, _request(base, tmp_path, round_index=2, traj=traj), monkeypatch)

    assert "prompt" not in got, "回退路径不该花一次模型调用"
    assert got["stdout"]["changed"] is True

    step = json.loads(traj.read_text())["steps"][0]
    reported = step["method_reported"]
    assert reported["harnessx_gate"]["decision"] == "REVERTED"
    directive = reported["harnessx_revert_directive"]
    assert directive["base_state"] == "_harnessgrad/states/round-0", directive
    assert directive["base_round"] == 0 and directive["state_available"] is True

    dest = tmp_path / "ws" / "candidate"
    assert (dest / "agent.py").read_text() == "print('v0')\n", \
        "候选必须逐字来自历史最佳状态,不是当前这个"


def test_the_revert_path_writes_a_trajectory_and_stops_when_the_state_is_missing(
        tmp_path, monkeypatch):
    """最佳状态没被 materialize 时:写记录、`changed: false`,而不是在错的基础上改。

    `states/index.json` 只保留最近 12 个点(`harnessgrad/channel.py:STATES_KEPT`)。猜路径会找到一个空目录,
    然后报一个平台造成的失败;这里要求如实报告 `state_available: false` 并停下。
    """
    hx = _load("harnessx")
    base = _channel(tmp_path, [_point(0, 0.90, 9), _point(1, 0.60, 6)], states=[])
    traj = tmp_path / "traj.json"

    got = _invoke(hx, _request(base, tmp_path, round_index=2, traj=traj), monkeypatch)

    assert traj.exists(), "回退/停止路径也必须写 trajectory"
    reported = json.loads(traj.read_text())["steps"][0]["method_reported"]
    assert reported["harnessx_gate"]["decision"] == "REVERTED"
    assert reported["harnessx_revert_directive"]["state_available"] is False
    assert reported["harnessx_revert_directive"]["base_state"] == \
        "_harnessgrad/states/round-0"
    assert got["stdout"]["changed"] is False


def test_the_accept_path_carries_the_gate_arithmetic_too(tmp_path, monkeypatch):
    """接受路径同样要把这轮门的算术写进 `method_reported`。

    门的结论只有和输入放在一起才可核对;只写一个 "ACCEPTED" 的曲线没法复核。
    """
    hx = _load("harnessx")
    base = _channel(tmp_path, [_point(0, 0.90, 9), _point(1, 0.89, 9)], states=[0, 1])
    traj = tmp_path / "traj.json"

    got = _invoke(hx, _request(base, tmp_path, round_index=2, traj=traj), monkeypatch,
                  reply={"files": [{"path": "agent.py", "content": "print('new')\n"}],
                         "hypothesis": "h"})

    assert got["stdout"]["changed"] is True
    gate = json.loads(traj.read_text())["steps"][0]["method_reported"]["harnessx_gate"]
    assert gate["decision"] == "ACCEPTED"
    assert gate["tunables"]["tolerance"] == 0.03
    assert gate["count_delta"] == 0 and gate["adjusted_score"] is not None


# ------------------------------------------------ 预注册的字段要进记录 ---

def test_the_pre_registration_is_recorded_before_measurement(tmp_path, monkeypatch):
    """`hypothesis_id/levers/predicted_affected/rollback_trigger/...` 必须进记录。

    这是 HarnessX 的 journal frontmatter(`journal/SKILL.md:40-51`);orchestrator 之后
    拿 `predicted_affected` 去对实际翻盘(`journal.py:605-667`)。平台不做这件事,所以
    方法至少要把预测写下来,否则下一轮无从对账。
    """
    hx = _load("harnessx")
    base = _channel(tmp_path, [_point(0, 0.90, 9), _point(1, 0.89, 9)], states=[0, 1])
    traj = tmp_path / "traj.json"

    _invoke(hx, _request(base, tmp_path, round_index=2, traj=traj), monkeypatch,
            reply={"files": [{"path": "agent.py", "content": "print('new')\n"}],
                   "hypothesis": "h",
                   "hypothesis_id": "h_retry_v1",
                   "levers": ["control"],
                   "predicted_affected": ["t01"],
                   "rollback_trigger": "pass_rate down, cost up >10%",
                   "expected_global_gain": "flips the stuck cluster",
                   "regression_risk": "may slow fast tasks",
                   "cost_shift": "+5%",
                   "lever_argument": "why control and not instruction"})

    step = json.loads(traj.read_text())["steps"][0]
    prereg = step["method_reported"]["harnessx_preregistration"]
    assert prereg["hypothesis_id"] == "h_retry_v1"
    assert prereg["levers"] == ["control"] and prereg["levers_valid"] is True
    assert prereg["predicted_affected"] == ["t01"]
    assert prereg["rollback_trigger"] and prereg["lever_argument"]
    assert step["edit_kind"] == "harnessx:control"


def test_an_invalid_lever_is_recorded_not_silently_dropped(tmp_path, monkeypatch):
    """HarnessX 的 `append_entry` 会拒绝未知 lever(`journal.py:155-157`)。

    平台只记录不解释,所以方法要把"声明非法"记下来,而不是假装它没发生。
    """
    hx = _load("harnessx")
    base = _channel(tmp_path, [_point(0, 0.90, 9), _point(1, 0.89, 9)], states=[0, 1])
    traj = tmp_path / "traj.json"

    _invoke(hx, _request(base, tmp_path, round_index=2, traj=traj), monkeypatch,
            reply={"files": [{"path": "agent.py", "content": "print('new')\n"}],
                   "hypothesis": "h", "levers": ["vibes"]})

    prereg = json.loads(traj.read_text())["steps"][0]["method_reported"][
        "harnessx_preregistration"]
    assert prereg["levers"] == ["vibes"] and prereg["levers_valid"] is False


def test_the_previous_rounds_prediction_is_read_back_from_the_curve_point():
    """curve point 携带 method_reported(`harnessgrad/records.py:_curve_point`),所以预测可以跨轮读到。"""
    hx = _load("harnessx")
    history = [_point(0, 0.9, 9), _point(
        1, 0.8, 8, method_reported={"harnessx_preregistration":
                                    {"hypothesis_id": "h_x", "levers": ["action"]}})]
    got = hx._previous_preregistration(history)
    assert got["hypothesis_id"] == "h_x"
    assert got["source"] == "previous_round_record"


# ------------------------------------------------------ 每条出口都有记录 ---

def test_a_provider_error_still_writes_the_gate_record(tmp_path, monkeypatch):
    """provider 报错时既要有 trajectory,也要有本轮门的算术。"""
    hx = _load("harnessx")
    base = _channel(tmp_path, [_point(0, 0.90, 9), _point(1, 0.89, 9)], states=[0, 1])
    traj = tmp_path / "traj.json"

    got = _invoke(hx, _request(base, tmp_path, round_index=2, traj=traj), monkeypatch,
                  raises=RuntimeError("503 no available channel"))

    assert traj.exists(), "provider 报错时没有写出 trajectory"
    reported = json.loads(traj.read_text())["steps"][0]["method_reported"]
    assert "503" in json.dumps(reported), "真正的原因必须进记录"
    assert reported["harnessx_gate"]["decision"] == "ACCEPTED"
    assert got["stdout"]["changed"] is False


def test_a_no_change_reply_records_gate_and_preregistration(tmp_path, monkeypatch):
    hx = _load("harnessx")
    base = _channel(tmp_path, [_point(0, 0.90, 9), _point(1, 0.89, 9)], states=[0, 1])
    traj = tmp_path / "traj.json"

    got = _invoke(hx, _request(base, tmp_path, round_index=2, traj=traj), monkeypatch,
                  reply={"no_change": "the evidence supports nothing",
                         "hypothesis_id": "h_none", "levers": []})

    assert traj.exists()
    reported = json.loads(traj.read_text())["steps"][0]["method_reported"]
    assert reported["harnessx_gate"]["decision"] == "ACCEPTED"
    assert reported["harnessx_preregistration"]["hypothesis_id"] == "h_none"
    assert got["stdout"]["changed"] is False


# ------------------------------------------------------------ 旋钮 ---

def test_tunables_default_to_the_published_values(monkeypatch):
    """默认值必须来自 GAIA 那次运行:`tolerance 0.03`、噪声阈值 3、cost_weight 0.0。"""
    hx = _load("harnessx")
    for name in ("HARNESSX_TOLERANCE", "HARNESSX_COST_WEIGHT",
                 "HARNESSX_PASS_COUNT_NOISE_THRESHOLD", "HARNESSX_COST_FIELD"):
        monkeypatch.delenv(name, raising=False)
    assert hx._tunables() == TUN

    monkeypatch.setenv("HARNESSX_TOLERANCE", "0.1")
    monkeypatch.setenv("HARNESSX_PASS_COUNT_NOISE_THRESHOLD", "5")
    monkeypatch.setenv("HARNESSX_COST_FIELD", "wall_clock_s")
    tun = hx._tunables()
    assert tun["tolerance"] == 0.1 and tun["pass_count_noise_threshold"] == 5
    assert tun["cost_field"] == "wall_clock_s"


def test_a_malformed_tunable_falls_back_instead_of_crashing(monkeypatch):
    """一个拼错的 env var 不该让整轮方法直接死掉、什么都不报告。"""
    hx = _load("harnessx")
    monkeypatch.setenv("HARNESSX_TOLERANCE", "not-a-number")
    monkeypatch.setenv("HARNESSX_PASS_COUNT_NOISE_THRESHOLD", "three")
    tun = hx._tunables()
    assert tun["tolerance"] == 0.03 and tun["pass_count_noise_threshold"] == 3
