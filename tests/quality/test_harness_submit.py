"""交卷闸门:harness 不能把"模型说完成"当成"完成"。

实测那一次(`terminal_bench` round 0,2026-10-02)五道题的 agent 阶段耗时是
101s / 31s / 183s / **4.5s** / 105s,而步数上限是 32 —— 4.5 秒是一次模型调用,
不是"用完了步数"。证据在判分里:`File /app/run.py does not exist`。
所以 0 分的机制是**提前交卷**,而 harness 从不检查交付物。

这个文件测两件事,它们各自都能单独让一道题得 0:

  * `answer` 出现时,harness 必须先看**这条回复有没有命令** —— 一条同时带
    `command` 和 `answer` 的回复,以前会被当成"空答案"直接结束,那条命令永远不执行;
  * 交卷前检查题面点名的文件在不在;不在就退回给模型,**并记进 trace**。

trace 里的记录不是为了好看:平台拿不到容器内部的判断,只有 trace。没有
`submit_rejected`,一次"提前交卷"在记录里就是"正常的 4.5 秒"。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = ROOT / "base_harness" / "loop" / "agent.py"
    spec = importlib.util.spec_from_file_location("hg_loop_agent", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def agent():
    return _load()


# --------------------------------------------------- 交付物推断(纯函数)

CHESS = {"task_id": "chess-best-move",
         "goal": "Write the best move for white to play to /app/move.txt in the form "
                 "[src][dst], for example e2e4 or h1h8."}
BN = {"task_id": "bn-fit-modify",
      "goal": "Save the edges to /app/learned_dag.csv\nSample 10k points and save to "
              "/app/final_bn_sample.csv"}
PLAIN = {"task_id": "plain", "goal": "What is 2+2? Answer with just the number."}


def test_a_task_that_names_no_file_has_no_gate(agent, tmp_path):
    """题面没点文件时闸门不生效 —— harness 不该假装知道答案对不对。

    那是平台的判断(§2.2)。一个对纯问答也"检查交付物"的 harness 会把每道题都退回,
    而那是一次坏掉的测量,不是一次失败的测量。
    """
    assert agent._missing_deliverables(PLAIN, tmp_path) == []


def test_missing_files_are_reported_and_present_ones_are_not(agent, tmp_path):
    assert agent._missing_deliverables(CHESS, tmp_path) == ["/app/move.txt"]
    (tmp_path / "move.txt").write_text("e2e4")
    assert agent._missing_deliverables(CHESS, tmp_path) == []


def test_every_named_file_is_checked_not_just_the_first(agent, tmp_path):
    (tmp_path / "learned_dag.csv").write_text("to,from\n")
    missing = agent._missing_deliverables(BN, tmp_path)
    assert missing == ["/app/final_bn_sample.csv"], missing


# ------------------------------------------------- 两种交卷路径(端到端)

def _scripted(agent, replies):
    """把 `_complete` 换成脚本化的回复序列,并记录模型看到的消息。"""
    seen = []

    def fake_complete(messages):
        seen.append(messages[-1].get("content", ""))
        return replies[min(len(seen) - 1, len(replies) - 1)]

    agent._complete = fake_complete
    return seen


def test_a_command_carrying_reply_is_executed_not_treated_as_an_answer(agent, tmp_path):
    """**回归**:一条回复里同时有 command 和 answer 时,命令必须执行。

    实测的原文:`{"tool": "bash", "command": "cat > /app/run.py << 'EOF'…", "answer": ""}`。
    旧逻辑先看 `"answer" in action` 就 break,于是那条 `cat >` 从未执行,判分是
    `File /app/run.py does not exist` —— 而模型其实已经把实现写出来了。

    这里模拟成"先给命令再给答案"的两步,断言文件真的被创建、答案真的被接受。
    """
    _scripted(agent, [
        json.dumps({"tool": "bash", "command": "printf 'print(1)\\n' > run.py",
                    "answer": ""}),
        json.dumps({"answer": "wrote it"}),
    ])
    out = agent.run({"task_id": "w", "goal": "Put it in /app/run.py"}, tmp_path)
    assert (tmp_path / "run.py").is_file(), "命令没有被执行 —— 又被当成空答案了"
    assert out == "wrote it"


def test_answering_without_the_deliverable_is_rejected_and_retried(agent, tmp_path):
    """交卷但产物不存在 → 退回,让模型继续;trace 里必须留下这次拒绝。

    这条是 4.5 秒那道题的形状:模型答"做完了",而文件不在。
    """
    seen = _scripted(agent, [
        json.dumps({"answer": "done"}),                                    # 被拒
        json.dumps({"tool": "bash", "command": "printf 'e2e4\\n' > move.txt"}),
        json.dumps({"answer": "e2e4"}),                                    # 通过
    ])
    out = agent.run(CHESS, tmp_path)
    assert out == "e2e4"
    assert (tmp_path / "move.txt").read_text().strip() == "e2e4"
    assert len(seen) >= 3, "被拒之后没有继续对话"

    trace = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text().splitlines()
             if l.strip()]
    rejected = [r for r in trace if r.get("submit_rejected")]
    assert rejected, "返回给模型的拒绝没有记进 trace —— 平台将无从知道这次是提前交卷"
    assert rejected[0]["submit_rejected"] == ["/app/move.txt"]
    assert any(r.get("submitted") for r in trace), "成功交卷也没有记录"


def test_the_gate_gives_up_after_its_retry_budget(agent, tmp_path, monkeypatch):
    """闸门不能把一道题无限拖住:退回次数用完就接受(并如实记录)。

    一个永远拿不到交付物的 harness 应该"答错",而不是"跑不完" —— 后者会被记成
    平台侧的问题(§2.5.7 的 invalid),那是把 harness 的缺陷算到数据集头上。
    """
    monkeypatch.setattr(agent, "SUBMIT_RETRIES", 2)
    seen = _scripted(agent, [json.dumps({"answer": "still nothing"})])
    out = agent.run(CHESS, tmp_path)
    assert out == "still nothing"
    # 数脚本化的调用次数,不数 `USAGE["calls"]` —— 那是 `_complete` 里加的,而这里
    # 已经把 `_complete` 换掉了。用自己的假计数器,断言才不会测到测试脚手架。
    assert len(seen) == 3, f"应该是 1 次尝试 + 2 次退回 = 3 次模型调用,实际 {len(seen)}"
    # 退回时喂回去的那句话必须点出缺哪个文件,否则模型不知道要补什么。
    assert "/app/move.txt" in seen[1], seen[1]


# ------------------------------------------- 方法侧:harness 内部事实
#
# 第二步(把 harness 内部事实给方法看)是这一步的另一半:交卷闸门让 harness 会检查,
# 而这些事实让**方法**能看见"harness 是怎么结束的"。没有它们,RRSI 会把 0 分归因成
# "环境问题(缺 python 包)" —— 在它能看到的东西上那是合理的,而真机制是"一次调用就交卷"。

def test_loop_facts_distinguish_how_the_harness_ended(tmp_path):
    """四种结束方式必须能分开,因为它们的修法完全不同。"""
    from eval.observe import tracing as trace_mod

    def facts(records):
        p = tmp_path / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return trace_mod.loop_facts(p.read_text())

    # 1) 一次调用就交卷:命令 0、答案 1 —— 最可疑的那一类
    f = facts([{"step": 0, "reply": '{"answer": "done"}'}, {"usage": {"calls": 1,
              "input_tokens": 1, "output_tokens": 1}}])
    assert (f["model_calls"], f["commands"], f["answers"]) == (1, 0, 1)
    assert f["ended"] == "answered", f

    # 2) 步数用满:每次回复都是工具调用,一次都没交卷
    f = facts([{"step": i, "reply": '{"tool":"bash","command":"ls"}'} for i in range(32)]
              + [{"step": i, "command": "ls", "exit": 0, "output": "x"} for i in range(32)]
              + [{"usage": {"calls": 32, "input_tokens": 10, "output_tokens": 2}}])
    assert f["ended"] == "budget_exhausted", f
    assert f["model_calls"] == 32 and f["commands"] == 32

    # 3) 崩了:**没有**收尾的 usage 记录。实测 `bn-fit-modify` 第 3 轮:10 次调用、
    #    0 个答案,原因是 `APIConnectionError` —— 报成"步数用满"会让方法去查预算。
    f = facts([{"step": 0, "reply": '{"tool":"bash","command":"ls"}'},
               {"step": 0, "command": "ls", "exit": 0, "output": ""}])
    assert f["ended"] == "crashed", f

    # 4) 解析失败就死:一次命令都没执行过
    f = facts([{"step": 0, "reply": "not json"}, {"step": 0, "parse_error": "Extra data"},
               {"usage": {"calls": 1, "input_tokens": 1, "output_tokens": 1}}])
    assert f["ended"] == "parse_error", f

    # 5) 带闸门退回的记录:missing_at_submit 要说清缺哪个文件
    f = facts([{"step": 0, "submit_rejected": ["/app/move.txt"]},
               {"step": 1, "reply": '{"answer": "e2e4"}'},
               {"usage": {"calls": 2, "input_tokens": 1, "output_tokens": 1}}])
    assert f["submit_rejected"] == 1
    assert f["missing_at_submit"] == ["/app/move.txt"]


def test_a_command_carrying_reply_counts_as_a_command_not_an_answer(tmp_path):
    """一条同时带 command 和 answer 的回复,事实里必须算"命令",不算"交卷"。

    这正是 `cancel-async-tasks` 的形状:回复里有实现、也有一个空 answer。旧记录把它
    读成"答案 0 条命令"(于是看不见任何问题);现在要看得出"这条回复想执行命令"。
    """
    from eval.observe import tracing as trace_mod
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"step": 0, "reply":
        '{"tool": "bash", "command": "cat > /app/run.py << \'EOF\'\\nx=1\\nEOF", "answer": ""}'})
        + "\n")
    f = trace_mod.loop_facts(p.read_text())
    assert f["model_calls"] == 1
    assert f["commands"] == 0, "命令在回复里但从未执行 —— 命令数应当仍是 0"


def test_multi_object_replies_are_counted_so_a_fixed_bug_is_not_diagnosed_twice(tmp_path):
    """"一个回复多个 JSON" 要能被看见 —— 并且要和 `解析错误` 一起读。

    实测(RRSI 的第 2/3/4 轮):它的假设连续三轮都是"解析器处理不了多行 JSON",而那个
    bug 已经修好了。原因很简单:它在 trace 里看得见那个**形状**,看不见**处理结果**。
    这两个数放在一起,答案就唯一了:

        multi_object_replies=32, parse_errors=0   -> 解析器吸收了,别再去改它
        multi_object_replies=32, parse_errors=32  -> 还是没吸收
    """
    from eval.observe import tracing as trace_mod
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"step": 0, "reply": '{"tool":"bash","command":"a"}}\n{"answer":""}'},
        {"step": 0, "command": "a", "exit": 0, "output": ""},
        {"step": 1, "reply": '{"tool":"bash","command":"b"}}'},
        {"step": 1, "command": "b", "exit": 0, "output": ""},
        {"usage": {"calls": 2, "input_tokens": 5, "output_tokens": 5}},
    ]) + "\n")
    f = trace_mod.loop_facts(p.read_text())
    assert f["multi_object_replies"] == 1, f
    assert f["parse_errors"] == 0, f
    assert f["commands"] == 2, f


# ------------------------------------- 接口事件必须进摘要和进 prompt ---

def test_the_summary_counts_empty_commands_separately(tmp_path):
    """**`commands: 13` 和"其中 10 条什么都没执行"是两件事。**

    实测(`local-floor-9`,`cancel-async-tasks`):16 次模型调用、13 条命令、0 个解析错误 ——
    一个方法读到的是"这只 harness 工作正常"。而其中 10 条执行的是空字符串:重复的 JSON 键
    把模型的真命令换成了 `""`。这道题得 0.0,原因不在模型。
    """
    import eval.observe.tracing as trace_mod
    trace = tmp_path / "t.jsonl"
    trace.write_text(
        '{"step": 0, "reply": "{\\"tool\\": \\"bash\\", \\"command\\": \\"ls\\", '
        '\\"command\\": \\"\\"}"}\n'
        '{"step": 0, "command": "", "exit": 0, "output": ""}\n'
        '{"step": 1, "reply": "{\\"tool\\": \\"bash\\", \\"command\\": \\"pwd\\"}"}\n'
        '{"step": 1, "command": "pwd", "exit": 0, "output": "/app\\n"}\n'
        '{"step": 2, "reply": "x", "duplicate_keys": ["command"]}\n'
        '{"step": 2, "command": "echo hi", "exit": 0, "output_clipped": 9000, '
        '"shown": 4000}\n'
        '{"step": 3, "history_dropped": 4}\n'
        '{"usage": {"calls": 3, "input_tokens": 10, "output_tokens": 5}}\n')
    facts = trace_mod.loop_facts(trace.read_text())
    assert facts["commands"] == 3, facts            # 三条记录带 command 键
    assert facts["empty_commands"] == 1, facts      # 其中一条什么都没跑
    assert facts["duplicate_keys"] == 1, facts
    assert facts["output_clipped_events"] == 1 and facts["output_clipped_chars"] == 9000, facts
    assert facts["history_dropped"] == 4, facts


def test_the_round_summary_names_the_tasks_the_interface_ate(tmp_path):
    """轮级摘要(方法读的第一份)必须点名"哪几道题被接口吃掉了"。"""
    from harnessgrad.channel import _loop_summary
    good = ('{"step": 0, "reply": "{}"}\n'
            '{"step": 0, "command": "pwd", "exit": 0, "output": "/app\\n"}\n')
    eaten = ('{"step": 0, "reply": "{}"}\n'
             '{"step": 0, "command": "", "exit": 0, "output": ""}\n'
             '{"step": 1, "duplicate_keys": ["command"]}\n')
    summary = _loop_summary({"t01": good, "t02": eaten})
    assert summary["empty_commands_total"] == 1, summary
    assert summary["tasks_with_empty_commands"] == ["t02"], summary
    assert summary["tasks_with_duplicate_keys"] == ["t02"], summary
    # 干净的那道题不该出现在任何"被吃掉"的名单里
    for key in ("tasks_with_empty_commands", "tasks_with_duplicate_keys",
                "tasks_with_context_trims", "tasks_with_clipped_output"):
        assert "t01" not in summary[key], (key, summary[key])


def test_the_renderer_keeps_the_diagnostic_tail_and_says_what_it_hid(tmp_path):
    """渲染给模型看的那份:回复要留尾巴、空命令要点名、截断要说还差几条。

    实测那条回复 875 字符,真命令在前 799 个字符里,而**把命令换成空串的重复键在最后 40 个
    字符**里 —— 旧渲染器每行截断 220 字符,恰好把这唯一的解释扔掉。
    """
    sys.path.insert(0, str(ROOT / "methods"))
    import editor
    long_reply = ('{"tool": "bash", "command": "cat > /app/run.py << \'EOF\'\\n'
                  + "x" * 400 + '\\nEOF\\n", "command": ""}')
    trace = (f'{{"step": 0, "reply": {json.dumps(long_reply)}}}\n'
             '{"step": 0, "command": "", "exit": 0, "output": ""}\n'
             '{"step": 0, "output_clipped": 9000, "shown": 4000}\n'
             '{"harness_identity": {"agent_model": "probe"}}\n'
             '{"wall_clock_s": {"model": 1.0, "commands": 0.0, "total": 1.0}}\n'
             '{"usage": {"calls": 1}}\n')
    rendered = editor.render_trace(trace, max_events=14, width=220)
    # 尾巴在:重复键那句看得见
    assert '"command": ""}' in rendered, rendered
    assert "characters omitted" in rendered, rendered
    # 空命令被点名
    assert "EMPTY COMMAND" in rendered, rendered
    # 接口事件有话说
    assert "output clipped" in rendered, rendered
    # 记账记录不占额度
    assert "harness_identity" not in rendered and "wall_clock_s" not in rendered, rendered
    # 被 max_events 砍掉的条数要写出来 —— 只有砍到了才会写,所以换个更小的上限再渲染一次
    capped = editor.render_trace(trace, max_events=2, width=220)
    assert "not shown" in capped, capped
    # 而且账目记录不算进上限(它们本来就不该占额度)
    assert "harness_identity" not in capped, capped


def test_repeated_commands_are_counted_apart_from_the_harness_nudge(tmp_path):
    """重发的命令要单独计数,并且和 harness 自己的重复提醒**分开**。

    实测(9B,`large-scale-text-editing`,2026-10-05):32 条命令里 **23 条是本轮已经发过的**,
    而 `repeat_nudge` 只触发 2 次 —— 这个差距就是已知的盲区(提醒只看紧挨着的一次重复)。
    轮级记录原本只有一个 0.000,方法看不出"预算被原地打转烧光了"。
    """
    from eval.observe import tracing as trace_mod

    records = [
        {"step": 0, "command": "ls"},
        {"step": 1, "command": "cat a"},
        {"step": 2, "command": "ls"},                       # 重发
        {"step": 3, "repeat_nudge": "ls"},                  # harness 自己发现了
        {"step": 4, "command": "ls"},                       # 再重发
        {"step": 5, "command": ""},                         # 空命令,单独计数
    ]
    f = trace_mod.loop_facts("\n".join(json.dumps(r) for r in records))
    assert f["commands"] == 5, f
    assert f["repeated_commands"] == 2, f
    assert f["repeat_nudges"] == 1, f
    assert f["empty_commands"] == 1, f


def test_the_rules_prompt_carries_the_per_task_facts(tmp_path):
    """给改进器的 prompt 里要有那张按题一行的事实表。

    这张表是"数据告诉方法该修什么"的载体:它一行里同时给出预算用满了几道
    (`ended budget_exhausted`)、有多少步是重发(`repeats`)而 harness 自己只提醒了几次
    (`nudges`)。没有它,方法只能去读几十 KB 的 JSONL,而实测它读不出来。
    """
    import editor

    root = tmp_path / "h"
    hg = root / "_harnessgrad"
    hg.mkdir(parents=True)
    (hg / "round.json").write_text(json.dumps({
        "per_task": {"large-scale-text-editing": 0.0, "gcode-to-text": 1.0},
        "loop_summary": {"per_task": {
            "large-scale-text-editing": {
                "model_calls": 32, "commands": 32, "answers": 0,
                "ended": "budget_exhausted", "repeated_commands": 23,
                "repeat_nudges": 2, "empty_commands": 0,
                "output_clipped_events": 0, "parse_errors": 0},
            "gcode-to-text": {
                "model_calls": 2, "commands": 2, "answers": 1,
                "ended": "answered", "repeated_commands": 0, "repeat_nudges": 0,
                "empty_commands": 0, "output_clipped_events": 0, "parse_errors": 0},
        }}}))

    block = editor.loop_facts_block(root)
    assert "large-scale-text-editing" in block and "budget_exhausted" in block
    assert "repeats" in block and "nudges" in block
    assert "23" in block and "32" in block
    # **结果列必须在。** 实测(`v5-dgm`):表里只有过程列时,codex 读到"5/10 道题
    # budget_exhausted",就把收尾提醒从 4 步提前到 8 步,把两道**本来通过**的题打坏
    # (0.200 -> 0.000)。`ended` 不是目标:round 0 里 `budget_exhausted` 和 `crashed`
    # 那两道题**都是 1.0**,因为分数看的是 artifact。
    assert "1.00" in block and "0.00" in block, "没有结果列,方法只能去优化过程指标"
    assert "score is the platform's verdict on the artifact" in block, "缺那句警告"
    # 没有摘要时返回空串,调用方可以直接拼
    (hg / "round.json").write_text("{}")
    assert editor.loop_facts_block(root) == ""
