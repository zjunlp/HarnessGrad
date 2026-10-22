"""一次读不懂的回复,不该结束整道题。

**这是数出来的,不是想出来的。** 统计所有跑道里"方法自己写了理由"的 35 个轮次,
**16 个在修解析或格式容错**,而且几乎每条跑道的第一轮都在修它:

    flash-three-2 r1      Stop one unparseable reply from ending the task
    deepseek-eigen-2 r1   ...unparseable model reply is fed back as an observation and retried
    strong-model-1 r2     Changing parse errors from terminating the loop into a user-facing retry
    loop-terminal_bench-26452c r1   Fixed the JSON parsing logic in `_parse()` to be more robust

而 `loop-terminal_bench-26452c` 的轨迹说明死得多干净:五道题,每道题**只调了一次模型**
(`calls: 1`),就写着 `parse_error: Extra data`,整道题结束。那一轮的每一次实验,都从
同一个不能用的 harness 出发。

更贵的代价在方法之间:RRSI 的 leakage critic 两次把"修 `_parse`"的候选判成
TASK-SPECIALIZATION / LEAKAGE 并拒绝 —— 一轮就这样没了。方法们开始比"谁先想到修 parser",
而这不是我们要测的能力。

所以修在起点 harness 里。三次测试:能救回来、救不回来时记录里有 N 次尝试、
而"第一个 JSON 对象"的容错仍然照旧。
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
    spec = importlib.util.spec_from_file_location("hg_loop_parse", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def agent():
    return _load()


def _scripted(agent, replies: list[str]):
    """按剧本回复的假模型,同时记下每次发过去的历史长度。"""
    seen: list[int] = []

    def fake(messages):
        seen.append(len(messages))
        return replies[min(len(seen) - 1, len(replies) - 1)]

    return fake, seen


def _events(workdir: Path) -> list[dict]:
    return [json.loads(l) for l in (workdir / "trace.jsonl").read_text().splitlines()]


def test_a_prose_reply_is_fed_back_and_the_task_continues(agent, tmp_path, monkeypatch):
    """模型先说了一段话,然后才给出动作。以前这里整道题就结束了。"""
    fake, seen = _scripted(agent, [
        "I will inspect the directory first.",              # 读不出动作
        '{"tool": "bash", "command": "true"}',               # 一个合法动作
        '{"answer": "done"}',
    ])
    monkeypatch.setattr(agent, "_complete", fake)
    assert agent.run({"goal": "say done"}, tmp_path) == "done"

    events = _events(tmp_path)
    retries = [e for e in events if "parse_error" in e]
    assert len(retries) == 1, events
    assert retries[0]["parse_retry"] == 0
    # 退回的消息真的发出去了:第二次调用时历史更长(assistant + user 两条)
    assert seen[1] >= seen[0] + 2, seen


def test_when_it_never_parses_the_record_shows_every_attempt(agent, tmp_path, monkeypatch):
    """救不回来时,记录里要看得见试了几次 —— 一次尝试和 N 次尝试不是同一件事。"""
    fake, seen = _scripted(agent, ["still just talking"])
    monkeypatch.setattr(agent, "_complete", fake)
    assert agent.run({"goal": "unreachable"}, tmp_path) == ""

    events = _events(tmp_path)
    retries = [e for e in events if "parse_error" in e]
    assert len(retries) == agent.PARSE_RETRIES + 1, (len(retries), agent.PARSE_RETRIES)
    assert [e["parse_retry"] for e in retries] == list(range(agent.PARSE_RETRIES + 1))
    assert len(seen) == agent.PARSE_RETRIES + 1, "每次重试都必须真的再调一次模型"


def test_the_first_json_object_is_still_extracted(agent, tmp_path, monkeypatch):
    """容错那条路不能被这次改动弄坏:两个对象、外加散文,取第一个。"""
    fake, _ = _scripted(agent, [
        'Sure:\n{"tool": "bash", "command": "true"}}\n{"answer": ""}\n',
        '{"answer": "ok"}',
    ])
    monkeypatch.setattr(agent, "_complete", fake)
    assert agent.run({"goal": "ok"}, tmp_path) == "ok"
    assert not [e for e in _events(tmp_path) if "parse_error" in e], _events(tmp_path)


# ------------------------------------------------ 收尾提醒与重复提醒 ---

def _run_scripted(agent, tmp_path, monkeypatch, replies, *, max_steps=6, nudge=2):
    """跑一次脚本化的 run(),把 MAX_STEPS 缩小,返回 trace 事件。"""
    monkeypatch.setattr(agent, "MAX_STEPS", max_steps)
    monkeypatch.setattr(agent, "FINISH_NUDGE_STEPS", nudge)
    fake, _ = _scripted(agent, replies)
    monkeypatch.setattr(agent, "_complete", fake)
    agent.run({"goal": "say done"}, tmp_path)
    return _events(tmp_path)


def test_a_repeated_command_gets_a_nudge(agent, tmp_path, monkeypatch):
    """266 条历史轨迹里 73 条跑满步数、0 条交卷,而最后两步常是同一条命令。

    实测抽样:`python /app/test_outputs.py 2>&1` 两次、同一条 `echo ... >` 两次、
    同一个 `cat > /app/run.py << 'EOF'` 两次。模型不是做错了,是没停下来。
    """
    same = '{"tool": "bash", "command": "true"}'
    events = _run_scripted(agent, tmp_path, monkeypatch, [same, same, same], max_steps=6)
    nudges = [e for e in events if "repeat_nudge" in e]
    assert len(nudges) == agent.REPEAT_NUDGE_LIMIT, nudges
    assert nudges[0]["repeat_nudge"] == "true"


def test_different_commands_do_not_trigger_the_repeat_nudge(agent, tmp_path, monkeypatch):
    replies = ['{"tool": "bash", "command": "true"}',
               '{"tool": "bash", "command": "echo hi"}',
               '{"answer": "done"}']
    events = _run_scripted(agent, tmp_path, monkeypatch, replies, max_steps=6)
    assert not [e for e in events if "repeat_nudge" in e], events


def test_the_finish_nudge_fires_once_entering_the_zone_and_once_at_the_last_step(
        agent, tmp_path, monkeypatch):
    """收尾区进一次、最后一步再一次 —— 每步都喊同一句话只会挤掉最近的输出。"""
    reply = '{"tool": "bash", "command": "echo x"}'
    events = _run_scripted(agent, tmp_path, monkeypatch, [reply], max_steps=6, nudge=2)
    lefts = [e["finish_nudge"] for e in events if "finish_nudge" in e]
    assert lefts == [2, 1], lefts


def test_the_nudges_are_written_to_the_trace_before_they_are_sent(agent, tmp_path,
                                                                 monkeypatch):
    """平台看不到容器内部,只有 trace。没有这两条记录,"原地打转"和"认真工作"长得一样。"""
    same = '{"tool": "bash", "command": "true"}'
    events = _run_scripted(agent, tmp_path, monkeypatch, [same], max_steps=6, nudge=2)
    kinds = [k for e in events for k in ("repeat_nudge", "finish_nudge") if k in e]
    assert "repeat_nudge" in kinds and "finish_nudge" in kinds, kinds


def test_the_trace_is_on_disk_before_the_next_command_runs(agent, tmp_path, monkeypatch):
    """行缓冲:命令跑起来之前,上一步的事件已经落盘。

    实测那一次(本地模型跑 `bn-fit-modify`):harness 跑了 8 分钟、正在装 pandas,而
    `trace.jsonl` 是 0 字节、fd 的 `pos: 0` —— 默认的 8KB 块缓冲把一切都留在内存里。
    代价是实时轨迹看不见,而且被超时杀掉的题会丢掉缓冲区里的尾巴。
    """
    replies = [
        '{"tool": "bash", "command": "cp trace.jsonl step0_snapshot.txt"}',
        '{"answer": "done"}',
    ]
    fake, _ = _scripted(agent, replies)
    monkeypatch.setattr(agent, "_complete", fake)
    agent.run({"goal": "snapshot"}, tmp_path)
    snapshot = (tmp_path / "step0_snapshot.txt").read_text()
    assert '"step": 0' in snapshot, "第 0 步的事件在命令执行前没有落盘"


# --------------------------------------------- 上下文管理(本地模型那次) ---

def test_a_huge_command_output_is_clipped_with_a_marker(agent, tmp_path, monkeypatch):
    """实测:3 道题死于 `maximum context length is 32768 tokens ... at least 32769`。

    起点 harness 把每条命令的**完整**输出一直往历史里追加:一道题里单条输出 11773 字符。
    那不是模型的问题,是 harness 没有上下文管理。截断必须留痕 —— 悄悄改变模型所见历史的
    harness 和被测量的 harness 不是同一件事。
    """
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", 200)
    replies = ['{"tool": "bash", "command": "head -c 2000 /dev/zero | tr \\"\\\\0\\" x"}',
               '{"answer": "done"}']
    fake, _ = _scripted(agent, replies)
    monkeypatch.setattr(agent, "_complete", fake)
    agent.run({"goal": "big output"}, tmp_path)
    events = _events(tmp_path)
    clipped = [e for e in events if "output_clipped" in e]
    assert clipped, "超长输出没有被截断"
    assert clipped[0]["output_clipped"] >= 2000 and clipped[0]["shown"] <= 260, clipped[0]
    # 历史里的那条消息也确实短了
    big = [e for e in events if e.get("exit") is not None]
    assert big and len(str(big[0]["output"])) >= 2000, "轨迹里仍保留完整输出(给读记录的人)"


def test_the_history_is_trimmed_and_the_trim_is_recorded(agent, tmp_path, monkeypatch):
    """历史超预算就丢最旧的成对消息,并在轨迹里记下丢了几条。"""
    monkeypatch.setattr(agent, "CONTEXT_CHAR_BUDGET", 800)
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", 400)
    reply = '{"tool": "bash", "command": "head -c 400 /dev/zero | tr \\"\\\\0\\" y"}'
    fake, seen = _scripted(agent, [reply])
    monkeypatch.setattr(agent, "_complete", fake)
    monkeypatch.setattr(agent, "MAX_STEPS", 8)
    agent.run({"goal": "grow"}, tmp_path)
    events = _events(tmp_path)
    dropped = [e for e in events if "history_dropped" in e]
    assert dropped, "历史没有被修剪"
    # 修剪之后,历史长度不再单调增长 —— 这正是它要保证的
    assert max(seen) < 8, seen


# ------------------------------------------------ 重复键:一次静默的空动作 ---

#: `local-floor-9` 里 `cancel-async-tasks` 第 0 步**原样**的回复(875 字符)。
#: 真实命令在里面,末尾又被模型补了一个空的 `"command": ""`。
_DUPLICATE_KEY_REPLY = (
    '{"tool": "bash", "command": "cat > /app/run.py << \'EOF\'\\n'
    "import asyncio\\n\\nasync def run_tasks(tasks, max_concurrent):\\n"
    "    semaphore = asyncio.Semaphore(max_concurrent)\\n"
    "EOF\\n\", \"command\": \"\"}")


def test_a_duplicated_command_key_keeps_the_first_value(agent):
    """JSON 说重复键取最后一个。模型把真命令放在**第一个**里,于是命令变成了空串。

    实测(`local-floor-9`,`cancel-async-tasks`):第 0/2/13 步三次写文件的回复全都长这样,
    每一次 `_parse` 都返回 `{"tool": "bash", "command": ""}`,harness 于是执行空字符串 ——
    退出码 0、没有输出 —— 并把 `exit=0` 发给模型。模型据此回答"文件已创建",而判分脚本报
    `File /app/run.py does not exist`。16 步里没有一步写过文件。
    """
    action = agent._parse(_DUPLICATE_KEY_REPLY)
    assert action["command"].startswith("cat > /app/run.py"), action
    assert action["command"].rstrip().endswith("EOF"), action
    assert action.duplicates == ("command",), action.duplicates


def test_the_repair_is_written_to_the_trace(agent, tmp_path, monkeypatch):
    """被改写的动作与被原样执行的动作不是同一件事,记录里必须分得开。"""
    fake, _ = _scripted(agent, [_DUPLICATE_KEY_REPLY, '{"answer": "done"}'])
    monkeypatch.setattr(agent, "_complete", fake)
    agent.run({"goal": "say done"}, tmp_path)
    events = _events(tmp_path)
    noted = [e for e in events if "duplicate_keys" in e]
    assert noted and noted[0]["duplicate_keys"] == ["command"], events
    # 命令真的跑了(而且不是空串)
    ran = [e for e in events if "command" in e]
    assert ran and ran[0]["command"].startswith("cat >"), ran


def test_a_reply_with_no_action_never_runs_an_empty_command(agent, tmp_path,
                                                            monkeypatch):
    """空命令以前是**成功**的一步:exit 0、无输出。它必须是读不懂的回复。"""
    fake, _ = _scripted(agent, ['{"tool": "bash", "command": ""}',
                                '{"answer": "done"}'])
    monkeypatch.setattr(agent, "_complete", fake)
    agent.run({"goal": "say done"}, tmp_path)
    events = _events(tmp_path)
    assert [e for e in events if "parse_error" in e], events
    assert not [e for e in events if "command" in e], events


def test_a_truncated_reply_does_not_lend_its_nested_object(agent):
    """回复被截断时,里面嵌的那个完整对象不是动作。

    这条是同一个 bug 的第二副面孔:扫描器以前返回**第一个能解析的**对象,嵌套的那个也算。
    返回它 = 命令为空 = 静默空动作。现在它必须变成一次读不懂的回复。
    """
    truncated = ('{"tool": "bash", "command": "cat > /app/x.py << \'EOF\'\\n'
                 'import json\\nd = {"a": 1}\\n')
    with pytest.raises(ValueError):
        agent._parse(truncated)


def test_an_empty_answer_is_still_a_valid_reply(agent):
    """修窄了会伤到这条:`{"answer": ""}` 是一个合法动作(题面可能只要答案为空)。"""
    assert agent._parse('{"answer": ""}') == {"answer": ""}



# ------------------------------------- 历史太长是可以恢复的,不是整道题的死因 ---

def _rejecting_provider(limit: int, *, always: bool = False):
    """A fake `openai` module whose provider refuses any prompt over `limit` chars.

    The real message is vLLM's, verbatim -- the harness keys on the model's own words
    rather than on an estimate of its own.
    """
    import types

    state = {"calls": 0, "rejected": 0, "sizes": []}

    class Completions:
        def create(self, model, messages, temperature, max_tokens):
            assert isinstance(max_tokens, int) and max_tokens > 0
            state["calls"] += 1
            size = sum(len(m.get("content") or "") for m in messages)
            state["sizes"].append(size)
            if always or size > limit:
                state["rejected"] += 1
                raise Exception(
                    "This model's maximum context length is 32768 tokens. However, you "
                    "requested 0 output tokens and your prompt contains at least 32769 "
                    "input tokens. Please reduce the length of the input prompt")
            body = '{"tool": "bash", "command": "head -c 3000 /dev/zero | tr \\"\\\\0\\" z"}'
            return types.SimpleNamespace(
                usage=None,
                choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content=body))])

    module = types.ModuleType("openai")
    module.OpenAI = lambda **kw: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=Completions()))
    return module, state


@pytest.fixture()
def openai_env(monkeypatch):
    monkeypatch.setenv("HG_AGENT_BACKEND", "openai")
    monkeypatch.setenv("HG_AGENT_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("HG_AGENT_API_KEY", "probe")
    monkeypatch.setenv("HG_AGENT_MODEL", "probe")
    monkeypatch.setenv("HG_AGENT_RETRIES", "3")


def test_a_prompt_the_model_rejects_is_trimmed_and_retried(agent, tmp_path, monkeypatch,
                                                           openai_env):
    """**实测 `local-screen-1`:`git-leak-recovery` 和 `regex-chess` 就是这样死的。**

    两道题都跑了十几步有效功,然后死在
    `maximum context length is 32768 tokens ... at least 32769 input tokens`。
    字符预算挡不住它:代码、git 对象、base64 每 token 只有一两个字符,而预算是按字符
    算的(见 `_trim_history`)。所以判据改成模型自己的措辞,削一半历史再问 —— 那两道题
    本来都还能继续做。
    """
    module, state = _rejecting_provider(limit=5000)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setattr(agent, "MAX_STEPS", 8)
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", 4000)
    # 故意留一个挡不住它的字符预算:这正是线上那个 60000
    monkeypatch.setattr(agent, "CONTEXT_CHAR_BUDGET", 60000)

    agent.run({"goal": "grow"}, tmp_path)

    events = _events(tmp_path)
    assert state["rejected"] >= 1, "这个测试没有真的触发拒绝"
    assert len([e for e in events if "command" in e]) == 8, \
        "被拒绝之后整道题就结束了 —— 这正是要修的那件事"
    usage = [e["usage"] for e in events if "usage" in e][0]
    assert usage["context_trims"] == state["rejected"], (usage, state)
    # 削短过历史这件事必须在记录里,不能悄悄发生
    trims = [e for e in events if "context_trims" in e and "reply" not in e]
    assert trims and trims[0]["history_kept"] >= 3, events
    # 削短之后请求确实变小了
    assert min(state["sizes"]) < 5000, state["sizes"]


def test_a_prompt_that_is_still_too_long_is_a_harness_failure(agent, tmp_path, monkeypatch,
                                                              openai_env):
    """反向的一半:修不好的情况必须响亮地失败,而不是继续跑出一个空轨迹。

    一个已经有 N 次尝试的失败和一个"从没跑起来"的失败在记录里长得一样,所以这里要求
    异常照旧抛出去(平台会记成 harness failure),而不是被吞掉。
    """
    module, state = _rejecting_provider(limit=0, always=True)
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setattr(agent, "MAX_STEPS", 3)
    with pytest.raises(Exception):
        agent.run({"goal": "hopeless"}, tmp_path)
    assert state["rejected"] >= 1
