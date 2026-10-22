"""RRSI 的逐轮决策规则,移植到 HarnessGrad 的曲线上。

为什么这些测试存在
------------------
RRSI 的贡献不是编辑器,是它**允许什么替换现任、一轮最多捆绑几个编辑,以及评估前
拦掉什么**的那套规则。把一套规则移植到另一个平台,最容易出的错不是代码写错,而是
规则被**悄悄丢掉**:名字还在,判定已经不再发生。这里每一条测试钉住一种丢法:

  1. 退火预算被当成常数 —— b_t 的名字还在,退火已经不在了;
  2. 验收下界被写成 `S' >= S_t - delta` —— 下界挂在现任而不是在历史最好之上,
     于是一个已经比历史最好差出噪声带的候选照样通过;
  3. 成本规则只剩一支 —— 带内和带外变成同一条判定;
  4. novelty 被放进增益分支 —— 它不再只是"带内平票";
  5. 停滞后不保留探索槽 —— 规则说"必须试没试过的组件",提案却可以不听;
  6. critic 只剩模型一层 —— 确定性的泄漏黑名单不再先跑,任务特化的编辑要靠模型
     的自觉;
  7. 用 `score`(考试侧)当 S —— 曲线会涨,涨的是过拟合。

mode A 没有否决权(一个候选、平台无条件接受),所以这些测试测的是**纯函数与记录**,
不是"平台会不会拒绝一个候选"。后半部分测主流程:每条出口都要留下 trajectory,
规则读的确实是 train 侧。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))


# --------------------------------------------------------------- 测试夹具 ---

def _load(name: str = "rrsi"):
    """按路径加载方法模块,和 test_editor.py 的做法一致。

    必须注册进 `sys.modules`:方法模块里的 `typing.NamedTuple` 在 Python 3.12 上会
    通过 `cls.__module__` 回查 `sys.modules` 来决定字段顺序,不注册就会在加载时报
    `AttributeError: 'NoneType' object has no attribute '__dict__'` —— 那是测试
    夹具的缺陷,不是方法的。
    """
    path = ROOT / "methods" / name / "run.py"
    spec = importlib.util.spec_from_file_location(f"m_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"m_{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_harness(root: Path, *, traces: dict[str, str] | None = None,
                   history: list[dict] | None = None) -> Path:
    """一个 base harness,channel 按契约放在 harness 目录里。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.json").write_text(json.dumps(
        {"name": "probe", "version": "1.0", "entrypoint": "agent.py"}))
    (root / "agent.py").write_text("print('hello')\n")
    hg = root / "_harnessgrad"
    (hg / "traces").mkdir(parents=True, exist_ok=True)
    (hg / "history").mkdir(parents=True, exist_ok=True)
    for tid, body in (traces or {"t01": '{"reply": "hi"}\n'}).items():
        (hg / "traces" / f"{tid}.jsonl").write_text(body)
    for point in (history or []):
        (hg / "history" / f"round-{point.get('round', 0)}.json").write_text(
            json.dumps(point))
    (hg / "round.json").write_text(json.dumps(
        (history or [{}])[-1] if history else {"round": 0, "score": 0.0}))
    return root


def _request(base: Path, work: Path, traj: Path, round_index: int = 1) -> dict:
    return {"platform_api_version": "0.1.0", "mode": "A", "base_harness": str(base),
            "workspace": str(work), "round_index": round_index,
            "incumbent_score": 0.0, "task_ids": ["t01"], "train_task_ids": ["t01"],
            "trajectory_out": str(traj)}


def _invoke(mod, request: dict, monkeypatch, respond):
    """用打桩的模型跑一遍 main(),返回 stdout 对象和每一次 ask 的参数。"""
    import editor

    seen: list[dict] = []

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        seen.append({"prompt": prompt, "system": system})
        return respond(prompt, system)

    monkeypatch.setattr(editor, "ask", fake_ask)
    monkeypatch.setattr(mod.editor, "ask", fake_ask, raising=False)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys, "stdout", out)
    mod.main()
    return {"stdout": json.loads(out.getvalue() or "{}"), "calls": seen}


