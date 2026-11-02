#!/usr/bin/env python3
"""The reference base harness: a minimal ReAct loop.

Deliberately the simplest thing that satisfies the contract in
`docs/writing_a_harness.md`. Its job is not to be strong -- it is to be:

  1. a valid starting point that a method can improve, and
  2. the proof that the contract is implementable at all.

A method may replace every file here. That is the point.

**What it ships, and why that changed.** This harness used to be a bare loop: no
context policy, no output bounding, no retries. That is still the design -- but
these mechanisms are now in it, and each is here because a measurement said the
bare loop could not be measured at all, not because a mechanism is nice to have:

  * `PARSE_RETRIES`, `SUBMIT_RETRIES`, `FINISH_NUDGE_STEPS`, `REPEAT_NUDGE_LIMIT`
    -- one unreadable reply, or one premature "I am done", used to end the whole
    task. Measured: 73 of 266 runs reached the step cap with nothing submitted,
    and one task died after exactly one model call.
  * `OUTPUT_LIMIT`, `CONTEXT_CHAR_BUDGET` -- a command that printed a large file
    left the next call over the model's context limit, which killed the task.
  * line-buffered trace writes -- a block buffer meant a killed task lost its tail,
    so "killed at step 30" and "never started" were the same record.

All of them are still named switches with an `HG_AGENT_*` override, so a method
may change or remove any of them; what a method adds *on top* is the measurement.
The values actually in force go into the trace (`harness_identity.config`, below)
and land on the curve point, because two runs of this same file at two different
step caps are two different harnesses and the file hash cannot tell them apart.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


#: How many model calls one task gets. **A floor on capability, not a tuning knob.**
#:
#: Measured at 8, on Terminal-Bench, 4 rounds x 5 tasks: four of the five tasks used all
#: eight and produced no graded artifact at all -- `build-pmars` was still installing
#: packages, `bn-fit-modify` had just finished installing pandas/numpy/sklearn and
#: printed one correlation matrix, `chess-best-move` had opened the PNG and never wrote
#: `/app/move.txt`. The score was 0.0 on every task in every round, which is not a weak
#: harness being measured: it is a floor effect, and a floor makes every method look
#: identical because there is no gradient for one to move.
#:
#: Raising this does not make the harness cleverer -- `legacy/adapters/harnessgrad_domain/`
#: already names this lever as the trap ("helps only if the model is already using the
#: steps it has"). Here the trace shows it *was* using them, which is exactly when the
#: lever is legitimate. What it costs is bounded by the task, not by this number: the
#: wall clock is capped by the environment's `agent_timeout_s` (900 s for most
#: Terminal-Bench tasks, 3600 s for `bn-fit-modify`). A step is fast (~0.2 s model round
#: trip through the local gateway), so the extra ceiling mostly buys back the steps that
#: environment setup eats. The loop still stops the moment the model answers -- the cap
#: is a ceiling, not a quota.
MAX_STEPS = int(os.environ.get("HG_AGENT_MAX_STEPS", "32"))
MAX_OUTPUT_TOKENS = int(os.environ.get("HG_AGENT_MAX_OUTPUT_TOKENS", "2048"))
if MAX_OUTPUT_TOKENS < 1:
    raise ValueError("HG_AGENT_MAX_OUTPUT_TOKENS must be positive")

#: 模型说"我做完了"之后,允许它再被退回几次。
#:
#: 为什么需要这个:harness 以前**从不检查交付物**。模型回一句 {"answer": ...} 就被当成
#: 任务结束、退出循环。实测(2026-10-02,terminal_bench round 0):五道题的 agent 阶段是
#: 101s / 31s / 183s / **4.5s** / 105s —— 而步数上限是 32。4.5 秒等于一次模型调用就
#: "交卷"了,产物根本不存在;`cancel-async-tasks` 的判分因此是
#: `File /app/run.py does not exist`。
#:
#: 所以 0 分的机制不是"步数不够",而是"**harness 把模型说的完成当成了完成**"。
#: 一个会自己检查交付物、并把"文件不存在"退回给模型重试的 harness,才是这一步该有的样子。
SUBMIT_RETRIES = int(os.environ.get("HG_AGENT_SUBMIT_RETRIES", "2"))

#: 模型吐出一段**读不出动作**的回复之后,允许它再试几次。
#:
#: 和上面那条同源,方向相反:一条是"模型说做完了,但产物不在",一条是"模型说的话读不出
#: 动作"。两条以前都会让**整道题**结束。
#:
#: 为什么它必须由起点 harness 负责,而不是留给方法去发现 —— 这是数出来的,不是想出来的。
#: 统计所有跑道里"方法自己写了理由"的 35 个轮次:**16 个在修解析或格式容错**,而且几乎
#: 每条跑道的第一轮都在修它:
#:
#:   `loop-terminal_bench-26452c` 五道题各只调了一次模型就死在
#:   `parse_error: Extra data`(`calls: 1`),此后每一轮都从同一个不能用的 harness 出发;
#:   `flash-three-1/2`、`deepseek-eigen-2`、`strong-model-1` 的第一/第二轮全都写成
#:   "让一次读不懂的回复不再结束整道题"。
#:
#: 代价不只是浪费一轮。方法之间开始比"谁先想到修 parser":RRSI 的 leakage critic 两次把
#: 改 `_parse` 的候选判成 TASK-SPECIALIZATION / LEAKAGE 而拒绝,那一轮于是什么都没改。
#: 一个起点 harness 必须在被测量之前就活过一次跑题的回复,否则第一轮比较的不是解题能力,
#: 是管线运气。
PARSE_RETRIES = int(os.environ.get("HG_AGENT_PARSE_RETRIES", "2"))

#: 步数还剩这么多时,明确要求模型收尾。
#:
#: 数出来的,不是猜的:266 条历史轨迹里 **73 条(27%)跑满 32 步、一次都没交卷**,而那些
#: 轨迹的最后两步常常是同一条命令重复(抽样:`python /app/test_outputs.py 2>&1` 两次、
#: 同一条 `echo ... >` 两次、同一个 `cat > /app/run.py << 'EOF'` 两次)。那些任务不是
#: "做错了",是**没停下来交卷**:步数用完,`answer` 是空字符串,平台只能拿检查的判决当分数。
#: `improvers/skill.md` 早就把这条记成已知失败模式("预算用满、什么都没交"),但起点 harness
#: 一直没有对应动作。一个被测量的 harness 应该在预算耗尽前把话说完。
FINISH_NUDGE_STEPS = int(os.environ.get("HG_AGENT_FINISH_NUDGE_STEPS", "4"))

#: 同一条命令连续重复时提醒几次(每条跑道限量,免得把上下文塞满)。
REPEAT_NUDGE_LIMIT = int(os.environ.get("HG_AGENT_REPEAT_NUDGE_LIMIT", "2"))

#: 单条命令的输出进入消息历史时的字符上限。先压紧 JSON,再保留完整记录预览;
#: 纯文本留头尾。完整输出留在工作目录,模型可按需读取。
#:
#: 实测(2026-10-04,本地 9B,5 道题):**3 道题死于
#: `This model's maximum context length is 32768 tokens ... your prompt contains at least
#: 32769 input tokens`(BadRequestError 400)**。起点 harness 以前把每条命令的**完整**输出
#: 一直往历史里追加,永不截断:一道题里单条输出就有 11773 字符,装一次包、cat 一个大文件
#: 就够撞上 32k 的窗口。那不是模型的问题,是 harness 没有上下文管理。
OUTPUT_LIMIT = int(os.environ.get("HG_AGENT_OUTPUT_LIMIT", "4000"))

#: 消息历史(不含 system 与题面)的字符预算。按 4 字符 ≈ 1 token 粗估,60k 字符约 15k token,
#: 给 32k 窗口的模型留出输出空间。超了就丢**最旧的成对消息**,并在轨迹里记下丢了几条 ——
#: 一个悄悄改变模型所见历史的 harness,和一个被测量的 harness,不是同一件事。
CONTEXT_CHAR_BUDGET = int(os.environ.get("HG_AGENT_CONTEXT_CHARS", "60000"))

SYSTEM = """You are an agent solving a task in a working directory.

You have one tool: run a shell command.

Reply with EXACTLY one of:
  {"tool": "bash", "command": "<shell command>"}
  {"answer": "<your final answer>"}

For a structured final answer, "answer" may be a JSON object containing the task's
prediction fields.

