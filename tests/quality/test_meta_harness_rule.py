"""Meta-Harness 的 frontier 规则:每题最佳 + 总体最佳 + 更宽的历史。

**为什么这个移植值得单独一个文件。** 它是八家里唯一一个**选择形状**和我们不同的:
不是"一个 incumbent",而是**每道题一张最佳表**(`update_frontier`),外加总体最佳。
它的论文主张也不是编辑质量,而是 **proposer 看到的历史有多宽**——"an agentic proposer
that accesses the source code, scores, and execution traces of *all prior candidates*
through a filesystem"。而我们的 `_harnessgrad/` channel 就是同一个东西,所以这条规则
是我们设计的一次外部确认,同时它的**不可表达部分**也最清楚(mode A 没有 smoke 门、

上游:**Meta-Harness**,arXiv 2603.28052(`stanford-iris-lab/meta-harness`)。frontier 规则引自它
发布的搜索循环(`update_frontier` / `propose_claude` / `validate_agent_class` / `harbor_run`);
本文件钉的是“每题最佳 + 总体最佳 + 同分不夺位”这三条,以及被省略的 smoke 门。
12 个 state 的历史窗口、搜索集与报告集不分开)。

这个文件测纯规则 + 一次带假模型的 main() 集成。
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


def _load():
    spec = importlib.util.spec_from_file_location("meta_harness",
                                                  ROOT / "methods" / "meta_harness" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MH = _load()


def _training(round_no: int, per_task: dict, *, hypothesis: str = "",
              files: list[str] | None = None, tokens: int = 100) -> dict:
    score = sum(per_task.values()) / len(per_task) if per_task else None
    return {"round": round_no, "score": score, "score_kind": "training",
            "per_task": per_task, "method_hypothesis": hypothesis,
            "edits_applied": files or [],
            "identity": {"harness_sha": f"sha{round_no}"},
            "cost": {"harness_tokens": tokens, "harness_model_calls": 2,
                     "wall_clock_s": 12.0}}


# ------------------------------------------------------------- frontier ---

def test_the_frontier_is_per_task_and_keeps_the_overall_best():
    """`update_frontier` 的本体:每题一张表(谁在哪道题最好),外加 `_best`。"""
    history = [
        _training(0, {"a": 0.0, "b": 1.0}),
        _training(1, {"a": 1.0, "b": 0.0}),
        _training(2, {"a": 0.5, "b": 0.5}),
    ]
    f = MH.frontier_of(history)
    assert f["tasks"]["a"] == {"best_agent": "r1", "pass_rate": 1.0}
    assert f["tasks"]["b"] == {"best_agent": "r0", "pass_rate": 1.0}
    # 均分:0.5 / 0.5 / 0.5 —— 严格大于,所以最早的持有者不被后来的平局顶掉
    assert f["_best"] == {"agent": "r0", "avg_pass_rate": 0.5}


def test_a_tie_does_not_dethrone_the_earliest_holder():
    """`>`,不是 `>=`。这一条不是代码风格:平局换持有者会让 frontier 在噪声上抖动,
    而抖动的前沿会反过来让"下一轮建在谁身上"变成一个随机决定。"""
    f = MH.frontier_of([_training(0, {"a": 1.0}), _training(1, {"a": 1.0})])
    assert f["tasks"]["a"]["best_agent"] == "r0"
    assert f["_best"]["agent"] == "r0"


def test_every_candidate_gets_a_row_with_its_hypothesis_and_measured_delta():
    """`update_evolution_summary`:每个候选一行,声明的是假说,量出来的是 delta。

    行里的 `hypothesis` 来自候选自己交的东西,`delta`/`outcome` 来自**实测分** ——
    这两件事分开写是原实现的做法,也是这个平台 method_reported 的用法。
    """
    history = [
        _training(0, {"a": 0.0}, hypothesis="start"),
        _training(1, {"a": 1.0}, hypothesis="read the task file first",
                  files=["agent.py"]),
    ]
    rows = MH.frontier_of(history)["rows"]
    assert [r["agent"] for r in rows] == ["r0", "r1"]
    assert rows[0]["hypothesis"] == "start"
    assert rows[1]["hypothesis"] == "read the task file first"
    assert rows[1]["changes"] == ["agent.py"]
    # 新最佳与**更新后的** frontier 比,所以是 0.000,不是相对于它前任的 +1.0
    assert rows[1]["avg_pass_rate"] == 1.0 and rows[1]["delta"] == 0.0
    assert rows[1]["outcome"] == "100.0% (+0.0%)"
    assert rows[1]["rollout_metrics"]["harness_tokens"] == 100
    assert rows[1]["harness_sha"] == "sha1"


def test_the_exam_side_never_enters_the_frontier():
    """**这条是重点。** 一个 eval 点带着 `per_task`,但那不是方法可以读的一侧。

    Meta-Harness 原实现搜索集与报告集是同一批题;我们分开,所以这条规则必须只从
    `protocol.studied_per_task` 取值 —— 直接读 `per_task` 就是把考试分喂进选择规则。
    """
    exam = {"round": 0, "score": 0.9, "score_kind": "exam", "per_task": {"a": 1.0},
            "split": {"train": ["a"], "eval": ["a"]}, "identity": {"harness_sha": "x"}}
    f = MH.frontier_of([exam])
    assert f["tasks"] == {} and f["_best"] == {}
    assert f["rows"][0]["avg_pass_rate"] is None
    assert f["rows"][0]["outcome"] == "not measured"


# ---------------------------------------------------------------- gate ---

def test_the_gate_declines_a_round_with_no_failure_evidence():
    reason = MH.validity_gate([_training(0, {"a": 1.0})], {"a": 1.0})
    assert reason and "already passes" in reason


def test_the_gate_lets_a_round_with_a_failing_task_through():
    assert MH.validity_gate([_training(0, {"a": 0.0})], {"a": 0.0}) is None


def test_the_gate_does_not_decline_before_anything_was_measured():
    """第一轮没有历史、没有失败证据,但那不是"没什么可做" —— 那是什么都还没测。"""
    assert MH.validity_gate([], {}) is None


def test_the_gate_declines_when_the_studied_side_has_no_per_task_evidence():
    reason = MH.validity_gate([_training(0, {})], {})
    assert reason and "no per-task evidence" in reason


# ------------------------------------------------------------ edit base ---

def _channel(tmp_path: Path, *, staged: dict[str, str] | None = None) -> Path:
    """一个带 channel 的 incumbent,`staged` 是 round -> 已 staged 的 harness 目录。"""
    root = tmp_path / "ws"
    hg = root / "_harnessgrad"
    (hg / "states").mkdir(parents=True)
    index = {"kept": [], "window": 12, "available": 0}
    for round_no, harness_dir in (staged or {}).items():
        index["kept"].append(f"round-{round_no}")
        index[f"round-{round_no}"] = {"sha": f"sha{round_no}", "files": 1,
                                      "harness_dir": harness_dir}
    (hg / "states" / "index.json").write_text(json.dumps(index))
    return root


def test_the_best_staged_state_is_the_edit_base(tmp_path):
    """frontier 的 `_best` 是一个 agent;在 mode A 里 agent 就是一轮,而只有被 staged
    的那些轮能拿来当编辑基准。"""
    staged = tmp_path / "state0"
    staged.mkdir()
    ws = _channel(tmp_path, staged={"0": str(staged)})
    # r0 是总体最佳,而 incumbent 是 r2 —— 只有这种形状才走"建在历史最佳上"的分支
    history = [_training(0, {"a": 1.0}), _training(1, {"a": 0.0}),
               _training(2, {"a": 0.0})]
    base, why = MH.base_round(MH.frontier_of(history), history, ws)
    assert base == staged
    assert "r0" in why and "staged" in why


def test_an_unstaged_best_falls_back_to_the_incumbent_and_says_so(tmp_path):
    """取不到的 state 不能猜路径,也不能静默回退 —— 回退本身要进记录。"""
    ws = _channel(tmp_path, staged={})
    # 最佳是 r0(且没被 staged),incumbent 是更差的 r1
    history = [_training(0, {"a": 1.0}), _training(1, {"a": 0.0})]
    base, why = MH.base_round(MH.frontier_of(history), history, ws)
    assert base == ws
    assert "not staged" in why and "r0" in why


def test_an_incumbent_that_is_already_the_best_is_not_copied(tmp_path):
    ws = _channel(tmp_path, staged={"1": str(tmp_path)})
    history = [_training(0, {"a": 0.0}), _training(1, {"a": 1.0})]
    base, why = MH.base_round(MH.frontier_of(history), history, ws)
    assert base == ws and "already holds" in why


# ----------------------------------------------- main() 带一个假模型 ---

def _harness(tmp_path: Path, history: list[dict]) -> Path:
    root = tmp_path / "base"
    (root / "_harnessgrad" / "history").mkdir(parents=True)
    (root / "_harnessgrad" / "traces").mkdir(parents=True)
    (root / "harness.json").write_text(json.dumps(
        {"name": "probe", "version": "1.0", "entrypoint": "agent.py"}))
    (root / "agent.py").write_text("print('hello')\n")
    for point in history:
        (root / "_harnessgrad" / "history" / f"round-{point['round']}.json").write_text(
            json.dumps(point))
    (root / "_harnessgrad" / "round.json").write_text(json.dumps(history[-1]))
    return root


def _invoke(mod, request: dict, monkeypatch, reply: dict) -> dict:
    import editor

    captured: dict = {}

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        captured["prompt"] = prompt
        return reply

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


def _request(base: Path, work: Path, traj: Path, history: list[dict]) -> dict:
    return {"platform_api_version": "0.1.0", "mode": "A", "base_harness": str(base),
            "workspace": str(work), "round_index": 1, "incumbent_score": 0.0,
            "task_ids": ["t01"], "train_task_ids": ["t01"],
            "trajectory_out": str(traj), "history": history}


def test_main_puts_the_frontier_and_every_row_in_the_prompt(tmp_path, monkeypatch):
    """**这是这个移植的主张本身**:proposer 看到的是 frontier 表 + 每一个历史候选的
   假说与实测结果,不是只有上一轮的 trace。"""
    # t02 仍然失败,否则门会先拒绝这一轮,prompt 根本不会生成
    history = [_training(0, {"t01": 0.0, "t02": 0.0}, hypothesis="first idea"),
               _training(1, {"t01": 1.0, "t02": 0.0}, hypothesis="second idea")]
    base = _harness(tmp_path, history)
    got = _invoke(MH, _request(base, tmp_path / "ws", tmp_path / "traj.json", history),
                  monkeypatch, {"no_change": "nothing left to fix"})
    prompt = got["prompt"]
    assert "Meta-Harness frontier" in prompt
    assert "| t01 |" in prompt, "frontier 表没有进 prompt"
    assert "first idea" in prompt and "second idea" in prompt, "历史候选的假说没有进 prompt"
    reply = got["stdout"]
    assert reply["changed"] is False and reply["hypothesis"] == "nothing left to fix"
    frontier = json.loads((tmp_path / "traj.json").read_text())["steps"][0][
        "method_reported"] if (tmp_path / "traj.json").is_file() else None
    assert frontier is None or "meta_harness_frontier" in frontier


def test_main_hands_the_frontier_back_on_the_wire(tmp_path, monkeypatch):
    """frontier 必须落在方法交回的记录里,而不是只活在 prompt 里。"""
    import editor

    history = [_training(0, {"t01": 0.0})]
    base = _harness(tmp_path, history)
    captured: dict = {}
    real_report = editor.report

    def spy(req, **kw):
        captured.update(kw)
        return real_report(req, **kw)

    monkeypatch.setattr(MH.editor, "report", spy, raising=False)
    _invoke(MH, _request(base, tmp_path / "ws", tmp_path / "traj.json", history),
            monkeypatch, {"no_change": "stop"})
    reported = captured["method_reported"]
    assert reported["meta_harness_frontier"]["tasks"]["t01"] == {
        "best_agent": "r0", "pass_rate": 0.0}
    assert "meta_harness_base" in reported


def test_main_reports_a_real_edit_as_changed(tmp_path, monkeypatch):
    """一次真的编辑要走到 `changed: true` 并把文件报回来。"""
    history = [_training(0, {"t01": 0.0})]
    base = _harness(tmp_path, history)
    got = _invoke(MH, _request(base, tmp_path / "ws", tmp_path / "traj.json", history),
                  monkeypatch, {"files": [{"path": "agent.py", "find": "print('hello')",
                                           "replace": "print('hello world')"}],
                                "hypothesis": "make it print more"})
    assert got["stdout"]["changed"] is True
    assert got["stdout"]["files"] == ["agent.py"]
    assert got["stdout"]["hypothesis"] == "make it print more"


def test_a_generation_does_not_end_the_search(tmp_path, monkeypatch):
    """**一代 ≠ 整个搜索。**

    The platform's `--rounds` counts measured states, so a three-candidate generation costs
    three of them; if the batch submission also ended the run (`stop=True`), the method got
    exactly one generation and never saw the frontier its own measurements updated. Measured
    on 2026-10-17: that was the entire study for `meta_harness` -- three candidates, one
    call, no feedback. `stop=False` is what lets the next call start the next generation.
    """
    history = [_training(0, {"t01": 0.0})]
    base = _harness(tmp_path, history)
    got = _invoke(MH, _request(base, tmp_path / "ws", tmp_path / "traj.json", history),
                  monkeypatch, {"files": [{"path": "agent.py", "find": "print('hello')",
                                           "replace": "print('hello world')"}],
                                "hypothesis": "one more generation"})
    assert got["stdout"]["changed"] is True
    assert got["stdout"].get("stop") is False, (
        "a batch submission must not end the study; the next call is the next generation")


def test_the_generation_index_is_on_the_record(tmp_path, monkeypatch):
    """代次与轮次是两种单位,记录里两个都要有。"""
    import editor

    history = [_training(0, {"t01": 0.0})]
    base = _harness(tmp_path, history)
    captured: dict = {}
    real_report = editor.report

    def spy(req, **kw):
        captured.update(kw)
        return real_report(req, **kw)

    monkeypatch.setattr(MH.editor, "report", spy, raising=False)
    _invoke(MH, _request(base, tmp_path / "ws", tmp_path / "traj.json", history),
            monkeypatch, {"no_change": "nothing left"})
    assert captured["method_reported"].get("meta_harness_generation") == 1


def test_a_method_that_declines_keeps_its_budget(tmp_path, monkeypatch):
    """拒绝 ≠ 结束。`stop=False`,否则一次"没什么可改"会终结整条轨迹
    (INTERFACE.md 的 `stop` 一节)。"""
    history = [_training(0, {"t01": 1.0})]
    base = _harness(tmp_path, history)
    got = _invoke(MH, _request(base, tmp_path / "ws", tmp_path / "traj.json", history),
                  monkeypatch, {"no_change": "all green"})
    assert got["stdout"].get("stop") is False


# Upstream reference_examples/terminal_bench_2/.claude/skills/
# meta-harness-terminal-bench-2/SKILL.md:65,70-98: design one mechanism,
# then hand that candidate's name and specific changes to the implementer.
DESIGNS = [
    {"name": "bounded_retry", "hypothesis": "Retrying transient errors avoids early exits",
     "changes": "Retry transient tool errors once"},
    {"name": "trim_observations", "hypothesis": "Shorter observations preserve context",
     "changes": "Trim long tool observations before storing them"},
    {"name": "verify_completion", "hypothesis": "Checking outputs catches unfinished work",
     "changes": "Check expected output artifacts before finishing"},
]


def _batch(tmp_path, monkeypatch, design_reply, *, count=2, remaining=None,
           no_change=False, design_error=None):
    """Real edit/report paths; only the design and implementation model calls are faked."""
    history = [_training(0, {"probe": 0.0})]
    base = _harness(tmp_path, history)
    req = _request(base, tmp_path / "ws", tmp_path / "traj.json", history)
    if count is None:
        monkeypatch.delenv("HG_META_HARNESS_CANDIDATES", raising=False)
    else:
        monkeypatch.setenv("HG_META_HARNESS_CANDIDATES", str(count))
    if remaining is not None:
        req["epochs_remaining"] = remaining
    calls, design_calls = [], []

    def design(prompt, **kwargs):
        design_calls.append((prompt, kwargs))
        if design_error is not None:
            raise design_error
        return design_reply

    def implement(edit_base, request, system, prompt_for, *, parent):
        calls.append({"prompt": prompt_for(""), "repair_prompt": prompt_for("repair needed"),
                      "base": edit_base, "parent": parent})
        reply = ({"no_change": "nothing useful"} if no_change else {
            "files": [{"path": "agent.py", "find": "print('hello')",
                       "replace": f"print('variant {len(calls)}')"}],
            "hypothesis": f"implementation {len(calls)}"})
        # Match editor.propose_and_apply's actual default destination. Sharing this
        # path across implementations would overwrite earlier submitted trees.
        return reply, MH.editor.candidate_path(request), ""

    monkeypatch.setattr(MH.editor, "read_request", lambda: req)
    monkeypatch.setattr(MH.editor, "ask", design)
    monkeypatch.setattr(MH.editor, "propose_and_apply", implement)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert MH.main() == 0
    trajectory = json.loads(Path(req["trajectory_out"]).read_text())
    return calls, design_calls, trajectory["steps"], json.loads(output.getvalue()), base


def test_batch_implements_different_designed_mechanisms_from_the_same_base(tmp_path, monkeypatch):
    calls, designs, steps, _, base = _batch(
        tmp_path, monkeypatch, {"candidates": DESIGNS[:2]})
    assert len(calls) == 2
    assert len({call["prompt"] for call in calls}) == 2, "children received identical prompts"
    assert len(designs) == 1, "design the whole batch in one editor.ask"
    assert "2" in designs[0][0] and "mechanism" in designs[0][0]
    assert "failed" in designs[0][0] and "successful" in designs[0][0]
    assert designs[0][1]["endpoint_only"] is True
    assert designs[0][1]["base"] == base and designs[0][1]["parent"] == base
    for call, candidate, step in zip(calls, DESIGNS, steps):
        assert call["base"] == base and call["parent"] == base
        for field in ("name", "hypothesis", "changes"):
            assert candidate[field] in call["prompt"]
            assert candidate[field] in call["repair_prompt"]
        assert step["method_reported"]["meta_harness_candidate"] == candidate
        assert step["method_reported"]["meta_harness_design"]["mode"] == "designed"
    paths = [Path(step["harness_dir"]) for step in steps]
    assert len(set(paths)) == 2
    assert [path.joinpath("agent.py").read_text() for path in paths] == [
        "print('variant 1')\n", "print('variant 2')\n"]
    assert base.joinpath("agent.py").read_text() == "print('hello')\n"


@pytest.mark.parametrize("count,remaining", [(1, None), (3, 1)])
def test_single_candidate_skips_design(tmp_path, monkeypatch, count, remaining):
    calls, designs, steps, _, _ = _batch(
        tmp_path, monkeypatch, None, count=count, remaining=remaining)
    assert len(calls) == 1 and designs == []
    assert steps[0]["method_reported"]["meta_harness_design"]["mode"] == "direct_single"


@pytest.mark.parametrize("design_reply", [
    None, [], {}, {"candidates": "invalid"}, {"candidates": [None, "invalid", {}]},
    {"candidates": DESIGNS[:1]},
    {"candidates": [DESIGNS[0], {**DESIGNS[1], "changes": "  RETRY transient  tool errors once  "}]},
    {"candidates": [DESIGNS[0], {**DESIGNS[1], "hypothesis": []}]},
])
def test_unusable_or_repeated_designs_fall_back_to_one_and_report_it(
        tmp_path, monkeypatch, design_reply):
    calls, designs, steps, _, _ = _batch(tmp_path, monkeypatch, design_reply)
    assert len(calls) == len(designs) == len(steps) == 1
    record = steps[0]["method_reported"]["meta_harness_design"]
    assert record["requested"] == 2 and record["mode"] == "single_fallback"
    assert record["fallback_reason"]
    if isinstance(design_reply, dict) and isinstance(design_reply.get("candidates"), list):
        if DESIGNS[0] in design_reply["candidates"]:
            assert DESIGNS[0]["changes"] in calls[0]["prompt"]


def test_batch_deduplicates_changes_before_implementing(tmp_path, monkeypatch):
    reply = {"candidates": [DESIGNS[0], {**DESIGNS[0], "name": "same_mechanism"}, DESIGNS[1]]}
    calls, _, steps, _, _ = _batch(tmp_path, monkeypatch, reply, count=3)
    assert len(calls) == len(steps) == 2
    assert [step["method_reported"]["meta_harness_candidate"] for step in steps] == DESIGNS[:2]
    assert all(step["method_reported"]["meta_harness_children"] == 2 for step in steps)


@pytest.mark.parametrize("remaining,expected", [(None, 3), (2, 2)])
def test_default_batch_size_and_remaining_budget_bound_designs(tmp_path, monkeypatch, remaining, expected):
    calls, designs, steps, _, _ = _batch(
        tmp_path, monkeypatch, {"candidates": DESIGNS}, count=None, remaining=remaining)
    assert len(calls) == len(steps) == expected and len(designs) == 1
    assert str(expected) in designs[0][0]


def test_unparseable_design_is_a_recorded_single_fallback(tmp_path, monkeypatch):
    calls, designs, steps, _, _ = _batch(
        tmp_path, monkeypatch, None, design_error=MH.editor.UnparseableReply("bad JSON"))
    assert len(calls) == len(designs) == len(steps) == 1
    record = steps[0]["method_reported"]["meta_harness_design"]
    assert record["mode"] == "single_fallback" and "bad JSON" in record["fallback_reason"]


def test_design_fallback_survives_a_no_change_implementation(tmp_path, monkeypatch):
    calls, _, steps, output, _ = _batch(tmp_path, monkeypatch, {}, no_change=True)
    assert len(calls) == 1 and output["changed"] is False
    record = steps[0]["method_reported"]["meta_harness_design"]
    assert record["mode"] == "single_fallback" and record["fallback_reason"]


def test_a_batch_of_candidates_is_one_generation_not_three(tmp_path, monkeypatch):
    """**修一个既存缺陷**：`generation_of` 曾按 `round` 去重，于是一次"设计 3 个候选"的调用
    把代数推进 3 格。

    实测：`rsi10-20261018T062351Z-meta_harness-train` 的 r1–r3 是一次设计 + 三次实现
    （`steps_in_submission = 3`、`step_index = 0/1/2`），却被记成第 2、3、4 代 —— 记录里
    四次设计，实际只有两次。红检：把 `step_index` 判断去掉，这条立刻失败。
    """
    import editor

    history = [_training(0, {"t01": 0.0})]
    # 一代 3 个候选：同一批提交，三行只有 step_index 不同。
    for index in range(3):
        row = _training(index + 1, {"t01": float(index) / 3.0})
        row["steps_in_submission"] = 3
        row["step_index"] = index
        history.append(row)
    base = _harness(tmp_path, history)
    captured: dict = {}
    real_report = editor.report

    def spy(req, **kw):
        captured.update(kw)
        return real_report(req, **kw)

    monkeypatch.setattr(MH.editor, "report", spy, raising=False)
    _invoke(MH, _request(base, tmp_path / "ws", tmp_path / "traj.json", history),
            monkeypatch, {"no_change": "nothing left"})
    assert captured["method_reported"].get("meta_harness_generation") == 2, (
        "一代三个候选只算一代，所以这一次调用是第 2 代")