def test_the_critic_is_never_routed_to_a_cli_improver(tmp_path, monkeypatch):
    """critic 要的是 `{"verdict": ...}`,而 CLI 改进器给不出 —— 它只会改文件、写一段话。

    实测(`v4-rrsi`,2026-10-05):critic 被送进 codex,codex 去改了 `.gitignore`,把
    `{"verdict":"accept","reasons":[...]}` 写在**prose**里,`_ask_cli` 于是返回
    `{"files": [...], "hypothesis": "<那段 prose>"}`;`accept()` 在顶层找不到 `verdict`,
    重试 3 次,每轮的候选都被这个 critic 拒掉 —— A 形状 arm 三轮**一个候选都没测到**,
    而曲线上的说法像是"提议太弱"。

    这条测试用真的 `ask` 跑派发:round.json 里放一个 CLI improver,把 `_ask_cli` 换成
    会抛异常的桩,再把 OpenAI 客户端换掉,断言 critic 仍然走 endpoint。
    """
    import openai
    import editor

    base = _write_harness(tmp_path / "h")
    (base / "_harnessgrad" / "round.json").write_text(json.dumps(
        {"identity": {"improver": {"name": "codex", "kind": "cli",
                                   "path": "/x/codex.js"}}}))

    def explode_cli(*_a, **_k):
        raise AssertionError("critic 被送进了 CLI 改进器 —— 它给不出 `verdict`")
    monkeypatch.setattr(editor, "_ask_cli", explode_cli)

    class _Msg:
        content = '{"verdict": "accept", "reasons": [], "risk_notes": []}'

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]
        usage = None
        model = "stub"

    class _Client:
        def __init__(self, **_kw):
            self.chat = type("Chat", (), {
                "completions": type("Comp", (), {
                    "create": staticmethod(lambda **_kw: _Resp())})()})()

    monkeypatch.setattr(openai, "OpenAI", _Client)
    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://stub/v1")
    monkeypatch.setenv("HG_METHOD_API_KEY", "stub")
    monkeypatch.setenv("HG_METHOD_MODEL", "stub-model")

    mod = _load()
    out = mod._critic_review("--- a/agent.py\n+++ b/agent.py\n+print(1)\n",
                             "a one line change", ["agent.py"], 3, base=base)
    assert out["verdict"] == "accept", out
    assert out.get("model_used") is True


def _point(round_no: int, train: float, *, score: float = 0.0,
           tokens: int | None = None, components: list[str] | None = None) -> dict:
    cost = {"harness_tokens": tokens, "harness_tokens_reported_by_tasks": 4 if tokens else 0}
    reported = ({"rrsi_rule": {"touched_components": components}}
                if components is not None else {})
    return {"round": round_no, "train_score": train, "score": score, "cost": cost,
            "method_reported": reported}


# ------------------------------------------------- 1) 退火编辑预算 b_t ---

def test_the_annealed_budget_matches_the_documented_schedule():
    """默认参数下 b_t 的整条序列必须是文档里那一条。

    `rrsi/schedule.py:43-50`:`b_t = ceil(b_min + (b_max-b_min) * 1/2 (1+cos(pi t/T)))`。
    取整最容易在两处出错,所以两处都断言:t=0 必须是 b_max(不是 b_max-1),
    t=T 必须是 b_min(cos 的浮点误差 `1.0000000002` 会 ceil 成 b_min+1,
    RRSI 用 `round(v, 9)` 挡住,这个测试挡住回归)。
    """
    mod = _load()
    assert mod._budget_table(20, 1, 4) == [
        4, 4, 4, 4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2]
    assert mod._edit_budget(0, 20, 1, 4) == 4, "t=0 是 b_max,不是 b_max-1"
    assert mod._edit_budget(20, 20, 1, 4) == 1, "t=T 是 b_min,浮点误差不许 ceil 上去"


# ---------------------------------------- 2) 噪声带验收下界(纯函数) ---