Rules:
- One action per reply. No prose outside the JSON.
- Keep commands concise. Use a loop or a script instead of enumerating many calls.
- To inspect files use shell commands (ls, cat, grep, ...).
- Give the final answer only when you are confident.
"""


# ---------------------------------------------------------------- backends ---

def _backend() -> str:
    """Which completion backend to use.

    `mock` exists so the framework can be exercised end-to-end with zero network
    and zero cost. Any real curve must declare its backend and model on the curve
    point (INTERFACE.md §3, `identity.agent_model`).
    """
    return os.environ.get("HG_AGENT_BACKEND", "mock")


def _call_mock(messages: list[dict]) -> str:
    """Deterministic stand-in: read answer.txt if present, else list the dir."""
    task_text = messages[1]["content"] if len(messages) > 1 else ""
    if "answer.txt exists" in task_text or "already" in task_text:
        return json.dumps({"answer": "READ_FROM_FILE"})
    return json.dumps({"tool": "bash", "command": "ls -a"})


# --------------------------------------------------------------- usage ---

#: Token usage accumulated over the run, filled by whichever backend is active.
#: Read into the trace at the end. Recorded because a mechanism that spends model
#: calls (history_summary does) is only comparable against a free one if the
#: spend is visible -- and because a harness whose cost is unknown cannot be
#: ranked against a cheaper one that scores the same.
#: `context_trims` 单独计数:它是"模型拒绝了这条 prompt,于是我们削短了历史"的次数。
#: 和 `retries`(网络/限流)不同 —— 那一种重试发的是同一条 prompt,这一种发的是**更短的**
#: 一条,模型看到的历史因此和上一步不同。要能分开,才能解释"那一步为什么换了做法"。
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "retries": 0,
         "context_trims": 0}


def _record_usage(usage) -> None:
    """Fold provider token counts into the run's total.

    The call count is NOT incremented here: it is incremented by `_complete`,
    which is the one place every model call goes through. Counting calls at the
    provider boundary would miss the mock backend entirely -- and a mechanism that
    spends model calls on a mock run would then report zero cost, which is exactly
    the invisible-spend the usage block exists to prevent.
    """
    if usage is None:
        return
    # Provider fields that are absent stay absent rather than becoming zero: a
    # zero that means "not reported" is indistinguishable from a real zero.
    for attr, key in (("prompt_tokens", "input_tokens"),
                      ("completion_tokens", "output_tokens")):
        v = getattr(usage, attr, None)
        if isinstance(v, int):
            USAGE[key] += v


def _call_openai(messages: list[dict]) -> str:
    """One completion, with a deadline and bounded retries.

    A harness that dies on a transient provider error loses a whole task run, and
    an unbounded retry loop turns a provider outage into a hung experiment. Both
    failures are silent in a batch unless the harness is defensive here.
    """
    from openai import OpenAI

    # **每次请求用一条新连接,不复用。** 不是性能取舍,是正确性:
    #
    # 实测(2026-10-02,容器内复现):vLLM 的 uvicorn 默认 5 秒关掉空闲 keep-alive,而
    # 容器到模型之间还有平台发布的一层字节转发网关,它在上游 EOF 时会把 EOF 半关到
    # 客户端。于是"跑一条几十秒的命令、再发起下一次模型调用"就必然复用一条死连接 ——
    # `RemoteDisconnected` → openai 报 `APIConnectionError: Connection error`。
    # 实测那一次,两道题(跑 pandas 的、跑 PIL 的)就是这样死的,而平台把它们记成了 0 分。
    #
    # keep-alive 本身是 HTTP 客户端的正常行为,所以这是网关的缺陷(见 eval/modelgate.py);
    # 但一个 harness 不该把自己的测量押在"中间那一层是否正确处理空闲连接"上。一次本地
    # TCP 握手的代价可以忽略。
    #
    # **用 `default_headers`,不要 import httpx。** 第一版写的是
    # `http_client=httpx.Client(...)`,结果是每次调用都 `ModuleNotFoundError: No module
    # named 'httpx'` —— 这个平台的 harness 依赖由覆盖层提供,而这里 `openai` 3.22.1 依赖的
    # 是 `httpx2`(不是 `httpx`)。所以"顺手 import 一个我以为在的库"在这条路上是错的:
    # 唯一可以依赖的是 `requirements.txt` 声明的那个发行包自己的接口。
    client = OpenAI(
        base_url=os.environ["HG_AGENT_BASE_URL"],
        api_key=os.environ["HG_AGENT_API_KEY"],
        timeout=float(os.environ.get("HG_AGENT_TIMEOUT_S", "180")),
        max_retries=0,          # retried here, so the count is ours to record
        default_headers={"Connection": "close"},
    )
    model = os.environ["HG_AGENT_MODEL"]
    attempts = int(os.environ.get("HG_AGENT_RETRIES", "3"))
    last: Exception | None = None

    for attempt in range(attempts):
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0.0,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
            _record_usage(getattr(resp, "usage", None))
            return resp.choices[0].message.content or ""
        except Exception as exc:                      # noqa: BLE001
            last = exc
            # **历史太长是可恢复的,以前不是。** 实测(2026-10-04):`git-leak-recovery` 和
            # `regex-chess` 死在 `maximum context length is 32768 tokens ... at least 32769
            # input tokens`,而两道题之前都已经跑了十几步的有效功。字符预算挡不住它
            # (见 `_trim_history`),所以这里按模型自己的拒绝削一半历史再试一次 —— 削掉的
            # 步数记进 `USAGE["context_trims"]`,由主循环写进 trace,不是悄悄改历史。
            if any(marker in str(exc) for marker in _CONTEXT_ERROR_MARKERS) \
                    and len(messages) > 4 and attempt + 1 < attempts:
                dropped = _trim_history(messages, budget=(CONTEXT_CHAR_BUDGET // 2))
                if not dropped:
                    dropped = _trim_history(messages, budget=1)
                USAGE["context_trims"] += 1
                USAGE["retries"] += 1
                continue
            if attempt + 1 < attempts:
                USAGE["retries"] += 1
                time.sleep(min(2 ** attempt, 30))
    # Out of attempts: raise, so the caller records a harness failure rather than
    # scoring an empty answer as a wrong one.
    raise RuntimeError(f"{attempts} attempts failed, last: {last}") from last


def _complete(messages: list[dict]) -> str:
    """Every model call in this harness goes through here, so this is where the
    count is kept. Counting anywhere else means some caller is invisible."""
    USAGE["calls"] += 1
    return _call_mock(messages) if _backend() == "mock" else _call_openai(messages)


# ------------------------------------------------------------- the loop -----

class _Action(dict):
    """A parsed action, plus what the parser had to repair to get it.

    An attribute rather than a second return value, because every caller wants the
    dict and only one wants the repair note. `duplicates` empty means the reply was
    well formed and nothing was guessed.
    """
    duplicates: tuple[str, ...] = ()


def _first_key_wins(pairs):
    """Duplicated JSON keys: keep the **first** value, and say which keys repeated.

    **This is measured, not defensive.** The local 9B (Qwen3.5-9B) writes a file like
    this, and it is the whole reason `cancel-async-tasks` scored 0.0 in
    `local-floor-9`:

        {"tool": "bash", "command": "cat > /app/run.py << 'EOF'\n...\nEOF\n", "command": ""}

    The real command is there, and then the model appends `, "command": ""`. JSON says
    a duplicate key keeps the **last** value, so `json.loads` (and `raw_decode`) handed
    the harness an empty command. The harness then ran the empty string -- exit 0, no
    output -- and told the model `exit=0`. The model read that as success, answered
    "the file /app/run.py has been created", and the verifier found no file. All 16
    steps of that trajectory are the model writing the file, being told it worked, and
    checking. Not one step of it wrote anything.

    Taking the first value is the repair that matches intent here: the long command is
    what the model meant, and the trailing `""` is noise from the same malformation
    that produces its stray braces. It is recorded (`duplicate_keys` in the trace)
    because a harness that silently rewrites the model's action and one that does not
    are different harnesses.
    """
    action = _Action()
    repeated: list[str] = []
    for key, value in pairs:
        if key in action:
            repeated.append(key)
            continue
        action[key] = value
    if repeated:
        action.duplicates = tuple(repeated)
    return action


def _json_block_end(text: str, start: int) -> int:
    """Skip a malformed JSON block without treating its strings as actions."""
    depth = 0
    quoted = escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _parse(reply: str) -> dict:
    """First complete action, across prose/fences and preceding structured data.

    Consume whole JSON blocks, so nested data and quoted example actions cannot
    become commands. Never close a truncated command or execute its prefix.
    """
    decoder = json.JSONDecoder(object_pairs_hook=_first_key_wins)
    i = 0
    while i < len(reply):
        if reply[i] not in "{[":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(reply, i)
        except json.JSONDecodeError:
            i = _json_block_end(reply, i)
            continue
        i = end
        if not isinstance(obj, dict) or obj.get("tool", "bash") != "bash":
            continue
        cmd = obj.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            return obj
        if cmd == "" and "answer" in obj:
            # An outer action can be valid JSON while its answer contains broken
            # inner JSON. Reject it here so the ordinary parse retry can recover.
            _answer_text(obj["answer"])
            return obj
    raise ValueError("no complete JSON action found in reply")


def _answer_text(value: object) -> str:
    """Transport a final answer without inventing or dropping prediction fields.

    Schema validation belongs to the task's verifier. This only checks transport:
    objects must stay JSON (str(dict) produces Python syntax), and JSON-looking
    strings must be complete. Never close truncated JSON or reinterpret literals.
    Numbers, booleans and lists are valid JSON answers (AppWorld questions often
    ask for a count or an amount), so they are serialized rather than rejected.
    """
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, (int, float, bool, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if not isinstance(value, str):
        raise ValueError("final answer must be a string, number, list or a JSON object")
    if value.lstrip().startswith("{"):
        try:
            json.loads(value)
        except ValueError as exc:
            raise ValueError(f"final answer contains malformed JSON: {exc}") from exc
    return value


#: goal 里形如 `/app/move.txt`、`/app/learned_dag.csv` 的路径。这是**通用**的:
#: 它不针对任何一道题,只看题面自己说了要写到哪个文件。
_PATH_RE = re.compile(r"/[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,6}\b")


def _missing_deliverables(task: dict, workdir: Path) -> list[str]:
    """题面点名要写、但现在**不存在**的文件。

    这是 `loop` 自带的交卷闸门,刻意做成通用的两条:

      * 只看题面(goal)里写出来的绝对路径 —— harness 没有任何途径知道判分脚本要什么
        (`task.json` 里只有 goal),所以它只能相信题面;
      * 只做 `exists` 检查,不执行任何东西。

    题面没提路径(比如"回答一个问题")时返回空列表,闸门不生效 —— 这时判断对错是平台
    的事,harness 不该假装知道。
    """
    goal = str(task.get("goal") or "")
    wanted = {m for m in _PATH_RE.findall(goal)}
    missing = []
    for raw in sorted(wanted):
        # 容器里绝对路径就是它自己;宿主上则落在 workdir 之内。两种都查,取存在的那个。
        candidates = [Path(raw)]
        try:
            candidates.append(workdir / Path(raw).relative_to("/app"))
        except ValueError:
            pass
        if not any(c.exists() for c in candidates):
            missing.append(raw)
    return missing


# --------------------------------------------------------------- appworld ---
#
# **为什么这里要认识 AppWorld,而不是继续装成一个通用终端 agent。**
#
# 这一批任务的判分**不看 `answer.txt`**。check.py 回放的是环境里那份
# `logs/api_calls.jsonl`:AppWorld 的 TGC 只认 `supervisor.complete_task` 写进
# `supervisor.Task.answer` 的值。可是 `loop` 的"交卷"是模型回一句
# `{"answer": ...}` 就结束,那句话从来没有进过 AppWorld。实测(round 0,90 条轨迹):
# 24 道题**除 answer 一条以外全部检查通过**,却因为 `'<<not_given>>' == '34'`
# 或 `'<<not_given>>' == 'null'` 得 0 分;另有 8 道题动作全做对了,只因为
# `complete_task(answer="followed ...")` 而 ground truth 是 null 被判错。
#
# 所以下面做的不是"再提醒模型一次",而是把**交卷**这件事从模型手里拿回来:
#   * 任务的最后一个动作由 harness 执行 `apis.supervisor.complete_task(...)`;
#   * 问句题传模型给的精确值,动作题传 None(AppWorld 的官方约定:非问句留空);
#   * 模型自己调用过也没关系 —— `complete_task` 没有"已完成"守卫,后写覆盖先写,
#     所以最后那一次一定是我们这一次。
#
# 识别方式是环境事实,不是题号:workdir 里有 `aw`,task 里有 `task_id`。

#: 题面在**要一个答案**时的开头。`Name the artist ...` 是祈使句但要答案;
#: `Give a 5-star rating ...` 同样祈使但不要答案 —— 这两类都在这 90 条里实测过,
#: 下面的分类与每条题的 ground truth 一一对上(问句->非 null,动作->null)。
_ANSWER_GOAL_RE = re.compile(
    r"^\s*(?:what|how|which|who|whom|whose|when|where|name|list|tell|is|are|was|were|"
    r"do|does|did|can|could|would|should|give\s+me|show\s+me|find\s+me)\b",
    re.IGNORECASE,
)

#: AppWorld 的 app 名 -> 题面里可能出现的说法。用来在 init 之后只抓**这道题会用到**
#: 的 API 索引,避免把 10 个 app 的文档全塞进 prompt。
#:
#: `phone` 还要覆盖关系词:round 1 里 "like all transactions involving my roommates"
#: 这类题面一个字都没提 phone,模型因此既没有拿到 phone 的 API 索引,也没有想到
#: 联络人簿才是"谁是室友"的唯一来源(6ea6792_2 把**所有**待处理请求都拒绝了)。
_APP_HINTS = (
    ("spotify", ("spotify",)),
    ("venmo", ("venmo", "payment request", "transaction")),
    ("simple_note", ("simple note", "simple_note")),
    ("phone", ("phone", "text message", "voice message", "alarm", "roommate",
               "coworker", "co-worker", "sibling", "friend", "family", "contact")),
    ("file_system", ("file system", "file_system", "directory", "directories", "~/",
                     "file", "csv", "backup")),
    ("gmail", ("gmail", "email")),
    ("splitwise", ("splitwise",)),
    ("todoist", ("todoist", "todo list")),
    ("amazon", ("amazon",)),
)

#: 题面里出现这些词时,harness 会顺手把 phone 的联络人簿(关系 + 邮箱)注入 system
#: message。这些题的最后一步几乎都是"把邮件地址按关系筛一遍",而 round 1 的失败
#: 轨迹里模型要么没查联络人簿,要么只按 `status` 筛完就交卷。
_RELATION_RE = re.compile(
    r"\b(roommates?|coworkers?|co-workers?|friends?|siblings?|brothers?|sisters?|"
    r"parents?|family|relatives?|neighbou?rs?|classmates?|partners?|spouses?|"
    r"children|kids)\b",
    re.IGNORECASE,
)


#: Questions whose answer is a quantity. Used only to normalise a prose answer
#: into the number the official evaluator compares (`'$314.'` -> `314`).
_QUANTITY_GOAL_RE = re.compile(
    r"\bhow\s+(?:much|many|long)\b|\btotal\s+(?:cost|amount|price|number|count)\b"
    r"|\bnumber\s+of\b|\bcount\s+of\b",
    re.IGNORECASE,
)


def _appworld(task: dict, workdir: Path) -> dict | None:
    """The AppWorld adapter, or None for an ordinary terminal task."""
    task_id = task.get("task_id")
    aw = (workdir / "aw").absolute()
    if not (isinstance(task_id, str) and task_id and aw.exists()):
        return None
    goal = str(task.get("goal") or "")
    return {"task_id": task_id, "aw": aw, "question": _goal_asks_question(goal),
            "goal": goal, "tokens": {}, "sigs": {}, "supervisor": {}}


def _goal_asks_question(goal: str) -> bool:
    return "?" in goal or bool(_ANSWER_GOAL_RE.match(goal))


def _appworld_apps(goal: str, limit: int = 3) -> list[str]:
    text = goal.lower()
    apps = [name for name, hints in _APP_HINTS if any(h in text for h in hints)]
    return ["supervisor"] + apps[:limit]


def _appworld_system_prompt(question: bool) -> str:
    ask = (
        "This task asks for an answer: end with the exact concise value (a number, a name, "
        "a title), not a sentence.\n"
        if question else
        "This task asks you to perform actions: the answer text is ignored; the harness "
        "marks the task complete with no answer.\n"
    )
    return (
        "\n\nAPPWORLD ENVIRONMENT (verified in this workdir).\n"
        "- `./aw init --task <id>` initializes the task; call it once.\n"
        "- `./aw action --task <id> --code '<python code>'` runs Python with an `apis` object "
        "that talks to the app APIs.\n"
        "- **All task state lives behind `apis.*`.** The task's file system is virtual: inspect "
        "and change it with `apis.file_system.*`; shell commands like ls/cat/zip see a different "
        "directory and cannot touch the files the task is graded on.\n"
        "- **`aw action` only shows what you `print(...)`.** A read call without an explicit "
        "print returns `Execution successful.` and no data. Always print the result you need.\n"
        "- **You are already logged in.** The harness pre-authenticates every app right after "
        "init and lists the access tokens in the system message. Pass "
        "`access_token=<that app's token>` on every app call. Do NOT call login, signup or "
        "verify_account; the harness also fills a missing or wrong access_token in for you.\n"
        "- Do several API calls in ONE `--code` action (import json, loop over pages, one "
        "script per attempt). One API call per step wastes the budget.\n"
        "- Call every API with **keyword arguments** from the API INDEX, e.g. "
        "`apis.spotify.show_playlist(playlist_id=224)`. Never pass a bare positional "
        "value; AppWorld's generated functions accept keywords only.\n"
        "- Keep the **whole** Python script inside the single-quoted `--code '...'` argument. "
        "Write plain Python: do NOT backslash-escape the double quotes (`\"`) inside that "
        "single-quoted argument, do not append a stray quote after the script, and write "
        "real newlines, not a literal backslash-n.\n"
        "- Put one compound statement per line. A `for`, `if` or `while` cannot follow a "
        "semicolon; use real newlines and indentation for the loop body.\n"
        "- The harness submits for you: do NOT call `apis.supervisor.complete_task`.\n"
        "- Finish by replying with exactly one JSON object: {\"answer\": <value>}. "
        + ask
    )


def _aw_run(shell: str, workdir: Path, aw: Path, task_id: str, code: str,
            timeout: float = 120.0) -> tuple[int, str]:
    """Run a harness-owned AppWorld action, exactly the way the model would."""
    command = " ".join([
        shlex.quote(str(aw)), "action", "--task", shlex.quote(task_id),
        "--code", shlex.quote(code),
    ])
    try:
        proc = subprocess.run(
            [shell, "-c", command], cwd=workdir, capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
        return proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        return -1, f"harness AppWorld call timed out after {timeout:.0f}s"


def _render_docs_block(data: dict, sigs: dict | None = None,
                       cap: int = 12000) -> str:
    """Compact an API index that keeps every name **and its parameter names**.

    Names alone were not enough: the model then spent 4-10 steps fetching one
    `show_api_doc` at a time just to learn argument names, and a model call is
    the expensive unit here.  A signature is the part that changes behaviour
    (keyword vs. positional, required vs. optional); the prose description is
    the part that can be shortened.  `access_token` is deliberately absent --
    the harness injects it -- and `*` marks a required argument.
    """
    sigs = sigs or {}
    entries = [(str(app), docs) for app, docs in data.items() if isinstance(docs, list)]
    if not entries:
        return ""
    header = (
        "\n\nAPPWORLD API INDEX (fetched by the harness after init). Every API name "
        "and parameter is kept; descriptions are shortened. `name(...)` lists the "
        "keyword arguments in order, `*` means required. access_token is filled in "
        "by the harness -- never pass it and never use a positional argument. "
        "Use this index instead of calling show_api_doc/show_app_descriptions.\n"
    )

    def render(desc_cap: int | None) -> str:
        lines = [header.rstrip("\n")]
        for app, docs in entries:
            lines.append(f"[{app}]")
            app_sigs = sigs.get(app) if isinstance(sigs.get(app), dict) else {}
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                name = str(doc.get("name") or "")
                if not name:
                    continue
                meta = app_sigs.get(name)
                params, required = [], set()
                hints = {}
                if isinstance(meta, dict):
                    params = [str(p) for p in (meta.get("params") or [])]
                    required = {str(p) for p in (meta.get("required") or [])}
                    raw_hints = meta.get("hints")
                    if isinstance(raw_hints, dict):
                        hints = {str(k): str(v) for k, v in raw_hints.items()}
                signature = ", ".join(
                    p + ("*" if p in required else "") for p in params)
                label = f"{name}({signature})" if params else name
                notes = [hints[p] for p in params if p in hints]
                if notes:
                    label += "  [" + "; ".join(notes) + "]"
                desc = str(doc.get("description") or "").strip().replace("\n", " ")
                if desc_cap is not None and len(desc) > desc_cap:
                    desc = desc[:desc_cap].rstrip() + "..."
                lines.append(f"  {label}: {desc}" if desc else f"  {label}")
        return "\n".join(lines)

    text = render(None)
    if len(text) <= cap:
        return text
    names_only = render(0)
    if len(names_only) >= cap:
        return names_only[:cap]
    per_item = max(1, (cap - len(names_only)) // max(1, sum(len(d) for _, d in entries)))
    return render(per_item)


#: How many API docs one `aw action` may fetch while the harness builds the
#: signature index.  AppWorld caps interactions per action, so the harness
#: splits the list instead of asking for 91 docs in one call.
_DOC_FETCH_CHUNK = 40


def _appworld_docs_block(shell: str, workdir: Path, aw: Path, task_id: str,
                         goal: str) -> tuple[str, dict]:
    """Fetch API names *and signatures* for the apps this goal mentions.

    Measured in round 0: the model spent 20-31 of 32 steps on
    `show_api_doc(...)` one API at a time (`07b42fd_2`, `229360a_1`), or looped on
    a single doc because `show_api_descriptions` was clipped from 91 items to 40.
    Round 2 traces show the next cost: the index has names but not argument names,
    so the model either fetches docs one by one or calls an API positionally and
    gets `_api_request() takes 0 positional arguments` until the budget is gone.

    The index costs the harness a few processes and zero model calls, and it is
    put in the system message, which `_trim_history` never drops.  The second
    return value is the same signature map, injected into the auth proxy so a
    positional call the model still writes can be bound to the right keyword.
    """
    apps = _appworld_apps(goal, limit=4)
    code = (
        "import json\n"
        f"names = {apps!r}\n"
        "out = {}\n"
        "for name in names:\n"
        "    try:\n"
        "        out[name] = apis.api_docs.show_api_descriptions(app_name=name)\n"
        "    except Exception as exc:\n"
        "        out[name] = str(exc)\n"
        "print(json.dumps(out))\n"
    )
    rc, out = _aw_run(shell, workdir, aw, task_id, code, timeout=90.0)
    if rc != 0:
        return "", {}
    start = out.find("{")
    if start < 0:
        return "", {}
    try:
        data, _ = json.JSONDecoder().raw_decode(out[start:])
    except ValueError:
        return "", {}
    if not isinstance(data, dict):
        return "", {}

    sigs: dict[str, dict] = {}
    for app, docs in data.items():
        if not isinstance(docs, list) or app == "supervisor":
            continue
        names = [str(d.get("name")) for d in docs
                 if isinstance(d, dict) and d.get("name")]
        app_sigs: dict[str, dict] = {}
        for pos in range(0, len(names), _DOC_FETCH_CHUNK):
            chunk = names[pos:pos + _DOC_FETCH_CHUNK]
            fetch = (
                "import json\n"
                f"app = {app!r}\n"
                f"names = {chunk!r}\n"
                "out = {}\n"
                "for name in names:\n"
                "    try:\n"
                "        doc = apis.api_docs.show_api_doc(app_name=app, api_name=name)\n"
                "        params, required, hints = [], [], {}\n"
                "        for p in (doc.get('parameters') or []):\n"
                "            pn = p.get('name')\n"
                "            if not pn or pn == 'access_token':\n"
                "                continue\n"
                "            params.append(pn)\n"
                "            if p.get('required'):\n"
                "                required.append(pn)\n"
                "            parts = []\n"
                "            for c in [str(x) for x in (p.get('constraints') or [])]:\n"
                "                if 'permitted:' in c:\n"
                "                    parts.append('one of ' + c.split('permitted:', 1)[1].strip())\n"
                "                    continue\n"
                "                for op in ('<=', '>='):\n"
                "                    if op in c:\n"
                "                        val = c.split(op, 1)[1].strip().split()[0].strip(\"'\\\"\")\n"
                "                        parts.append(op + val)\n"
                "                        break\n"
                "            if parts:\n"
                "                hints[pn] = pn + ' ' + ', '.join(parts[:2])\n"
                "        out[name] = {'params': params, 'required': required,\n"
                "                     'hints': hints}\n"
                "    except Exception as exc:\n"
                "        out[name] = {'params': [], 'required': [], 'hints': {},\n"
                "                     'error': str(exc)}\n"
                "print(json.dumps(out))\n"
            )
            rc2, out2 = _aw_run(shell, workdir, aw, task_id, fetch, timeout=30.0)
            if rc2 != 0:
                break
            begin = out2.find("{")
            if begin < 0:
                break
            try:
                got, _ = json.JSONDecoder().raw_decode(out2[begin:])
            except ValueError:
                break
            if isinstance(got, dict):
                app_sigs.update({str(k): v for k, v in got.items()
                                 if isinstance(v, dict)})
        if app_sigs:
            sigs[str(app)] = app_sigs
    return _render_docs_block(data, sigs), sigs


def _appworld_answer_value(raw: object) -> object:
    """The value to store in `supervisor.Task.answer`, from the model's answer.

    `AnswerType` accepts a scalar, a list or a dict; the evaluation compares
    `answer_to_text`. A single-key object is almost always the model wrapping the
    value (`{"artist": "Seraphina Dawn"}` vs ground truth `seraphina dawn`), so
    unwrap it. Everything else is passed through untouched.
    """
    value = raw
    if isinstance(value, str):
        text = value.strip()
        if text[:1] == "{":
            try:
                value = json.loads(text)
            except ValueError:
                return value
        else:
            return None if text.lower() in ("", "none", "null") else value
    if isinstance(value, dict):
        if len(value) == 1:
            return next(iter(value.values()))
        for key in ("answer", "value", "result"):
            if key in value and len(value) <= 2:
                return value[key]
    return value


_PLAN_RE = re.compile(
    r"^\s*(?:i\s+(?:need|will|should|have\s+to|must)|need\s+to|let\s+me|first,|"
    r"to\s+(?:answer|do|find|inspect)|the\s+previous|based\s+on\s+the|"
    r"looking\s+at\s+the)\b",
    re.IGNORECASE,
)


def _looks_like_plan(value: object) -> bool:
    """A model that is out of ideas often submits a *plan* as the answer.

    Measured (`27e1026_1`): the final answer was "Need to inspect the output of
    show_song_library, ...", which the AppWorld evaluator compares to a song
    title and scores 0.  That is not a wrong answer; it is the model telling the
    harness what it still intends to do.  Rejecting it costs one step and gives
    the loop a chance to produce the value, while a long title or a list is left
    untouched.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    if _PLAN_RE.match(text):
        return True
    low = text.lower()
    return any(marker in low for marker in (
        "harness says", "previous command", "let me try", "need to check",
        "i need to", "we need to"))


