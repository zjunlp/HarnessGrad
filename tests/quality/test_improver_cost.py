"""测量代价的记录：CLI improver 的花费是"未知"，不是"0"。

背景（实测）：九个方法都走 `editor.ask → _ask_cli`。CLI 的 provider 在 agent 进程内部，
它只吐 transcript 不吐 usage —— 所以平台拿不到 token。此前 `spent_tokens()` 把它求和成
**0**，曲线点上写着 `method_generation_tokens: 0`，读起来是"改进器一分钱没花"，而真相是
"没人数过"。做效率分析时这两件事差得很远。

同时 `_ask_cli` 把 improver 自己返回的 `reported`（退出码、秒数、改了哪些文件、环境白名单
剥掉了什么）**整块丢掉**了，于是"每轮 improver 花了多久"在记录里根本查不到。
"""
from __future__ import annotations

import json
import pathlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import methods.editor as editor                                          # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    editor.reset_spent()
    editor._LAST_IMPROVER.clear()
    yield
    editor.reset_spent()
    editor._LAST_IMPROVER.clear()


class _Usage:
    def __init__(self, prompt, completion, model="m"):
        self.prompt_tokens, self.completion_tokens, self.model = prompt, completion, model


def test_a_cli_improvers_spend_is_unknown_not_zero():
    """CLI 路径必须记 `None`。0 是一个声明，而这里没人能做这个声明。"""
    editor._SPENT["source"] = "cli_unreported"
    assert editor.spent_tokens() is None
    assert editor.spend_source() == "cli_unreported"


def test_an_endpoint_reply_after_a_cli_call_restores_counting():
    """flag 不能越过它描述的那次调用活下去。"""
    editor._SPENT["source"] = "cli_unreported"
    editor._record_usage(_Usage(1500, 500), "m")
    assert editor.spend_source() == "endpoint"
    assert editor.spent_tokens() == 2000


def test_reset_clears_the_flag_too():
    editor._SPENT["source"] = "cli_unreported"
    editor.reset_spent()
    assert editor.spend_source() is None
    assert editor.spent_tokens() == 0


def test_the_improvers_own_account_rides_along_under_its_own_key():
    """方法的 key 不能和 improver 的 key 撞；没有 CLI improver 时整块不出现。"""
    editor._LAST_IMPROVER.update({"exit_code": 0, "seconds": 597.3, "files_changed": 1,
                                  "prompt_chars": 24000, "spend_reported": False})
    merged = editor._reported_with_improver({"sica_rule": {"x": 1}})
    assert merged["sica_rule"] == {"x": 1} and merged["improver_run"]["seconds"] == 597.3
    editor._LAST_IMPROVER.clear()
    assert "improver_run" not in editor._reported_with_improver({"sica_rule": {}})


def test_a_one_step_batch_puts_the_methods_record_on_the_envelope():
    """**这条是修一个既存缺陷**：`report_many` 的信封曾只写 `{"steps": n}`，
    于是 DGM / HyperAgents / Meta-Harness 这类批量提交的方法，它们自己的规则记录只落在
    step payload 里，曲线点上读不到。"""
    envelope = editor._batch_reported([{"method_reported": {"sica_rule": {"a": 1}}}], [{}])
    assert envelope["sica_rule"] == {"a": 1}
    assert envelope["steps"] == 1


def test_a_multi_step_batch_does_not_choose_between_its_steps():
    envelope = editor._batch_reported(
        [{"method_reported": {"a": 1}}, {"method_reported": {"a": 2}}], [{}, {}])
    assert envelope["per_step"] == [{"a": 1}, {"a": 2}]
    assert "a" not in envelope, "两个不同的记录不能被悄悄合并成一个"


def test_the_step_says_where_its_spend_numbers_came_from():
    step = editor._step_payload(harness_dir=ROOT / "methods" / "noop", label="x",
                                edit_kind="none", changed=False, tokens_now=None,
                                method_reported=None, incumbent_round=None)
    assert step["claimed_cost"]["generation_tokens"] is None
    assert "method_spend_source" in step["claimed_cost"]