def test_the_acceptance_floor_rejects_a_candidate_inside_the_noise_band():
    """下界挂在 S*(历史最好)上,不是挂在现任 S_t 上。

    这正是最容易被移植错的一处。S*=0.80,现任=0.75,delta=0.05:一个降到 0.74 的
    候选,相对现任只跌了 0.01(在噪声带内),但已经掉到 S*-delta=0.75 之下,
    `rrsi/selection.py:107-111` 会先把它拒掉,根本不会去问成本规则。相对现任的
    "带内"和相对 S* 的下界是两件事。
    """
    mod = _load()
    cfg = mod.RRSIConfig()
    below = mod._judge(0.74, 100.0, 0.75, 90.0, 0.80, 0.05,
                       components=[], counts={}, cfg=cfg)
    assert below["stage"] == "floor" and below["admissible"] is False
    assert abs(below["delta_S"] - (-0.01)) < 1e-9, "这个候选相对现任确实只在噪声带内"
    assert "below noise-adjusted floor" in below["reason"]

    inside = mod._judge(0.76, 100.0, 0.75, 90.0, 0.80, 0.05,
                        components=[], counts={}, cfg=cfg)
    assert inside["stage"] != "floor", "过了下界的候选才有资格被成本规则审视"


# ------------------------------------------- 3) 成本规则的两支 ---

def test_the_two_cost_rule_branches_behave_differently():
    """同一个 dC,带外看 `beta0 + beta1*dS`,带内看 `w_s dS - w_c dC + w_n nu`。

    `rrsi/selection.py:81-94`。dC=+0.20 在 dS=+0.10(超出 delta)时完全付得起
    (预算 4.10),在 dS=+0.01(带内)时被形状规则否决(-2.00)。如果移植时只剩一支,
    这两条判定会变成同一条。
    """
    mod = _load()
    cfg = mod.RRSIConfig()
    above, branch_a, why_a = mod._cost_rule(0.10, 0.20, 0, 0.05, cfg)
    assert above is True and branch_a == "gain_above_band"
    assert "budget" in why_a

    within, branch_w, why_w = mod._cost_rule(0.01, 0.20, 0, 0.05, cfg)
    assert within is False and branch_w == "within_band"
    assert "nu=0" in why_w


# ------------------------------------------- 4) novelty 只在带内平票 ---

def test_novelty_only_tie_breaks_inside_the_noise_band():
    """`nu` 在带内能把一个零增益的候选拉过线,在带外一点用都没有。

    `rrsi/components.py:103-108` 说它 "only ever tie-breaks a candidate inside the
    noise band";`selection.py:84-94` 里它也只出现在 shaped 分支。带外那条分支用
    的是预算比较,`nu` 根本不参与。
    """
    mod = _load()
    cfg = mod.RRSIConfig()          # w_s=100, w_c=15, w_n=0.5

    # dS=0,dC=+0.02 -> shaped = -0.30。一个从未被接受过的结构化组件(nu=1)
    # 加 0.5,把 -0.30 拉到 +0.20,刚好过线。
    with_nu = mod._judge(0.75, 102.0, 0.75, 100.0, 0.80, 0.05,
                         components=["skill"], counts={}, cfg=cfg)
    without = mod._judge(0.75, 102.0, 0.75, 100.0, 0.80, 0.05,
                         components=["prompt"], counts={}, cfg=cfg)
    assert without["stage"] == "cost" and without["admissible"] is False
    assert with_nu["admissible"] is True and with_nu["novelty"] == 1

    # 带外:dS=+0.15,dC=+9.0,预算 6.10,dC 超了。给两个结构化 novelty 也救不回来。
    above = mod._judge(0.90, 500.0, 0.75, 50.0, 0.80, 0.05,
                       components=["skill", "memory"], counts={}, cfg=cfg)
    assert above["branch"] == "gain_above_band"
    assert above["admissible"] is False
    assert above["novelty"] == 2, "novelty 仍然被算出来记进记录,只是不参与判定"


# ---------------------------------------- 5) 停滞 + 未试组件 -> 探索槽 ---