def _numeric_answer(value: str) -> object:
    """A quantity question answered in prose -> the quantity.

    `answer_to_text` compares numbers after a float conversion, so `"$314."`
    never matches ground truth `314`, while the model almost always writes the
    number. Only applied to how much/many/long questions, and only when the
    number is unambiguous (one currency amount, or exactly one number).
    """
    money = re.findall(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)", value)
    numbers = re.findall(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", value)
    raw = money[-1] if money else (numbers[0] if len(numbers) == 1 else None)
    if raw is None:
        return value
    raw = raw.replace(",", "")
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return value


def _appworld_complete(shell: str, workdir: Path, appworld: dict,
                       raw_answer: object) -> dict:
    """Make AppWorld's task record say what the harness decided, once, at the end."""
    value = _appworld_answer_value(raw_answer) if appworld["question"] else None
    if isinstance(value, str) and _QUANTITY_GOAL_RE.search(appworld.get("goal", "")):
        value = _numeric_answer(value)
    if appworld["question"] and value is None:
        # Nothing better than what the model may already have recorded; a second
        # `complete_task()` here would *erase* a correct answer it submitted itself.
        return {"skipped": "question task with no answer from the model"}
    code = ("apis.supervisor.complete_task()" if value is None else
            f"apis.supervisor.complete_task(answer={value!r})")
    try:
        rc, out = _aw_run(shell, workdir, appworld["aw"], appworld["task_id"], code,
                          timeout=90.0)
        if rc != 0:
            time.sleep(1.0)
            rc, out = _aw_run(shell, workdir, appworld["aw"], appworld["task_id"], code,
                              timeout=90.0)
    except Exception as exc:                          # noqa: BLE001
        return {"value": value, "exit": -1, "output": f"{type(exc).__name__}: {exc}"}
    return {"value": value, "exit": rc, "output": out[-600:]}


#: A write action only counts for the submission gate when its own output does
#: not say it failed.  `writes_attempted` alone was not enough: round-4 traces
#: contain tasks whose only "write" was a `Validation error` or a `Traceback`
#: (e.g. `ce359b5_1`, `3c13f5a_1`), and the old gate then accepted a plan answer.
_WRITE_FAILURE_MARKERS = (
    "execution failed", "traceback", "validation error", "not authorized",
    "usage of the following function is not allowed", "aw: error:",
)


def _write_took_effect(exit_code: int, out: str) -> bool:
    if exit_code != 0:
        return False
    text = (out or "").lower()
    return not any(marker in text for marker in _WRITE_FAILURE_MARKERS)


def _observation_digest(observations: list[dict], cap: int = 3600) -> str:
    """A compact, harness-maintained view of what the last commands returned.

    The forced-action turn is deliberately built from this rather than appended
    to the live transcript: by the time the model gives up, the tail of that
    transcript is usually the same failing command repeated several times, and
    putting it in front of the model is what produces another copy of it.
    """
    if not observations:
        return "No command has been run in this task yet."
    lines = ["RECENT OBSERVATIONS (harness-maintained; oldest first):"]
    for obs in observations[-4:]:
        text = str(obs.get("output") or "")
        if len(text) > 1100:
            text = text[:750] + "\n...\n" + text[-250:]
        lines.append(f"[step {obs.get('step')}] command: "
                     f"{str(obs.get('command') or '')[:350]}")
        lines.append("output:")
        lines.append(text)
    return "\n".join(lines)[:cap]


_ACT_NOW_PROMPT = (
    "You are being asked for ACTION, not for a plan or a description. This task "
    "is graded only on state changed through `apis.*`; answer text is ignored. "
    "Reply with exactly one JSON object containing ONE shell command that "
    "performs every remaining state-changing AppWorld API call (create, update, "
    "delete, move, copy, like, follow, rate, review, approve, reject, send, "
    "post, comment) in a single `./aw action --task <id> --code '<python>'` "
    "script. The script may read whatever it still needs first, then it MUST "
    "perform the mutation(s). Write real newlines, use keyword arguments, loop "
    "over all items, and print each call's result. Do not call login and do not "
    "call apis.supervisor.complete_task. If you have verified there is genuinely "
    "nothing to change, reply instead with {\"answer\": \"what you verified\"}."
)


def _ask_for_action(messages: list[dict], appworld: dict,
                    observations: list[dict], trace, step: int,
                    label: str, attempt: int = 0) -> tuple[str, str]:
    """One clean-context model call that must produce a mutating command.

    Returns `(command, reply)`.  The context is the system message (which
    already carries the API index and the auth tokens), the original task, and
    the bounded observation digest -- not the poisoned tail of the loop.
    """
    system = str(messages[0].get("content") or "") if messages else SYSTEM
    task = str(messages[1].get("content") or "{}") if len(messages) > 1 else "{}"
    user = _observation_digest(observations) + "\n\n" + _ACT_NOW_PROMPT
    if attempt:
        user += ("\n\nThe previous attempt still only read data. Reading again "
                 "cannot change the task. Include the mutation calls themselves "
                 "in the SAME script, after the reads.")
    extra = [{"role": "system", "content": system},
             {"role": "user", "content": task},
             {"role": "user", "content": user}]
    t0 = time.time()
    try:
        reply = _complete(extra)
    except Exception as exc:                          # noqa: BLE001
        reply = ""
        trace.write(json.dumps({"step": step, f"{label}_error":
                                f"{type(exc).__name__}: {exc}"}) + "\n")
    trace.write(json.dumps({"step": step, "reply": reply, label: True,
                            "attempt": attempt,
                            "model_ms": int((time.time() - t0) * 1000)}) + "\n")
    try:
        action = _parse(reply)
    except Exception:                                 # noqa: BLE001
        action = {}
    cmd = action.get("command", "") if action else ""
    return (cmd.strip() if isinstance(cmd, str) else ""), reply


# ------------------------------------------------- appworld authentication ---
#
# **Why the harness logs the model in instead of telling it to.**
#
# Counted on the round-1 traces (90 tasks): 46 of the 72 failing tasks contain
# at least one `not authorized` API response, and not one of those runs
# recovered from it. The cause is mechanical:
#
#   * `apis.<app>.login(...)` returns `{"access_token": ...}`, but `aw action`
#     only prints what the code prints. The model calls login without
#     `print(...)`, sees `Execution successful.`, and then passes the password,
#     its email address, `"logged_in"`, `"test"`, `""` or `None` as
#     `access_token` — every one of those shapes occurs in the traces
#     (`27e1026_1`, `287e338_1`, `e7a10f8_1`, `60d0b5b_1`, `d0b1f43_1`).
#   * `file_system.login` is called with `account_name=`/`email=` and phone with
#     an email address, although both want `username` (email for apps, phone
#     number for phone).
#   * the model then repeats one failing command; the repeat breaker refuses to
#     run it, the model re-emits it, and the rest of the 32-step budget goes by
#     (34d9492_1: 23 blocked repeats, 287e338_1: 13, 82e2fac_3: 24).
#
# Authentication is an environment fact, exactly like the API index the harness
# already fetches. So the harness logs in to every app once, after `aw init`,
# and then (a) puts the real tokens in the system message and (b) rewrites each
# `aw action --code` script so a wrong literal token is replaced and a missing
# one is injected by a tiny proxy around `apis`. The model may still call
# login; the harness's token is simply the one that works.

_AUTH_CODE_TEMPLATE = '''\
import json as _hg_json
_hg_tokens = {}
try:
    _hg_pw = apis.supervisor.show_account_passwords()
except Exception:
    _hg_pw = []
_hg_email = __EMAIL__
_hg_phone = __PHONE__
_hg_want = __APPS__
for _hg_rec in (_hg_pw if isinstance(_hg_pw, list) else []):
    if not isinstance(_hg_rec, dict):
        continue
    _hg_app = _hg_rec.get("account_name")
    _hg_pwd = _hg_rec.get("password")
    if not _hg_app or _hg_pwd is None:
        continue
    if _hg_want is not None and _hg_app not in _hg_want:
        continue
    _hg_users = ([_hg_phone, _hg_email] if _hg_app == "phone"
                 else [_hg_email, _hg_phone])
    for _hg_user in _hg_users:
        if not _hg_user:
            continue
        for _hg_kw in ({"username": _hg_user}, {"email": _hg_user},
                       {"account_name": _hg_user}):
            try:
                _hg_res = getattr(apis, _hg_app).login(password=_hg_pwd,
                                                       **_hg_kw)
            except Exception:
                continue
            if isinstance(_hg_res, dict) and _hg_res.get("access_token"):
                _hg_tokens[_hg_app] = _hg_res["access_token"]
                break
        if _hg_app in _hg_tokens:
            break
print("HG_AUTH_TOKENS=" + _hg_json.dumps(_hg_tokens))
'''

#: Prepended to every model-authored `aw action --code`. It does not touch the
#: model's own text: it rebinds the global name `apis` to a proxy that knows the
#: real token for each app and replaces whatever `access_token` the model passed
#: (missing, empty, the password, a stale JWT) with the working one.
#:
#: **No imports.** AppWorld parses the submitted script and rejects a disallowed
#: module before running any of it (`Usage of the following module is not
#: allowed: apis.` in `6104387_2`), so a helper that imports `inspect` would make
#: every rewritten command fail. Signature handling is therefore done with a
#: try/except: if the function does not accept `access_token`, the first call
#: raises `TypeError` before its body runs, and the call is retried without it.
_AUTH_SHIM_TEMPLATE = '''\
try:
    _HG_TOKENS = __TOKENS__
    _HG_SIGS = __SIGS__
    _HG_LAST = [None]
    _HG_READS = ("show_", "search_", "get_", "list_", "find_", "read_",
                 "load_", "check_")
    _HG_REAL_API_DOCS = apis.api_docs
    def _HG_VALIDATION(value):
        try:
            return (isinstance(value, dict)
                    and "validation error" in str(value.get("message", "")).lower())
        except Exception:
            return False
    def _HG_ALIAS(app_name, name, kwargs):
        try:
            sig = (_HG_SIGS.get(app_name) or {}).get(name) or {}
            params = set(sig.get("params") or [])
            required = set(sig.get("required") or [])
            unknown = [k for k in kwargs if k not in params and k != "access_token"]
            missing = [p for p in required if p not in kwargs]
            if len(unknown) == 1 and len(missing) == 1:
                kwargs[missing[0]] = kwargs.pop(unknown[0])
                return True
        except Exception:
            pass
        return False
    class _HGDocsProxy(object):
        # The model often writes `apis.spotify.api_docs.show_api_doc(...)`.
        # Treat that as the global docs with the app already filled in rather
        # than letting an AttributeError cost a step.
        def __init__(self, real, app):
            object.__setattr__(self, "_hg_real", real)
            object.__setattr__(self, "_hg_app", app)
        def __getattr__(self, name):
            attr = getattr(object.__getattribute__(self, "_hg_real"), name)
            if not callable(attr):
                return attr
            app = object.__getattribute__(self, "_hg_app")
            def _hg_doc(*args, **kwargs):
                if not args and "app_name" not in kwargs:
                    kwargs["app_name"] = app
                return attr(*args, **kwargs)
            return _hg_doc
    class _HGAppProxy(object):
        def __init__(self, app, token, name):
            object.__setattr__(self, "_hg_app", app)
            object.__setattr__(self, "_hg_token", token)
            object.__setattr__(self, "_hg_name", name)
        def __getattr__(self, name):
            if name.startswith("_hg_"):
                raise AttributeError(name)
            if name == "api_docs":
                return _HGDocsProxy(
                    _HG_REAL_API_DOCS,
                    object.__getattribute__(self, "_hg_name"))
            attr = getattr(object.__getattribute__(self, "_hg_app"), name)
            if not callable(attr):
                return attr
            token = object.__getattribute__(self, "_hg_token")
            app_name = object.__getattribute__(self, "_hg_name")
            if name == "login":
                def _hg_login(*args, **kwargs):
                    res = attr(*args, **kwargs)
                    if isinstance(res, dict) and res.get("access_token"):
                        _HG_TOKENS[app_name] = res["access_token"]
                        object.__setattr__(self, "_hg_token", res["access_token"])
                    return res
                return _hg_login
            if name in ("get_current_date_and_time", "signup",
                        "send_verification_code", "verify_account",
                        "reset_password"):
                return attr
            def _hg_call(*args, **kwargs):
                call_args = list(args)
                if call_args:
                    try:
                        sig = (_HG_SIGS.get(app_name) or {}).get(name) or {}
                        names = sig.get("params") or []
                    except Exception:
                        names = []
                    mapped = []
                    for i, value in enumerate(call_args):
                        if i < len(names):
                            kwargs.setdefault(names[i], value)
                            mapped.append(True)
                        else:
                            mapped.append(False)
                    if mapped and all(mapped):
                        call_args = []
                    elif any(mapped):
                        call_args = [v for v, ok in zip(call_args, mapped) if not ok]
                kwargs["access_token"] = token
                try:
                    res = attr(*call_args, **kwargs)
                except TypeError as exc:
                    if "access_token" in str(exc):
                        kwargs.pop("access_token", None)
                        res = attr(*call_args, **kwargs)
                    elif (name.startswith(_HG_READS)
                          and _HG_ALIAS(app_name, name, kwargs)):
                        res = attr(*call_args, **kwargs)
                    else:
                        raise
                if _HG_VALIDATION(res):
                    # The local model frequently guesses a parameter name
                    # (`path=` for `file_path`, `email=` for `user_email`).
                    # The call failed validation, so no side effect happened;
                    # when exactly one required name is missing and exactly one
                    # supplied name is unknown, rename it and retry once.
                    if _HG_ALIAS(app_name, name, kwargs):
                        res = attr(*call_args, **kwargs)
                if name.startswith(_HG_READS):
                    _HG_LAST[0] = res
                return res
            return _hg_call
    class _HGAPIsProxy(object):
        def __init__(self, real):
            object.__setattr__(self, "_hg_real", real)
        def __getattr__(self, name):
            if name.startswith("_hg_"):
                raise AttributeError(name)
            attr = getattr(object.__getattribute__(self, "_hg_real"), name)
            if name in _HG_TOKENS:
                return _HGAppProxy(attr, _HG_TOKENS[name], name)
            return attr
    apis = _HGAPIsProxy(apis)
except Exception:
    pass
'''

#: Appended when the model's own script never calls `print`. `aw action` shows
#: only printed text, so a read call without print is invisible: the model sees
#: `Execution successful.` and believes it has the data. 33 of the round-1
#: traces contain at least one such action (`6104387_2` ran six in a row). The
#: proxy above records the last read result; this prints it, bounded, only when
#: the model printed nothing itself. Writes are never recorded, so a mutation
#: cannot be repeated by this.
_AUTH_POSTLUDE = '''\
try:
    if _HG_LAST[0] is not None:
        print("HG_LAST_RESULT=" + repr(_HG_LAST[0])[:4000])
except Exception:
    pass
'''

#: `apis.<app>.<api>(` — used both to associate an `access_token` literal with
#: the app it belongs to and to tell a read-only action from a mutation.
_APP_CALL_RE = re.compile(
    r"apis\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_ACCESS_TOKEN_LITERAL_RE = re.compile(
    r"(access_token\s*=\s*)(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|None)")
_READ_API_PREFIXES = ("show_", "search_", "get_", "list_", "find_", "read_",
                      "load_", "check_")
_SHELL_OPERATORS = {"&&", "||", "|", ";", ">", ">>", "<", "2>", "2>>", "&"}


def _extract_auth_tokens(out: str) -> dict:
    marker = "HG_AUTH_TOKENS="
    pos = out.rfind(marker)
    if pos < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(out[pos + len(marker):].strip())
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str) and v}