def test_report_many_puts_the_methods_record_where_the_platform_reads_it(tmp_path, capsys):
    """端到端：`report_many` 的 stdout 信封必须带方法的记录。

    平台读的是方法进程的 stdout，所以信封就是曲线点 `method_reported` 的来源。
    红检：把信封改回 `{"steps": n}`，这条立刻失败 —— 那正是修复前的行为。
    """
    out = tmp_path / "traj.json"
    step = {"harness_dir": ROOT / "methods" / "noop", "changed": False, "label": "x",
            "edit_kind": "none", "method_reported": {"sica_rule": {"a": 1}},
            "hypothesis": "h"}
    editor.report_many({"trajectory_out": str(out)}, method="noop", steps=[step],
                       acceptance_rule="t")
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["method_reported"]["sica_rule"] == {"a": 1}


# ---------------------------------------------------------------- CLI 自报的 token ---

DASH = "dsh-usage "


def test_usage_lines_are_summed_across_a_round():
    """一轮里 CLI 会打很多次 usage（每次模型调用一次），所以一轮的花费是它们的和。"""
    transcript = "\n".join([
        DASH + '{"input_tokens": 1200, "output_tokens": 80, "cache_read_tokens": 300}',
        "some prose the agent printed",
        DASH + '{"input_tokens": 900, "output_tokens": 40, "cache_read_tokens": 120}',
    ])
    got = editor.parse_usage_lines(transcript)
    assert got == {"input": 2100, "output": 120, "cache_read": 420, "calls": 2}


def test_no_usage_line_is_unknown_not_zero():
    """老 CLI / 别的 improver 不打这行时，必须是 None，不能求和成 0。"""
    assert editor.parse_usage_lines("just prose\n") is None


def test_a_malformed_usage_line_is_ignored_rather_than_crashing_the_round():
    transcript = DASH + '{"input_tokens": oops}\n' + DASH + '{"input_tokens": 10, "output_tokens": 1}'
    got = editor.parse_usage_lines(transcript)
    assert got == {"input": 10, "output": 1, "cache_read": 0, "calls": 1}


def test_a_reported_cli_round_records_tokens_and_cache_read(monkeypatch, tmp_path):
    """端到端：`_ask_cli` 把 transcript 里的 usage 变成 `_SPENT` 和 `method_spend_source`。"""
    base = tmp_path / "base"; base.mkdir()
    parent = tmp_path / "parent"; parent.mkdir()
    transcript = (DASH + '{"input_tokens": 5000, "output_tokens": 200, '
                         '"cache_read_tokens": 4000}')

    def fake_propose(req, parent_path, **kwargs):
        ws = pathlib.Path(req["workspace"])
        return {"candidate": str(ws / "candidate"), "files": [], "changed": [],
                "exit_code": 0, "seconds": 12.5, "note": "no change",
                "transcript": transcript,
                "reported": {"exit_code": 0, "seconds": 12.5, "files_changed": [],
                             "env_withheld": []}}

    # `_ask_cli` imports the module by its bare name (it puts `methods/` on the path), and
    # `methods.cli_improver` is a *different* module object for the same file -- patching
    # that one would leave the real `propose` in place.
    sys.path.insert(0, str(ROOT / "methods"))
    import cli_improver
    monkeypatch.setattr(cli_improver, "propose", fake_propose)
    reply = editor._ask_cli("do something", base, parent, {"name": "dsh"})
    assert reply.get("no_change")
    assert editor.spend_source() == "cli_reported"
    assert editor.spent_tokens() == 5200          # input 已不含 cache read
    assert editor.cache_read_tokens() == 4000
    run = editor.improver_run()
    assert run["spend_reported"] is True and run["usage"]["calls"] == 1


def test_the_cost_record_carries_the_cache_read_count():
    """端到端最后一公里：`claimed_cost` 里的 cache_read 必须进到曲线的 `cost` 块。"""
    from harnessgrad.records import _cost_of
    claimed = {"generation_tokens": 11118, "method_input_tokens": 10159,
               "method_output_tokens": 959, "method_model_calls": 7,
               "method_model": "deepseek-flash", "method_spend_source": "cli_reported",
               "method_cache_read_tokens": 61056}
    cost = _cost_of(None, 0, 0.0, method_claimed=claimed)
    assert cost["method_generation_tokens"] == 11118
    assert cost["method_cache_read_tokens"] == 61056
    assert cost["method_spend_source"] == "cli_reported"