def test_a_stalled_round_with_untried_components_reserves_an_exploration_slot(monkeypatch):
    """sigma_t=1 且有没试过的组件时,提案里必须有一个落在未试组件上。

    `rrsi/history.py:189-211` 的 `sigma_t = 1[S_t - S_{t-w} <= delta]`,以及
    `rrsi/loop.py:294` 的保留槽。这里同时测三件事:停滞判定、探索文本、以及
    `_gate` 真的会把"没有未试组件"的提案打回去。
    """
    mod = _load()
    trajectory = [0.5, 0.5, 0.5, 0.5]
    assert mod._stall_flag(trajectory, 3, 3, 0.0) == 1
    assert mod._stall_flag(trajectory, 2, 3, 0.0) == 0, "窗口不足 w 时不能报停滞"
    assert mod._stall_flag([0.5, 0.5, 0.5, 0.9], 3, 3, 0.0) == 0, "真的涨了就不停滞"

    explore = mod._exploration(3, 1, tried={"prompt"}, m_draft=1)
    assert explore["sigma"] == 1 and "skill" in explore["untried"]
    assert "RESERVED" in explore["text"]

    # 合规的那次会走到模型 critic,所以这里要打桩,否则测试依赖网络/凭据。
    import editor
    monkeypatch.setattr(editor, "ask", lambda prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True,
                        parent=None, endpoint_only=False:
                        {"verdict": "accept", "reasons": [], "risk_notes": []})

    cfg = mod.RRSIConfig()
    rule = {"budget": 4, "reserved": True, "untried": ["skill", "memory"]}
    objections, _ = mod._gate(["agent.py"], ["prompt"], "+x = 1\n", rule, cfg, "s")
    assert any("reserved exploration slot" in o for o in objections), objections

    ok, _ = mod._gate(["agent.py"], ["skill"], "diff\n", rule, cfg, "s")
    assert not any("reserved" in o for o in ok)


# ------------------------------------------- 6) critic 的两层 ---

def test_the_critic_rejects_a_task_specialized_edit(monkeypatch):
    """确定性黑名单先跑,模型复核后跑;两者都必须能拒。

    `rrsi/critic.py:105-119`:denylist 命中是**硬拒绝**,连模型都不问。任务特化的
    编辑有两类:直接引用评分侧的(这里的 `scorable`),和把某个任务写死进控制流
    的(确定性规则看不出,由模型层拒)。只保留模型一层的移植会在没有凭据/离线时
    静默放行。
    """
    mod = _load()
    hits = mod._critic_precheck('+ ANSWER = scorable["t03"]\n')
    assert hits and "answer key" in hits[0]

    import editor

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        assert system == mod.CRITIC_SYSTEM
        return {"verdict": "reject",
                "reasons": ["task-specialization: hard-codes task t03"]}

    monkeypatch.setattr(editor, "ask", fake_ask)
    verdict = mod._critic_review('+ if task == "t03": return "42"\n',
                                 "special-case one task", ["control_flow"], 3)
    assert verdict["verdict"] == "reject"
    assert any("task" in r for r in verdict["reasons"])
    assert verdict["model_used"] is True


def test_an_unparseable_critic_reply_rejects_rather_than_accepts(monkeypatch):
    """看不出来就是拒 —— 默认接受等于把泄漏放行。

    `rrsi/critic.py:152-154`:重试 `attempts` 次仍拿不到 verdict,返回 reject。

    断言的是**这个不变量**,不是某个措辞:回复读不出来 → 拒,并且理由里带上那个回复,
    好让读的人知道该改什么。它同时钉住**每次重试都换一个提问** —— 以前三次发的是同一段
    文本,于是一个确定性的格式失败会一模一样地重复三次,把好候选因为格式而非因为泄漏毙掉。
    """
    mod = _load()
    import editor
    prompts: list[str] = []

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        prompts.append(prompt)
        return {"no_change": "???"}

    monkeypatch.setattr(editor, "ask", fake_ask)
    verdict = mod._critic_review("+ x = 1\n", "s", ["prompt"], 2)
    assert verdict["verdict"] == "reject"
    assert verdict["model_used"] is True
    assert "critic output" in verdict["reasons"][0], verdict["reasons"]
    assert "verdict" in verdict["reasons"][0], (
        "拒了却没说清回复缺什么,模型下一轮还是会照样回错")
    assert len(prompts) == 2
    assert prompts[1] != prompts[0], (
        "重试发了同一段文本 —— 确定性的格式失败会原样重复,重试就等于没重试")
    assert "could not be used" in prompts[1], (
        "第二次提问没有把失败原因回给模型")


