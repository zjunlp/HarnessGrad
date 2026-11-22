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
    """
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if not isinstance(value, str):
        raise ValueError("final answer must be a string or a JSON object")
    if value.lstrip().startswith("{"):
        try:
            json.loads(value)
        except ValueError as exc:
            raise ValueError(f"final answer contains malformed JSON: {exc}") from exc
    return value


#: goal 里形如 `/app/move.txt`、`/app/learned_dag.csv` 的路径。这是**通用**的:
#: 它不针对任何一道题,只看题面自己说了要写到哪个文件。
_PATH_RE = __import__("re").compile(r"/[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,6}\b")


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
    messages = [
        {"role": "system", "content": SYSTEM + (
            f"\nShell runtime: {shell} -c ({dialect}). Each command starts a new shell.\n"
            + ("Bash arrays and PIPESTATUS are supported.\n" if dialect == "Bash" else
               "Use POSIX syntax; Bash arrays, PIPESTATUS and [[ ]] are unavailable.\n"))},
        {"role": "user", "content": json.dumps(task)},
    ]

    answer = ""
    submits = 0
    parse_retries = 0
    repeats = 0
    last_cmd = ""
    output_dir = None
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
            if not cmd and "answer" in action:
                missing = _missing_deliverables(task, workdir)
                if missing and submits < SUBMIT_RETRIES:
                    submits += 1
                    note = ("You answered but these paths the task asks for do not exist: "
                            + ", ".join(missing)
                            + ". Create them first, then answer again.")
                    trace.write(json.dumps({"step": step, "submit_rejected": missing}) + "\n")
                    messages.append({"role": "user", "content": note})
                    continue
                trace.write(json.dumps({"step": step, "submitted": True,
                                        "missing": missing}) + "\n")
                answer = _answer_text(action["answer"])
                break
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
            trace.write(json.dumps({"step": step, "command": cmd,
                                    "exit": proc.returncode,
                                    "shell": shell,
                                    "command_ms": int(took * 1000), "output": out}) + "\n")
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
            messages.append({"role": "user", "content":
                             f"exit={proc.returncode}{output_note}\n{shown}"})

            # **两条提醒,都放在命令跑完之后**,因为它们改变的是"下一步做什么"。
            #
            # 先记进 trace 再发出去:平台看不到容器内部,没有这两条记录,"模型在原地打转"
            # 在一次 32 步的运行里和"模型在认真工作"长得一模一样。
            if cmd and cmd == last_cmd and repeats < REPEAT_NUDGE_LIMIT:
                repeats += 1
                trace.write(json.dumps({"step": step, "repeat_nudge": cmd[:120]}) + "\n")
                messages[-1]["content"] += "\n" + (
                    "That is the same command as the previous step and its output has not "
                    "changed. Do something different, or finish now with "
                    '{"answer": "<your final answer>"} if the work is already done.')
            last_cmd = cmd

            left = MAX_STEPS - (step + 1)
            # 只在进入收尾区和最后一步各说一次:每一步都重复同一句话不会更有用,
            # 只会把最近几轮的命令输出挤远。
            if left and (left == FINISH_NUDGE_STEPS or left == 1):
                trace.write(json.dumps({"step": step, "finish_nudge": left}) + "\n")
                messages[-1]["content"] += "\n" + (
                    f"You have {left} step(s) left. Stop exploring. Create every file the "
                    "task asks for now, then reply with exactly one JSON object: "
                    '{"answer": "<your final answer>"}')

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
