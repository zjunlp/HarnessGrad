"""TTHE 每轮的决策规则,作为 HarnessGrad 方法。

被测的不是"TTHE 能不能涨分"——分数是平台的,方法看不到。被测的是**它的决策**在
mode A 下是否被如实执行:

  1. **固定分支角色按周期轮转。** TTHE 用 `DIVERSITY[gi % 3]` 给每条分支钉死一个
     角色,轮次不改变它(`proposer.py:145-147`)。mode A 每轮只有一条分支,于是轮转
     的周期由 round index 决定;角色顺序一旦漂移,移植的就不是 TTHE 的调度。

  2. **有效性闸门:没有可修的证据就不花这一轮。** TTHE 把不可加载、没有 proposal
     card 的子代丢回父代(`optimize.py:404-406`)。mode A 事后不能拒绝一个已经被测量
     的候选,所以闸门只能前置到模型调用之前:证据里没有失败题目时,任何挑战者都与
     现任无法区分,方法报 `changed:false` 并留下轨迹。

  3. **回滚闸门:现任永远是可接受的结果。** TTHE 的 judge 池里始终有传入的 H,judge
     失败也保留 H(`optimize.py:437-458`)。mode A 没有 judge,于是每一条失败路径
     (`no_change`、card 缺失/类型不对、编辑不可用、provider 报错)都返回现任并停轮。

  4. **label-free 纪律必须真的出现在交给编辑器的 prompt 里**,不是写在 docstring 里:
     不许硬编码题目字面值、任务描述/Hint 权威、不许写自定义 parser/regex(会卡 GIL)、
     控制器而不是提案者执行子代。这些是 TTHE 提案规则的可移植部分
     (`proposer.py:344-351,359-371,387`)。

  5. **proposal card 是结构化自报**,编辑路径和停轮路径都要落在 `method_reported`
     里,并且键集与类型要和 `proposer.py:154-193` 的校验一致。
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


def _write_harness(root: Path, *, traces: dict[str, str] | None = None,
                   history: list[dict] | None = None) -> Path:
    """A base harness with the channel staged exactly where the contract puts it.

    `round.json` 由 history 的最后一点生成——这正是 driver 的做法:交给方法的是
    **现任那一轮**的曲线点,`train_per_task` 是方法能看到的那一侧。
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.json").write_text(
        json.dumps({"name": "probe", "version": "1.0",
                    "entrypoint": "agent.py"}))
    (root / "agent.py").write_text("print('hello')\n")
    hg = root / "_harnessgrad"
    (hg / "traces").mkdir(parents=True, exist_ok=True)
    (hg / "history").mkdir(parents=True, exist_ok=True)
    for tid, body in (traces or {}).items():
        (hg / "traces" / f"{tid}.jsonl").write_text(body)
    for point in (history or []):
        (hg / "history" / f"round-{point.get('round', 0)}.json").write_text(
            json.dumps(point))
    point = (history or [{}])[-1] if history else {"round": 0, "score": 0.0}
    (hg / "round.json").write_text(json.dumps(point))
    return root