# ---------------------------------------- 7) 规则读 train_score ---

def test_the_rule_reads_train_score_not_the_eval_score():
    """S 来自 train 侧;`score` 是考试侧,规则一个字都不许读。

    第一段:两点 train=0.2/0.3、score=0.9/0.1,S* 必须是 0.3 而不是 0.9。
    第二段:一个只有 `score`、没有 `train_score` 的点(数据集没声明 split)不能让
    规则退回去读考试分 —— 它必须报告"规则没被评估"。
    """
    mod = _load()
    cfg = mod.RRSIConfig()
    rule = mod._rule_report(
        [_point(0, 0.2, score=0.9), _point(1, 0.3, score=0.1)], 1, cfg)
    assert rule["S_star"] == 0.3, "S* 必须来自诊断侧"
    assert rule["train_scores"] == [0.2, 0.3]
    # `score_source` 记的是「这次运行学的那一侧」,不是一个固定的字段名:§2.6 之后
    # 哪一侧是诊断侧由 `score_kind` 决定。断言它说了这件事,并且没有声称读过考试 ——
    # 绑在字面量 `"train_score"` 上,会在规则本身变对的时候先失败一次。
    assert "score_kind" in rule["score_source"], rule["score_source"]
    assert "never the exam" in rule["score_source"], rule["score_source"]

    # 训练运行的曲线点:`train_score` **按设计就是 null**,诊断分在 `score` 里
    # (`harnessgrad/records.py:_curve_point`:单侧运行里 `train_*` 会是 per_task 的副本,所以不留)。
    # 这是那条实测故障的直接回归 —— 修复前这里得到 S* == None,规则不触发,每一轮
    # 都报 "no edit",而那只 harness 其实一步都没跑起来。
    training = [
        {"round": 0, "side": "train", "score_kind": "training", "score": 0.2,
         "per_task": {"t01": 0.0}, "train_score": None, "train_per_task": {},
         "split": {"train": ["t01"]}},
        {"round": 1, "side": "train", "score_kind": "training", "score": 0.3,
         "per_task": {"t01": 1.0}, "train_score": None, "train_per_task": {},
         "split": {"train": ["t01"]}},
    ]
    rule3 = mod._rule_report(training, 1, cfg)
    assert rule3["train_scores"] == [0.2, 0.3], rule3["train_scores"]
    assert rule3["S_star"] == 0.3, "训练点上诊断分在 score 里,没读它"
    assert rule3["rule_evaluated"] is True

    exam_only = [{"round": 0, "score": 0.9}, {"round": 1, "score": 0.95}]
    assert mod._train_score(exam_only[0]) is None
    rule2 = mod._rule_report(exam_only, 1, cfg)
    assert rule2["S_star"] is None and rule2["rule_evaluated"] is False
    assert rule2["next_floor"] is None, "没有 train 信号时不许编出一个下界"


# -------------------------------- 8) 没有 token 时成本规则不假装开火 ---

def test_the_cost_rule_is_inactive_when_the_platform_reported_no_tokens():
    """平台没报 token 时,不能把 `Delta C = 0` 的替代输入当成规则判定。

    RRSI 自己会替换成 0(`rrsi/evaluate.py:131-135`),而喂恒等于 0 的成本规则
    什么都放行。移植的选择是:算出同样的 0,但标 `cost_rule_active: false` 并把
    admissibility 留成 null —— 记录里不能出现一个从未触发的判定。
    """
    mod = _load()
    cfg = mod.RRSIConfig()
    no_cost = mod._judge(0.76, None, 0.75, None, 0.80, 0.05,
                         components=[], counts={}, cfg=cfg)
    assert no_cost["admissible"] is None and no_cost["cost_rule_active"] is False
    assert no_cost["stage"] == "cost_skipped"

    # 主流程里的顶层标记也必须是 false,而不是一个看起来通过的成本结论。
    rule = mod._rule_report(
        [_point(0, 0.5, tokens=None), _point(1, 0.6, tokens=None)], 1, cfg)
    assert rule["cost_rule_active"] is False