# ------------------------------------------------ 端点通道也要 cache 拆分 ---

class _Details:
    def __init__(self, cached):
        self.cached_tokens = cached


class _UsageOpenAI:
    """OpenAI 形状：prompt_tokens 含缓存，明细里给 cached_tokens。"""
    def __init__(self, prompt, completion, cached):
        self.prompt_tokens, self.completion_tokens = prompt, completion
        self.prompt_tokens_details = _Details(cached)


class _UsageDeepSeek:
    """DeepSeek 原生形状：prompt_cache_hit_tokens。"""
    def __init__(self, prompt, completion, hit):
        self.prompt_tokens, self.completion_tokens = prompt, completion
        self.prompt_cache_hit_tokens = hit


def test_an_endpoint_reply_reports_its_cache_split():
    """**用户指出的缺口**：端点调用只记了 prompt/completion，cache read 丢了。

    而且两个通道必须是**互斥**口径：CLI 的 `inputTokens` 已减去 cache，端点这边也要
    把 cached 从 prompt 里减出去，否则混合方法的两行加不起来。
    """
    editor._record_usage(_UsageOpenAI(prompt=1000, completion=50, cached=800), "m")
    assert editor.spend_source() == "endpoint"
    assert editor.spent_tokens() == 250          # (1000-800) + 50
    assert editor.cache_read_tokens() == 800


def test_the_deepseek_native_field_is_also_read():
    editor._record_usage(_UsageDeepSeek(prompt=500, completion=20, hit=300), "m")
    assert editor.spent_tokens() == 220
    assert editor.cache_read_tokens() == 300


def test_a_provider_without_a_cache_split_reports_zero_not_unknown():
    """端点报了 usage 但没给拆分：cache_read 只能是 0，不能是 None —— 前者是"没命中"，
    后者是"没人说"。"""
    editor._record_usage(_UsageOpenAI(prompt=100, completion=10, cached=0), "m")
    assert editor.cache_read_tokens() == 0
    assert editor.spent_tokens() == 110


def test_a_mixed_run_adds_both_channels_without_double_counting(monkeypatch, tmp_path):
    """端点（设计阶段）+ CLI（实现阶段）混在一个进程里时，两边都要进账且不重复。"""
    editor._record_usage(_UsageOpenAI(prompt=1000, completion=100, cached=600), "m")
    assert editor.spent_tokens() == 500
    assert editor.cache_read_tokens() == 600

    transcript = DASH + '{"input_tokens": 200, "output_tokens": 20, "cache_read_tokens": 900}'
    base = tmp_path / "base"; base.mkdir()
    parent = tmp_path / "parent"; parent.mkdir()

    def fake_propose(req, parent_path, **kwargs):
        return {"candidate": str(pathlib.Path(req["workspace"]) / "candidate"),
                "files": [], "changed": [], "exit_code": 0, "seconds": 1.0,
                "note": "no change", "transcript": transcript, "reported": {}}

    sys.path.insert(0, str(ROOT / "methods"))
    import cli_improver
    monkeypatch.setattr(cli_improver, "propose", fake_propose)
    editor._ask_cli("do something", base, parent, {"name": "dsh"})

    assert editor.spent_tokens() == 500 + 220       # 端点的 (400+100) + CLI 的 (200+20)
    assert editor.cache_read_tokens() == 600 + 900  # 两边都算进来