def _load(name: str = "tthe"):
    path = ROOT / "methods" / name / "run.py"
    spec = importlib.util.spec_from_file_location(f"m_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _invoke(mod, request: dict, monkeypatch, reply=None, raises=None) -> dict:
    """Run a method's main() with a stubbed model, returning its stdout object.

    `asked` 记录模型是否真的被调用过:停轮路径必须是**不花这一轮**的,所以可以用
    它把"方法先看了证据才决定"和"方法只是没被调用"分开。
    """
    import editor

    captured: dict = {"asked": 0}

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        captured["asked"] += 1
        captured["prompt"] = prompt
        captured["system"] = system
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


def _request(base: Path, work: Path, traj: Path, round_index: int = 1) -> dict:
    return {"platform_api_version": "0.1.0", "mode": "A", "base_harness": str(base),
            "workspace": str(work), "round_index": round_index,
            "incumbent_score": 0.5, "task_ids": ["t01"], "train_task_ids": ["t01"],
            "trajectory_out": str(traj)}


def _edit_reply(content: str = "print('better')\n") -> dict:
    return {"files": [{"path": "agent.py", "content": content}],
            "hypothesis": "ground values before filtering",
            "proposal_card": {"behavior_changes": [
                                  {"trace": "t01", "observed_issue": "wrong value",
                                   "change": "look up the stored value first",
                                   "evidence": "t01 ran and returned EMPTY",
                                   "expected_effect": "fewer empty results"}],
                              "preserved_behaviors": ["the existing tool loop"],
                              "verification": [{"trace": "t01",
                                                "runtime_status": "not executed",
                                                "evidence": "observed in t01's trace"}],
                              "risks": ["value lookup may be slow"]}}


# ------------------------------------------- 固定分支角色:周期 3,且对给定轮稳定 ---

def test_the_branch_role_cycles_with_period_three_and_is_stable():
    """`DIVERSITY[gi % 3]` 是 TTHE 的调度;角色顺序漂了就不是同一个方法。

    TTHE 把角色钉在分支下标上,轮次不改变它(`proposer.py:145-147`)。mode A 每轮
    一条分支,所以轮转的参数变成 round index——周期仍必须是 3,且同一个 index 必须
    永远给出同一个角色。
    """
    mod = _load()

    names = [mod.role_for(r)[1] for r in range(9)]
    assert names[0:3] == names[3:6] == names[6:9], \
        f"角色没有按周期 3 轮转:{names}"
    assert set(names) == {"conservative-repair", "independent-exploration",
                          "adversarial-audit"}, f"角色名不是 TTHE 的三个:{names}"
    assert mod.role_for(4) == mod.role_for(4), "同一个 round index 给出了不同角色"
    assert mod.role_for(0) == mod.role_for(3) == mod.role_for(6)


# ------------------------------------ 有效性闸门:没有可修的证据就不花这一轮 ---

def test_the_validity_gate_stops_when_nothing_is_failing(tmp_path, monkeypatch):
    """证据里没有失败题目时,挑战者与现任无法区分,所以停轮而不是硬改。

    TTHE 从不把一轮花在不可接受的子代上(`optimize.py:404-406`)。mode A 无法事后
    拒绝,于是闸门前置:没有失败题,任何编辑都只是掷硬币。停轮必须报
    `changed:false`,并且**没有调用模型**——否则"停轮"就不是真的停。
    """
    mod = _load()
    base = _write_harness(tmp_path / "base",
                          traces={"t01": json.dumps({"reply": "done"}) + "\n",
                                  "t02": json.dumps({"reply": "done"}) + "\n"},
                          history=[{"round": 0, "score": 1.0, "train_score": 1.0,
                                    "train_per_task": {"t01": 1.0, "t02": 1.0}}])
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"

    got = _invoke(mod, _request(base, work, traj), monkeypatch,
                  reply=_edit_reply())          # 若闸门失效,这个回复会制造一个候选

    assert got["asked"] == 0, "闸门说没东西可修,却还是花了模型调用"
    assert got["stdout"]["changed"] is False
    assert traj.exists(), "停轮也必须留下轨迹"
    tthe = json.loads(traj.read_text())["steps"][0]["method_reported"]["tthe"]
    assert tthe["decision"] == "stop", tthe
    assert set(tthe["proposal_card"]) == set(mod.CARD_KEYS)
    assert tthe["card_valid"] is True


def test_no_traces_is_also_a_stop_and_does_not_invent_a_diagnosis(tmp_path, monkeypatch):
    """没有 trace = 没有可依赖的行为证据,不能凭空编一个诊断去改 harness。"""
    mod = _load()
    base = _write_harness(tmp_path / "base", traces={},
                          history=[{"round": 0, "score": 0.0,
                                    "train_per_task": {"t01": 0.0}}])
    work = tmp_path / "ws"; work.mkdir()

    got = _invoke(mod, _request(base, work, tmp_path / "traj.json"), monkeypatch,
                  reply=_edit_reply())

    assert got["asked"] == 0
    assert got["stdout"]["changed"] is False


# ------------------------------------------- label-free 纪律必须真的在 prompt 里 ---

def test_the_label_free_constraints_reach_the_editor(tmp_path, monkeypatch):
    """docstring 里写规则不算数,模型收到的那段文字里必须有这些规则。

    这些是 TTHE 提案 prompt 的可移植部分(`proposer.py:344-351,359-371,387`):
    不硬编码题目字面值、任务描述/Hint 权威、不写自定义 parser/regex(卡 GIL)、
    控制器而不是提案者执行子代。
    """
    mod = _load()
    base = _write_harness(tmp_path / "base",
                          traces={"t01": json.dumps({"reply": "wrong"}) + "\n"},
                          history=[{"round": 0, "score": 0.0, "train_score": 0.0,
                                    "train_per_task": {"t01": 0.0}}])
    work = tmp_path / "ws"; work.mkdir()

    got = _invoke(mod, _request(base, work, tmp_path / "traj.json"), monkeypatch,
                  reply=_edit_reply())

    assert got["asked"] == 1
    prompt = got["prompt"].lower()
    for needle in ("never hardcode", "authoritative", "regex", "gil",
                   "never execute", "label-free", "failing"):
        assert needle in prompt, f"prompt 里缺了 label-free 约束:{needle!r}"
    # 固定角色也要真的交给编辑器,否则角色就只是记录里的装饰
    assert mod.role_for(1)[1] in got["prompt"]


# ------------------------------------------------ proposal card:两条路径都要有 ---

def test_a_proposal_card_is_recorded_on_the_edit_path(tmp_path, monkeypatch):
    """编辑路径:card 的键集、类型、控制器字段都要与 TTHE 的校验一致。

    `candidate` / `base_candidate` / `branch_id` / `generation_round` /
    `peer_candidates` 是控制器知道的,方法自己填;`role` 是这一轮钉死的角色。
    """
    mod = _load()
    base = _write_harness(tmp_path / "base",
                          traces={"t01": json.dumps({"reply": "wrong"}) + "\n"},
                          history=[{"round": 0, "score": 0.0, "train_score": 0.0,
                                    "train_per_task": {"t01": 0.0}}])
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"

    got = _invoke(mod, _request(base, work, traj), monkeypatch, reply=_edit_reply())

    assert got["stdout"]["changed"] is True
    tthe = got["stdout"]["method_reported"]["tthe"]
    card = tthe["proposal_card"]
    assert tthe["decision"] == "propose"
    assert set(card) == set(mod.CARD_KEYS), f"card 键集不对:{sorted(card)}"
    assert card["candidate"] == "cand_round1"
    assert card["base_candidate"] == "cand_round0"
    assert card["branch_id"] == 1
    assert card["generation_round"] == "round1"
    assert card["peer_candidates"] == [], "mode A 没有同批 peer,列表必须是空的"
    assert card["role"] == mod.role_for(1)[1]
    assert mod.valid_proposal_card(
        card, candidate="cand_round1", base_candidate="cand_round0", branch_id=1,
        generation_round="round1", peer_candidates=[]) is True
    # 轨迹文件里也必须有同一张 card,否则曲线点上就看不到这次决策
    step = json.loads(traj.read_text())["steps"][0]
    assert step["method_reported"]["tthe"]["proposal_card"] == card


def test_a_proposal_card_is_recorded_on_the_stop_path(tmp_path, monkeypatch):
    """停轮路径也要有一张结构完整的 card:空 `behavior_changes` 就是"没改"。"""
    mod = _load()
    base = _write_harness(tmp_path / "base",
                          traces={"t01": json.dumps({"reply": "done"}) + "\n"},
                          history=[{"round": 0, "score": 1.0, "train_score": 1.0,
                                    "train_per_task": {"t01": 1.0}}])
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"

    got = _invoke(mod, _request(base, work, traj), monkeypatch, reply=_edit_reply())

    card = got["stdout"]["method_reported"]["tthe"]["proposal_card"]
    assert set(card) == set(mod.CARD_KEYS)
    assert card["behavior_changes"] == []
    assert mod._card_ok(card, 1, "cand_round0") is True
    assert traj.exists()


def test_an_invalid_card_falls_back_to_the_incumbent(tmp_path, monkeypatch):
    """没有 card = 没有可接受的子代,TTHE 会退回父代(`proposer.py:456-515`)。

    这条最容易静默出错:模型给了文件、但 card 键不全,若方法照收,它就把一个 TTHE
    从未接受过的东西交了出去。
    """
    mod = _load()
    base = _write_harness(tmp_path / "base",
                          traces={"t01": json.dumps({"reply": "wrong"}) + "\n"},
                          history=[{"round": 0, "score": 0.0, "train_score": 0.0,
                                    "train_per_task": {"t01": 0.0}}])
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"

    got = _invoke(mod, _request(base, work, traj), monkeypatch,
                  reply={"files": [{"path": "agent.py", "content": "print('x')\n"}],
                         "hypothesis": "no card here"})

    assert got["asked"] == 1
    assert got["stdout"]["changed"] is False, "card 无效却交出了候选"
    tthe = json.loads(traj.read_text())["steps"][0]["method_reported"]["tthe"]
    assert tthe["decision"] == "stop"
    assert tthe["card_valid"] is False
    assert json.loads(traj.read_text())["steps"][0]["harness_dir"] == str(base), \
        "回退时必须指向现任(incumbent),而不是一个半成品候选"


def test_a_provider_error_still_writes_a_trajectory_with_the_decision(tmp_path, monkeypatch):
    """不可读的失败比被报告的失败更糟:连报错路径也要留下 TTHE 的决策记录。"""
    mod = _load()
    base = _write_harness(tmp_path / "base",
                          traces={"t01": json.dumps({"reply": "wrong"}) + "\n"},
                          history=[{"round": 0, "score": 0.0, "train_score": 0.0,
                                    "train_per_task": {"t01": 0.0}}])
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"

    got = _invoke(mod, _request(base, work, traj), monkeypatch,
                  raises=RuntimeError("503 no available channel"))

    assert traj.exists(), "provider 报错时没有写出 trajectory"
    tthe = json.loads(traj.read_text())["steps"][0]["method_reported"]["tthe"]
    assert "503" in json.dumps(tthe), "真正的原因必须进记录"
    assert tthe["decision"] == "error"
    assert set(tthe["proposal_card"]) == set(mod.CARD_KEYS)
    assert got["stdout"]["changed"] is False