def _appworld_auth(shell: str, workdir: Path, aw: Path, task_id: str,
                   supervisor: dict, apps: list[str] | None = None) -> dict:
    """Log in to the task's apps once; return app -> access_token."""
    code = (_AUTH_CODE_TEMPLATE
            .replace("__EMAIL__", repr(str(supervisor.get("email") or "")))
            .replace("__PHONE__", repr(str(supervisor.get("phone_number") or "")))
            .replace("__APPS__", repr(sorted(set(apps))) if apps else "None"))
    rc = -1
    for attempt in range(2):
        rc, out = _aw_run(shell, workdir, aw, task_id, code, timeout=120.0)
        tokens = _extract_auth_tokens(out)
        if tokens:
            return tokens
        if rc == 0 and attempt == 0:
            # The call ran but nothing logged in; retrying the same call cannot
            # change that, and a second `show_account_passwords` costs a step.
            break
    return {}


def _supervisor_from_init(out: str) -> dict:
    """Pull the supervisor's identifiers out of the `aw init` JSON."""
    start = out.find("{")
    if start < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(out[start:])
    except ValueError:
        return {}
    sup = data.get("supervisor") if isinstance(data, dict) else None
    if not isinstance(sup, dict):
        return {}
    return {"email": sup.get("email") or "",
            "phone_number": sup.get("phone_number") or ""}


def _render_contacts(rows: list, cap: int = 6000) -> str:
    lines, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = " ".join(str(x) for x in (row.get("first_name"), row.get("last_name"))
                        if x)
        email = str(row.get("email") or "")
        rel = row.get("relationships")
        if rel is None:
            rel = row.get("relationship")
        if isinstance(rel, list):
            rel = "/".join(str(x) for x in rel if x)
        rel = str(rel or "?")
        key = (name.lower(), email.lower())
        if key in seen:
            continue
        seen.add(key)
        phone = str(row.get("phone_number") or "")
        lines.append(f"  - {rel}: {name} <{email}> {phone}".rstrip())
    text = "\n".join(lines)
    if len(text) > cap:
        text = text[:cap].rstrip() + "\n  ... (more contacts omitted)"
    return text


def _appworld_contacts(shell: str, workdir: Path, aw: Path, task_id: str,
                       token: str) -> str:
    """The phone app's contact book, as relationship -> name/email.

    This is the environment fact behind every "roommates / coworkers /
    siblings / friends" task. In round 1 the model either never queried it
    (`6ea6792_2` rejected every pending request, not the friends' ones) or
    queried one relationship and guessed the rest. One harness call, zero model
    steps, makes it visible.
    """
    if not token:
        return ""
    code = (
        "import json\n"
        f"token = {token!r}\n"
        "rows, seen = [], set()\n"
        "def _hg_add(chunk):\n"
        "    for c in (chunk if isinstance(chunk, list) else []):\n"
        "        if not isinstance(c, dict):\n"
        "            continue\n"
        "        key = (c.get('email'), c.get('phone_number'))\n"
        "        if key in seen:\n"
        "            continue\n"
        "        seen.add(key)\n"
        "        rows.append(c)\n"
        "for page in range(12):\n"
        "    try:\n"
        "        chunk = apis.phone.search_contacts(access_token=token, "
        "page_index=page, page_limit=20)\n"
        "    except Exception:\n"
        "        break\n"
        "    if not isinstance(chunk, list) or not chunk:\n"
        "        break\n"
        "    _hg_add(chunk)\n"
        "    if len(chunk) < 20:\n"
        "        break\n"
        "if not rows:\n"
        "    for rel in ('friend', 'roommate', 'coworker', 'sibling'):\n"
        "        for page in range(10):\n"
        "            try:\n"
        "                chunk = apis.phone.search_contacts(access_token=token, "
        "relationship=rel, page_index=page, page_limit=20)\n"
        "            except Exception:\n"
        "                break\n"
        "            if not isinstance(chunk, list) or not chunk:\n"
        "                break\n"
        "            _hg_add(chunk)\n"
        "            if len(chunk) < 20:\n"
        "                break\n"
        "print(json.dumps(rows))\n"
    )
    rc, out = _aw_run(shell, workdir, aw, task_id, code, timeout=120.0)
    if rc != 0:
        return ""
    start = out.find("[")
    if start < 0:
        return ""
    try:
        data, _ = json.JSONDecoder().raw_decode(out[start:])
    except ValueError:
        return ""
    if not isinstance(data, list):
        return ""
    return _render_contacts(data)


def _appworld_auth_block(tokens: dict, contacts: str) -> str:
    """What the model is told about the login the harness already did."""
    lines = [
        "\n\nAUTHENTICATION (done by the harness, verified).",
        "You are already logged in to every app below. Pass the matching "
        "access_token on every app call. Do NOT call login, signup or "
        "verify_account; do not call apis.supervisor.complete_task. If you omit "
        "access_token or pass a wrong one, the harness fills in the right token "
        "for that app automatically.",
    ]
    for app in sorted(tokens):
        lines.append(f"  {app}: {tokens[app]}")
    if contacts:
        lines.append("")
        lines.append("PHONE CONTACT BOOK (the phone app's relationships, name and "
                     "email; use it to decide who is a roommate / coworker / "
                     "sibling / friend):")
        lines.append(contacts)
    return "\n".join(lines)


#: `--code` as a shell token.  Matched by position rather than by `shlex.split`
#: because the payload itself is the thing that breaks `shlex`.
_CODE_FLAG_RE = re.compile(r"(?:(?<=\s)|^)--code(?:\s+|=)")


def _aw_code_parts(cmd: str) -> tuple[str, str, str] | None:
    """`(head, code, tail)` of an `aw action --code ...` command.

    **Why this exists, and why it is not `shlex.split`.** The local model writes
    a Python string with its own quotes inside the single-quoted `--code '...'`
    argument:

        --code '... if match else 'No match' ...'

    Shell rules say that inner `'` closes the argument, so `shlex.split` hands
    back a truncated script plus three stray tokens, and the harness then runs
    the *mangled* reconstruction (`aw: error: unrecognized arguments: match}"`).
    Measured in round-4 traces: `76f2c72_1`, `22cc237_2`, `6104387_3` and
    `c901732_3` all lost steps to this; the harness's own quote repair never got
    the real payload because the split had already destroyed it.

    For an `aw action` command there is exactly one `--code` argument and it is
    the last thing on the line (`&&`-chained variants are rejected by the
    caller).  So the payload is everything between the opening quote and the
    **last** matching quote.  That recovers the script the model meant, including
    any single/double quotes inside it, and lets `shlex.quote` re-emit a command
    the shell will parse correctly.
    """
    if not isinstance(cmd, str):
        return None
    match = _CODE_FLAG_RE.search(cmd)
    if match is None:
        return None
    head = cmd[:match.start()].rstrip()
    if not head:
        return None
    i = match.end()
    while i < len(cmd) and cmd[i] in " \t":
        i += 1
    if i >= len(cmd):
        return None
    quote = cmd[i]
    if quote in "'\"":
        start = i + 1
        end = -1
        j = len(cmd) - 1
        while j >= start:
            if cmd[j] == quote and (j == 0 or cmd[j - 1] != "\\"):
                end = j
                break
            j -= 1
        if end < 0:
            return None
        return head, cmd[start:end], cmd[end + 1:]
    token = re.match(r"\S+", cmd[i:])
    if token is None:
        return None
    return head, token.group(0), cmd[i + token.end():]


def _aw_code(cmd: str) -> str | None:
    """The Python script of an `aw action --code ...` command, if any.

    A command the harness already re-emitted is valid shell, so `shlex.split`
    recovers it exactly.  A *model-authored* command may contain its own quotes
    inside the single-quoted argument; then `shlex.split` returns a truncated
    script that does not compile, and `_aw_code_parts` is the truthful payload.
    Prefer the shell parse only when it compiles.
    """
    if not isinstance(cmd, str) or "action" not in cmd or "--code" not in cmd:
        return None
    parts = _aw_code_parts(cmd)
    shlex_code = None
    try:
        argv = shlex.split(cmd)
        if "--code" in argv:
            idx = argv.index("--code")
            if idx + 1 < len(argv):
                shlex_code = argv[idx + 1]
    except ValueError:
        shlex_code = None
    if shlex_code is not None and _aw_compiles(shlex_code):
        return shlex_code
    if parts is not None:
        return parts[1]
    return shlex_code


def _aw_compiles(code: str) -> bool:
    try:
        compile(code, "<hg-aw>", "exec")
        return True
    except Exception:                                  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Deterministic repair of the local model's one-line AppWorld scripts.
#
# Measured on round-3 traces: 26 of 90 tasks contain an `aw action --code`
# script that never compiled, and most of them are the same mistake -- a `for`,
# `if` or `while` written after a semicolon:
#
#   import os; files = [...] ; for f in files: mtime = ...; print(f, mtime)
#
# Python rejects a compound statement after `;`, so the script dies in the
# compiler and the model re-emits it verbatim; the repeat breaker then blocks
# the re-emission, the transcript collapses, and the task ends with no mutation.
# Earlier rounds repaired escaped quotes but not this shape.  The splitter below
# rewrites only *non-compiling* code, and every candidate is accepted only after
# `compile()` succeeds, so a valid script is never touched and a semantic error
# still reaches the model as an error.
# ---------------------------------------------------------------------------

_COMPOUND_KEYWORDS = ("for", "if", "while", "with", "try", "except", "elif",
                      "else", "finally", "def", "class")