def test_a_batched_step_records_the_split_it_used_to_drop():
    """**修一个既存缺陷**：批量路径把 input/output/calls/cache 全写死成 `None`。

    实测（2026-10-18，九方法批次）：**每一个方法的每一个曲线点**都是
    `method_generation_input: null` / `method_output: null` / `method_calls: null` /
    `method_spend_source: null`，包括 provider 已经报了 usage 的轮次 —— 因为九个方法全都
    走 `report_many`。同一份记录的单候选路径（`report`）是读的，于是平台的两半对同一次调用
    给出不同答案。红检：把这三行改回 `None`，这条立刻失败。
    """
    editor._SPENT.update(input=1200, output=300, calls=2, model="deepseek-flash",
                         cache_read=800, source="endpoint")
    step = editor._step_payload(harness_dir=ROOT / "methods" / "noop", label="x",
                                edit_kind="none", changed=False, tokens_now=1500,
                                method_reported=None)
    cost = step["claimed_cost"]
    assert cost["method_input_tokens"] == 1200
    assert cost["method_output_tokens"] == 300
    assert cost["method_model_calls"] == 2
    assert cost["method_model"] == "deepseek-flash"
    assert cost["method_cache_read_tokens"] == 800
    assert cost["method_spend_source"] == "endpoint"


def test_a_step_says_why_its_split_is_null_when_nobody_counted():
    """`null` 必须可解释：CLI improver 的数没人拿到，和"免费"是两件事。"""
    editor._SPENT["source"] = "cli_unreported"
    step = editor._step_payload(harness_dir=ROOT / "methods" / "noop", label="x",
                                edit_kind="none", changed=False, tokens_now=None,
                                method_reported=None)
    cost = step["claimed_cost"]
    assert cost["method_input_tokens"] is None and cost["method_output_tokens"] is None
    assert cost["method_spend_source"] == "cli_unreported", (
        "null 的钱数没有理由码，读者无法区分'没人数'和'没花钱'")


def test_a_multi_step_batch_does_not_give_every_step_the_same_improver_run():
    """一个进程只有一个 `_LAST_IMPROVER`；把它写进 k 个 step 就是说它跑了 k 次。

    `improver_run_scope: "call"` 是这条的可见化：批次级别的账只记一次，并说明它是调用级的。
    """
    editor._LAST_IMPROVER.update({"exit_code": 0, "seconds": 12.0, "files_changed": 1,
                                  "prompt_chars": 2400, "spend_reported": False})
    step = editor._step_payload(harness_dir=ROOT / "methods" / "noop", label="x",
                                edit_kind="none", changed=False, tokens_now=None,
                                method_reported=None, improver=None)
    assert "improver_run" not in step["method_reported"]
    envelope = editor._batch_reported([{"method_reported": {}}, {"method_reported": {}}],
                                      [step, step])
    assert envelope["improver_run"]["seconds"] == 12.0
    assert envelope["improver_run_scope"] == "call"


def test_a_one_step_submission_can_attribute_the_improver_run_exactly():
    """k=1 没有歧义：这一次调用买的就是这一个候选。"""
    editor._LAST_IMPROVER.update({"exit_code": 0, "seconds": 3.0, "files_changed": 1,
                                  "prompt_chars": 100, "spend_reported": False})
    step = editor._step_payload(harness_dir=ROOT / "methods" / "noop", label="x",
                                edit_kind="none", changed=False, tokens_now=None,
                                method_reported=None, improver=editor.improver_run())
    assert step["method_reported"]["improver_run"]["seconds"] == 3.0


def test_report_many_puts_the_split_on_the_wire(tmp_path, capsys):
    """端到端：平台读的是 stdout，所以信封里的 step 必须带着拆分。"""
    editor._SPENT.update(input=900, output=100, calls=1, model="m",
                         cache_read=400, source="endpoint")
    step = {"harness_dir": ROOT / "methods" / "noop", "changed": False, "label": "x",
            "edit_kind": "none", "method_reported": {"a": 1}, "hypothesis": "h"}
    editor.report_many({"trajectory_out": str(tmp_path / "t.json")}, method="noop",
                       steps=[step, dict(step)], acceptance_rule="t")
    envelope = json.loads(capsys.readouterr().out)
    written = json.loads((tmp_path / "t.json").read_text())
    for payload in written["steps"]:
        assert payload["claimed_cost"]["method_input_tokens"] == 900
        assert payload["claimed_cost"]["method_spend_source"] == "endpoint"
    assert envelope["steps"] == 2
