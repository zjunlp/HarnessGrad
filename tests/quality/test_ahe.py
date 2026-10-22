"""AHE 的每轮决策规则:预注册预测 → 归因判决 → 回滚/换层。

AHE 真正的方法不是"改一版、分高就留",而是**改之前先把预测写下来**:每条改动声明
`predicted_fixes` 和 `risk_tasks`,下一轮拿 per-task 的翻转去判它。这里钉住三件事:

  1. 判决的五个分支和它们的**优先级** —— `MIXED` 在 `EFFECTIVE` 之前判,所以"该修的
     都修了、但踩中自己声明的风险任务"是 MIXED 而不是 EFFECTIVE(`evolve.py:2294-2303`);
     `n_risk_hit > 0` 而 `n_fixed == 0` 是 HARMFUL,即使一条预测都没有。
  2. HARMFUL 必须产生回滚/换层指令,并**点名不要再碰的组件层** —— 这是 AHE 的
     "同一失败类在同一个层上两轮没修好就回滚、换个层再来"(`evolve_prompt.md:103`)。
  3. 这一轮的 manifest 必须进 `method_reported.ahe_manifest`,因为下一轮的归因只能
     从这里读到预测;不写下来,判决就永远无从谈起。

另外钉住一个容易被误读的事实:**AHE 没有验收门槛**。它的回滚是模型自己把文件拷回去
(`evolve.py:4377`, `:2860-2861`),循环无条件把工作区带进下一轮。所以这里也只做
"回滚",不做"拒收"。
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


def _load(name: str = "ahe"):
    path = ROOT / "methods" / name / "run.py"
    spec = importlib.util.spec_from_file_location(f"m_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(round_no: int, *, predicted, risks, level="middleware",
              pattern="tool-error", change_id="chg-1", description="tweak") -> dict:
    """一条 AHE `change_manifest.json`,按 `evolve_prompt.md:201-218` 的字段。"""
    return {
        "iteration": round_no,
        "declared": True,
        "changes": [{
            "id": change_id,
            "type": "improvement",
            "description": description,
            "files": ["agent.py"],
            "failure_pattern": pattern,
            "predicted_fixes": list(predicted),
            "risk_tasks": list(risks),
            "constraint_level": level,
            "why_this_component": "test",
        }],
    }


def _point(round_no: int, per_task: dict, *, manifest: dict | None = None) -> dict:
    values = list(per_task.values())
    score = sum(values) / len(values) if values else 0.0
    reported = {"ahe_manifest": manifest} if manifest is not None else {}
    return {
        "round": round_no, "score": score, "score_ci95": [score, score],
        "per_task": dict(per_task), "train_per_task": dict(per_task),
        "train_score": score, "measured_by_platform": True,
        "identity": {"harness_sha": f"sha{round_no}"},
        "method_reported": reported,
    }


def _channel(tmp_path: Path, points: list[dict], states: list[int] = (),
             traces: dict[str, str] | None = None) -> Path:
    """带 channel 的 base harness:history、states、traces 都按契约摆好。

    每个 state 目录里放一个 `NOTES.md`,这样"候选到底建在哪个状态上"是可核对的 ——
    只看 `agent.py` 会被模型的编辑覆盖掉,看不出回滚有没有真的发生。
    """
    base = tmp_path / "base"
    base.mkdir(parents=True)
    (base / "harness.json").write_text(json.dumps({"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    (base / "agent.py").write_text("print('incumbent')\n")
    (base / "NOTES.md").write_text("notes from the incumbent\n")

    hg = base / "_harnessgrad"
    (hg / "traces").mkdir(parents=True)
    (hg / "history").mkdir(parents=True)
    (hg / "states").mkdir(parents=True)
    for p in points:
        (hg / "history" / f"round-{p['round']}.json").write_text(json.dumps(p))
    (hg / "round.json").write_text(json.dumps(points[-1] if points else {}))

    index: dict = {"kept": [], "window": 12, "available": len(states)}
    for r in states:
        d = hg / "states" / f"round-{r}"
        d.mkdir(parents=True)
        (d / "harness.json").write_text((base / "harness.json").read_text())
        (d / "agent.py").write_text(f"print('v{r}')\n")
        (d / "NOTES.md").write_text(f"notes from round {r}\n")
        index["kept"].append(f"round-{r}")
        index[f"round-{r}"] = {"sha": f"sha{r}", "harness_dir": str(d)}
    (hg / "states" / "index.json").write_text(json.dumps(index))

    for tid, body in (traces or {"t1": json.dumps({"command": "ls"}) + "\n"}).items():
        (hg / "traces" / f"{tid}.jsonl").write_text(body)
    return base


def _invoke(mod, request: dict, monkeypatch, reply=None, raises=None) -> dict:
    """跑 `main()`,模型被替换成 stub,把 stdout 对象和 prompt 一起返回。"""
    import editor

    captured: dict = {}

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
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


def _request(base: Path, tmp_path: Path, round_index: int = 2,
             task_ids: tuple = ("t1", "t2")) -> dict:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return {"platform_api_version": "0.1.0", "mode": "A",
            "base_harness": str(base), "workspace": str(ws),
            "round_index": round_index, "incumbent_score": 0.0,
            "task_ids": list(task_ids), "train_task_ids": list(task_ids),
            "trajectory_out": str(tmp_path / "traj.json")}


# ----------------------------------------------------- 判决的五个分支和优先级 ---

@pytest.mark.parametrize("predicted,risks,flipped,regressed,expected", [
    # 风险任务踩中、什么都没修 —— HARMFUL
    (["t1"], ["t2"], [], ["t2"], "HARMFUL"),
    # 修了一个、又踩了一个 —— MIXED。注意:predicted 全中也会是 MIXED,因为
    # MIXED 的分支排在 EFFECTIVE 前面(evolve.py:2296-2299)。
    (["t1"], ["t2"], ["t1"], ["t2"], "MIXED"),
    # 预测的全中、没有风险 —— EFFECTIVE
    (["t1", "t2"], [], ["t1", "t2"], [], "EFFECTIVE"),
    # 只中了一部分 —— PARTIALLY_EFFECTIVE
    (["t1", "t2"], [], ["t1"], [], "PARTIALLY_EFFECTIVE"),
    # 一条都没中 —— INEFFECTIVE
    (["t1"], [], [], [], "INEFFECTIVE"),
    # 一条预测都没声明,却踩了风险 —— 仍然是 HARMFUL(evolve.py:2294-2295)
    ([], ["t2"], [], ["t2"], "HARMFUL"),
])
def test_the_five_verdict_branches(predicted, risks, flipped, regressed, expected):
    """判决是由 declared predicted/risk 与 observed flipped/regressed 算出来的,
    不是模型自报的。"""
    mod = _load()
    manifest = _manifest(1, predicted=predicted, risks=risks)
    out = mod.evaluate_changes(manifest, {"flipped": flipped, "regressed": regressed})
    assert out["change_evaluations"][0]["verdict"] == expected, out


def test_a_regression_outside_every_declaration_is_unattributed():
    """没在 predicted_fixes / risk_tasks 里的回归不属于任何改动,必须单独报出来
    (`evolve.py:2318-2320`) —— 否则它会消失,而不是变成一个待解释的现象。"""
    mod = _load()
    manifest = _manifest(1, predicted=["t1"], risks=["t2"])
    out = mod.evaluate_changes(manifest, {"flipped": [], "regressed": ["t2", "t9"]})
    assert out["unattributed_regressions"] == ["t9"], out


def test_observed_diff_reads_the_one_zero_scores():
    """平台 per-task 分就是 0.0/1.0(`eval/run/runner.py:168-172`),所以 AHE 的
    `reward >= 1.0`(`evolve.py:634`)可以原样映射,没有阈值要调。"""
    mod = _load()
    before = _point(0, {"t1": 1.0, "t2": 0.0, "t3": 1.0})
    after = _point(1, {"t1": 0.0, "t2": 1.0, "t3": 1.0})
    diff = mod.observed_diff(before, after)
    assert diff["flipped"] == ["t2"]      # fail -> pass
    assert diff["regressed"] == ["t1"]    # pass -> fail
    assert diff["stable_pass"] == ["t3"]


# --------------------------------------------- HARMFUL 必须回滚并点名组件层 ---

def _harmful_history():
    """上一轮的改动踩中风险任务 t2、没修任何东西 —— HARMFUL。"""
    manifest = _manifest(1, predicted=["t1"], risks=["t2"], level="middleware",
                         pattern="shell-loop")
    return [_point(0, {"t1": 1.0, "t2": 1.0}), _point(1, {"t1": 0.0, "t2": 0.0},
                                                     manifest=manifest)]


def test_harmful_verdict_asks_for_a_rollback_and_names_the_level_to_avoid(
        tmp_path, monkeypatch):
    """只有回滚不够:AHE 的规则是回滚**并且换个组件层**(`evolve_prompt.md:103`)。
    指令里必须出现那一层,否则模型会原样在同一层再试一次。"""
    mod = _load()
    base = _channel(tmp_path, _harmful_history(), states=[0, 1])

    got = _invoke(mod, _request(base, tmp_path), monkeypatch,
                  reply={"no_change": "nothing"})

    prompt = got["prompt"]
    assert "HARMFUL" in prompt
    assert "Must rollback" in prompt, "AHE 的 Suggested Action 没进 prompt"
    assert "different component level" in prompt, "缺少「换一层重来」的指令"
    assert "'middleware'" in prompt, f"没有点名不要再碰的层:{prompt[-1200:]}"


def test_a_harmful_change_is_rolled_back_onto_the_state_before_it(
        tmp_path, monkeypatch):
    """回滚必须是真的:候选要建在上一轮**之前**的那个状态上(`states/round-0`),
    而不只是 prompt 里的一句话。`NOTES.md` 就是那条证据 —— 模型只改 `agent.py`,
    所以没有被覆盖的 `NOTES.md` 只能来自被选中的底。"""
    mod = _load()
    base = _channel(tmp_path, _harmful_history(), states=[0, 1])

    _invoke(mod, _request(base, tmp_path), monkeypatch,
            reply={"files": [{"path": "agent.py", "content": "print('new')\n"}],
                   "hypothesis": "re-approach at tool_impl"})

    dest = Path(_request(base, tmp_path)["workspace"]) / "candidate"
    assert (dest / "agent.py").read_text() == "print('new')\n"
    assert (dest / "NOTES.md").read_text() == "notes from round 0\n", \
        "HARMFUL 之后候选没有建在回滚状态上"


def test_a_non_harmful_verdict_does_not_roll_back(tmp_path, monkeypatch):
    """EFFECTIVE 不该被"顺手回滚":没有判决要求回滚时,底就是当前 harness。"""
    mod = _load()
    manifest = _manifest(1, predicted=["t1"], risks=[])
    # 分数必须低于 target(0.95),否则先触发的是停止规则而不是这一条判决。
    history = [_point(0, {"t1": 0.0, "t2": 0.0}),
               _point(1, {"t1": 1.0, "t2": 0.0}, manifest=manifest)]
    base = _channel(tmp_path, history, states=[0, 1])

    _invoke(mod, _request(base, tmp_path), monkeypatch,
            reply={"files": [{"path": "agent.py", "content": "print('new')\n"}]})

    dest = Path(_request(base, tmp_path)["workspace"]) / "candidate"
    assert (dest / "NOTES.md").read_text() == "notes from the incumbent\n", \
        "EFFECTIVE 的候选不该来自更早的状态"


def test_the_two_iteration_rule_names_an_abandoned_level(tmp_path, monkeypatch):
    """同一个失败类在同一个层上两轮都不是 EFFECTIVE,这一层就要被标成"别再试"
    (`evolve_prompt.md:103`)。AHE 让模型自己从 evolution_history 里看出来;这里
    算出来,因为一次性编辑器看不了历史文件。"""
    mod = _load()
    m1 = _manifest(1, predicted=["t1"], risks=[], level="prompt", pattern="shell-loop")
    m2 = _manifest(2, predicted=["t1"], risks=[], level="prompt", pattern="shell-loop")
    history = [_point(0, {"t1": 0.0, "t2": 1.0}),
               _point(1, {"t1": 0.0, "t2": 1.0}, manifest=m1),
               _point(2, {"t1": 0.0, "t2": 1.0}, manifest=m2)]
    base = _channel(tmp_path, history, states=[0, 1, 2])

    records = mod.attribution_history(mod.editor.load_history(base))
    assert mod.abandoned_levels(records) == {"shell-loop": ["prompt"]}

    got = _invoke(mod, _request(base, tmp_path, round_index=3), monkeypatch,
                  reply={"no_change": "x"})
    assert "shell-loop" in got["prompt"] and "'prompt'" in got["prompt"]


# ----------------------------------- 这一轮的 manifest 必须被记下来给下一轮判 ---

def test_this_rounds_manifest_is_recorded_for_the_next_round(tmp_path, monkeypatch):
    """预注册的意义在于**在测量之前**写进记录。平台把 step 的 `method_reported`
    原样复制到曲线点(`harnessgrad/records.py:_curve_point`),所以下一轮的归因只能读这里;不写就没有
    任何东西可判。"""
    mod = _load()
    history = [_point(0, {"t1": 0.0, "t2": 1.0}), _point(1, {"t1": 0.0, "t2": 1.0})]
    base = _channel(tmp_path, history, states=[0, 1])
    traj = tmp_path / "traj.json"

    got = _invoke(mod, _request(base, tmp_path), monkeypatch, reply={
        "files": [{"path": "agent.py", "content": "print('better')\n"}],
        "hypothesis": "fix the shell loop",
        "changes": [{"id": "chg-1", "type": "improvement",
                     "description": "retry on non-zero exit",
                     "failure_pattern": "shell-loop",
                     "predicted_fixes": ["t1"], "risk_tasks": ["t2"],
                     "constraint_level": "tool_impl"}]})

    step = json.loads(traj.read_text())["steps"][0]
    manifest = step["method_reported"]["ahe_manifest"]
    assert manifest["declared"] is True
    assert manifest["iteration"] == 2, "manifest 的 iteration 必须是产出它的那一轮"
    assert manifest["changes"][0]["predicted_fixes"] == ["t1"]
    assert manifest["changes"][0]["risk_tasks"] == ["t2"]
    assert manifest["changes"][0]["constraint_level"] == "tool_impl"
    assert got["stdout"]["method_reported"]["ahe_manifest"]["changes"][0][
        "predicted_fixes"] == ["t1"]
    # `constraint_level` 在 AHE 里只是一句声明,这里把它变成可读的 edit_kind,
    # 而不是拿它做任何判断(INTERFACE.md §4.6)。
    assert step["edit_kind"] == "tool_impl"


def test_no_declared_manifest_is_recorded_as_an_empty_declaration(tmp_path,
                                                                  monkeypatch):
    """模型没声明预测时,记录要能区分"没有预测"和"方法没跑";空声明下一轮会被
    判成 INEFFECTIVE,这是忠实的,而不是一个缺失的测量。"""
    mod = _load()
    history = [_point(0, {"t1": 0.0}), _point(1, {"t1": 0.0})]
    base = _channel(tmp_path, history, states=[0, 1])
    traj = tmp_path / "traj.json"

    _invoke(mod, _request(base, tmp_path, task_ids=("t1",)), monkeypatch,
            reply={"files": [{"path": "agent.py", "content": "x=1\n"}]})

    manifest = json.loads(traj.read_text())["steps"][0]["method_reported"]["ahe_manifest"]
    assert manifest["declared"] is False and manifest["changes"] == []
    assert "note" in manifest


def test_the_previous_rounds_manifest_is_graded_from_history(tmp_path, monkeypatch):
    """归因读的是上一轮写下的 manifest 和两轮之间的 `per_task` 翻转,而不是模型
    这一轮的自我评价。"""
    mod = _load()
    manifest = _manifest(1, predicted=["t1", "t2"], risks=[], level="skill",
                         pattern="parse-error")
    history = [_point(0, {"t1": 0.0, "t2": 0.0}),
               _point(1, {"t1": 1.0, "t2": 0.0}, manifest=manifest)]
    base = _channel(tmp_path, history, states=[0, 1])

    got = _invoke(mod, _request(base, tmp_path), monkeypatch,
                  reply={"no_change": "x"})
    # t1 flipped, t2 没有 -> PARTIALLY_EFFECTIVE
    assert got["stdout"]["method_reported"]["ahe_attribution"]["summary"] == \
        "chg-1: PARTIALLY_EFFECTIVE", got["stdout"]["method_reported"]
    assert "PARTIALLY_EFFECTIVE" in got["prompt"]


def test_a_round_with_no_previous_manifest_skips_attribution(tmp_path, monkeypatch):
    """没有 manifest 就没有预测可判 —— AHE 会跳过归因(`evolve.py:4389-4390`),
    这里也必须说出来,而不是编一个判决。"""
    mod = _load()
    base = _channel(tmp_path, [_point(0, {"t1": 0.0}), _point(1, {"t1": 0.0})],
                    states=[0, 1])

    got = _invoke(mod, _request(base, tmp_path), monkeypatch,
                  reply={"no_change": "x"})
    assert "attribution step is skipped" in got["prompt"]


# ------------------------------------------------------------- 停止规则 ---

def test_the_stop_rule_writes_a_trajectory_with_changed_false(tmp_path, monkeypatch):
    """AHE 在 `pass_rate >= target_pass_rate` 时直接 break(`evolve.py:4396`,
    默认 0.95)。mode A 里对应的答案就是 `changed: false`,而且**不该花一次模型调用**
    (`INTERFACE.md` §4.55)。"""
    mod = _load()
    base = _channel(tmp_path, [_point(0, {"t1": 1.0, "t2": 1.0})], states=[0])
    traj = tmp_path / "traj.json"

    got = _invoke(mod, _request(base, tmp_path, round_index=1), monkeypatch)

    assert traj.exists(), "停止路径也必须有 trajectory"
    assert got["stdout"]["changed"] is False
    assert "prompt" not in got, "已经达到目标就不该再花一次模型调用"
    manifest = json.loads(traj.read_text())["steps"][0]["method_reported"]["ahe_manifest"]
    assert manifest["stopped"] is True and manifest["changes"] == []
    assert "target" in got["stdout"]["hypothesis"]


def test_a_provider_error_still_produces_a_trajectory(tmp_path, monkeypatch):
    """不可读的失败比被报告的失败更糟:它看起来像方法什么都没做。"""
    mod = _load()
    base = _channel(tmp_path, [_point(0, {"t1": 0.0}), _point(1, {"t1": 0.0})],
                    states=[0, 1])
    traj = tmp_path / "traj.json"

    got = _invoke(mod, _request(base, tmp_path), monkeypatch,
                  raises=RuntimeError("503 no available channel"))

    assert traj.exists()
    assert got["stdout"]["changed"] is False
    assert "503" in json.dumps(
        json.loads(traj.read_text())["steps"][0]["method_reported"])