_COMPOUND_RE = re.compile(r"^\s*(for|if|while|with|try|except|elif|else|finally|def|class)\b")


def _scan_top_level(text: str, sep: str) -> list[str]:
    """Split `text` on `sep` only outside strings, comments and brackets."""
    parts, start, i, depth = [], 0, 0, 0
    n = len(text)
    quote = None
    triple = False
    escaped = False
    while i < n:
        ch = text[i]
        if quote is not None:
            if triple:
                if ch == quote and text[i:i + 3] == quote * 3:
                    quote, triple = None, False
                    i += 3
                    continue
                i += 1
                continue
            if escaped:
                escaped = False
                i += 1
                continue
            if ch == "\\":
                escaped = True
                i += 1
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            if text[i:i + 3] == ch * 3:
                quote, triple = ch, True
                i += 3
                continue
            quote, triple = ch, False
            i += 1
            continue
        if ch == "#":
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if ch in "([{":
            depth += 1
            i += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and ch == sep:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _first_top_level_colon(text: str) -> tuple[str, str] | None:
    parts = _scan_top_level(text, ":")
    if len(parts) <= 1:
        return None
    return parts[0], ":".join(parts[1:])


def _split_statement_line(line: str) -> str:
    """Turn `a; for x in y: b; c` into correctly indented real Python lines."""
    match = re.match(r"^([ \t]*)", line)
    base = match.group(1) if match else ""
    body = line[len(base):].rstrip()
    segments = _scan_top_level(body, ";")
    out: list[str] = []
    stack: list[str] = []

    def emit(segment: str) -> None:
        segment = segment.strip()
        if not segment:
            return
        keyword_match = _COMPOUND_RE.match(segment)
        keyword = keyword_match.group(1) if keyword_match else None
        if keyword in ("else", "elif"):
            for pos in range(len(stack) - 1, -1, -1):
                if stack[pos] in ("if", "elif"):
                    del stack[pos:]
                    break
        elif keyword in ("except", "finally"):
            for pos in range(len(stack) - 1, -1, -1):
                if stack[pos] in ("try", "except", "finally"):
                    del stack[pos:]
                    break
        level = len(stack)
        if keyword in _COMPOUND_KEYWORDS:
            split = _first_top_level_colon(segment)
            if split is not None:
                header, inline_body = split
                out.append(base + "    " * level + header.strip() + ":")
                stack.append(keyword)
                if inline_body.strip():
                    emit(inline_body)
                return
        out.append(base + "    " * level + segment)

    for segment in segments:
        emit(segment)
    return "\n".join(out)


def _multiline_string_lines(code: str) -> list[bool]:
    """Which physical lines begin inside a triple-quoted string."""
    flags: list[bool] = []
    quote = None
    for line in code.split("\n"):
        flags.append(quote is not None)
        i = 0
        while i < len(line):
            ch = line[i]
            if quote is not None:
                if ch == quote and line[i:i + 3] == quote * 3:
                    quote = None
                    i += 3
                    continue
                i += 1
                continue
            if ch in "\"'":
                if line[i:i + 3] == ch * 3:
                    quote = ch
                    i += 3
                    continue
                i += 1
                continue
            if ch == "\\":
                i += 2
                continue
            i += 1
    return flags


def _split_inline_compounds(code: str) -> str:
    lines = code.split("\n")
    in_string = _multiline_string_lines(code)
    changed = False
    result: list[str] = []
    for pos, line in enumerate(lines):
        if not in_string[pos] and ";" in line and len(_scan_top_level(line, ";")) > 1:
            new_line = _split_statement_line(line)
            if new_line != line:
                changed = True
            result.append(new_line)
        else:
            result.append(line)
    return "\n".join(result) if changed else code


def _unescape_newlines_outside_strings(code: str) -> str:
    """A literal `\\n` between statements is a missing real newline, not content.

    Inside a string it is a Python escape and must stay; replacing it there would
    split the literal.  Measured on `cf6abd2_1`: the model finished a one-line
    script with `...Venice")\\nprint(result)`; the backslash-n was outside the
    string, so Python reported "unexpected character after line continuation".
    """
    out: list[str] = []
    i, n = 0, len(code)
    quote = None
    triple = False
    escaped = False
    changed = False
    while i < n:
        ch = code[i]
        if quote is not None:
            out.append(ch)
            if triple:
                if ch == quote and code[i:i + 3] == quote * 3:
                    out.extend([quote, quote])
                    i += 3
                    quote, triple = None, False
                    continue
                i += 1
                continue
            if escaped:
                escaped = False
                i += 1
                continue
            if ch == "\\":
                escaped = True
                i += 1
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            if code[i:i + 3] == ch * 3:
                out.append(ch * 3)
                i += 3
                quote, triple = ch, True
                continue
            out.append(ch)
            quote, triple = ch, False
            i += 1
            continue
        if ch == "\\" and i + 1 < n and code[i + 1] == "n":
            out.append("\n")
            i += 2
            changed = True
            continue
        out.append(ch)
        i += 1
    return "".join(out) if changed else code


def _repair_variants(code: str):
    """One-step repairs; the caller searches a few steps ahead for a compiling one."""
    yield _split_inline_compounds(code)
    yield _unescape_newlines_outside_strings(code)
    yield code.replace('\\"', '"').replace("\\'", "'")
    yield code.replace('["', "['").replace('"]', "']")
    yield _repair_code_quotes(code)
    trimmed = code.rstrip()
    for _ in range(4):
        if not trimmed or trimmed[-1] not in '"\'\\}])':
            break
        trimmed = trimmed[:-1]
        yield trimmed


def _repair_aw_code(code: str) -> str:
    """Repair the local model's AppWorld scripts until one compiles.

    The repair is only accepted when the rewritten script actually compiles, so
    a genuine API/semantic error is never silently changed.  It is applied before
    execution, so a task that would have spent its budget repeating an invalid
    script gets to run its intended API calls instead.
    """
    if not isinstance(code, str) or not code or _aw_compiles(code):
        return code
    seen = {code}
    frontier = [code]
    # Two or three repair steps cover the observed compositions: split a
    # one-line `for`, unescape the quotes inside it, then drop a stray trailing
    # quote/brace.  The frontier is tiny because each step is deterministic.
    for _ in range(3):
        following: list[str] = []
        for candidate in frontier:
            for repaired in _repair_variants(candidate):
                if repaired in seen:
                    continue
                seen.add(repaired)
                if _aw_compiles(repaired):
                    return repaired
                following.append(repaired)
        frontier = following
        if not frontier:
            break
    return code


_PAGE_LIMIT_RE = re.compile(r"\bpage_limit\s*=\s*([0-9]+)")


def _clamp_page_limits(code: str) -> str:
    """AppWorld rejects `page_limit > 20`; a wasted step on that error is common.

    Measured in round-3 traces: 43 `Validation error ... page_limit <= 20`
    responses across 31 tasks.  Every one is the model asking for 50/100/1000
    items in one call; AppWorld then returns no data at all.  Clamping to the
    API's documented maximum keeps the call useful rather than spending a model
    call to learn a limit that is the same for every app.
    """
    if not isinstance(code, str) or "page_limit" not in code:
        return code

    def replace(match: re.Match) -> str:
        value = int(match.group(1))
        if value <= 20:
            return match.group(0)
        return f"page_limit=20"

    return _PAGE_LIMIT_RE.sub(replace, code)


def _augment_appworld_command(cmd: str, tokens: dict,
                              sigs: dict | None = None) -> tuple[str, bool]:
    """Replace the model's token literal and inject the auth shim.

    Returns `(command, changed)`. The rewrite only happens for commands that
    are a single `aw action --code ...` invocation.  The payload is recovered
    with `_aw_code_parts` (not `shlex.split`) so a script that contains its own
    quotes is rebuilt intact -- see the comment there for the measured cost of
    splitting it first.
    """
    if not isinstance(cmd, str):
        return cmd, False
    if "action" not in cmd or "--code" not in cmd:
        return cmd, False
    parts = _aw_code_parts(cmd)
    if parts is None:
        return cmd, False
    head, raw_code, tail = parts
    # A trailing redirect/pipeline (`2>&1`, `| head`) is kept verbatim; it is
    # outside the payload and does not affect how the payload is quoted.
    #
    # But a tail that looks like *Python* (`; apis.spotify.play_music(...)`) is
    # a misplaced closing quote, not shell: `c901732_3` wrote the actual write
    # call after the quote and it ran as `apis.spotify...: command not found`.
    # Put such a tail back inside the script instead of letting the shell run it.
    tail_text = tail.strip()
    if (tail_text.startswith(";")
            and re.search(r"\b(apis\.|print\s*\()", tail_text[1:])):
        raw_code = raw_code.rstrip() + "\n" + tail_text[1:].strip()
        tail = ""
    try:
        head_argv = shlex.split(head)
    except ValueError:
        return cmd, False
    if not any(Path(x).name == "aw" for x in head_argv[:3] if x):
        return cmd, False
    if "action" not in head_argv:
        return cmd, False
    if any(x in _SHELL_OPERATORS for x in head_argv):
        return cmd, False
    # Repair is independent of authentication: a syntax error must be fixed even
    # when the auth probe failed, and the page-limit clamp is a property of the
    # AppWorld API, not of the token.
    code = _repair_aw_code(_clamp_page_limits(raw_code))
    if not tokens and not sigs:
        # Even without auth we must re-emit the command: the payload may contain
        # its own quote characters, and only `shlex.quote` makes that safe for
        # the shell.  Returning the original string here is exactly the round-4
        # bug (`No' 'match`) this extractor exists to fix.
        try:
            return head + " --code " + shlex.quote(code) + tail, True
        except (TypeError, ValueError):
            return cmd, False
    # 1) Replace a literal token with the real token of the nearest preceding app.
    events = [(m.start(), 0, m) for m in _APP_CALL_RE.finditer(code)]
    events += [(m.start(), 1, m) for m in _ACCESS_TOKEN_LITERAL_RE.finditer(code)]
    events.sort(key=lambda e: e[0])
    pieces, last, app, api = [], 0, None, None
    for _, kind, m in events:
        if kind == 0:
            app, api = m.group(1), m.group(2)
            continue
        if app in tokens and api != "login":
            pieces.append(code[last:m.start()])
            pieces.append(m.group(1) + repr(tokens[app]))
            last = m.end()
    if pieces:
        pieces.append(code[last:])
        code = "".join(pieces)
    # 2) A script that prints nothing would give the model no observation at all;
    #    let the proxy surface the last read result instead.
    read_names = [m.group(2) for m in _APP_CALL_RE.finditer(code)]
    if "print(" not in code and any(
            name.startswith(_READ_API_PREFIXES) for name in read_names):
        code = code.rstrip("\n") + "\n" + _AUTH_POSTLUDE
    # 3) Inject the proxy so even a missing / variable token works, and so a
    #    positional argument is bound to the API's real parameter name.
    shim = (_AUTH_SHIM_TEMPLATE
            .replace("__TOKENS__", repr(tokens))
            .replace("__SIGS__", repr(sigs or {}))
            .rstrip("\n"))
    new_code = shim + "\n" + code
    try:
        return head + " --code " + shlex.quote(new_code) + tail, True
    except (TypeError, ValueError):
        return cmd, False


def _looks_read_only_action(cmd: str) -> bool:
    code = _aw_code(cmd)
    if not code:
        return False
    names = [_api_name(m) for m in _APP_CALL_RE.finditer(code)]
    names = [n for a, n in names if a != "supervisor"]
    if not names:
        return False
    return all(name.startswith(_READ_API_PREFIXES) for name in names)


def _api_name(match: re.Match) -> tuple[str, str]:
    return match.group(1), match.group(2)


def _appworld_action_kind(cmd: str) -> str:
    """`write`, `read` or `none`, from the API names in a command.

    Used by the submission gate: an action task whose transcript contains only
    reads has not been attempted, no matter how confidently the model describes
    what it did.  The distinction is lexical on purpose -- executing the script
    to see whether state changed would duplicate its side effects.

    `apis.supervisor.*` is ignored: `complete_task()` is bookkeeping for the
    evaluator, not a change to task state.  Counting it as a write would let a
    model that only calls `complete_task` pass the "did you actually mutate
    anything" gate without mutating anything.
    """
    code = _aw_code(cmd)
    if not code:
        return "none"
    names = [n for a, n in (_api_name(m) for m in _APP_CALL_RE.finditer(code))
             if a != "supervisor"]
    if not names:
        return "none"
    if all(name.startswith(_READ_API_PREFIXES) for name in names):
        return "read"
    return "write"


#: `\"key\"` inside a Python source. The local 9B writes f-strings as
#: `f"Rated song {song_id} ({song[\"title\"]})"`; Python < 3.12 refuses a
#: backslash in an f-string expression, and the model then re-emits the identical
#: failing script (82e2fac_1 sent it 11 times, 302c169_2 13 times). The harness
#: cannot ask the model to fix it and wait: by then the budget is gone. So on a
#: syntax error it rewrites the escaped quotes to the other quote character —
#: which is what the model meant — and runs the repaired script once.
_ESCAPED_QUOTE_RE = re.compile(r'\\(["\'])([^"\'\\]*?)\\\1')


def _repair_code_quotes(code: str) -> str:
    """Turn `\"key\"` into `'key'` — what the model meant inside an f-string."""
    return _ESCAPED_QUOTE_RE.sub(
        lambda m: ("'" + m.group(2) + "'") if m.group(1) == '"'
        else ('"' + m.group(2) + '"'), code)


def _repair_fstring_command(cmd: str) -> tuple[str, bool]:
    """Repair a model `aw action` script in place after a Python syntax error."""
    if not isinstance(cmd, str):
        return cmd, False
    parts = _aw_code_parts(cmd)
    if parts is None:
        return cmd, False
    head, _raw_payload, tail = parts
    code = _aw_code(cmd)
    if code is None:
        return cmd, False
    repaired = _repair_aw_code(code)
    if repaired == code:
        return cmd, False
    try:
        return head + " --code " + shlex.quote(repaired) + tail, True
    except (TypeError, ValueError):
        return cmd, False


def _model_repair_command(goal: str, code: str, error: str,
                          original: str) -> tuple[str, str]:
    """One focused rewrite of a script the harness could not repair locally.

    The ordinary loop feeds the syntax error back as the next observation, but a
    deterministic 9B often re-emits the same one-line `for`/`if` script (21 round-2
    tasks did exactly that until the step cap).  A separate, short prompt that
    contains only the task, the failing script and the compiler error gives the
    model a different context to rewrite it from -- and the rewrite is executed
    in the same step, so a loop that would have cost 25 steps costs one call.
    """
    system = (
        "You repair an AppWorld bash action whose Python `--code` script failed "
        "to compile. Reply with exactly one JSON object: "
        '{"tool": "bash", "command": "<complete ./aw action ... command>"}. '
        "Inside the single-quoted --code argument write real newlines, one "
        "compound statement per line (never `for`/`if` after a semicolon), and "
        "keyword arguments for every API call. No prose outside the JSON.")
    user = (
        f"Task: {goal[:800]}\n\n"
        f"The command failed to compile. Error:\n{error[-1500:]}\n\n"
        f"The script was:\n{code[:4000]}\n\n"
        "Rewrite the same action so it compiles. Keep the API calls; do not add "
        "new exploration.")
    reply = ""
    for attempt in range(2):
        try:
            reply = _complete([{"role": "system", "content": system},
                               {"role": "user", "content": user}])
        except Exception:                              # noqa: BLE001
            return original, reply
        try:
            action = _parse(reply)
        except Exception:                              # noqa: BLE001
            action = {}
        fixed = action.get("command", "")
        # A rewrite that dropped `action` or `--code` is not executable; reject it
        # and ask once more instead of spending the step on `aw: invalid choice`
        # (measured on `34d9492_2`, where the one repair attempt was thrown away).
        if (isinstance(fixed, str) and fixed.strip() and fixed != original
                and _aw_code(fixed) is not None):
            return fixed, reply
        user += ("\n\nYour previous reply was not a complete executable command. "
                 "It must contain `./aw action --task <id> --code '<python>'` with "
                 "a non-empty --code argument. Reply with exactly one JSON object.")
    return original, reply