# ------------------------------------------------- 9) 主流程与出口 ---

def test_main_records_the_rule_and_carries_components_between_rounds(tmp_path, monkeypatch):
    """主流程:规则进 `method_reported`;本轮组件写进记录,下一轮才读得回来。

    这是 mode A 里唯一的状态通道:平台每轮只给历史曲线点,RRSI 的 `history.jsonl`
    不存在,所以 `touched_components` 由本方法写进方法自报块,下一轮从点里读回来。
    没有它,novelty 和探索槽都无从谈起。
    """
    base = _write_harness(tmp_path / "base", history=[
        _point(0, 0.5, tokens=1000),
        dict(_point(1, 0.6, tokens=900), method_reported={
            "rrsi_rule": {"touched_components": ["prompt"]}}),
    ])
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"

    mod = _load()
    proposal = {"files": [{"path": "agent.py", "content": "print('better')\n",
                           "component": "prompt"}],
                "hypothesis": "tighten the loop"}

    def respond(prompt, system, base=None, skill=True):
        if system == mod.CRITIC_SYSTEM:
            return {"verdict": "accept", "reasons": [], "risk_notes": []}
        return proposal

    got = _invoke(mod, _request(base, work, traj, round_index=2), monkeypatch, respond)

    assert got["stdout"]["changed"] is True
    step = json.loads(traj.read_text())["steps"][0]
    rule = step["method_reported"]["rrsi_rule"]
    assert rule["touched_components"] == ["prompt"]
    assert rule["S_star"] == 0.6
    assert rule["tried"] == ["prompt"], "上一轮的组件必须被重建出来"
    assert rule["budget"] == 4, "t=1 时 b_t 仍是 4"
    assert rule["prune_set"] == [], "prompt 近期增益 +0.1,不该进剪枝集"
    assert rule["next_floor"] == 0.6
    assert step["edit_kind"] == "prompt"
    # 提案用的是平台已经在磁盘上量好的候选目录,不是方法自报的分数。
    assert Path(step["harness_dir"]).is_dir()


def test_an_over_budget_proposal_is_rejected_and_recorded(tmp_path, monkeypatch):
    """b_t 是提案侧约束:超预算的提案不能悄悄当没看见。

    RRSI 的 b_t 约束的是 `||z_t||_0`(一轮里活跃的独立编辑数)。写文件的方法没有
    别的地方能表达它,所以 `_gate` 会数文件数并把超预算的提案打回去。
    """
    base = _write_harness(tmp_path / "base", history=[_point(0, 0.5, tokens=1000)])
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"
    mod = _load()

    proposal = {"files": [
        {"path": "agent.py", "content": "a = 1\n", "component": "prompt"},
        {"path": "b.py", "content": "b = 1\n", "component": "prompt"},
        {"path": "c.py", "content": "c = 1\n", "component": "prompt"},
        {"path": "d.py", "content": "d = 1\n", "component": "prompt"},
        {"path": "e.py", "content": "e = 1\n", "component": "prompt"},
    ], "hypothesis": "bundle five"}

    # round_index=1 -> t=0 -> b_t=4,五个文件超预算。critic 不该被问到(确定性拒绝)。
    asked_critic = []

    def respond(prompt, system, base=None, skill=True):
        if system == mod.CRITIC_SYSTEM:
            asked_critic.append(True)
            return {"verdict": "accept"}
        return proposal

    got = _invoke(mod, _request(base, work, traj, round_index=1), monkeypatch, respond)

    assert got["stdout"]["changed"] is False
    rule = json.loads(traj.read_text())["steps"][0]["method_reported"]["rrsi_rule"]
    assert any("edit budget" in o for o in rule["dropped"])
    assert not asked_critic, "确定性拒绝不该再花一次模型调用"


