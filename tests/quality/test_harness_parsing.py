"""harness 怎么读模型的一句话,决定了任务会不会在第 0 步就死掉。

`base_harness/loop` 的 `_parse` 曾经是"先 `json.loads`,不行就从第一个 `{` 切到**最后
一个** `}`"。这两种都栽在同一个回复形状上,而那个形状是实测模型真的会产出的 ——
要一个动作,它给了两三个 JSON 对象,一行一个:

    {"tool": "bash", "command": "cat /app/filter.py"}}
    {"answer": ""}

`json.loads` 报 `Extra data: line 1 column 50`;`rfind` 那条路切出 `{...}}{...}`,也不是
JSON。而 harness 把"解析不了"当成 harness 失败并 **break 循环**,于是任务在第 0 步结束、
答案为空、得 0 分。

实测 `loop-terminal_bench-41133` round 0:五道题里四道在**一次**模型调用后就死了
(`calls: 1`,`parse_error: Extra data`),后面每一轮都继承这个什么都做不了的 harness。
旧的 `loop-terminal_bench-26452c` 拿到的是单对象回复,每题 8 次调用 —— 同样的代码、
同样的提示词。所以这是模型输出的一种形状,解析器必须吸收它。

这个文件不检查 harness 的"性格",只检查**它不会因为一句多余的话把自己关掉**。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 三个 base harness。它们对解析必须给出同一个答案:如果 `loop_plain`(对照)和
#: `loop` 读同一句话读出不同的动作,那"两个候选的差别"里就有一份是平台自己造的。
HARNESSES = ("loop", "loop_plain", "loop_rrsi_parse")


def _load(name: str):
    path = ROOT / "base_harness" / name / "agent.py"
    spec = importlib.util.spec_from_file_location(f"hg_harness_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def parsers():
    return {name: _load(name)._parse for name in HARNESSES}


#: 真实回复的形状。第一条是这次事故的原文。
REPLIES = {
    "two-objects": ('{"tool": "bash", "command": "cat /app/filter.py"}}\n'
                    '{"answer": ""}',
                    {"tool": "bash", "command": "cat /app/filter.py"}),
    "three-objects": ('{"tool": "bash", "command": "a"}}\n'
                      '{"tool": "bash", "command": "b"}}\n'
                      '{"tool": "bash", "command": "c"}}',
                      {"tool": "bash", "command": "a"}),
    "fenced": ('```json\n{"tool": "bash", "command": "ls"}\n```',
               {"tool": "bash", "command": "ls"}),
    "prose-around": ('Sure! {"tool": "bash", "command": "ls"} hope that helps',
                     {"tool": "bash", "command": "ls"}),
    "plain-answer": ('{"answer": "e2e4"}', {"answer": "e2e4"}),
}


@pytest.mark.parametrize("name", sorted(REPLIES))
def test_the_first_json_object_is_the_action(name, parsers):
    reply, want = REPLIES[name]
    for harness, parse in parsers.items():
        got = parse(reply)
        assert got == want, f"{harness} 读了 {name!r} 得到 {got!r},期望 {want!r}"


def test_a_reply_with_no_json_still_fails_loudly(parsers):
    """容忍"多余的话",不等于容忍"什么都没说"。

    解析不了必须是失败:一个把散文当动作执行的 harness 会拿一条 shell 报错当观测,
    然后继续跑很久,而那是一个坏掉的测量而不是一次失败的测量。
    """
    for harness, parse in parsers.items():
        with pytest.raises(Exception):
            parse("I will look around.")


def test_the_three_base_harnesses_parse_identically(parsers):
    """平台发的三个 base harness 必须在这一层没有差别。"""
    for reply, _ in REPLIES.values():
        answers = {name: parse(reply) for name, parse in parsers.items()}
        assert len(set(map(repr, answers.values()))) == 1, answers


def test_the_action_survives_a_reply_that_answers_and_commands(parsers):
    """命令和 answer 同时出现时,取命令。

    这是实测那个形状里最坏的一种:模型写了 `{"tool": "bash", "command": "cat > /app/run.py
    << EOF ..."}}` 又在同一句里给了 `{"answer": ""}`。旧代码先看到 answer 就直接交卷,
    于是一个**从未被创建的** `run.py` 成了这一轮的产物,判分正确地给了 0。
    取第一个对象(命令)至少让它真的跑起来。
    """
    reply = ('{"tool": "bash", "command": "cat > /app/run.py << \'EOF\'\\n'
             'print(1)\\nEOF"}}\n{"answer": ""}')
    for harness, parse in parsers.items():
        action = parse(reply)
        assert "command" in action, f"{harness} 把命令丢掉了:{action!r}"
        assert action["command"].startswith("cat > /app/run.py")