def _appworld_command_hint(cmd: str, out: str, tokens: dict) -> str:
    """One precise sentence about a failure the model otherwise repeats verbatim."""
    text = (out or "").strip()
    if not text or not isinstance(cmd, str) or "aw action" not in cmd:
        return ""
    if "unexpected character after line continuation" in text:
        return ("\n\n[harness] Your Python code contained a literal backslash-n "
                "instead of a real newline. Put each statement on its own line "
                "inside the single-quoted --code '...' argument.")
    if ("import: command not found" in text
            or "syntax error near unexpected token" in text
            or "unexpected EOF while looking for matching" in text):
        return ("\n\n[harness] Part of your Python script was executed by the "
                "shell, not by AppWorld. Put the WHOLE script inside the "
                "single-quoted --code '...' argument; no code or comments "
                "outside the quotes.")
    if text == "Execution successful." and _looks_read_only_action(cmd):
        return ("\n\n[harness] This action printed nothing: `aw action` only "
                "shows what you `print(...)`. The read itself ran. Re-run it "
                "with print(...) to see the data; it is a read, so that is safe.")
    if "takes 0 positional arguments" in text:
        return ("\n\n[harness] This API accepts keyword arguments only. Use the "
                "signature in the APPWORLD API INDEX, e.g. "
                "`apis.file_system.show_file(file_path=...)`, not a bare value.")
    if "Validation error" in text and "page_limit" in text:
        return ("\n\n[harness] AppWorld caps page_limit at 20; asking for more "
                "returns no data at all. Use page_limit=20 and loop over "
                "page_index if you need every page. The harness also clamps it "
                "from now on.")
    if "Usage of the following function" in text and (
            "builtins.open" in text or "csv." in text):
        return ("\n\n[harness] builtins.open and the csv module are not allowed "
                "inside `aw action`. Write the task's virtual file with "
                "`apis.file_system.create_file(file_path=..., content=...)` or "
                "`apis.file_system.update_file(file_path=..., content=...)`.")
    if "Validation error" in text and "field required" in text:
        return ("\n\n[harness] A required parameter is missing or has the wrong "
                "name. Copy the parameter names from the signature in the API "
                "INDEX (`*` marks required) and pass them as keywords.")
    if "is not available in your account" in text:
        return ("\n\n[harness] That path is not the task's virtual file system. "
                "Find the real one with `apis.file_system.show_directory("
                "\"~/\", \"\", \"all\", False)` (the third argument must be "
                "'all', 'files' or 'directories') and pass the returned path.")
    blocked = re.search(r"Usage of the following function is not allowed: "
                        r"([A-Za-z_.]+)", text)
    if blocked:
        return ("\n\n[harness] `" + blocked.group(1) + "` is forbidden inside "
                "`aw action`. Use the app APIs instead: `apis.file_system.*` for "
                "the virtual file system, and read timestamps from the entry's "
                "`created_at`/`updated_at`/`timestamp` fields rather than "
                "`os.path.getmtime`. Do not re-run the same script.")
    if "NameError" in text:
        return ("\n\n[harness] A variable in the script is undefined -- the "
                "traceback names it. In `for x in items:` the loop variable is "
                "`x`; a name from an earlier comprehension is not available later. "
                "Fix that name and run the corrected script once.")
    if "KeyError" in text or "AttributeError" in text or "TypeError" in text:
        return ("\n\n[harness] The script raised a Python error unrelated to the "
                "API call. Print the object first (`print(type(x), x)`) and use "
                "only the keys/fields the API INDEX response schema lists. Fix "
                "the script; do not re-run it unchanged.")
    if "Syntax error" in text or "invalid syntax" in text:
        return ("\n\n[harness] The script did not compile. Rewrite it as plain "
                "Python: one compound statement per line (no `for`/`if` after a "
                "semicolon), no backslash-escaped quotes, and keyword arguments "
                "for every API call.")
    if "not authorized" in text and tokens:
        return ("\n\n[harness] The access_token was rejected. The harness has "
                "already logged in and fixes a wrong token automatically; omit "
                "access_token or use one from the system message. Do not call "
                "login again.")
    return ""


def _repeat_advice(prior: dict, tokens: dict) -> str:
    """A repeat is a failed experiment, so name the failure, not just the repeat."""
    text = str(prior.get("shown") or "")
    if "not authorized" in text and tokens:
        return ("This command failed with an authentication error; the API never "
                "received a valid token. Omit access_token (the harness injects "
                "the working one for that app) or use a token from the system "
                "message. Do not call login again.")
    if "Syntax error" in text:
        return ("This command failed with a Python syntax error. Rewrite it: keep "
                "every statement on its own line inside the --code '...' quotes, "
                "and avoid backslashes inside f-string expressions.")
    if text.strip() == "Execution successful.":
        return ("This command printed nothing, so you learned nothing from it. "
                "Add print(...) around the value you need (safe for read calls).")
    if "page_limit" in text:
        return ("This call failed only because page_limit was above AppWorld's "
                "maximum of 20. Use page_limit=20 (and paginate) instead.")
    if "not allowed: builtins.open" in text or "not allowed: csv" in text:
        return ("This script used a Python function AppWorld forbids. Write the "
                "virtual file with apis.file_system.create_file/update_file.")
    return "Running it again cannot change the task state."



def _clip_text(out: str, limit: int) -> str:
    if len(out) <= limit:
        return out
    marker = f"\n... [text clipped; original {len(out)} characters] ...\n"
    room = max(0, limit - len(marker))
    head = (room + 1) // 2
    tail = room // 2
    return (out[:head] + marker + (out[-tail:] if tail else ""))[:limit]


def _json_preview(value: list | dict, limit: int) -> str | None:
    """A bounded, valid JSON preview containing whole first/last entries."""
    mapping = isinstance(value, dict)
    items = list(value.items()) if mapping else value
    head, tail = [], []

    def render():
        return json.dumps({"_harness_preview": {
            "total_items": len(items), "omitted_items": len(items) - len(head) - len(tail)},
            "head": dict(head) if mapping else head,
            "tail": dict(tail) if mapping else tail}, ensure_ascii=False, separators=(",", ":"))

    shown = render()
    if len(shown) > limit:
        return None
    front, back = 0, len(items) - 1
    while front <= back:
        added = False
        head.append(items[front])
        candidate = render()
        if len(candidate) <= limit:
            shown = candidate
            front += 1
            added = True
        else:
            head.pop()
        if front <= back:
            tail.insert(0, items[back])
            candidate = render()
            if len(candidate) <= limit:
                shown = candidate
                back -= 1
                added = True
            else:
                tail.pop(0)
        if not added:
            break
    return shown


def _name_index_view(value: list, limit: int) -> str | None:
    """A bounded list view that keeps **every item name**, dropping descriptions.

    The head/tail JSON preview answers "what does this list look like"; for an
    AppWorld API index the question is "which APIs exist", and hiding 51 of 91
    names is what made the model crawl `show_api_doc` one endpoint at a time for
    30 steps. Every item name is the part that must survive; descriptions are
    the part that can be shortened. Returns None when even names do not fit, so
    the caller keeps the ordinary preview for genuinely huge result sets.
    """
    if not isinstance(value, list) or len(value) < 2:
        return None
    if not all(isinstance(item, dict) for item in value):
        return None
    # Documentation index only (items carry a description). Other large lists keep
    # the lossless head/tail preview, where whole records (e.g. song + artists) matter.
    if not all(isinstance(item.get("description"), str) for item in value):
        return None
    for key in ("name", "title"):
        if all(isinstance(item.get(key), str) and item.get(key) for item in value):
            break
    else:
        return None

    def render(desc_cap: int | None) -> str:
        lines = [f"# {len(value)} items; every name kept, other fields omitted"]
        for item in value:
            name = str(item[key])
            desc = str(item.get("description") or "").strip().replace("\n", " ")
            if desc_cap is not None and len(desc) > desc_cap:
                desc = desc[:desc_cap].rstrip() + "..."
            lines.append(f"  {name}: {desc}" if desc else f"  {name}")
        return "\n".join(lines)

    text = render(60)
    if len(text) <= limit:
        return text
    names_only = render(0)
    if len(names_only) > limit:
        return None
    # Spend the room that is left on descriptions instead of dropping them all.
    per_item = (limit - len(names_only)) // max(1, len(value))
    return render(per_item) if per_item > 0 else names_only


def _clip_output(out: str) -> str:
    """Compact leading JSON losslessly before bounding; retain whole list items.

    Non-JSON suffixes (including shell errors) remain visible. The caller saves
    the original separately whenever this view differs from the output.
    """
    if len(out) <= OUTPUT_LIMIT:
        return out
    stripped = out.lstrip()
    try:
        obj, end = json.JSONDecoder().raw_decode(stripped)
    except (ValueError, RecursionError):
        return _clip_text(out, OUTPUT_LIMIT)
    if not isinstance(obj, (dict, list)):
        return _clip_text(out, OUTPUT_LIMIT)
    suffix = stripped[end:]
    compact = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + suffix
    if len(compact) <= OUTPUT_LIMIT:
        return compact
    if isinstance(obj, list):
        index_view = _name_index_view(obj, OUTPUT_LIMIT - len(suffix))
        if index_view is not None:
            return index_view + suffix
    preview = _json_preview(obj, OUTPUT_LIMIT - len(suffix))
    if preview is not None:
        return preview + suffix
    return _clip_text(compact, OUTPUT_LIMIT)


def _shell_runtime() -> tuple[str, str]:
    """Choose once before any task action; never retry side effects in another shell."""
    bash = shutil.which("bash")
    return (bash, "Bash") if bash else ("/bin/sh", "POSIX sh")


def _trim_history(messages: list[dict], budget: int | None = None) -> int:
    """丢最旧的成对消息,直到历史在 `budget`(默认 `CONTEXT_CHAR_BUDGET`)以内。返回丢掉的条数。

    `messages[0]` 是 system,`messages[1]` 是题面,两者永不丢。之后严格成对
    (assistant 动作、user 结果),所以一次删两条,而不是一条 —— 删一条会让"结果"成为
    历史里的第一条,模型会读到一段没有对应动作的输出。

    **为什么需要一个更小的 `budget` 参数。** 字符数不是 token 数,两者的比例随内容在
    2:1 到 4:1 之间浮动:代码、git 对象、base64 每 token 只有一两个字符,英文散文有
    四个。实测(2026-10-04,`local-screen-1`):60000 字符的历史在 `git-leak-recovery` 和
    `regex-chess` 上被 vLLM 判为 "at least 32769 input tokens" —— 字符预算满足了,token
    预算没有。所以唯一可靠的判据是**模型自己说的**那个错误:`_call_openai` 收到它就按
    一半预算再削一次历史并重试,而不是让整道题死掉。
    """
    limit = CONTEXT_CHAR_BUDGET if budget is None else budget
    dropped = 0
    while len(messages) > 4 and \
            sum(len(m.get("content") or "") for m in messages) > limit:
        del messages[2:4]
        dropped += 2
    return dropped


#: 模型拒绝一条 prompt 时的两种说法(vLLM/OpenAI 兼容端点各一种)。判据是模型自己的措辞,
#: 不是我们对 token 的估算 —— 估错了会削掉本该保留的历史,或者留下一个必然失败的长度。
_CONTEXT_ERROR_MARKERS = ("maximum context length", "context length is", "context_length_exceeded",
                          "reduce the length of the input prompt")