def test_a_provider_error_still_produces_a_trajectory(tmp_path, monkeypatch):
    """失败必须被报告:不可读的失败比被报告的失败更糟。"""
    base = _write_harness(tmp_path / "base", history=[_point(0, 0.5, tokens=1000)])
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"
    mod = _load()

    def respond(prompt, system, base=None, skill=True):
        raise RuntimeError("503 no available channel")

    got = _invoke(mod, _request(base, work, traj, round_index=1), monkeypatch, respond)

    assert traj.exists()
    step = json.loads(traj.read_text())["steps"][0]
    assert "503" in json.dumps(step["method_reported"])
    assert got["stdout"]["changed"] is False


def test_prune_set_lists_accepted_machinery_with_nonpositive_recent_yield():
    """B_t 只收录近期最好增益 <= 0 的组件;正增益的组件不进剪枝集。

    `rrsi/history.py:143-163`。ds 由 train 侧配对差重建:round1 相对 round0 跌了
    0.1,round2 相对 round1 涨了 0.2。prompt 进 B_t,skill 不进。
    """
    mod = _load()
    points = [
        _point(0, 0.5),
        _point(1, 0.4, components=["prompt"]),
        _point(2, 0.6, components=["skill"]),
    ]
    tried, counts, comps_by_round, ds_by_round = mod._edit_history(points)
    prune = mod._prune_set(2, 4, tried, comps_by_round, ds_by_round, counts)
    by_component = {p["component"]: p for p in prune}
    assert "prompt" in by_component and "skill" not in by_component
    assert by_component["prompt"]["recent_best_gain"] == -0.1
    assert by_component["prompt"]["accepted_edits_in_incumbent"] == 1


# --------------------------------------------------- delta 的校准通道 ---

def test_delta_is_calibrated_from_the_platform_s_own_spread():
    """**RRSI 的噪声带现在有东西可校准了。**

    这段代码以前写着"per-trial rewards 拿不到,所以没法校准" —— 那时是真的。现在每个曲线点
    都带 `per_task_trials` 和 `score_std`,而 `score_std` 就是**同一次测量内部**运行级分数
    在多次试验上的总体标准差,也就是固定 harness 下的 `sd(S)`。两次独立测量的
    `sd(Delta S) = sqrt(2) * sd(S)`,于是 `delta = z * sqrt(2) * score_std` 正是
    `_delta_from_repeated_base` 的公式,只是喂进去的是平台自己的数。

    实测(5 题 3 次重复):`score_std = 0.094` -> delta ≈ 0.266,正好是那个把对照臂骗成
    "涨了 0.200" 的重测幅度。噪声带的意义就在这里:只接受越过它的人,就骗不动。
    """
    mod = _load()
    cfg = mod.RRSIConfig(delta=None, delta_fallback=0.0, delta_z=2.0)
    history = [{"round": 0, "score": 0.2, "score_std": 0.094, "n_trials": 3}]
    delta, source = mod._delta(history, cfg)
    assert delta == pytest.approx(2.0 * 0.094 * (2 ** 0.5), rel=1e-6), delta
    assert delta == pytest.approx(0.2659, abs=1e-3), delta
    assert "calibrated" in source, source
    assert "0.0940" in source and "3 trials" in source, source


def test_one_trial_has_no_spread_to_calibrate_from():
    """反向的一半:`n_trials = 1` 时点上没有 `score_std`,于是回落并且说明原因。

    这条边界不能糊:"没有方差可报"和"方差是零"是两件事,而按零方差校准等于把噪声带
    关掉 —— 那正好会让重测噪声冒充成真增益。
    """
    mod = _load()
    cfg = mod.RRSIConfig(delta=None, delta_fallback=0.11, delta_z=2.0)
    delta, source = mod._delta([{"round": 0, "score": 0.2}], cfg)
    assert delta == pytest.approx(0.11), delta
    assert "fallback" in source and "n_trials" in source, source
    # 显式给的值照样优先
    delta2, source2 = mod._delta([{"round": 0, "score_std": 0.5, "n_trials": 3}],
                                 mod.RRSIConfig(delta=0.05, delta_z=2.0))
    assert delta2 == pytest.approx(0.05) and "explicit" in source2, (delta2, source2)