def run(task: dict, workdir: Path) -> str:
    """Execute one task. Returns the answer string.

    The trace written to <workdir>/trace.jsonl is what the framework collects
    and hands to the Trainer (INTERFACE.md §4.1).
    """
    trace_path = workdir / "trace.jsonl"
    shell, dialect = _shell_runtime()
    appworld = _appworld(task, workdir)
    system_text = SYSTEM + (
        f"\nShell runtime: {shell} -c ({dialect}). Each command starts a new shell.\n"
        + ("Bash arrays and PIPESTATUS are supported.\n" if dialect == "Bash" else
           "Use POSIX syntax; Bash arrays, PIPESTATUS and [[ ]] are unavailable.\n"))
    if appworld:
        system_text += _appworld_system_prompt(appworld["question"])
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": json.dumps(task)},
    ]

    answer = ""
    submits = 0
    parse_retries = 0
    output_dir = None
    #: 已经执行过的命令 -> 它当时的输出。不是"连续重复检测",是"这条命令已经跑过"
    #: 的完整集合:实测 34d9492_1 把同一条 `show_directory` 跑了 19 次、229360a_1 把
    #: 同一条 `show_api_doc` 跑了 23 次,而它们中间夹着别的命令,所以旧的
    #: `cmd == last_cmd` 判据一次都没拦住。重复执行既不改状态,又把上下文塞满。
    executed: dict[str, dict] = {}
    repeat_resets = 0
    syntax_model_repairs = 0
    docs_injected = False
    aw_initialized = False
    completed = False
    model_completed = False
    #: AppWorld action tasks are graded on state, not on the answer text.  These
    #: count executed *write* API calls (anything not in `_READ_API_PREFIXES`) and
    #: how many of those commands actually reported success.  Measured in
    #: round-3 traces: 19 failing action tasks submitted after reading only, and
    #: every passing action task had executed at least one write.  Measured in
    #: round-4: a write that returns `Validation error` or a `Traceback` is not a
    #: write either, yet the old gate counted it and accepted the plan answer.
    writes_attempted = 0
    writes_effective = 0
    #: How many clean-context "act now" turns this run has spent.  Bounded so a
    #: model that never mutates cannot turn the gate into an endless loop.
    action_forces = 0
    #: The last few command/observation pairs, kept aside for those turns.  The
    #: live history is not reusable: it ends with the loop the model is stuck in.
    last_observations: list[dict] = []

    # **行缓冲,不是默认的 8KB 块缓冲。** 实测(2026-10-04,本地模型跑 `bn-fit-modify`):
    # harness 已经跑了 8 分钟、正在装 pandas,而 `/app/trace.jsonl` 是 0 字节、打开的 fd
    # `pos: 0` —— 一行都还没落盘。代价有三条:
    #   * 实时的轨迹看不见,面板和调试都只能等它结束;
    #   * 被超时杀掉的题会丢掉缓冲区里的尾巴(≤8KB)。一般长跑无所谓(`adaptive-rejection-sampler`
    #     那次写了 1MB,尾巴只占零头),但一道死在第一个命令里的题,尾巴就是**全部**;
    #   * `harness_failed` 的判据是"轨迹为空",于是"被杀"和"从没跑起来"在记录里同形。
    # 行缓冲每写一行就落盘,这三条一起消失,代价是一次 write 系统调用。
    with trace_path.open("w", buffering=1) as trace:
        # **这份 harness 自己声明它的运行时预算。**
        #
        # 平台从外面只能看见 harness 目录的哈希。可哈希相同、行为不同的两份 harness 是
        # 存在的:`HG_AGENT_MAX_STEPS=16` 和 `=32` 跑同一个文件,一条轨迹 16 步就停、
        # 另一条能跑 32 步 —— 实测(2026-10-04,本地 9B 跑 `build-pmars`)32 步需要约
        # 1100 秒,超过题目自带的 900 秒预算,于是它超时;16 步则跑得完。这两次测量的
        # 曲线点上原来没有任何一个字段不同,读者无法分辨"同一外壳跑两次"和"两个预算
        # 各跑一次"。
        #
        # 所以这里声明一次,由平台记到曲线点上(`harnessgrad/identity.py:harness_config`)。
        # 同时声明 model/backend:这个 harness 读 `HG_AGENT_MODEL`,但只有它自己知道读到
        # 了什么,平台的环境变量在别处会因改名而与实际不符(INTERFACE.md §3)。
        identity = {"harness": "loop", "config": {
            "max_steps": MAX_STEPS, "submit_retries": SUBMIT_RETRIES,
            "parse_retries": PARSE_RETRIES, "finish_nudge_steps": FINISH_NUDGE_STEPS,
            "repeat_nudge_limit": REPEAT_NUDGE_LIMIT, "output_limit": OUTPUT_LIMIT,
            "context_chars": CONTEXT_CHAR_BUDGET,
            "shell": shell, "shell_dialect": dialect,
            "max_output_tokens": MAX_OUTPUT_TOKENS}}
        for key, value in (("agent_model", os.environ.get("HG_AGENT_MODEL")),
                           ("agent_backend", _backend())):
            if value:
                identity[key] = value
        trace.write(json.dumps({"harness_identity": identity}) + "\n")
        # **每一步的时间去哪了。** 实测(2026-10-04):本地 9B 跑 `build-pmars`,26 步撞上
        # 题目自带的 900 秒预算被杀。可它是"模型慢"还是"命令慢",记录里看不出来 —— trace
        # 只有步数,没有任何时间字段。这两件事的平台处置完全相反:模型慢要缩步数上限或
        # 换模型,命令慢是这道题自己的成本(装依赖),换模型没用。
        #
        # 所以每步记两个毫秒数:等模型的时间、跑命令的时间。总和不等于墙钟时,差额就是
        # harness 自己的开销,那也是要看得见的东西。
        started = time.time()
        model_s = command_s = 0.0
        trims_before = USAGE["context_trims"]
        for step in range(MAX_STEPS):
            dropped = _trim_history(messages)
            if dropped:
                trace.write(json.dumps({"step": step, "history_dropped": dropped}) + "\n")
            t0 = time.time()
            reply = _complete(messages)
            took = time.time() - t0
            model_s += took
            trace.write(json.dumps({"step": step, "reply": reply,
                                    "model_ms": int(took * 1000)}) + "\n")
            # 这一步的 prompt 被模型拒绝过,harness 因此削短了历史再问。模型这一步看到的
            # 历史比上一步短 —— 不记下来的话,"它为什么换了做法"在 trace 里没有答案。
            if USAGE["context_trims"] > trims_before:
                trace.write(json.dumps({
                    "step": step, "context_trims": USAGE["context_trims"],
                    "history_kept": len(messages)}) + "\n")
                trims_before = USAGE["context_trims"]
            messages.append({"role": "assistant", "content": reply})

            try:
                action = _parse(reply)
            except Exception as exc:
                # 退回给模型,而不是结束整道题。trace 里记下第几次重试,因为平台看不到
                # 容器内部:没有这条记录,一次"读不懂的回复"在记录里和一次"一步就交卷"
                # 长得一样。次数用完仍然是 harness 失败(它确实读不出动作),但那时记录
                # 里已经有 N 次尝试,而不是一次。
                trace.write(json.dumps({"step": step, "parse_error": str(exc),
                                        "parse_retry": parse_retries}) + "\n")
                if parse_retries >= PARSE_RETRIES:
                    break
                parse_retries += 1
                # Keep the full failed reply in the trace, not as an example for
                # the next completion to copy. In particular, token-cut commands
                # should be shortened by the model, never completed by the parser.
                messages[-1]["content"] = "[Unparseable reply omitted; see trace.jsonl.]"
                messages.append({"role": "user", "content": (
                    f"Your reply could not be read as an action: {exc}\n"
                    "Nothing was executed. It may have been cut off by the output token limit.\n"
                    "Send one short command; use a loop/script instead of enumerating calls.\n"
                    f"You have {MAX_STEPS - step - 1} step(s) left.\n"
                    "Reply with EXACTLY ONE JSON object and nothing else, one of:\n"
                    '  {"tool": "bash", "command": "<shell command>"}\n'
                    '  {"answer": "<your final answer>"}\n'
                    'For structured predictions, put the complete JSON object in "answer"; '
                    'preserve the task\'s prediction schema.')})
                continue

            parse_retries = 0
            # Prose or echoed data before a recovered action is not part of the
            # executed command. Store only the parsed action in model history.
            messages[-1]["content"] = json.dumps(action)

            # **修过的地方要留痕。** 重复键的回复被解析成"第一个值放前面"之后,模型看到的
            # 命令和它写的不完全一样(它写了两遍,harness 取了第一遍)。一次被静默改写的
            # 动作和一次原样执行的动作不是同一件事,记录里必须分开。
            repaired = getattr(action, "duplicates", ())
            if repaired:
                trace.write(json.dumps({"step": step,
                                        "duplicate_keys": list(repaired)}) + "\n")

            # **交卷闸门。** 顺序按"它想干什么"判断,而不是按哪个键先出现:
            # 一条回复里同时带 command 和 answer 时,以前"先看 answer"会让那条命令
            # 永远不执行(实测:`{"tool":"bash","command":"cat > /app/run.py …"}` 被当成
            # 空答案,文件从未创建)。所以:有命令就先执行命令;只有**没有**命令时才算交卷。
            cmd = action.get("command", "")
            raw_cmd = cmd
            # **登录由 harness 完成,模型只需要调用 API。** 已经执行过的命令不重跑,所以
            # 改写必须发生在查重之前:同一条模型命令每次得到同一个真实 token,查重键稳定。
            auth_rewritten = False
            if appworld and isinstance(cmd, str) and cmd:
                cmd, auth_rewritten = _augment_appworld_command(
                    cmd, appworld.get("tokens") or {},
                    appworld.get("sigs") or {})
            if not cmd and "answer" in action:
                # **交卷前先确认改动过状态。** 这类题的判分只看 AppWorld 里
                # `models.changed_records`,不看 answer 文本。round-3 轨迹里 19 条
                # 失败的动作题在只做过读调用(甚至只打印了 "Approving request" 而
                # 没有调用 approve API)之后就交了卷,harness 直接 `complete_task()`,
                # 于是检查看到 `set() == {'venmo.PaymentRequest'}`。一条**生效的**写入
                # 都没执行时,不要只是把同一句话退回去 —— 实测模型会原样再答一遍
                # (`ce359b5_1`、`692c77d_1`、`229360a_2`)。这里换一个干净的上下文,
                # 只给它任务、API 索引和最近几条观察,要求它直接产出**执行变更**的命令,
                # 由 harness 在同一步执行。次数有界,之后仍会接受它的回答。
                if (appworld and not appworld["question"]
                        and writes_effective == 0 and action_forces < 2):
                    action_forces += 1
                    trace.write(json.dumps({
                        "step": step, "submit_rejected": "no-state-change",
                        "action_force": action_forces}) + "\n")
                    raw_forced, _forced_reply = _ask_for_action(
                        messages, appworld, last_observations, trace, step,
                        "action_forced", action_forces - 1)
                    if raw_forced:
                        forced, _ = _augment_appworld_command(
                            raw_forced, appworld.get("tokens") or {},
                            appworld.get("sigs") or {})
                        t0 = time.time()
                        proc = subprocess.run(
                            [shell, "-c", forced], cwd=workdir, capture_output=True,
                            text=True, errors="replace")
                        command_s += time.time() - t0
                        f_out = proc.stdout + proc.stderr
                        f_shown = _clip_output(f_out)
                        f_kind = _appworld_action_kind(forced)
                        if f_kind == "write":
                            writes_attempted += 1
                            if _write_took_effect(proc.returncode, f_out):
                                writes_effective += 1
                        executed[forced] = {"exit": proc.returncode, "shown": f_shown,
                                            "step": step, "times": 1}
                        last_observations.append({
                            "step": step, "command": raw_forced[:600],
                            "output": f_shown})
                        trace.write(json.dumps({
                            "step": step, "command": forced, "exit": proc.returncode,
                            "output": f_out, "action_forced": f_kind,
                            "writes_effective": writes_effective}) + "\n")
                        # The turn the model actually took was the forced command,
                        # not the answer it tried to submit -- keep history honest.
                        messages[-1]["content"] = json.dumps(
                            {"tool": "bash", "command": raw_forced})
                        messages.append({"role": "user", "content": (
                            f"exit={proc.returncode}\n{f_shown}\n\n[harness] That "
                            "was the harness's action turn. It executed the command "
                            "above. Run any remaining state-changing call(s), then "
                            'submit {"answer": ...}.')})
                        continue
                    # It would not produce a command even from a clean context.
                    # Accept the answer rather than spending the rest of the budget
                    # on the same refusal.
                    action_forces = 2
                # AppWorld 的交付物是 API 调用序列,不是 workdir 里的文件。
                # `_missing_deliverables` 会把题面里的 `~/backups/spotify.csv` 当成
                # `/backups/spotify.csv`,在一个虚拟文件系统任务里永远判缺失 —— 这道
                # 闸门在 AppWorld 模式下不适用。
                missing = [] if appworld else _missing_deliverables(task, workdir)
                if missing and submits < SUBMIT_RETRIES:
                    submits += 1
                    note = ("You answered but these paths the task asks for do not exist: "
                            + ", ".join(missing)
                            + ". Create them first, then answer again.")
                    trace.write(json.dumps({"step": step, "submit_rejected": missing}) + "\n")
                    messages.append({"role": "user", "content": note})
                    continue
                candidate = _answer_text(action["answer"])
                # A plan is not a completion, for a question task *or* an action
                # task.  Round-4 traces: `6ea6792_1` had already made three
                # effective writes, then answered "I need to accept ... Let me
                # first identify who my roommates and coworkers are" -- the old
                # gate only rejected plans on question tasks, so a half-finished
                # action task was submitted.  No passing task in that round had a
                # plan-like final answer, so rejecting it cannot cost a pass.
                if appworld and _looks_like_plan(candidate):
                    trace.write(json.dumps({
                        "step": step, "answer_rejected": "plan-not-answer",
                        "answer_preview": candidate[:200]}) + "\n")
                    if submits < SUBMIT_RETRIES:
                        submits += 1
                        if appworld["question"]:
                            note = (
                                "That is a plan, not the task's answer. The evaluator "
                                "compares the submitted value with one exact value (a "
                                "title, name, number, list, or object). Reply now with "
                                'exactly one JSON object: {"answer": <the exact value>}. '
                                "Do not describe what you would do next.")
                        else:
                            note = (
                                "That is a plan, not a completed task. This task is "
                                "graded only on the state you change. Execute the "
                                "remaining state-changing call(s) NOW in one "
                                "`./aw action --task ... --code '...'` script, then "
                                'submit {"answer": ...} with what actually changed.')
                        messages.append({"role": "user", "content": note})
                        continue
                    # Do not store a plan as the answer. Fall through to the
                    # finalization turn, which asks the model once more with the
                    # whole transcript and no opportunity to explore.
                    break
                # Only an accepted submission becomes `answer`; a rejected plan
                # must not survive in answer.txt as if it had been the result.
                answer = candidate
                record = {"step": step, "submitted": True, "missing": missing}
                if appworld and aw_initialized:
                    done = _appworld_complete(shell, workdir, appworld, action["answer"])
                    record["appworld_complete"] = done
                    completed = True
                else:
                    record["appworld_complete"] = {"skipped": "task was never initialized"}
                trace.write(json.dumps(record) + "\n")
                break

            # **重复命令不执行。** 已经执行过的命令,其输出就在模型的上下文里;再跑一遍
            # 不改变任何状态,只会把同样的输出再塞一遍。round 0 里 34d9492_1 把同一条
            # `show_directory` 跑了 19 次、229360a_1 把同一条 `show_api_doc` 跑了 23 次,
            # 而旧的"与上一条相同"判据一次都没拦住(中间夹着别的命令)。
            prior = executed.get(cmd) if cmd else None
            if prior is not None:
                prior["times"] += 1
                times = prior["times"]
                advice = _repeat_advice(
                    prior, (appworld.get("tokens") or {}) if appworld else {})
                if times <= REPEAT_NUDGE_LIMIT:
                    body = f"exit={prior['exit']}\n{prior['shown']}"
                    note = (
                        f"\n\n[harness] You already ran this exact command at step "
                        f"{prior['step']}; its output above is unchanged (repeat {times}). "
                        + advice + " Do something different: put the remaining API calls in "
                        "ONE `--code` action, or finish with {\"answer\": ...}.")
                else:
                    body = (f"exit={prior['exit']} (repeated command; same result)\n"
                            + str(prior.get("shown") or "")[:1500])
                    note = ("\n\n[harness] Still the same command; nothing new will come "
                            "from it. " + advice + " Take a different action now, or submit "
                            '{"answer": ...}.')
                trace.write(json.dumps({"step": step, "repeat_blocked": cmd[:200],
                                        "times": times}) + "\n")
                # **A repeat loop is a context failure, not just a bad action.**
                # The model keeps seeing its own last action in the history and
                # re-emits it.  After several blocks, collapse the loop to the
                # system prompt, the task, and one compact account of the last
                # result, so the repeated action is no longer the most recent
                # example to copy.  Two resets are allowed: the first round-4
                # measurement showed the model often abandons command A after
                # the reset and then loops on command B (34d9492_1 spent 24 of
                # its 32 steps after the single reset re-emitting a second read).
                if repeat_resets < 2 and times >= REPEAT_NUDGE_LIMIT + 3:
                    repeat_resets += 1
                    trace.write(json.dumps({
                        "step": step, "repeat_reset": times}) + "\n")
                    messages[:] = [
                        messages[0], messages[1],
                        {"role": "assistant", "content":
                         "[The harness collapsed a repeated-command loop here.]"},
                        {"role": "user", "content": (
                            f"You have run the same command {times} times. Its result "
                            "has not changed and is reproduced below. Do not run it "
                            "again. Take a different action: if you still need data, "
                            "request a different field/API; if you have enough data, "
                            "perform the remaining mutation(s) in one script; if the "
                            "task asks a question, reply with the exact value.\n\n"
                            + str(prior.get("shown") or "")[:2500])},
                    ]
                    continue
                messages.append({"role": "user", "content": body + note})
                if MAX_STEPS - (step + 1) == 1:
                    messages[-1]["content"] += (
                        '\nYou have 1 step left. Reply now with {"answer": ...}.')
                continue

            t0 = time.time()
            proc = subprocess.run(
                # `errors="replace"`: a command may emit arbitrary bytes, and a strict decode
                # raises UnicodeDecodeError inside the harness, which ends the episode and is
                # recorded as a zero for a task nobody finished. Measured 2026-10-15.
                [shell, "-c", cmd], cwd=workdir, capture_output=True, text=True,
                errors="replace"
            )
            took = time.time() - t0
            command_s += took
            out = proc.stdout + proc.stderr
            syntax_repair = ""
            # **一条读不动的回复不能只靠模型自己改。** 模型会把同一条
            # `f"...{d[\"k\"]}..."` 语法错误原样重发十几步(82e2fac_1 11 次、
            # 302c169_2 13 次)。这里在**同一步**内修一次再执行,不花模型调用:
            # 不只是 f-string,任何 AppWorld 脚本的 Python 语法错误都走这条路。
            syntax_error = (
                "Syntax error in line:" in out
                or "SyntaxError" in out
                or "invalid syntax" in out
                or "unterminated string" in out
                or "unexpected character after line continuation" in out
                or "f-string" in out
            )
            if syntax_error:
                repair_kind = ""
                repaired_cmd, changed = _repair_fstring_command(cmd)
                if changed:
                    repair_kind = "python-syntax"
                elif appworld and syntax_model_repairs < 1:
                    syntax_model_repairs += 1
                    tokens = appworld.get("tokens") or {}
                    sigs = appworld.get("sigs") or {}
                    fixed, fix_reply = _model_repair_command(
                        appworld.get("goal") or "", _aw_code(cmd) or cmd,
                        out, cmd)
                    trace.write(json.dumps({
                        "step": step, "model_syntax_repair": fixed != cmd,
                        "reply": fix_reply[:1200]}) + "\n")
                    if fixed != cmd:
                        if appworld:
                            fixed, _ = _augment_appworld_command(fixed, tokens, sigs)
                        repaired_cmd, changed = fixed, True
                        repair_kind = "model-rewrite"
                if changed:
                    trace.write(json.dumps({
                        "step": step, "syntax_repair": repair_kind,
                        "failed_output": out[-500:]}) + "\n")
                    t0 = time.time()
                    proc = subprocess.run(
                        [shell, "-c", repaired_cmd], cwd=workdir,
                        capture_output=True, text=True, errors="replace")
                    r_took = time.time() - t0
                    command_s += r_took
                    took += r_took
                    cmd = repaired_cmd
                    auth_rewritten = True
                    syntax_repair = repair_kind
                    out = proc.stdout + proc.stderr
            record = {"step": step, "command": cmd, "exit": proc.returncode,
                      "shell": shell, "command_ms": int(took * 1000), "output": out}
            if auth_rewritten:
                # The executed command is not byte-identical to the model's reply;
                # say so, and keep the reply for comparison.
                record["raw_command"] = raw_cmd
            if syntax_repair:
                record["syntax_repair"] = syntax_repair
            trace.write(json.dumps(record) + "\n")
            if appworld:
                action_kind = _appworld_action_kind(cmd)
                if action_kind == "write":
                    writes_attempted += 1
                    if _write_took_effect(proc.returncode, out):
                        writes_effective += 1
                if action_kind != "none":
                    trace.write(json.dumps({"step": step, "appworld_action": action_kind,
                                            "writes_attempted": writes_attempted,
                                            "writes_effective": writes_effective}) + "\n")
            shown = _clip_output(out)
            output_note = ""
            if shown != out:
                if output_dir is None:
                    import tempfile
                    output_dir = Path(tempfile.mkdtemp(prefix=".hg-output-", dir=workdir))
                saved = output_dir / f"step-{step}.txt"
                saved.write_text(out)
                relative = saved.relative_to(workdir)
                output_note = (f"\nFull output: {relative} (use shell tools to inspect/filter it).\n"
                               "The view below may omit records; omitted does not mean absent.\n")
                trace.write(json.dumps({"step": step, "output_clipped": len(out),
                                        "shown": len(shown), "output_file": str(relative),
                                        "output_view": shown}) + "\n")
            executed[cmd] = {"exit": proc.returncode, "shown": shown,
                             "step": step, "times": 1}
            if (syntax_repair and isinstance(raw_cmd, str) and raw_cmd
                    and raw_cmd != cmd):
                # A repeat of the model's exact spelling must hit the same record
                # even though the executed command was repaired.
                executed.setdefault(raw_cmd, executed[cmd])
            if appworld and cmd:
                # Kept aside for the clean-context action turn: the live history
                # is not reusable once it has become a repeat loop.
                last_observations.append({
                    "step": step,
                    "command": (raw_cmd if isinstance(raw_cmd, str) and raw_cmd
                                else cmd)[:600],
                    "output": shown})
                del last_observations[:-8]
            if re.search(r"complete_task\s*\(", cmd):
                # The model recorded the completion itself. Do not erase a possibly
                # correct answer with a later extraction attempt.
                model_completed = True
            # **一次"什么都没打印的成功"要当场说清楚。** 33 道 round-1 轨迹里有这种
            # 动作(`apis.spotify.show_song_library(...)` 不带 print),模型看到的只有
            # `Execution successful.`,于是它以为查询成功了、信息在手里,直到交卷才发现
            # 什么都不知道。提示只针对只读调用,以免让模型重复一次写操作。
            hint = _appworld_command_hint(
                cmd, out, (appworld.get("tokens") or {}) if appworld else {})
            if hint:
                trace.write(json.dumps({"step": step, "command_hint": hint.strip()}) + "\n")
            messages.append({"role": "user", "content":
                             f"exit={proc.returncode}{output_note}\n{shown}{hint}"})

            # **init 之后由 harness 把 API 索引取好。** 这一步不花模型步数:一个
            # `aw action` 子进程,零次模型调用,结果写进 system message(永远不会被
            # `_trim_history` 丢掉)。round 0 的 75 条轨迹里,模型有 20-31 步花在逐条
            # `show_api_doc` 上,或者反复读同一条被截断的文档。
            if (appworld and proc.returncode == 0 and cmd
                    and re.search(r"\baw\s+init\b", cmd)):
                aw_initialized = True
                appworld["supervisor"] = _supervisor_from_init(out)
            if (appworld and not docs_injected and aw_initialized and cmd):
                docs_injected = True
                d0 = time.time()
                try:
                    block, sigs = _appworld_docs_block(
                        shell, workdir, appworld["aw"],
                        appworld["task_id"], appworld["goal"])
                except Exception as exc:              # noqa: BLE001
                    block, sigs = "", {}
                    trace.write(json.dumps({"step": step, "appworld_docs_error":
                                            f"{type(exc).__name__}: {exc}"}) + "\n")
                appworld["sigs"] = sigs
                command_s += time.time() - d0
                if block:
                    messages[0]["content"] += block
                    trace.write(json.dumps({"step": step,
                                            "appworld_docs_chars": len(block),
                                            "appworld_sig_apps": sorted(sigs)}) + "\n")

                # **登录一次,之后所有动作都带着能用的 token。** 见 `_appworld_auth`
                # 上方的说明:46/72 条失败轨迹里有 `not authorized`,而模型靠自己是
                # 修不好的(它连 login 的返回值都没打印出来)。
                d0 = time.time()
                try:
                    auth_apps = [a for a in _appworld_apps(appworld["goal"], limit=5)
                                 if a != "supervisor"]
                    tokens = _appworld_auth(shell, workdir, appworld["aw"],
                                            appworld["task_id"],
                                            appworld.get("supervisor") or {},
                                            auth_apps or None)
                except Exception as exc:              # noqa: BLE001
                    tokens = {}
                    trace.write(json.dumps({"step": step, "appworld_auth_error":
                                            f"{type(exc).__name__}: {exc}"}) + "\n")
                appworld["tokens"] = tokens
                trace.write(json.dumps({"step": step, "appworld_auth_apps":
                                        sorted(tokens)}) + "\n")
                if tokens:
                    contacts = ""
                    if _RELATION_RE.search(appworld.get("goal") or ""):
                        try:
                            contacts = _appworld_contacts(
                                shell, workdir, appworld["aw"], appworld["task_id"],
                                tokens.get("phone", ""))
                        except Exception as exc:      # noqa: BLE001
                            trace.write(json.dumps({
                                "step": step, "appworld_contacts_error":
                                f"{type(exc).__name__}: {exc}"}) + "\n")
                    auth_block = _appworld_auth_block(tokens, contacts)
                    messages[0]["content"] += auth_block
                    trace.write(json.dumps({"step": step, "appworld_auth_chars":
                                            len(auth_block),
                                            "appworld_contacts_chars": len(contacts)}) + "\n")
                command_s += time.time() - d0

            left = MAX_STEPS - (step + 1)
            # 只在进入收尾区和最后一步各说一次:每一步都重复同一句话不会更有用,
            # 只会把最近几轮的命令输出挤远。
            if left and (left == FINISH_NUDGE_STEPS or left == 1):
                trace.write(json.dumps({"step": step, "finish_nudge": left,
                                        "writes_attempted": writes_attempted,
                                        "writes_effective": writes_effective}) + "\n")
                if appworld and not appworld["question"] and writes_effective == 0:
                    messages[-1]["content"] += "\n" + (
                        f"You have {left} step(s) left, and no command so far has "
                        "changed any task state -- every call was a read. Stop "
                        "reading. This task is graded on the state you change: put "
                        "the create/update/delete/move/like/follow API call(s) in "
                        "ONE `./aw action --task ... --code '...'` script now, then "
                        'reply with exactly one JSON object: {"answer": <value>}.')
                else:
                    messages[-1]["content"] += "\n" + (
                        f"You have {left} step(s) left. Stop exploring. Do the remaining work "
                        "in one action, then reply with exactly one JSON object: "
                        '{"answer": <value>}. '
                        + ("This task asks a question: the value must be the exact concise answer."
                           if appworld and appworld["question"] else
                           "The harness records the final submission."))

        # **没有交卷也要把 AppWorld 的任务记录收尾。** 动作题(ground truth 是
        # null)只要状态改对,这一步就能把一整道题从 0 分变 1 分;问句题没有答案时
        # 不覆盖模型自己已经写进去的答案(见 `_appworld_complete`)。
        if appworld and not completed:
            final_answer = None
            # 问句题跑满预算、又没自己交过卷:把这**最后一次**模型调用花在收答案上,
            # 而不是让 32 步的探索以 `<<not_given>>` 结束。用模型已经算出来的东西,
            # 不额外执行任何命令。
            if aw_initialized and appworld["question"] and not model_completed:
                prompt = (
                    "You are out of steps. Do not run any more commands. Reply with exactly "
                    "one JSON object containing the final answer to the task: "
                    '{"answer": <the exact concise value: a number, a name, or a title>}. '
                    "If you could not determine it, answer with your best single value.")
                extra = [dict(m) for m in messages]
                dropped = _trim_history(extra)
                if dropped:
                    trace.write(json.dumps({"finalize_answer_dropped": dropped}) + "\n")
                if extra and extra[-1].get("role") == "user":
                    extra[-1]["content"] = str(extra[-1].get("content") or "") + (
                        "\n\n" + prompt)
                else:
                    extra.append({"role": "user", "content": prompt})
                t0 = time.time()
                try:
                    reply = _complete(extra)
                except Exception as exc:              # noqa: BLE001
                    reply = ""
                    trace.write(json.dumps({"finalize_answer_error":
                                            f"{type(exc).__name__}: {exc}"}) + "\n")
                took = time.time() - t0
                model_s += took
                trace.write(json.dumps({"step": MAX_STEPS, "reply": reply,
                                        "finalize_answer": True,
                                        "model_ms": int(took * 1000)}) + "\n")
                try:
                    action = _parse(reply)
                except Exception:
                    action = {}
                if action and not action.get("command") and "answer" in action:
                    final_answer = action["answer"]
            # 动作题跑满预算、却一次**生效的**写调用都没做过:同理,把这最后一次
            # 模型调用花在**执行**上,而不是只写一个 `complete_task()`。round-3 轨迹里
            # `229360a_1`、`34d9492_1`、`29caf6f_1` 都是读满 32 步、状态未动;
            # 交卷文本对判分没有意义,一次写入才有。
            #
            # 这里不再把指令追加到已经中毒的对话尾部:34d9492_1 的最后 24 步是同一条
            # 读命令的重复,模型看到它就会再生产一条读命令(实测 finalize_action 正是
            # 这么浪费掉的)。改用 `_ask_for_action` 的干净上下文,并允许第二次调用 ——
            # 第一次如果仍然只读,它读到的结果会进入下一次的观察摘要,第二次必须写。
            elif (aw_initialized and not appworld["question"]
                  and writes_effective == 0 and not model_completed):
                for attempt in range(2):
                    final_cmd, _final_reply = _ask_for_action(
                        messages, appworld, last_observations, trace,
                        MAX_STEPS, "finalize_action", attempt)
                    if not final_cmd:
                        break
                    raw_final = final_cmd
                    final_cmd, _ = _augment_appworld_command(
                        final_cmd, appworld.get("tokens") or {},
                        appworld.get("sigs") or {})
                    t0 = time.time()
                    proc = subprocess.run(
                        [shell, "-c", final_cmd], cwd=workdir, capture_output=True,
                        text=True, errors="replace")
                    command_s += time.time() - t0
                    out = proc.stdout + proc.stderr
                    shown_final = _clip_output(out)
                    final_kind = _appworld_action_kind(final_cmd)
                    took_effect = False
                    if final_kind == "write":
                        writes_attempted += 1
                        took_effect = _write_took_effect(proc.returncode, out)
                        if took_effect:
                            writes_effective += 1
                    last_observations.append({
                        "step": MAX_STEPS, "command": raw_final[:600],
                        "output": shown_final})
                    trace.write(json.dumps({
                        "step": MAX_STEPS, "command": final_cmd,
                        "raw_command": raw_final, "exit": proc.returncode,
                        "output": out, "finalize_action_executed": True,
                        "appworld_action": final_kind,
                        "writes_attempted": writes_attempted,
                        "writes_effective": writes_effective}) + "\n")
                    if took_effect:
                        break
            d0 = time.time()
            if aw_initialized:
                done = _appworld_complete(shell, workdir, appworld, final_answer)
            else:
                done = {"skipped": "task was never initialized"}
            command_s += time.time() - d0
            trace.write(json.dumps({"finalize": True,
                                    "appworld_complete": done}) + "\n")

        # Where the wall clock went. Separate from `usage` (which counts tokens):
        # a reader asking "was this killed for being slow, and slow at what" gets one
        # line, and a harness that is slow on its own shows up as `total` exceeding
        # model + commands.
        trace.write(json.dumps({"wall_clock_s": {
            "model": round(model_s, 1), "commands": round(command_s, 1),
            "total": round(time.time() - started, 1)}}) + "\n")

        # What the run cost. Kept because a harness that cannot say what it spent
        # cannot be compared with a cheaper one at the same score -- and because
        # `USAGE` is the only place the model calls are counted.
        trace.write(json.dumps({"usage": dict(USAGE)}) + "\n")

    (workdir / "answer.txt").write_text(answer)
    return answer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    task = json.loads(Path(args.task).read_text())
    run(task, Path(args.workdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
