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
import ast
import json
import os
import re
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
#: How many *consecutive* identical commands are tolerated before the loop stops
#: spending steps on them and moves to the answer phase. Measured: the old value
#: (3) aborted 34/90 round-3 and 52/90 round-5 runs at step 5-9, often one repair
#: away from a finished task -- 07b42fd_1 was a single stray `"message"` suffix
#: away from following the artist it had already found. Six leaves room for a
#: resampled reply to escape the loop, and the answer phase still guarantees the
#: run finishes rather than burning the whole budget.
STUCK_REPEAT_LIMIT = int(os.environ.get("HG_AGENT_STUCK_REPEAT_LIMIT", "6"))
#: Temperature used for the completion *after* a repeat was blocked. At 0.0 the
#: 9B re-emits the identical action forever (the context is identical), so the
#: repeat warning alone cannot break the loop; a non-zero sample can.
REPEAT_RESAMPLE_TEMPERATURE = float(
    os.environ.get("HG_AGENT_REPEAT_RESAMPLE_TEMPERATURE", "0.8"))
#: Model calls spent asking for a final answer after the loop ended without one.
#: AppWorld grades the answer recorded by `complete_task`; a run whose budget is
#: spent exploring records `null` and fails a question even when every fact it
#: needed was already printed in the transcript.
FINAL_ANSWER_ATTEMPTS = int(os.environ.get("HG_AGENT_FINAL_ANSWER_ATTEMPTS", "2"))

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


#: AppWorld is not a file-editing task. The graded artifact is the *world state*
#: and the answer recorded by `apis.supervisor.complete_task`, which lives inside
#: the AppWorld service, not in this working directory. The base loop had no idea
#: about that: it only wrote `answer.txt` and left the world un-completed, so the
#: verifier replayed the API log and reported `<<not_given>>` for 76 of 90 tasks
#: in round 0. This block is what makes the loop submit where the check looks.
AW_SYSTEM = """
This task runs in AppWorld. You control the simulated world ONLY through the
`./aw` command in the working directory. The world is ALREADY initialized: do
NOT run `./aw init`.

Run Python against the AppWorld API collection with:

  ./aw action --task {task_id} --code 'print(apis.api_docs.show_app_descriptions())'

Rules for that command:
- Put your code inside single quotes, and use ONLY double quotes inside the code.
- Put `for`, `if`, `while`, `try` statements on their own line, never after a
  semicolon. One-line bodies after a colon are fine.
- Do as much as possible in ONE command. A single command may call many APIs and
  print a compact summary. Do not spend steps reading API documentation one call
  at a time: call `show_api_descriptions(app_name=...)` once, then read the doc
  of the one API you are about to use.
- Every app is ALREADY logged in and the harness supplies the correct
  `access_token` for each `apis.<app>.<api>` call. Do NOT call `login` and do
  NOT paste a token: just call the API. If you need the value explicitly it is
  `tokens["<app>"]`; never write `apis.tokens[...]`, and never `import apis`.
- Every AppWorld API takes KEYWORD arguments only. `show_directory("~/x")`
  fails with "takes 0 positional arguments"; write
  `show_directory(directory_path="~/x")` and name every parameter.
- Every list API is paginated and the default `page_limit` is only 5. Do NOT
  pass `page_index` in a normal call: the harness automatically requests every
  page and returns the union, with `page_limit=20`. A task that says "all" is
  failed by acting on the first page only, so use the full result the harness
  returns. Only pass `page_index` if you deliberately want one specific page.
- `./aw action` prints `Execution failed. Traceback: ...` when the Python you
  sent raised. That is a failure even though the shell exit code is 0. Read the
  last line of the traceback, change the code, and run a DIFFERENT command --
  sending the same failing code again cannot change its result.
- Each `./aw action` runs in a NEW process: a variable you set in one command
  does not exist in the next one. Do the login and the calls that need its
  token in the SAME command, or reuse the `tokens` supplied at the start.
- The shell's files are NOT the AppWorld files. `ls`, `cat`, `open`,
  `os.makedirs` and the like neither see nor change the simulated file system;
  use `apis.file_system.*` for that. `apis` is already imported: do not install
  or import packages to reach an app.

When the world state is correct, reply with EXACTLY ONE JSON object:

  {{"answer": <exact short answer>}}

If the task is a question ("What is ...", "How long ...", "Name the ..."), put
ONLY the exact value (a title, a name, a number) in "answer". If the task only
changes the world, reply with {{"answer": null}}. The harness records the final
completion with AppWorld for you, so you do NOT need to call `complete_task`.
"""

#: First word of an AppWorld task that asks for an answer rather than only a
#: world change. Every state-only instruction in the training split starts with a
#: verb ("Follow", "Move", "Delete", "Add", ...); every task whose ground-truth
#: answer was non-null starts with a wh-word, "Name", or "How". This is a
#: property of the *environment's* task language, not of any one task.
_ANSWER_GOAL_RE = re.compile(
    r"^\s*(?:what|how|which|who|whom|whose|when|where|why|name|"
    r"is|are|was|were|am|do|does|did|have|has|had|can|could|would|should|will)\b",
    re.IGNORECASE,
)


def _goal_needs_answer(goal: object) -> bool:
    """Whether the task's ground-truth answer is a value (vs. `null`)."""
    text = str(goal or "").strip()
    if not text:
        return False
    if re.match(r"^\s*(?:(?:please\s+)?tell me|i (?:need|want) to know)\b",
                text, re.IGNORECASE):
        return True
    return "?" in text or bool(_ANSWER_GOAL_RE.match(text))


def _coerce_answer(value: object) -> str | None:
    """Reduce the model's final answer to the scalar AppWorld compares.

    AppWorld compares `answer_to_text(predicted)` with
    `answer_to_text(ground_truth)`. Models wrap the value in status objects
    (`{"task_completed": true, "description": ...}`) or in single-key objects
    (`{"artist": "..."}`); the first is not an answer at all, the second is.
    """
    if isinstance(value, dict):
        for key in ("answer", "value", "result"):
            if key in value:
                return _coerce_answer(value[key])
        if len(value) == 1:
            return _coerce_answer(next(iter(value.values())))
        return None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (list, tuple)):
        parts = [part for part in (_coerce_answer(item) for item in value)
                 if part is not None]
        if not parts:
            return None
        # AppWorld's answer_to_text joins a list as " | " after sorting its
        # lowercased items; reproduce that spelling so a list answer can match.
        return " | ".join(sorted(parts, key=str.lower))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _coerce_answer(json.loads(text))
            except ValueError:
                pass
        return text or None
    return None


_AW_ACTION_RE = re.compile(
    r"^\s*(?:\./)?aw\s+action\s+--task\s+(\S+)\s+--code\s+(.*)\Z", re.DOTALL)
_AW_INIT_RE = re.compile(r"^\s*(?:\./)?aw\s+init\b")
#: A compound statement may not follow a semicolon *or another compound
#: statement's colon* in Python. The local 9B writes both forms constantly:
#: `...; for song in songs: ...`, and the worse one-liner
#: `for f in files: if f.endswith(".txt"): ...; if m: total += ...`.
#: AppWorld rejects the whole command with `invalid syntax`, the model sees no
#: result, and repeats it until the step budget is gone (76f2c72_* spent every
#: step this way). Expanding the inline suite is a validity repair, not a
#: semantic one: the indentation below is the only reading Python itself gives
#: a one-line suite.
_COMPOUND_HEAD_RE = re.compile(
    r"^\s*(?:async\s+)?(for|if|while|with|try|def|class|elif|else|except|finally|match|case)\b")


_PAGE_LIMIT_RE = re.compile(r"(page_limit\s*=\s*)(\d+)")
_LITERAL_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}
_TRAILING_LITERAL_RE = re.compile(r"([\)\]\w])\s*(\"[^\"\n]*\"|'[^'\n]*')\s*$")


def _unescape_literal_escapes(code: str) -> tuple[str, bool]:
    """Turn a literal ``\\n`` (two characters) outside a string into a newline.

    Measured on this model: it wants a newline between statements but emits the
    *escape text* ``\\n`` in the JSON string, so AppWorld receives one long line
    ending in a backslash and reports `unexpected character after line
    continuation character`. 17/90 round-5 traces hit this, then repeated the
    command until the repeat breaker ended the run. Escapes *inside* a string
    literal are left alone (they are what the model meant); a doubled backslash
    is a literal backslash and is also left alone.
    """
    out: list[str] = []
    i = 0
    quote: str | None = None
    escaped = False
    changed = False
    while i < len(code):
        ch = code[i]
        if quote is not None:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < len(code):
            nxt = code[i + 1]
            if nxt == "\\":                      # keep a literal backslash pair
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            if nxt in _LITERAL_ESCAPES:
                out.append(_LITERAL_ESCAPES[nxt])
                changed = True
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out), changed


_LOGIN_KEYWORD_RE = re.compile(r"(\.login\s*\(\s*)(?:email|phone_number)\s*=")
_PHONE_USERNAME_RE = re.compile(
    r"(apis\.phone\.login\(\s*username\s*=\s*)([\"'])([^\"']*)\2")


def _repair_login(code: str, contact: dict | None) -> tuple[str, list[str]]:
    """Fix the two login spellings the model gets wrong for every app.

    Measured: 52 `Invalid credentials` and 22 `KeyError: 'access_token'` in one
    round. AppWorld's login always takes `username`; the model calls it with
    `email=` / `phone_number=` (Validation error: username field required) and
    passes an email to *phone*, whose username is the account phone number.
    Both are environment conventions, so repair them at the boundary instead of
    spending a step on a certain failure.
    """
    repairs: list[str] = []
    new = _LOGIN_KEYWORD_RE.sub(lambda m: m.group(1) + "username=", code)
    if new != code:
        repairs.append("login-username")
    code = new
    contact = contact or {}
    phone = str(contact.get("phone_number") or "").strip()

    def fix_phone(match: re.Match) -> str:
        value = match.group(3)
        if phone and "@" in value:
            return match.group(1) + match.group(2) + phone + match.group(2)
        return match.group(0)

    new = _PHONE_USERNAME_RE.sub(fix_phone, code)
    if new != code:
        repairs.append("phone-username")
    return new, repairs


def _clamp_page_limit(code: str) -> tuple[str, bool]:
    """Lower an out-of-range ``page_limit`` to AppWorld's maximum of 20.

    49 of 90 round-5 traces asked for page_limit=50/100 and got
    `ensure this value is less than or equal to 20` instead of records; many
    then repeated the same call until the repeat breaker fired. The value the
    model asked for is impossible, so clamping it is a validity repair.
    """
    def repl(match: re.Match) -> str:
        value = int(match.group(2))
        return match.group(1) + str(min(value, 20))
    new = _PAGE_LIMIT_RE.sub(repl, code)
    return new, new != code


#: AppWorld methods take keyword arguments only; these API names are the ones
#: that do not need (and usually must not receive) an `access_token`.
_AW_NO_AUTH_APIS = frozenset({
    "login", "logout", "signup", "send_verification_code", "verify_account",
    "send_password_reset_code", "reset_password",
})

#: One call site inside the model's Python: `apis.<app>.<api>(`.
_AW_CALL_RE = re.compile(r"\bapis\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(")
_AW_KWARG_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=(?!=)")
#: `import apis` inside an action is *always* fatal: the module is banned there
#: and `apis` is already present in the namespace. Measured in 17/90 round-8
#: tasks (84 occurrences); 29caf6f_1 burned six steps on it before the repeat
#: breaker ended the run.
_AW_IMPORT_APIS_RE = re.compile(
    r"(?m)^[ \t]*import[ \t]+apis(?=[ \t]*(?:[;,\n]|$))[ \t]*(?:;[ \t]*)?")
_AW_IMPORT_FROM_RE = re.compile(
    r"(?m)^[ \t]*from[ \t]+apis[ \t]+import[^\n]*(?:\n|$)")
_AW_IMPORT_APIS_COMMA_RE = re.compile(
    r"(?m)^([ \t]*)import[ \t]+apis[ \t]*,[ \t]*")
_AW_INLINE_IMPORT_RE = re.compile(r";[ \t]*import[ \t]+apis\b[ \t]*;?")
#: The system prompt tells the model that a `tokens` mapping is supplied. It was
#: not: `apis.tokens[...]` resolves to "No app named 'tokens'" and `tokens[...]`
#: to a NameError. Both spellings are rewritten to the real mapping.
_AW_TOKEN_ALIAS_RE = re.compile(r"\bapis\.(?:supervisor\.)?tokens\s*\[")


def _strip_apis_import(code: str) -> tuple[str, bool]:
    """Remove `import apis`, which can never work inside `./aw action`."""
    new = _AW_IMPORT_FROM_RE.sub("", code)
    new = _AW_IMPORT_APIS_COMMA_RE.sub(r"\1import ", new)
    new = _AW_IMPORT_APIS_RE.sub("", new)
    new = _AW_INLINE_IMPORT_RE.sub(";", new)
    return new, new != code


def _call_close(text: str, open_at: int) -> int | None:
    """Index of the `)` matching the `(` at `open_at`, ignoring quoted parens."""
    depth = 0
    quote: str | None = None
    escaped = False
    for i in range(open_at, len(text)):
        ch = text[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _split_call_args(args: str) -> list[str]:
    """Split a call's argument list on top-level commas only."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    start = 0
    for i, ch in enumerate(args):
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(args[start:i])
            start = i + 1
    tail = args[start:]
    if tail.strip():
        parts.append(tail)
    return parts


def _kwarg_name(part: str) -> str | None:
    match = _AW_KWARG_RE.match(part)
    return match.group(1) if match else None


def _looks_like_auth_value(part: str) -> bool:
    """A positional argument that is really an access token, not a parameter."""
    value = part.strip()
    if re.fullmatch(r"'[^'\n]*'|\"[^\"\n]*\"", value):
        return value.strip("'\"").startswith("eyJ")
    return bool(re.search(r"(?:^|_)tokens?$|Tokens?$", value)) or value in ("None",)


#: Semantic groups used to match a model's invented keyword to the API's
#: declared parameter. The old code renamed an unknown keyword to the *first*
#: missing parameter, which is not a repair: `show_transactions` lists `query`
#: and `user_email` before `min_created_at`, so `transaction_date_from=` became
#: `query=` and `transaction_date_to=` became `user_email=`; AppWorld rejected
#: the call with "user_email: value is not a valid email address" and the model
#: repeated it until the budget was gone (afc0fce_1/2/3, 45 occurrences).
_KWARG_GROUPS = (
    frozenset({"from", "start", "starting", "since", "after", "min", "minimum",
               "lower", "begin", "beginning"}),
    frozenset({"to", "end", "ending", "until", "before", "max", "maximum",
               "upper", "finish"}),
    frozenset({"query", "search", "q", "term", "keyword", "filter"}),
    frozenset({"name", "title", "label", "text", "note", "caption"}),
    frozenset({"email", "mail", "address"}),
    frozenset({"user", "recipient", "receiver", "friend", "contact", "sender",
               "member", "roommate", "coworker"}),
    frozenset({"path", "directory", "dir", "folder", "file", "filename"}),
    frozenset({"date", "time", "created", "updated", "timestamp", "at", "day"}),
    frozenset({"description", "note", "comment", "message", "content"}),
    frozenset({"amount", "value", "money", "price", "cost", "total"}),
    frozenset({"id", "identifier", "key"}),
    frozenset({"page", "index", "limit", "offset", "size"}),
    frozenset({"rating", "stars", "score", "review"}),
    frozenset({"status", "state", "enabled", "active"}),
)


def _kwarg_tokens(name: str) -> set[str]:
    return {token for token in name.lower().split("_") if token}


def _kwarg_related(left: str, right: str) -> bool:
    return any(left in group and right in group for group in _KWARG_GROUPS)


def _choose_missing_param(name: str, missing: list[str]) -> str | None:
    """The declared parameter a model's invented keyword most likely means.

    Return None when no candidate is close enough: renaming `playlist_name` to
    `is_public` (the first missing parameter) turns a harmless filter into a
    validation error, whereas dropping it leaves the API's own defaults and a
    result the model can still inspect.
    """
    if not missing:
        return None
    tokens = _kwarg_tokens(name)
    # Names/searches are query values, not email addresses. The generic score
    # below cannot see that "user_name" is a name and "user_email" is not.
    if tokens & {"name", "title", "search", "query", "keyword", "term"} \
            and "query" in missing:
        return "query"
    if tokens & {"note", "comment", "message", "description"} \
            and "description" in missing:
        return "description"
    if tokens & {"to", "recipient", "receiver"} and "user_email" in missing \
            and "email" in tokens:
        return "user_email"

    def score(candidate: str) -> float:
        candidate_tokens = _kwarg_tokens(candidate)
        value = 0.0
        for left in tokens:
            if left in candidate_tokens:
                value += 3.0
                continue
            for right in candidate_tokens:
                if _kwarg_related(left, right):
                    value += 1.2
                    break
        # Whole-name similarity catches spellings such as `directory_path`
        # vs `directory_paths` that token matching already handles, and keeps
        # unrelated names from scoring on one generic token.
        import difflib
        value += max(0.0, (difflib.SequenceMatcher(
            None, name.lower(), candidate.lower()).ratio() - 0.6)) * 2.0
        if (("email" in tokens) != ("email" in candidate_tokens)):
            value -= 1.5
        if tokens & {"name", "title"} and "email" in candidate_tokens:
            value -= 1.5
        if (tokens & {"from", "start", "since", "after", "min"}) and \
                (candidate_tokens & {"min", "start", "from", "since", "after",
                                     "created", "begin"}):
            value += 2.5
        if (tokens & {"to", "end", "until", "before", "max"}) and \
                (candidate_tokens & {"max", "end", "to", "until", "before",
                                     "updated", "finish"}):
            value += 2.5
        if name in candidate or candidate in name:
            value += 1.0
        return value

    ranked = sorted(((score(candidate), candidate) for candidate in missing),
                    reverse=True)
    if not ranked or ranked[0][0] < 2.0:
        return None
    return ranked[0][1]


#: One call shape that turns a paginated AppWorld API into the whole collection.
#: Injected only when a paginated call does not specify `page_index`. It is
#: deliberately self-contained (no imports) because it runs inside the action's
#: namespace, and it deduplicates by repr so an API that ignores `page_index`
#: cannot make the result list grow without bound.
_HG_PAGE_HELPER = '''def _hg_fetch_all(_hg_fn, **kw):
    _hg_kw = dict(kw)
    _hg_kw.pop("page_index", None)
    _hg_kw["page_limit"] = 20
    _hg_first = _hg_fn(**_hg_kw)
    if not isinstance(_hg_first, list) or len(_hg_first) < 20:
        return _hg_first
    _hg_out = list(_hg_first)
    _hg_seen = set()
    for _hg_item in _hg_out:
        _hg_seen.add(repr(_hg_item))
    for _hg_page in range(1, 51):
        _hg_kw["page_index"] = _hg_page
        _hg_next = _hg_fn(**_hg_kw)
        if not isinstance(_hg_next, list) or not _hg_next:
            break
        _hg_added = 0
        for _hg_item in _hg_next:
            _hg_key = repr(_hg_item)
            if _hg_key not in _hg_seen:
                _hg_seen.add(_hg_key)
                _hg_out.append(_hg_item)
                _hg_added += 1
        if not _hg_added:
            break
    return _hg_out
'''


def _ensure_result_printed(code: str) -> tuple[str, bool]:
    """Print the value of a command that would otherwise write no output.

    The model repeatedly runs `apis.file_system.show_directory(...)` as a bare
    expression: the world call succeeds, `./aw action` prints only
    "Execution successful.", and the model has no data. It then repeats the
    same command until the repeat breaker or the step cap ends the run
    (3c13f5a_1 spent 27 steps this way, 34d9492_1 and e7a10f8_2 similar).
    Wrapping the last bare API call (or printing the last assigned name) is a
    transport repair: the command's effect is unchanged and the observation is
    the value the model asked for.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return code, False
    has_print = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "print" for node in ast.walk(tree))
    if has_print or not tree.body:
        return code, False
    last = tree.body[-1]
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
        # Replace only the expression's source span. Replacing whole lines
        # would delete earlier statements when the model wrote the action and
        # a preceding assignment on one physical line.
        lines = code.splitlines(keepends=True)
        start = sum(len(line) for line in lines[:last.value.lineno - 1]) \
            + last.value.col_offset
        end = sum(len(line) for line in lines[:last.value.end_lineno - 1]) \
            + last.value.end_col_offset
        return (code[:start] + "print(" + code[start:end] + ")"
                + code[end:], True)
    if isinstance(last, ast.Assign) and len(last.targets) == 1 \
            and isinstance(last.targets[0], ast.Name) \
            and isinstance(last.value, ast.Call):
        return code + "\nprint(" + last.targets[0].id + ")", True
    return code, False


def _repair_aw_calls(code: str, tokens: dict[str, str],
                     signature=None) -> tuple[str, list[str]]:
    """Make the model's AppWorld calls match the API's declared signature.

    The model regularly (a) passes positional arguments to methods that the
    AppWorld proxy exposes as keyword-only (`takes 0 positional arguments`,
    29 occurrences in 10 round-8 tasks), (b) attaches `password=`/`email=` to a
    data call, and (c) re-types or invents an `access_token`. The harness owns
    the authenticated session and the API documentation, so it fixes all three
    at the boundary instead of spending the step budget on errors that cannot
    succeed.

    `signature(app, api)` returns the declared parameter names (or None). It is
    called once per (app, api) and cached by the caller.
    """
    repairs: list[str] = []
    if not code:
        return code, repairs
    starts = [m.start() for m in _AW_CALL_RE.finditer(code)]
    # Right-to-left so rewriting one call does not invalidate later indices.
    for at in reversed(starts):
        match = _AW_CALL_RE.match(code, at)
        if match is None:
            continue
        app, api = match.group(1), match.group(2)
        open_at = match.end() - 1
        if api == "login" and app in tokens:
            # The harness already holds a live token for this app. A login the
            # model writes is either unnecessary or uses the conventions this
            # model gets wrong every time (7d7fbf6_2 spent seven steps on
            # `Invalid credentials`). Answer it from the session instead.
            close_at = _call_close(code, open_at)
            if close_at is None:
                continue
            code = (code[:at] + "{\"access_token\": _hg_tokens[%r]}" % app
                    + code[close_at + 1:])
            repairs.append(f"login:{app}")
            continue
        authenticated = bool(tokens) and app in tokens \
            and api not in _AW_NO_AUTH_APIS
        if not authenticated:
            continue
        close_at = _call_close(code, open_at)
        if close_at is None:
            continue
        parts = _split_call_args(code[open_at + 1:close_at])
        params = signature(app, api) if signature is not None else None
        changed = False
        if params:
            names = [name for name in params if name]
            name_set = set(names)
            order = [name for name in names if name != "access_token"]
            mapped: list[str] = []
            index = 0
            for part in parts:
                if _kwarg_name(part) is not None:
                    mapped.append(part)
                    continue
                if _looks_like_auth_value(part) or index >= len(order):
                    # It is a token, or there is no parameter left to name it.
                    changed = True
                    continue
                mapped.append(f"{order[index]}={part.strip()}")
                index += 1
                changed = True
            parts = mapped
            # Duplicate keywords are a syntax error in Python (`keyword
            # argument repeated`), and the local 9B produces them by appending
            # the same paging arguments twice (`page_index=0, page_limit=20,
            # page_index=0, page_limit=20`). Keep the first spelling.
            deduped: list[str] = []
            seen_names: set[str] = set()
            for part in parts:
                name = _kwarg_name(part)
                if name is not None:
                    if name in seen_names:
                        changed = True
                        continue
                    seen_names.add(name)
                deduped.append(part)
            parts = deduped
            # An unknown keyword is usually the *right argument under the wrong
            # name* (`show_directory(path=...)`; the parameter is
            # `directory_path`). Match it semantically against the parameters
            # the call is missing; if nothing matches, drop it rather than
            # renaming it to the first missing parameter. The old wholesale
            # rename turned `transaction_date_from=` into `query=` and
            # `transaction_date_to=` into `user_email=`, and AppWorld rejected
            # the call on every attempt (afc0fce_1/2/3).
            present = {_kwarg_name(part) for part in parts
                       if _kwarg_name(part)}
            missing = [name for name in order if name not in present]
            rebuilt: list[str] = []
            for part in parts:
                name = _kwarg_name(part)
                if name is None or name in name_set or name == "access_token":
                    rebuilt.append(part)
                    continue
                if name in ("password", "username"):
                    changed = True
                    continue
                if name == "email" and "email" not in name_set:
                    changed = True
                    continue
                target = _choose_missing_param(name, missing)
                if target is None:
                    # No safe rename. Dropping the filter is less destructive
                    # than assigning its value to an unrelated parameter.
                    changed = True
                    continue
                missing.remove(target)
                rebuilt.append(re.sub(r"^(\s*)" + re.escape(name) + r"(\s*=)",
                                      lambda m: m.group(1) + target + m.group(2),
                                      part, count=1))
                changed = True
            parts = rebuilt
        else:
            filtered = [part for part in parts
                        if _kwarg_name(part) not in ("password", "email",
                                                     "username")]
            if len(filtered) != len(parts):
                changed = True
            parts = filtered
        # Deduplicate again after the signature repair: with no signature the
        # `else` branch above still sees the model's repeated paging keywords.
        deduped = []
        seen_names = set()
        for part in parts:
            name = _kwarg_name(part)
            if name is not None:
                if name in seen_names:
                    changed = True
                    continue
                seen_names.add(name)
            deduped.append(part)
        parts = deduped
        # The harness's own token is the only credential the world knows to be
        # valid for this app and this user. Replace whatever the model wrote
        # (a stale copy, a password, a `login(...)` dict) with it.
        #
        # Paginated APIs are the other half of "all": the default page size is
        # 5 and the local model routinely calls `show_song_library()` without a
        # page argument, then reasons over 5 of 20 songs (229360a_1,
        # 6ea6792_1/2/3). When the call does not say `page_index`, fetch every
        # page here. The helper is injected into the action; explicit manual
        # pagination is left alone so a page-by-page loop cannot be duplicated.
        paginated = bool(params) and "page_limit" in name_set \
            and "page_index" in name_set
        present_names = {_kwarg_name(part) for part in parts if _kwarg_name(part)}
        auto_page = False
        if paginated:
            if "page_limit" not in present_names:
                parts.append("page_limit=20")
                changed = True
            if "page_index" not in present_names \
                    and api.startswith(_READ_API_PREFIXES):
                auto_page = True
        token_part = f"access_token=_hg_tokens[{app!r}]"
        existing = [part.strip() for part in parts
                    if _kwarg_name(part) == "access_token"]
        if existing != [token_part]:
            changed = True
        parts = [part for part in parts
                 if _kwarg_name(part) != "access_token"]
        parts.insert(0, token_part)
        if changed or auto_page:
            rebuilt = ",".join(part.strip() for part in parts if part.strip())
            if auto_page:
                code = (code[:at] + f"_hg_fetch_all(apis.{app}.{api}, "
                        + rebuilt + ")" + code[close_at + 1:])
                repairs.append("page-all")
            else:
                code = code[:open_at + 1] + rebuilt + code[close_at:]
            repairs.append(f"call:{api}")
    return code, repairs


def _compiles(code: str) -> bool:
    try:
        compile(code, "<appworld-action>", "exec")
        return True
    except (SyntaxError, ValueError, OverflowError):
        return False


def _strip_trailing_literal(code: str) -> str:
    """Drop a bare string expression stuck to the end of a call.

    `apis.spotify.follow_artist(artist_id=14, access_token=token)"message"` is
    a real round-3 failure: valid call, one stray suffix, Python syntax error,
    then the model repeated it until the run was aborted -- one edit away from
    passing. Only applied when the code does not compile.
    """
    match = _TRAILING_LITERAL_RE.search(code)
    if match:
        return code[:match.start() + 1].rstrip()
    return code


def _longest_compiling_prefix(code: str) -> str | None:
    """The longest prefix that compiles, if it retains most of the script.

    Used only after every targeted repair failed: a trailing half-written
    statement otherwise makes Python reject the *whole* command, so the login
    and the reads before it produce no observation at all. Keeping the part
    that compiles turns "no progress" into partial progress.
    """
    boundaries = [m.start() for m in re.finditer(r"[\n;]", code)]
    for at in reversed(boundaries):
        prefix = code[:at].rstrip()
        if len(prefix) < max(40, int(len(code) * 0.5)):
            continue
        if _compiles(prefix):
            return prefix
    return None


def _split_top_level(text: str, sep: str = ";") -> list[str]:
    """Split on `sep` only outside strings and brackets."""
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for i, ch in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == sep:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _top_level_colon(text: str) -> int | None:
    """The first `:` outside strings/brackets (an inline suite's separator)."""
    depth = 0
    quote: str | None = None
    escaped = False
    for i, ch in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == ":":
            return i
    return None


def _starts_compound(text: str) -> bool:
    return bool(_COMPOUND_HEAD_RE.match(text))


def _expand_suite_legacy(text: str, indent: str) -> list[str]:
    """The old line-suite expander, kept as a compile-checked fallback.

    `for f in files: if ok: a; b; if m: c` is a single line with no dedent
    marker. This version treats everything after a compound colon as that
    compound's body, so the trailing `if m` is nested inside `if ok`. That is
    wrong for the common "loop body; if guard: action; final statement" shape:
    the final statement (often the only `print`) ends up inside the guard and
    the model sees an empty, successful command, so it repeats it forever
    (e3d6c94_1, steps 17-31). The version below fixes the common case; this one
    is only used if the new expansion does not compile.
    """
    parts = [part.strip() for part in _split_top_level(text)]
    lines: list[str] = []
    for index, part in enumerate(parts):
        if not part:
            continue
        if _starts_compound(part) and _top_level_colon(part) is not None:
            rest = ";".join(p.strip() for p in parts[index + 1:] if p.strip())
            combined = part + (";" + rest if rest else "")
            lines.extend(_expand_stmt_legacy(combined, indent))
            return lines
        lines.append(indent + part)
    return lines


def _expand_stmt_legacy(stmt: str, indent: str) -> list[str]:
    stmt = stmt.strip()
    if not stmt:
        return []
    colon = _top_level_colon(stmt)
    if colon is not None and _starts_compound(stmt):
        header = stmt[:colon + 1]
        rest = stmt[colon + 1:].strip()
        if not rest:
            return [indent + header]
        return [indent + header] + _expand_suite_legacy(rest, indent + "    ")
    return _expand_suite_legacy(stmt, indent)


def _expand_parts(parts: list[str], indent: str, outer: bool) -> list[str]:
    """Expand semicolon-separated parts at one indentation level.

    The outermost compound owns the rest of the physical line (so a `for`
    body really is the whole body). A *nested* compound owns only the next
    statement; later statements return to the parent body. That is the reading
    that makes both of the model's common malformed shapes useful:

        for r in rows: x = f(r); if x: append(x); print(out)
        for f in files: if ok(f): remove(f); count += 1

    In the first, `print` runs once per row rather than only for matching rows;
    in the second, `count += 1` is not silently moved inside the `if`.
    """
    lines: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if not part:
            i += 1
            continue
        colon = _top_level_colon(part)
        if colon is not None and _starts_compound(part):
            header = part[:colon + 1]
            inline = part[colon + 1:].strip()
            if outer:
                rest = ([inline] if inline else []) + \
                    [p.strip() for p in parts[i + 1:] if p.strip()]
                lines.append(indent + header)
                if not rest:
                    lines.append(indent + "    pass")
                else:
                    lines.extend(_expand_parts(rest, indent + "    ", outer=False))
                return lines
            lines.append(indent + header)
            if inline:
                lines.extend(_expand_parts([inline], indent + "    ",
                                           outer=False))
            else:
                lines.append(indent + "    pass")
            i += 1
            continue
        lines.append(indent + part)
        i += 1
    return lines


def _expand_suite(text: str, indent: str) -> list[str]:
    parts = [part.strip() for part in _split_top_level(text)]
    return _expand_parts(parts, indent, outer=True)


def _expand_stmt(stmt: str, indent: str) -> list[str]:
    stmt = stmt.strip()
    if not stmt:
        return []
    return _expand_suite(stmt, indent)


_WRAPPED_CODE_LITERAL_RE = re.compile(r"'([^'\n]*;[^'\n]*=[^'\n]*)'[ \t]*;?[ \t]*")


def _unwrap_wrapped_code(code: str) -> tuple[str, bool]:
    """Remove quote characters the model put around *code fragments*.

    The local 9B sometimes wraps each fragment of a multi-statement script in
    its own quotes:
        ... --code 'year = str(dt.year)'; recs = [...]; ... print(...)'
    The first quote pair closes after `dt.year)` and the next one opens just
    before `; recs`, so the middle is one long string literal that happens to
    contain Python. That is not valid code; removing the accidental quote pair
    restores the statements the model meant. Only tried on code that does not
    compile, and only for a quoted span that contains both `;` and `=`, so a
    real string literal is left alone.
    """
    new = _WRAPPED_CODE_LITERAL_RE.sub(lambda m: m.group(1) + "\n", code)
    return new, new != code


def _remove_stray_triple_quote(code: str) -> tuple[str, bool]:
    """Drop one unmatched triple-quote delimiter if that is what parses.

    The local 9B appends a stray triple quote after a call
    (`show_note(...)` then `\"\"\"` then `.get(...)`), producing
    `unterminated triple-quoted string literal` for a step that was otherwise
    correct. Only a single delimiter is removed at a time, and only when the
    result compiles, so a genuine triple-quoted literal is untouched.
    """
    for token in ('\"\"\"', "'''"):
        start = code.find(token)
        while start != -1:
            candidate = code[:start] + code[start + 3:]
            if _compiles(candidate):
                return candidate, True
            start = code.find(token, start + 1)
    return code, False


def _expand_inline_suites(code: str) -> tuple[str, bool]:
    """Turn model one-liners (`for ...: if ...: ...`) into valid indented code.

    Try the parent-aware expansion first. Fall back to the old right-nesting
    expansion only when the new one does not compile, so a shape it does not
    understand (e.g. one-line try/except) is not made worse.
    """
    def build(expander) -> tuple[str, bool]:
        lines: list[str] = []
        changed = False
        for line in code.split("\n"):
            stripped = line.lstrip(" \t")
            indent = line[:len(line) - len(stripped)]
            if not stripped:
                lines.append(line)
                continue
            expanded = expander(stripped, indent)
            if len(expanded) != 1 or expanded[0] != line:
                changed = True
            lines.extend(expanded)
        return "\n".join(lines), changed

    candidate, changed = build(_expand_stmt)
    if not changed:
        return candidate, False
    if _compiles(candidate):
        return candidate, True
    legacy, legacy_changed = build(_expand_stmt_legacy)
    if legacy_changed and _compiles(legacy):
        return legacy, True
    return candidate, True


def _fix_one_fstring(code: str, start: int) -> tuple[str, int, bool]:
    """Repair same-quote literals inside one f-string expression.

    Python < 3.12 ends an f-string at the first matching quote, so
    `f"{now.strftime("%Y-%m-%d")}"` is a syntax error even though the intent is
    unambiguous. Inside `{...}` a string literal that uses the f-string's own
    quote is respelled with the other quote -- a validity repair only.
    """
    chars = list(code)
    quote = code[start]
    other = "'" if quote == '"' else '"'
    triple = code[start:start + 3] == quote * 3
    i = start + (3 if triple else 1)
    depth = 0
    inner: str | None = None
    inner_repaired = False
    changed = False
    while i < len(code):
        ch = code[i]
        if inner is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == inner:
                if inner_repaired:
                    chars[i] = other
                inner = None
                inner_repaired = False
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
        elif depth == 0:
            if triple:
                if code[i:i + 3] == quote * 3:
                    return "".join(chars), i + 3, changed
            elif ch == quote:
                return "".join(chars), i + 1, changed
        elif ch in "'\"":
            if ch == quote:
                chars[i] = other
                inner = quote
                inner_repaired = True
                changed = True
            else:
                inner = ch
                inner_repaired = False
        i += 1
    return "".join(chars), len(code), changed


def _fix_fstring_quotes(code: str) -> tuple[str, bool]:
    """Scan Python code for f-strings and repair same-quote expressions."""
    out: list[str] = []
    changed = False
    i = 0
    while i < len(code):
        ch = code[i]
        if ch in "'\"":
            before = i - 1
            prefix = ""
            while before >= 0 and code[before] in "rRfFbBuU":
                prefix = code[before] + prefix
                before -= 1
            is_fstring = ("f" in prefix.lower()
                          and (before < 0
                               or not (code[before].isalnum()
                                       or code[before] == "_")))
            if is_fstring:
                new_code, end, did = _fix_one_fstring(code, i)
                if did:
                    code = new_code
                    changed = True
                out.append(code[i:end])
                i = end
                continue
            # Ordinary string: copy it whole so an `f"` inside it is ignored.
            quote = ch
            triple = code[i:i + 3] == quote * 3
            k = i + (3 if triple else 1)
            while k < len(code):
                if code[k] == "\\":
                    k += 2
                    continue
                if triple:
                    if code[k:k + 3] == quote * 3:
                        k += 3
                        break
                elif code[k] == quote:
                    k += 1
                    break
                k += 1
            out.append(code[i:k])
            i = k
            continue
        out.append(ch)
        i += 1
    return "".join(out), changed


def _sanitize_aw_code(code: str,
                      contact: dict | None = None,
                      tokens: dict[str, str] | None = None,
                      signature=None) -> tuple[str, tuple[str, ...]]:
    """Make the common model-generated AppWorld code actually execute.

    Every step here is a repair measured in the round-3/round-8 traces, not a
    guess: a literal ``\\n`` outside a string (17 traces), a page_limit above
    AppWorld's maximum (49 traces), the `; for ...` compound form, an escaped
    quote inside an f-string, a stray trailing string literal, the login
    spellings that cannot succeed, `import apis` (17 tasks), the phantom
    `tokens[...]` mapping the system prompt advertises (20 tasks), and calls
    whose arguments do not match the documented signature (10 tasks).
    """
    if not code:
        return code, ()
    repairs: list[str] = []

    unescaped, changed = _unescape_literal_escapes(code)
    if changed:
        repairs.append("literal-escape")
    code = unescaped

    # A second escaping layer (`\"` in the raw reply) still reaches the world
    # verbatim for some model replies. It is always a validity problem for the
    # action interpreter (`f\"{...}\"`, `s[\"title\"]`), and the model's intent
    # is the single-quote spelling. Collapse it *before* the f-string repair:
    # otherwise the scanner correctly sees an escaped quote and leaves the
    # invalid f-string alone, and the whole tail is later truncated away.
    if '\\"' in code:
        code = code.replace('\\"', "'")
        repairs.append("escaped-quote")

    # Python < 3.12 ends an f-string at the first matching quote. Repair the
    # same-quote literals inside its `{...}` expressions (measured on
    # `f"Now: {now.strftime("%Y-%m-%d")}"`, which cost afc0fce_1 a step).
    # Applied regardless of the *local* interpreter's version: the action runs
    # inside the task image (Python 3.11 here), whose f-string grammar is older
    # than the host's. It only rewrites same-quote literals inside `{...}`, so a
    # string with no such literal is untouched.
    fixed, changed = _fix_fstring_quotes(code)
    if changed:
        repairs.append("f-string-quote")
    code = fixed

    stripped, changed = _strip_apis_import(code)
    if changed:
        repairs.append("apis-import")
    code = stripped

    aliased = _AW_TOKEN_ALIAS_RE.sub("tokens[", code)
    if aliased != code:
        repairs.append("token-alias")
    code = aliased

    code, login_repairs = _repair_login(code, contact)
    repairs.extend(login_repairs)

    clamped, changed = _clamp_page_limit(code)
    if changed:
        repairs.append("page_limit-clamped")
    code = clamped

    if not _compiles(code):
        unwrapped, changed = _unwrap_wrapped_code(code)
        if changed and _compiles(_expand_inline_suites(unwrapped)[0]):
            code = unwrapped
            repairs.append("unwrapped-code-quotes")

    # Expand model one-liners before deciding the code is unrepairable. Doing
    # this first matters: the old path fed a nested one-liner to
    # `_longest_compiling_prefix`, which kept the prefix and silently dropped
    # the loop body -- valid Python that does the wrong thing. Only touch a
    # script that does not already compile; expanding a valid script would let
    # a `for ...:` inside a multi-line string be rewritten.
    if not _compiles(code):
        expanded, changed = _expand_inline_suites(code)
        if changed:
            repairs.append("inline-suite")
        code = expanded

    if not _compiles(code):
        without_triple, changed = _remove_stray_triple_quote(code)
        if changed:
            code = without_triple
            repairs.append("stray-triple-quote")

    if not _compiles(code):
        stripped_literal = _strip_trailing_literal(code)
        if stripped_literal != code and _compiles(stripped_literal):
            code = stripped_literal
            repairs.append("trailing-literal")
        else:
            prefix = _longest_compiling_prefix(code)
            if prefix is not None:
                code = prefix
                repairs.append("truncated-tail")

    tokens = tokens or {}
    code, call_repairs = _repair_aw_calls(code, tokens, signature)
    repairs.extend(call_repairs)
    printed, changed = _ensure_result_printed(code)
    if changed:
        code = printed
        repairs.append("auto-print")
    if "page-all" in repairs:
        code = _HG_PAGE_HELPER + "\n" + code
    if tokens and re.search(r"\btokens\b|\b_hg_tokens\b", code):
        # `tokens[...]` is referenced by both the system prompt and the call
        # repair, so the mapping has to exist inside the action's namespace.
        # The injected credential uses `_hg_tokens` so a model that assigns its
        # own `tokens` dict cannot shadow it.
        code = ("_hg_tokens = " + json.dumps(tokens, ensure_ascii=False) + "\n"
                "tokens = _hg_tokens\n" + code)
        repairs.append("tokens-defined")
    return code, tuple(repairs)


def _extract_aw_code(rest: str) -> tuple[str | None, str]:
    """Recover the raw Python code from the tail of an `aw action` command.

    The model writes the code inside shell quotes. When the code itself contains
    an apostrophe the shell quote closes early and bash reports `unexpected EOF`
    (35 of 90 round-0 traces hit a syntax/argument error of this shape). Because
    the harness has the reply text BEFORE the shell sees it, it can take the text
    inside those quotes and hand it straight to `./aw` as one argv element -- no
    shell, no escaping needed. Anything after the closing quote (a pipe, a
    redirect) is kept as a suffix.
    """
    rest = rest.strip()
    if not rest:
        return None, ""
    quote = rest[0]
    if quote not in "'\"":
        # No quoting: the whole tail is the code (single argv token).
        return rest, ""
    candidates = [i for i, ch in enumerate(rest)
                  if ch == quote and i > 0 and rest[i - 1] != "\\"]
    if not candidates:
        return None, ""

    def _plain_python(text: str) -> bool:
        text = text.strip()
        if not text or text.startswith(("}", ")", "]", "|", ">", "2>")):
            return False
        if re.match(r"^(?:echo|printf|ls|cat|grep|head|tail|wc|sed|awk|"
                    r"sort|uniq|cut|tr|python3?|true|false|cd|rm|mkdir|cp|mv|"
                    r"chmod|export|jq)\b", text):
            return False
        return bool(re.search(
            r"[=()\[\]{}]|\b(?:print|for|if|while|import|from|def|class|"
            r"return|assert|with|try|except)\b", text))

    # A closing quote followed by `;` is ambiguous: it can be a real shell
    # separator (`; echo done`) or -- far more often on this model -- a stray
    # quote in the middle of a Python script (`'...year)'; selected = [...]`).
    # Classify the tail, then prefer a candidate whose code actually compiles.
    # The old "first quote followed by `;` is a suffix" rule sent Python to
    # bash: e3d6c94_1/2 and ce359b5_2 died with `syntax error near unexpected
    # token` before a single API call in the script ran.
    parsed: list[tuple[int, str, str, str]] = []
    for index in candidates:
        code = rest[1:index]
        tail = rest[index + 1:]
        stripped_tail = tail.strip()
        if not stripped_tail:
            kind = "none"
        elif stripped_tail.startswith(("|", ">", "2>", "&&", "||")):
            kind = "shell"
        elif stripped_tail.startswith(";"):
            after = stripped_tail[1:].strip()
            if not after or after.startswith(("}", ")", "]")):
                kind = "junk"
            elif _plain_python(after):
                kind = "python"
            else:
                kind = "shell"
        elif stripped_tail.startswith(("}", ")", "]")):
            kind = "junk"
        else:
            kind = "python"
        parsed.append((index, code, tail, kind))

    def _extract_compiles(text: str) -> bool:
        # The candidate may still carry the model's `"` layer; collapse it for
        # the parse decision so a valid span is not rejected for escaping alone.
        text = text.replace('\\"', "'")
        return _compiles(text) or _compiles(_expand_inline_suites(text)[0])

    def _joined(code: str, tail: str) -> str:
        tail = tail.strip()
        if not tail:
            return code
        # A quote boundary is a fragment boundary: the model closed the quote
        # and started the next statement. Join with a newline, not `;`, so a
        # `for` body on the left does not silently swallow the right-hand
        # statements (e3d6c94_1) -- at top level the meaning is the same.
        if tail.startswith(";"):
            return code + "\n" + tail[1:].lstrip()
        return code + "\n" + tail

    # 1. A candidate that is the whole command and compiles.
    for _index, code, tail, kind in parsed:
        if kind == "none" and _extract_compiles(code):
            return code, ""
    # 2. A candidate that absorbs the following Python (and compiles once the
    #    one-line suites are expanded).
    for _index, code, tail, kind in reversed(parsed):
        if kind != "python":
            continue
        combined = _joined(code, tail)
        if _extract_compiles(combined):
            return combined, ""
    # 3. A genuine shell suffix, with code that compiles.
    for _index, code, tail, kind in reversed(parsed):
        if kind == "shell" and _extract_compiles(code):
            suffix = tail
            if suffix.strip().count(quote) % 2 == 1 and suffix.strip().endswith(quote):
                suffix = suffix.rstrip()[:-1]
            return code, suffix
    # 4. Junk after the quote (`}`/`)`) is dropped rather than handed to bash.
    for _index, code, _tail, kind in reversed(parsed):
        if kind == "junk" and _extract_compiles(code):
            return code, ""
    # 5. The model wrapped *several* fragments in their own quotes
    #    (`'code1' code2 'code3'`). No single candidate compiles, but removing
    #    the accidental quote pairs from the whole tail restores all three
    #    fragments (e3d6c94_1). Only when the result parses.
    whole = rest[1:]
    if whole.endswith(quote):
        whole = whole[:-1]
    unwrapped, did = _unwrap_wrapped_code(whole)
    if did and _extract_compiles(unwrapped):
        return unwrapped, ""
    # 5b. One stray quote in the middle of an otherwise complete script
    #     (`print(x)'; print(y)`): try removing each one individually. This is
    #     still only reached when every whole-span candidate failed to parse.
    for at, ch in enumerate(whole):
        if ch != quote or (at > 0 and whole[at - 1] == "\\"):
            continue
        candidate = whole[:at] + whole[at + 1:]
        if _extract_compiles(candidate):
            return candidate, ""
    # 6. Last resort: keep the longest span, exactly as the model wrote it.
    index = candidates[-1]
    return rest[1:index], rest[index + 1:]


def _rewrite_complete_task_none(code: str) -> str:
    """Force the answer argument of `complete_task(...)` to `null`.

    State-only AppWorld tasks have a ground-truth answer of `null`. The model
    almost always records a prose description ("Followed Grace Harrison ..."),
    which then fails the one check that compares answers even when every state
    check passed (07b42fd_1, 302c169_1/3, 771d8fc_1, 7d7fbf6_1/2/3, ...).
    The harness knows the task language, so it enforces the convention at the
    boundary instead of hoping the model guesses it.
    """
    at = code.find("complete_task")
    if at < 0:
        return code
    open_at = code.find("(", at)
    if open_at < 0:
        return code
    depth = 0
    quote: str | None = None
    escaped = False
    for i in range(open_at, len(code)):
        ch = code[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return code[:open_at + 1] + "answer=None" + code[i:]
    return code


def _run_aw(workdir: Path, argv: list[str], suffix: str = "",
            shell: str = "/bin/bash") -> tuple[int, str]:
    """Run one `aw` invocation without a shell; optionally feed a pipe/redirect."""
    try:
        proc = subprocess.run(
            argv, cwd=workdir, capture_output=True, text=True,
            errors="replace", timeout=180)
        out = proc.stdout + proc.stderr
        rc = proc.returncode
    except Exception as exc:                      # noqa: BLE001
        return 1, f"harness could not run {argv[0]}: {exc}"
    suffix = suffix.strip()
    if suffix:
        # The model asked for `| grep ...`, `> file`, etc. Run only the suffix,
        # with the aw output on stdin, so the shell never re-parses the code.
        script = suffix if suffix.startswith(("|", ">", "2>")) else (
            " " + suffix if suffix.startswith((";", "&&", "||")) else "| " + suffix)
        try:
            piped = subprocess.run(
                [shell, "-c", f"cat <<'HG_AW_EOF' {script}\n" + out +
                 "\nHG_AW_EOF"],
                cwd=workdir, capture_output=True, text=True, errors="replace",
                timeout=60)
            out = piped.stdout + piped.stderr
            rc = piped.returncode
        except Exception as exc:                  # noqa: BLE001
            out += f"\n[harness: suffix failed: {exc}]"
    return rc, out


_AW_FAILURE_MARKERS = (
    "execution failed",
    "traceback (most recent call last)",
    "invalid credentials",
    "validation error",
    "field required",
    "ensure this value",
)


def _aw_failed(out: str) -> bool:
    """Whether `./aw action` reported a *semantic* failure inside the world.

    AppWorld's client exits 0 even when the Python the model sent raised, so the
    observation said `exit=0` for a command that changed nothing. The model read
    that as success and repeated it (34-52 aborted runs per round). The harness
    reports the effect, not the transport code: an error inside the world is a
    failed step.
    """
    lowered = (out or "").lower()
    return any(marker in lowered for marker in _AW_FAILURE_MARKERS)


#: API names that only *read* (or authenticate). Everything else the model can
#: call changes the world, which is what a state task is graded on.
_READ_API_PREFIXES = (
    "show", "get", "search", "list", "find", "count", "check", "is_", "has_",
    "api_docs", "login", "logout", "signup", "send_verification", "verify_",
    "reset_password", "send_password_reset",
)
_AW_ANY_CALL_RE = re.compile(r"apis\.[A-Za-z_]\w*\.([A-Za-z_]\w*)\s*\(")


def _has_mutation(code: str) -> bool:
    """Whether this action calls an AppWorld API that can change the world.

    Used only as a submission guard for state tasks: a model that has issued
    nothing but `show_*`/`search_*` calls cannot have done the task, and in
    round 8 five state tasks submitted exactly like that (7d7fbf6_1 read the
    directory and answered; 692c77d_2 read the reviews and answered).
    """
    for match in _AW_ANY_CALL_RE.finditer(code or ""):
        if not match.group(1).startswith(_READ_API_PREFIXES):
            return True
    return False


def _aw_completion_recorded(code: str, out: str, rc: int) -> bool:
    """Whether this action really recorded `complete_task` in the world.

    The old test was "the output has no error and the *command text* mentions
    complete_task". That made merely reading the `supervisor.complete_task` API
    documentation count as a completed task: 6104387_2 printed the doc, the
    harness wrote `already-recorded`, never called `complete_task`, and the
    verifier reported `<<not_given>>` for a task that had also exported its
    file. A completion needs an actual call and a positive confirmation.
    """
    if rc != 0 or "complete_task(" not in (code or ""):
        return False
    if _aw_failed(out):
        return False
    lowered = (out or "").lower()
    return ("task completed" in lowered
            or "execution successful" in lowered
            or "marked the active task complete" in lowered
            or "already complete" in lowered)


#: Which apps a task is likely to need. Used only to decide which logins to
#: perform up front; a miss costs nothing (the model still has every password
#: and the login convention in its prompt).
_APP_HINTS = {
    "spotify": ("spotify", "song", "album", "playlist", "artist", "music",
                "genre", "listen", "follow"),
    "venmo": ("venmo", "payment", "transaction", "money", "paid", "pay",
              "reimburse", "like"),
    "phone": ("phone", "text message", "voice message", "alarm", "sms",
              "contact", "spam"),
    "file_system": ("file", "directory", "folder", "photograph", "photo",
                    "~/", "bill", "export"),
    "simple_note": ("note", "bucket list"),
    "todoist": ("todoist", "task", "project"),
    "amazon": ("amazon", "order", "product", "buy", "purchase", "cart"),
    "splitwise": ("splitwise", "expense", "settle", "owe", "split"),
    "gmail": ("gmail", "email", "inbox"),
}


def _goal_apps(goal: object) -> list[str]:
    """Apps whose vocabulary appears in the goal, matched on whole words.

    Whole-word matching matters: a substring test matched "owe" inside
    "followers" and would have logged the model into Splitwise for a Spotify
    task. A miss only means the harness does not pre-login; the model still has
    every password and the login convention.
    """
    text = str(goal or "").lower()
    hits: list[str] = []
    for app, words in _APP_HINTS.items():
        for word in words:
            if word.isalpha():
                if re.search(r"\b" + re.escape(word) + r"s?\b", text):
                    hits.append(app)
                    break
            elif word in text:
                hits.append(app)
                break
    return hits


def _appworld_init(workdir: Path, task_id: str, trace,
                   goal: object = "") -> tuple[bool, str, dict, dict, dict]:
    """Initialize the world and front-load the read-only setup.

    `show_account_passwords` and `show_app_descriptions` are pure reads that the
    model used to spend the first two steps on in every round-0 trace. Fetching
    them here gives the model the credentials and the app list in its first
    prompt, so the 32-step budget is spent on the task instead of on setup.

    Every app that has a `login` API is logged into here, and the resulting
    tokens are returned so the action sanitiser can supply `access_token`
    itself. The old version guessed the apps from goal vocabulary and left the
    rest to the model -- which then copied a 300-character JWT by hand or
    reused a password as a token. Measured: 77 `not authorized` observations
    and 16 tasks that reference `tokens[...]` in round 8, 32 of them failing.
    """
    code, out = _run_aw(workdir, ["./aw", "init", "--task", task_id])
    ok = code == 0 and "task_id" in out
    trace.write(json.dumps({"appworld_init": {"exit": code, "ok": ok,
                                              "output": out[:2000]}}) + "\n")
    if not ok:
        return False, out, {}, {}, {}
    supervisor: dict = {}
    try:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                supervisor = json.loads(line).get("supervisor") or {}
                break
    except (ValueError, AttributeError):
        supervisor = {}
    boot_code = _BOOTSTRAP_CODE % (json.dumps(supervisor),
                                   json.dumps(_goal_apps(goal)))
    boot_rc, boot = _run_aw(workdir, ["./aw", "action", "--task", task_id,
                                      "--code", boot_code])
    trace.write(json.dumps({"appworld_bootstrap": {"exit": boot_rc,
                                                   "output": boot[:4000]}}) + "\n")
    tokens: dict = {}
    if boot_rc == 0 and boot.strip():
        out = out.rstrip() + "\n" + boot
        tokens = _bootstrap_tokens(boot)
    catalog = _appworld_catalog(workdir, task_id, trace) if boot_rc == 0 else {}
    return True, out, supervisor, tokens, catalog


def _bootstrap_tokens(payload: str) -> dict[str, str]:
    """The app -> access_token mapping printed by `_BOOTSTRAP_CODE`."""
    for line in reversed(payload.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        raw = data.get("tokens") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            return {str(app): token for app, token in raw.items()
                    if isinstance(token, str) and token}
    return {}


#: One server-side read that returns every app's API names and descriptions.
#: The model otherwise spends its first 1-10 steps doing this one app at a time
#: (`show_api_descriptions` then `show_api_doc` for each candidate); in round 10
#: 19 of the 56 failures spent four or more steps on documentation alone. The
#: whole catalog is one action that costs the harness no model call, so the
#: model can start at the API it needs. Every section is guarded: one app
#: without documentation must not lose the rest.
_CATALOG_CODE = """import json as _hj
_apps_out = {}
try:
    _apps = apis.api_docs.show_app_descriptions()
except Exception:
    _apps = []
for _app in _apps:
    if not isinstance(_app, dict):
        continue
    _n = _app.get("name")
    if not _n or _n in ("api_docs", "supervisor"):
        continue
    try:
        _apis = apis.api_docs.show_api_descriptions(app_name=_n)
    except Exception:
        continue
    _rows = []
    for _a in _apis:
        if not isinstance(_a, dict):
            continue
        _name = _a.get("name")
        if not _name:
            continue
        _rows.append([str(_name), str(_a.get("description") or "")[:90]])
    _apps_out[_n] = _rows
_contacts = []
try:
    for _i in range(10):
        _page = apis.phone.search_contacts(query="", page_limit=20,
                                           page_index=_i)
        if not _page:
            break
        _contacts.extend(_page)
except Exception:
    pass
_crows = []
for _c in _contacts:
    if not isinstance(_c, dict):
        continue
    _rels = _c.get("relationships") or []
    _crows.append([str(_c.get("first_name") or ""),
                   str(_c.get("last_name") or ""),
                   str(_c.get("email") or ""),
                   str(_c.get("phone_number") or ""),
                   "/".join(str(_r) for _r in _rels)])
print(_hj.dumps({"apps": _apps_out, "contacts": _crows}))
"""


def _catalog_from_output(payload: str) -> dict:
    for line in reversed((payload or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _appworld_catalog(workdir: Path, task_id: str, trace) -> dict:
    rc, out = _run_aw(workdir, ["./aw", "action", "--task", task_id,
                                "--code", _CATALOG_CODE])
    catalog = _catalog_from_output(out) if rc == 0 else {}
    apps = catalog.get("apps") if isinstance(catalog.get("apps"), dict) else {}
    contacts = catalog.get("contacts") if isinstance(catalog.get("contacts"), list) else []
    trace.write(json.dumps({"appworld_catalog": {
        "exit": rc, "apps": len(apps), "contacts": len(contacts),
        "apis": sum(len(rows) for rows in apps.values()
                    if isinstance(rows, list))}}) + "\n")
    return {"apps": apps, "contacts": contacts}


def _catalog_text(catalog: dict, goal: object, budget: int = 18000) -> str:
    """Render the prefetched catalog compactly, describing goal-relevant apps.

    Names are cheap and let the model call the right API without a doc step;
    descriptions are only spent on apps whose vocabulary the goal actually
    names. Other apps still contribute every method name, so nothing is hidden.
    The contact list is included because tasks are phrased in terms of
    relationships ("roommates", "coworkers", "friends") and the world's only
    mapping from those words to people is `phone.search_contacts`; every venmo
    and simple-note task was otherwise re-reading it a page at a time.
    """
    if not catalog:
        return ""
    apps = catalog.get("apps") if isinstance(catalog.get("apps"), dict) else catalog
    contacts = catalog.get("contacts") if isinstance(catalog.get("contacts"), list) else []
    goal_apps = set(_goal_apps(goal))
    lines = ["World context already fetched. Use these names and descriptions "
             "instead of calling show_app_descriptions; call show_api_doc only "
             "if you need an exact parameter you cannot infer. Every call gets "
             "its access_token from the harness; never pass or paste one."]
    total = len(lines[0])

    def add(text: str) -> bool:
        nonlocal total
        if total + len(text) + 1 > budget:
            return False
        lines.append(text)
        total += len(text) + 1
        return True

    if contacts:
        entries = []
        for row in contacts:
            if not isinstance(row, list):
                continue
            name = " ".join(part for part in row[:2] if part).strip()
            email = str(row[2]) if len(row) > 2 else ""
            phone = str(row[3]) if len(row) > 3 else ""
            rels = str(row[4]) if len(row) > 4 else ""
            if not name:
                continue
            entries.append(" | ".join(part for part in
                                      (name, email, phone, rels) if part))
        if entries:
            add("Your contacts (name | email | phone | relationships):\n  "
                + "\n  ".join(entries))

    ordered = [app for app in apps if app in goal_apps]
    ordered += [app for app in apps if app not in goal_apps]
    for app in ordered:
        rows = apps.get(app)
        if not isinstance(rows, list) or not rows:
            continue
        names = [str(row[0]) for row in rows
                 if isinstance(row, list) and row]
        if not names:
            continue
        if app in goal_apps:
            parts = []
            for row in rows:
                if not (isinstance(row, list) and row):
                    continue
                name = str(row[0])
                description = str(row[1]) if len(row) > 1 else ""
                description = " ".join(description.split())[:70]
                parts.append(f"{name}: {description}" if description else name)
            if not add(f"{app} (relevant app):\n  " + "\n  ".join(parts)):
                add(f"{app}: " + ", ".join(names))
        else:
            add(f"{app}: " + ", ".join(names))
    return "\n".join(lines)


#: One server-side program that gathers everything the model needs to start:
#: passwords, the app list, and an access token per app. Multi-line code is sent
#: as one argv element, so no shell quoting is involved. Every section is
#: individually guarded -- one app without a `login` API must not lose the rest.
_BOOTSTRAP_CODE = """import json as _hj
_result = {}
try:
    _result["account_passwords"] = apis.supervisor.show_account_passwords()
except Exception:
    _result["account_passwords"] = []
try:
    _result["apps"] = apis.api_docs.show_app_descriptions()
except Exception:
    _result["apps"] = []
_sup = %s
_result["supervisor"] = _sup
_pw = {}
for _p in _result["account_passwords"]:
    try:
        _pw[_p["account_name"]] = _p["password"]
    except Exception:
        pass
_tokens = {}
_candidates = %s
_seen = set()
for _app in list(_candidates) + [a.get("name") for a in _result["apps"]
                                 if isinstance(a, dict)]:
    if not _app or _app in _seen or _app in ("api_docs", "supervisor"):
        continue
    _seen.add(_app)
    for _user in (_sup.get("email"), _sup.get("phone_number")):
        if not _user:
            continue
        try:
            _r = getattr(apis, _app).login(username=_user, password=_pw.get(_app, ""))
        except Exception:
            continue
        if isinstance(_r, dict) and _r.get("access_token"):
            _tokens[_app] = _r["access_token"]
            break
_result["tokens"] = _tokens
print(_hj.dumps(_result))
"""


def _py_literal(value: object) -> str:
    """A *Python* literal for the code sent to `aw action`.

    `json.dumps(None)` is `null`, which is a NameError inside the action -- the
    round-3 harness's own completion call failed on every state-only task with
    `NameError: name 'null' is not defined`.
    """
    return "None" if value is None else repr(value)


def _appworld_complete(workdir: Path, task_id: str, answer: str | None,
                       trace) -> None:
    """Record the task's final answer where AppWorld's verifier reads it."""
    code = "apis.supervisor.complete_task(answer=" + _py_literal(answer) + ")"
    rc, out = _run_aw(workdir, ["./aw", "action", "--task", task_id,
                                "--code", code])
    trace.write(json.dumps({"appworld_complete": {
        "answer": answer, "exit": rc, "output": out[:2000]}}) + "\n")


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


def _call_openai(messages: list[dict], temperature: float = 0.0) -> str:
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
                model=model, messages=messages, temperature=temperature,
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


def _complete(messages: list[dict], temperature: float = 0.0) -> str:
    """Every model call in this harness goes through here, so this is where the
    count is kept. Counting anywhere else means some caller is invisible."""
    USAGE["calls"] += 1
    if _backend() == "mock":
        return _call_mock(messages)
    return _call_openai(messages, temperature=temperature)


# ------------------------------------------------------------- the loop -----

class _Action(dict):
    """A parsed action, plus what the parser had to repair to get it.

    An attribute rather than a second return value, because every caller wants the
    dict and only one wants the repair note. `duplicates` empty means the reply was
    well formed and nothing was guessed.
    """
    duplicates: tuple[str, ...] = ()
    repairs: tuple[str, ...] = ()


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
    repaired = _repair_escaped_delimiters(reply)
    if repaired is None:
        repaired = _repair_malformed_tail(reply)
    if repaired is not None:
        return repaired
    raise ValueError("no complete JSON action found in reply")


#: The model sometimes backslash-escapes the *delimiters* of the command value:
#:   {"tool": "bash", "command": \"./aw action ... \\"spotify\\"))'\\""}
#: That is not JSON, so the action is lost and the step is spent on a retry.
#: Measured: 17 of the 32 parse failures in round 8 have exactly this shape and
#: all of them decode cleanly once the extra escaping layer is removed.
_ESCAPED_DELIMITER_RE = re.compile(
    r'^\s*\{\s*"tool"\s*:\s*"bash"\s*,\s*"command"\s*:\s*\\"(.*)\\"',
    re.DOTALL)


def _repair_escaped_delimiters(reply: str) -> dict | None:
    match = _ESCAPED_DELIMITER_RE.match(reply)
    if match is None:
        return None
    try:
        value = json.loads('"' + match.group(1) + '"')
    except ValueError:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    action = _Action({"tool": "bash", "command": value})
    action.repairs = ("escaped-command-delimiter",)
    return action


def _repair_malformed_tail(reply: str) -> dict | None:
    """Close the one-character JSON malformation the local model repeats.

    Measured in the round-13 traces: 21 of 22 `no complete JSON action found`
    failures end in exactly three shapes, all of which are a misplaced final
    delimiter, not truncated content:

        {"tool": "bash", "command": "...print(newest)'}"   # } before closing "
        {"tool": "bash", "command": "...print(alarms)'"    # missing final }
        {"tool": "bash", "command": "...print(len(x))'}    # no closing quote

    The command text itself is complete (it ends with the shell's closing
    single quote). Moving/adding the JSON delimiter recovers the action without
    completing a truncated command. This turns 27e1026_2's three dead parse
    retries into the command it was trying to run.
    """
    text = (reply or "").strip()
    if not text:
        return None
    candidates: list[str] = []
    if text.endswith('}"'):
        candidates.append(text[:-2] + '"}' )
    if text.endswith('}'):
        candidates.append(text[:-1] + '"}' )
    if text.endswith('"') and not text.endswith('}'):
        candidates.append(text + '}')
    for candidate in candidates:
        if candidate == text:
            continue
        try:
            obj = json.loads(candidate, object_pairs_hook=_first_key_wins)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        cmd = obj.get("command")
        if not ((isinstance(cmd, str) and cmd.strip()) or "answer" in obj):
            continue
        if not isinstance(obj, _Action):
            obj = _Action(obj)
        obj.repairs = ("closed-tail",)
        return obj
    return None


def _answer_text(value: object) -> str:
    """Transport a final answer without inventing or dropping prediction fields.

    Schema validation belongs to the task's verifier. This only checks transport:
    objects must stay JSON (str(dict) produces Python syntax), and JSON-looking
    strings must be complete. Never close truncated JSON or reinterpret literals.

    A bare number is a legitimate answer ("What is the total cost?") and
    AppWorld's `answer_to_text` compares `str(935.0)`, so rejecting numbers here
    only turned a usable submission into a parse retry -- measured 8 times in
    round 8 (`{"answer": 3166.0}`).
    """
    if value is None:
        # AppWorld state-only tasks take `null` as their legitimate answer. The
        # generic harness used to reject it as unparseable, which discarded the
        # submission that would have passed the answer check.
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, (int, float)):
        return str(value)
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


def _clip_text(out: str, limit: int) -> str:
    if len(out) <= limit:
        return out
    marker = f"\n... [text clipped; original {len(out)} characters] ...\n"
    room = max(0, limit - len(marker))
    head = (room + 1) // 2
    tail = room // 2
    return (out[:head] + marker + (out[-tail:] if tail else ""))[:limit]


#: Fields that identify a record. AppWorld's documentation endpoints return long
#: JSON lists of `{"name": ..., "description": ...}`; keeping only the head and
#: tail dropped 51 of 91 Spotify API names in round 0, so the model could not see
#: `follow_artist` at all and kept guessing.  Keeping every name is what makes the
#: command useful; descriptions of the records that do not fit can be fetched
#: individually afterwards.
_RECORD_ID_KEYS = ("name", "api_name", "title", "song_title", "path", "label", "id")


def _record_id_key(item: object) -> str | None:
    if isinstance(item, dict):
        for key in _RECORD_ID_KEYS:
            if key in item:
                return key
    return None


def _clip_record_list(items: list, limit: int) -> str | None:
    """Bound an API index by shortening descriptions, never by dropping names.

    The old version emitted JSON lines: `{"name": "show_account",
    "description": "..."}`. For an app with ~60 APIs the per-line JSON overhead
    is larger than the descriptions it carries, so the first size that fit was
    `description_limit=0` -- the model received a list of bare names, could not
    tell `show_transaction` from `show_transactions`, and spent the next eight
    steps calling `show_api_doc` one entry at a time (2a163ab_1). A plain
    `name: description` line carries the same information in roughly a third of
    the bytes, so every API keeps a usable description and the budget goes to
    the task instead of to documentation browsing.
    """
    keys = [_record_id_key(item) for item in items]
    if not all(keys):
        return None
    for description_limit in (200, 140, 100, 80, 60, 45, 30, 20, 12, 0):
        lines = [f"[harness API index: all {len(items)} entries; "
                 "descriptions shortened to fit]"]
        for item, key in zip(items, keys):
            name = str(item[key])
            description = item.get("description")
            if not isinstance(description, str):
                description = ""
            description = " ".join(description.split())
            if description_limit and description:
                description = description[:description_limit]
                lines.append(f"{name}: {description}")
            else:
                lines.append(name)
        listing = "\n".join(lines)
        if len(listing) <= limit:
            return listing
    names = ", ".join(str(item[key]) for item, key in zip(items, keys))
    return _clip_text(names, limit)


def _flatten_record(record: dict) -> dict:
    """One row of scalar cells: nested objects become `parent.child` keys."""
    flat: dict[str, object] = {}
    for key, value in record.items():
        if isinstance(value, dict):
            for child, child_value in value.items():
                flat[f"{key}.{child}"] = child_value
        elif isinstance(value, (list, tuple)):
            if all(not isinstance(part, (dict, list, tuple)) for part in value):
                flat[key] = "|".join(str(part) for part in value)
            else:
                flat[key] = json.dumps(value, ensure_ascii=False,
                                       separators=(",", ":"))
        else:
            flat[key] = value
    return flat


def _clip_table(items: list, limit: int) -> str | None:
    """A dense all-records table for a clipped list of objects.

    This is the fix for the head/tail preview, not a smaller constant. The old
    preview showed only the first and last few records, so a model asked to sum
    a page or match a name simply could not see the rows it needed; worse, the
    preview was a dict shaped like an API response and models wrote code against
    `_harness_preview`/`head` (d0b1f43_1). A tab-separated table of every record
    is smaller than indented JSON, keeps every row, and says plainly that it is
    a view.
    """
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    rows = [_flatten_record(item) for item in items]
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    if not columns:
        return None

    def cell(value: object, width: int) -> str:
        if value is None:
            text = ""
        elif isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (dict, list, tuple)):
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            text = str(value)
        text = " ".join(text.split()).replace("\t", " ")
        if width:
            text = text[:width]
        return text

    def render(active: list[str], width: int) -> str:
        lines = [
            f"[harness list view: all {len(rows)} records; this is a display "
            "of the returned list, not the return value itself; cells may be "
            "shortened to fit]",
            "\t".join(active),
        ]
        for row in rows:
            lines.append("\t".join(cell(row.get(column), width)
                                   for column in active))
        return "\n".join(lines)

    for width in (120, 80, 60, 40, 25, 15, 8):
        text = render(columns, width)
        if len(text) <= limit:
            return text
    # Still too wide: drop the widest columns, never the record count. Keep
    # identifiers and short fields over long free text (descriptions,
    # addresses, share links) so a row still identifies its record.
    protected = {"id", "name", "title", "amount", "path", "label", "time",
                 "date", "email", "phone_number", "song_id", "album_id",
                 "playlist_id", "transaction_id", "payment_request_id",
                 "note_id", "contact_id", "duration", "count"}
    active = list(columns)
    while len(active) > 2:
        widths = {}
        for column in active:
            values = [cell(row.get(column), 0) for row in rows]
            widths[column] = sum(len(value) for value in values) / max(1, len(values))
        droppable = [column for column in active if column not in protected]
        if not droppable:
            droppable = active
        active.remove(max(droppable, key=lambda column: widths.get(column, 0)))
        text = render(active, 8)
        if len(text) <= limit:
            return text
    return _clip_text(render(active, 8), limit)


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
    if isinstance(obj, list) and obj and all(
            isinstance(item, dict) and "description" in item for item in obj):
        # Documentation indexes are the one list where dropping records is
        # actively harmful: the model must see every API name to pick the right
        # call. Keep all names and shorten only the descriptions.
        listing = _clip_record_list(obj, OUTPUT_LIMIT - len(suffix))
        if listing is not None:
            return listing + suffix
    if isinstance(obj, list) and obj:
        # A data list (transactions, songs, playlists, contacts): an all-records
        # table keeps every row the model needs to filter or sum, unlike the
        # old head/tail preview which made `_harness_preview` look like the API
        # response and silently hid the middle of the page.
        table = _clip_table(obj, OUTPUT_LIMIT - len(suffix))
        if table is not None:
            return table + suffix
    preview = _json_preview(obj, OUTPUT_LIMIT - len(suffix))
    if preview is not None:
        return preview + suffix
    return _clip_text(compact, OUTPUT_LIMIT)


def _shell_runtime() -> tuple[str, str]:
    """Choose once before any task action; never retry side effects in another shell."""
    bash = shutil.which("bash")
    return (bash, "Bash") if bash else ("/bin/sh", "POSIX sh")


_PAGINATION_NOTE = (
    "\n[harness] This result is exactly one page ({n} records). It may not be the "
    "whole collection. Call the same API again with page_limit=20 and "
    "page_index=1, 2, ... until a page comes back empty, and act on the union of "
    "all pages.")


def _first_list_literal(out: str) -> list | None:
    """The first complete `[...]` in the output, parsed as data.

    The useful list is often printed next to other lines ("Pending requests:
    [...]" followed by "Approved ..."), so the value cannot be recovered by
    handing the whole output to a single parser. Scan for the matching bracket,
    respecting the repr's quotes, then parse exactly that slice.

    `./aw action` serialises return values as **JSON** (`true`/`false`/`null`),
    so `ast.literal_eval` alone rejects exactly the lists the pagination warning
    exists for: 6ea6792_1's 20-record page was never diagnosed because the JSON
    booleans made the old parser return None. Try JSON first, then Python
    repr -- the model may also `print(f"{value}")`, which is a repr.
    """
    at = out.find("[")
    if at < 0:
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for i in range(at, len(out)):
        ch = out[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                candidate = out[at:i + 1]
                for parser in (json.loads, ast.literal_eval):
                    try:
                        value = parser(candidate)
                    except (ValueError, SyntaxError, MemoryError, RecursionError):
                        continue
                    return value if isinstance(value, list) else None
                return None
    return None


def _pagination_hint(code: str, out: str) -> str:
    """Warn when a list call returned exactly one default-sized page.

    Measured: 6ea6792_1 fetched `show_received_payment_requests` with no
    `page_limit`, saw 5 pending requests, approved exactly those, and submitted --
    AppWorld wanted 10. 229360a_3 removed the 2 library songs on page 0 of 7.
    The default page size is 5, so a list of exactly 5 dicts is the signature of
    an unfetched collection. The note is advisory: the harness does not rewrite
    a call it cannot prove is paginated.
    """
    # An auto-paginated call already returned every page; warning that it
    # "may be one page" would make the model paginate a second time and can
    # reject a valid state submission through the `one_page` guard.
    if "_hg_fetch_all" in (code or ""):
        return ""
    value = _first_list_literal(out)
    if not value or not all(isinstance(item, dict) for item in value):
        return ""
    match = _PAGE_LIMIT_RE.search(code)
    limit = int(match.group(2)) if match else 5
    if len(value) != limit:
        return ""
    # A call already walking pages is doing the right thing.
    if "page_index" in code and not re.search(r"page_index\s*=\s*0\b", code):
        return ""
    return _PAGINATION_NOTE.format(n=len(value))


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


def _finalize_answer(messages: list[dict], task: dict, needs_answer: bool,
                     last_output: str, trace, step: int | None = None) -> str:
    """Ask for the final answer after the loop stopped without one.

    Measured: 34/90 round-3 runs ended on a repeated command with no answer at
    all, and AppWorld then records `null` -- so a question whose facts were
    already printed in the transcript still scores 0. The old harness called
    `complete_task` and hoped; this spends up to `FINAL_ANSWER_ATTEMPTS` model
    calls on turning the transcript into the one value the verifier compares.

    Commands are not executed here: the step budget is over, so an action can
    only make the answer later. A null/empty reply is rejected when the task's
    goal is a question, because `null` cannot be the ground-truth value there.
    """
    goal = str(task.get("goal") or "")
    _trim_history(messages)
    if needs_answer:
        prompt = (
            "Your step budget is over. Do not run any more commands.\n"
            f"The task was: {goal}\n\n"
            "Using the data already printed in this conversation, reply with "
            "EXACTLY ONE JSON object and nothing else:\n"
            '  {"answer": "<the exact value the task asks for>"}\n'
            'For a number, put the number (not a sentence). For a name or '
            'title, put that name or title verbatim. "answer": null is WRONG '
            "here: the task asks for a value, so give your best value.\n")
    else:
        prompt = (
            "Your step budget is over. Do not run any more commands.\n"
            f"The task was: {goal}\n"
            'Reply with exactly {"answer": null} to submit the world state you '
            "have produced.\n")
    if last_output:
        prompt += ("\nThe output of your last command was:\n"
                   + _clip_text(last_output, 1500) + "\n")
    for attempt in range(FINAL_ANSWER_ATTEMPTS):
        messages.append({"role": "user", "content": prompt})
        try:
            reply = _complete(messages, temperature=0.0 if attempt == 0 else 0.6)
        except Exception as exc:                     # noqa: BLE001
            trace.write(json.dumps({"final_answer_error": str(exc)}) + "\n")
            break
        trace.write(json.dumps({"step": step, "final_answer_attempt": attempt,
                                "reply": reply[:2000]}) + "\n")
        messages.append({"role": "assistant", "content": reply})
        try:
            action = _parse(reply)
        except ValueError as exc:
            prompt = (f"That reply could not be read as a JSON action ({exc}). "
                      "Reply with exactly one JSON object containing an "
                      '"answer" key and nothing else.\n')
            continue
        if action.get("command"):
            prompt = ("Commands cannot run any more: the step budget is over. "
                      'Reply with exactly {"answer": ...} using what is already '
                      "printed above.\n")
            continue
        if "answer" not in action:
            prompt = ('Missing "answer". Reply with exactly {"answer": ...}.\n')
            continue
        value = _answer_text(action.get("answer"))
        if needs_answer and not _coerce_answer(value):
            prompt = ('"answer": null is wrong for this question. Give the exact '
                      "value the task asks for, from the transcript above.\n")
            continue
        return value
    return ""


def run(task: dict, workdir: Path) -> str:
    """Execute one task. Returns the answer string.

    The trace written to <workdir>/trace.jsonl is what the framework collects
    and hands to the Trainer (INTERFACE.md §4.1).
    """
    trace_path = workdir / "trace.jsonl"
    shell, dialect = _shell_runtime()
    # AppWorld detection is by the `aw` client the environment ships in the
    # workdir, not by a task id or a benchmark name. When it is absent the loop
    # behaves exactly as before.
    aw_task_id = str(task.get("task_id") or "")
    aw_enabled = bool(aw_task_id) and (workdir / "aw").exists()
    aw_needs_answer = _goal_needs_answer(task.get("goal"))
    aw_ready = False
    aw_init_output = ""
    aw_supervisor: dict = {}
    aw_tokens: dict[str, str] = {}
    aw_catalog: dict = {}
    aw_signatures: dict[tuple[str, str], list[str] | None] = {}
    aw_completed = False

    def aw_signature(app: str, api: str) -> list[str] | None:
        """The declared parameter names of one AppWorld API (fetched once).

        The model cannot see a signature unless it spends a step on the docs,
        and 29 observations in round 8 are `_api_request() takes 0 positional
        arguments` from a model that guessed. The harness can read the same
        documentation the verifier uses, so it does.
        """
        key = (app, api)
        if key in aw_signatures:
            return aw_signatures[key]
        params: list[str] | None = None
        try:
            doc_code = (
                "import json as _hj\n"
                "print(_hj.dumps(apis.api_docs.show_api_doc("
                f"app_name={app!r}, api_name={api!r})))")
            _rc, _out = _run_aw(
                workdir, ["./aw", "action", "--task", aw_task_id,
                          "--code", doc_code])
            for line in _out.splitlines():
                if not line.strip().startswith("{"):
                    continue
                try:
                    doc = json.loads(line)
                except ValueError:
                    continue
                raw = doc.get("parameters") if isinstance(doc, dict) else None
                if isinstance(raw, list):
                    params = [entry.get("name") for entry in raw
                              if isinstance(entry, dict) and entry.get("name")]
                break
        except Exception as exc:                     # noqa: BLE001
            trace_debug = f"aw signature lookup failed: {exc}"
            trace.write(json.dumps({"aw_signature_error": trace_debug}) + "\n")
        aw_signatures[key] = params
        trace.write(json.dumps({"aw_signature": {"app": app, "api": api,
                                                 "params": params}}) + "\n")
        return params

    system_content = SYSTEM + (
        f"\nShell runtime: {shell} -c ({dialect}). Each command starts a new shell.\n"
        + ("Bash arrays and PIPESTATUS are supported.\n" if dialect == "Bash" else
           "Use POSIX syntax; Bash arrays, PIPESTATUS and [[ ]] are unavailable.\n"))
    if aw_enabled:
        system_content += "\n" + AW_SYSTEM.format(task_id=aw_task_id)
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json.dumps(task)},
    ]

    answer = ""
    submits = 0
    parse_retries = 0
    mutations = 0
    effect_guard_used = False
    last_cmd = ""
    last_output = ""
    seen_cmds: dict[str, int] = {}
    run_counts: dict[str, int] = {}
    blocked_repeats = 0
    repeat_streak = 0
    page_warning = False
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
            "stuck_repeat_limit": STUCK_REPEAT_LIMIT,
            "repeat_resample_temperature": REPEAT_RESAMPLE_TEMPERATURE,
            "final_answer_attempts": FINAL_ANSWER_ATTEMPTS,
            "context_chars": CONTEXT_CHAR_BUDGET,
            "shell": shell, "shell_dialect": dialect,
            "max_output_tokens": MAX_OUTPUT_TOKENS}}
        for key, value in (("agent_model", os.environ.get("HG_AGENT_MODEL")),
                           ("agent_backend", _backend())):
            if value:
                identity[key] = value
        trace.write(json.dumps({"harness_identity": identity}) + "\n")
        # Initialize the AppWorld world once, before the loop. The base harness
        # made the model spend step 0 on `./aw init`; more importantly, doing it
        # here means the harness itself has a live world to complete in even if
        # the model never does. The init payload (instruction + supervisor) is
        # fed back so the model does not need to re-read it.
        if aw_enabled:
            aw_ready, aw_init_output, aw_supervisor, aw_tokens, aw_catalog = (
                _appworld_init(workdir, aw_task_id, trace, task.get("goal")))
            if aw_ready:
                messages[1]["content"] += (
                    "\n\nThe AppWorld environment is already initialized. Do not run "
                    "`./aw init` again. Here is its output (task, supervisor, account "
                    "passwords and available apps):\n" + aw_init_output)
                if aw_tokens:
                    messages[1]["content"] += (
                        "\n\nEvery app listed above is already logged in. The harness "
                        "supplies the correct access_token for each `apis.<app>.<api>` "
                        "call, so you never need to copy a token or call login. Write "
                        "`tokens[\"<app>\"]` only if you need the value explicitly.")
                catalog_text = _catalog_text(aw_catalog, task.get("goal"))
                if catalog_text:
                    messages[1]["content"] += "\n\n" + catalog_text
            else:
                # Auto-init failed; let the model try the documented command.
                messages[1]["content"] += (
                    "\n\nAppWorld initialization from the harness failed; run "
                    f"`./aw init --task {aw_task_id}` yourself before acting.")
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
            # **A repeat at temperature 0 is a fixed point.** The same context
            # produces the same action, so re-asking cannot escape a loop. Once a
            # repeat has been blocked, sample hotter; the corrective message and
            # the changed sampling together are what make the next reply differ.
            temperature = 0.0
            if repeat_streak:
                temperature = min(REPEAT_RESAMPLE_TEMPERATURE, 0.4 + 0.2 * repeat_streak)
            reply = _complete(messages, temperature=temperature)
            took = time.time() - t0
            model_s += took
            trace.write(json.dumps({"step": step, "reply": reply,
                                    "model_ms": int(took * 1000),
                                    "temperature": temperature}) + "\n")
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
            action_repairs = getattr(action, "repairs", ())
            if action_repairs:
                trace.write(json.dumps({"step": step,
                                        "action_repaired": list(action_repairs)}) + "\n")

            # **交卷闸门。** 顺序按"它想干什么"判断,而不是按哪个键先出现:
            # 一条回复里同时带 command 和 answer 时,以前"先看 answer"会让那条命令
            # 永远不执行(实测:`{"tool":"bash","command":"cat > /app/run.py …"}` 被当成
            # 空答案,文件从未创建)。所以:有命令就先执行命令;只有**没有**命令时才算交卷。
            cmd = action.get("command", "")
            if not cmd and "answer" in action:
                # AppWorld's deliverable is the world/answer recorded inside the
                # service, not a file in this working directory; checking local
                # paths here would only reject valid submissions.
                candidate = _answer_text(action["answer"])
                missing = [] if aw_enabled else _missing_deliverables(task, workdir)
                # **A null answer to a question is never the ground truth.** The
                # goal language says whether AppWorld compares a value; when it
                # does, accepting null guarantees a failed `assert answers match`
                # (9 round-5 tasks ended `'null' == '<a real title>'`). Reject it
                # once and make the model produce the value it already has.
                null_answer = (aw_enabled and aw_needs_answer
                               and not _coerce_answer(candidate))
                # **A state task submitted right after a one-page list is usually
                # half-done.** 6ea6792_1 approved the 5 pending requests on page 0
                # and submitted; the verifier wanted 10 (page 1 held the rest).
                # The list observation already told the model it was one page;
                # this rejects the submission once so that warning cannot be
                # ignored. Answer tasks are exempt: their value may well come from
                # a single page.
                one_page = (aw_enabled and not aw_needs_answer and page_warning)
                # **A "done" with no change is not done.** A state task is graded
                # on the world, so a submission after only reads cannot pass; in
                # round 8 five such tasks (7d7fbf6_1, 692c77d_2, 22cc237_2,
                # 29caf6f_2, afc0fce_1) answered after reading and nothing else.
                # This is deterministic: it looks at the calls the model made,
                # not at what it claims.
                no_effect = (aw_enabled and not aw_needs_answer
                             and mutations == 0 and not effect_guard_used)
                if (missing or null_answer or one_page or no_effect) \
                        and submits < SUBMIT_RETRIES:
                    submits += 1
                    if missing:
                        note = ("You answered but these paths the task asks for do not "
                                "exist: " + ", ".join(missing)
                                + ". Create them first, then answer again.")
                        trace.write(json.dumps({"step": step,
                                                "submit_rejected": missing}) + "\n")
                    elif null_answer:
                        note = (
                            "You submitted a null/empty answer, but this task asks a "
                            "question and the verifier compares your answer with a "
                            "value. null is wrong. If you have not printed the value "
                            "yet, run ONE command to get it; otherwise reply now with "
                            'exactly {"answer": "<the exact value>"}.')
                        trace.write(json.dumps({"step": step,
                                                "submit_rejected": "null-answer"}) + "\n")
                    elif one_page:
                        note = (
                            "Your last list call returned only ONE page, and this task "
                            "asks about ALL matching records. Do not submit yet: call "
                            "that list API again with page_limit=20 and page_index=1, "
                            "2, ... until a page comes back empty, then finish.")
                        trace.write(json.dumps({"step": step,
                                                "submit_rejected": "one-page"}) + "\n")
                        page_warning = False
                    else:
                        note = (
                            "Nothing in the simulated world has been changed yet: you "
                            "have only READ data. The verifier grades the world state, "
                            "so this submission cannot pass. Run the API calls that "
                            "actually perform the task (create/update/remove/move/like/"
                            "follow/send...), then submit again.")
                        trace.write(json.dumps({"step": step,
                                                "submit_rejected": "no-effect"}) + "\n")
                        effect_guard_used = True
                    messages.append({"role": "user", "content": note})
                    continue
                trace.write(json.dumps({"step": step, "submitted": True,
                                        "missing": missing}) + "\n")
                answer = candidate
                break
            # **A loop is not always an immediate repeat.** The base loop told
            # the model so but still executed the command, so 229360a_1 ran the
            # same `show_api_doc` 23 times and 76f2c72_1 the same failing loop 18
            # times: every repetition re-entered the context as a fresh `exit=0`
            # and the task reached the cap with nothing submitted. Here the
            # command is NOT run; the previous output is re-shown once and the
            # model must change behaviour. Two shapes are blocked:
            #   * the immediate repeat (`A A A`), and
            #   * a command already run twice that keeps coming back
            #     (`login`, `print(result)`, `login`, `print(result)`, ... --
            #     302c169_1 spent 28 steps alternating two commands while an
            #     immediate-only detector never fired).
            # The old code *aborted* after three blocked repeats: 34/90 round-3
            # and 52/90 round-5 runs ended that way, frequently with the work one
            # edit from done (07b42fd_1 had already found the artist and only
            # needed its stray `"message"` suffix removed). Now the blocked
            # repeat is followed by a hotter resample and the answer phase,
            # instead of a zero.
            normalized = " ".join(cmd.split())
            prior_runs = run_counts.get(normalized, 0)
            immediate = bool(normalized) and normalized == last_cmd
            if normalized and (immediate or prior_runs >= 2):
                blocked_repeats += 1
                repeat_streak += 1
                trace.write(json.dumps({"step": step, "repeat_blocked": normalized[:200],
                                        "count": blocked_repeats,
                                        "prior_runs": prior_runs,
                                        "immediate": immediate,
                                        "streak": repeat_streak}) + "\n")
                if _aw_failed(last_output):
                    guidance = (
                        "\nThat command FAILED: its error is at the end of the output "
                        "above. Sending the same code again cannot change the result. "
                        "Edit the reported line or call a different API.")
                else:
                    guidance = (
                        "\nIt already succeeded and its output is above; use that "
                        "output and take the NEXT step that changes the world.")
                messages.append({"role": "user", "content": (
                    f"This exact command has already been run {prior_runs} time(s) "
                    f"(last at step {seen_cmds.get(normalized, step - 1)}). It was "
                    "NOT run again: its output cannot have changed.\nOutput from "
                    "that step:\n" + _clip_text(last_output, 1200) + guidance
                    + "\n\nDo not send it again. Run a DIFFERENT command that makes "
                    "progress, or finish now with "
                    '{"answer": "<your final answer>"}.')})
                if repeat_streak >= STUCK_REPEAT_LIMIT:
                    # **A fixed point is not progress.** The old branch reset
                    # `repeat_streak` to 0 here, which also reset the sampling
                    # temperature to 0.0 on the next call; the model then
                    # re-emitted the same action and the cycle repeated until
                    # the step cap (3c13f5a_1 blocked the same command 27 times,
                    # e7a10f8_2 22 times). Six consecutive blocked repeats,
                    # already re-sampled at 0.6-0.8, is evidence that this run
                    # will not escape. Leave the loop and let the answer phase
                    # turn the transcript into a submission (and a state task
                    # into the world it has). Zero of the observed passing runs
                    # ever needed a reset to recover.
                    trace.write(json.dumps({"step": step, "repeat_abort": True,
                                            "streak": repeat_streak}) + "\n")
                    break
                continue
            repeat_streak = 0

            # **Run the command where its effect is graded.** In AppWorld the
            # command `./aw action ...` talks to the service that the verifier
            # replays. Executing it through the model's raw shell string loses
            # the code to quote damage (35/90 round-0 traces hit a bash syntax
            # error), and executing `complete_task` with a prose answer records
            # the wrong answer for a state-only task. The harness therefore
            # parses this one command shape itself and invokes `./aw` directly.
            t0 = time.time()
            rc = 0
            out = ""
            repair_note = ""
            sent_code = ""
            if aw_enabled and aw_ready and _AW_INIT_RE.match(cmd):
                # The harness already initialized the world. Re-running init
                # would reset it and burn a step.
                rc = 0
                out = ("The AppWorld environment is already initialized. Do not run "
                       "`aw init` again. Its output is earlier in this conversation.\n")
                if aw_init_output:
                    out += aw_init_output
                repair_note = "appworld-init-blocked"
            else:
                aw_action = None
                if aw_enabled:
                    match = _AW_ACTION_RE.match(cmd)
                    if match:
                        task_arg, rest = match.group(1), match.group(2)
                        code, suffix = _extract_aw_code(rest)
                        if code is not None and code.strip():
                            raw_code = code
                            try:
                                code, repairs = _sanitize_aw_code(
                                    code, aw_supervisor, aw_tokens,
                                    aw_signature if aw_ready else None)
                            except Exception as exc:     # noqa: BLE001
                                # A repair must never take the task down with
                                # it: run what the model wrote and record that
                                # the sanitiser declined.
                                code = raw_code
                                repairs = (f"sanitizer-error:{type(exc).__name__}",)
                            if repairs:
                                trace.write(json.dumps({
                                    "step": step, "code_repaired": list(repairs)}) + "\n")
                            if not aw_needs_answer and "complete_task" in code:
                                code = _rewrite_complete_task_none(code)
                            sent_code = code
                            aw_action = (task_arg, code, suffix)
                if aw_action is not None:
                    rc, out = _run_aw(
                        workdir,
                        ["./aw", "action", "--task", aw_action[0],
                         "--code", aw_action[1]],
                        suffix=aw_action[2], shell=shell)
                    repair_note = "appworld-direct"
                    # The model may have completed the task and then hit a
                    # later runtime error in the same command. Only a positive
                    # confirmation counts as "already recorded"; merely reading
                    # the complete_task documentation used to be enough, and
                    # that left 6104387_2 with `<<not_given>>`.
                    if _aw_completion_recorded(aw_action[1], out, rc):
                        aw_completed = True
                else:
                    proc = subprocess.run(
                        # `errors="replace"`: a command may emit arbitrary bytes, and a
                        # strict decode raises UnicodeDecodeError inside the harness,
                        # which ends the episode and is recorded as a zero for a task
                        # nobody finished. Measured 2026-10-15.
                        [shell, "-c", cmd], cwd=workdir, capture_output=True,
                        text=True, errors="replace"
                    )
                    rc = proc.returncode
                    out = proc.stdout + proc.stderr
                    if _aw_completion_recorded(cmd, out, rc):
                        aw_completed = True
            # **Report the effect, not the transport.** `./aw action` exits 0 when
            # the Python it ran raised, so the observation used to say `exit=0`
            # for a command that changed nothing; the model read that as success
            # and repeated it. AppWorld's own failure text is authoritative.
            if aw_enabled and _aw_failed(out):
                if rc == 0:
                    rc = 1
                if not out.startswith("[AppWorld reported an error]"):
                    out = ("[AppWorld reported an error: this call did NOT do what the "
                           "code intended. Fix the code; do not send it again.]\n" + out)
            took = time.time() - t0
            command_s += took
            if rc == 0 and _has_mutation(sent_code or cmd):
                # A successful call that is not a pure read: the world may have
                # changed. This is what makes the no-effect submission guard
                # deterministic instead of a model self-report.
                mutations += 1
            # If auto-init failed but the model recovered the world itself, the
            # harness can still submit at the end.
            if aw_enabled and not aw_ready and _AW_INIT_RE.match(cmd) \
                    and rc == 0 and "task_id" in out:
                aw_ready = True
                aw_init_output = out
            trace_record = {"step": step, "command": cmd, "exit": rc,
                            "shell": shell, "command_ms": int(took * 1000),
                            "output": out}
            if repair_note:
                trace_record["repaired"] = repair_note
            trace.write(json.dumps(trace_record) + "\n")
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
            page_note = _pagination_hint(sent_code, out) if aw_enabled else ""
            page_warning = bool(page_note)
            if page_note:
                trace.write(json.dumps({"step": step,
                                        "pagination_hint": True}) + "\n")
            messages.append({"role": "user", "content":
                             f"exit={rc}{output_note}\n{shown}{page_note}"})

            seen_cmds[normalized] = step
            run_counts[normalized] = run_counts.get(normalized, 0) + 1
            last_cmd = normalized
            last_output = out

            left = MAX_STEPS - (step + 1)
            # 只在进入收尾区和最后一步各说一次:每一步都重复同一句话不会更有用,
            # 只会把最近几轮的命令输出挤远。
            if left and (left == FINISH_NUDGE_STEPS or left == 1):
                trace.write(json.dumps({"step": step, "finish_nudge": left}) + "\n")
                if aw_enabled:
                    if aw_needs_answer:
                        # Never invite null on a question: that is the one answer
                        # the verifier can be sure is wrong.
                        tail = ('reply now with exactly one JSON object: '
                                '{"answer": "<the exact value the task asks for>"}. '
                                "null is not a valid answer to a question.")
                    else:
                        tail = ('reply now with exactly one JSON object: '
                                '{"answer": null} if the world is already in the '
                                "required state; otherwise make the single remaining "
                                "API call that finishes the job.")
                    messages[-1]["content"] += "\n" + (
                        f"You have {left} step(s) left. Stop exploring. " + tail)
                else:
                    messages[-1]["content"] += "\n" + (
                        f"You have {left} step(s) left. Stop exploring. Create every file the "
                        "task asks for now, then reply with exactly one JSON object: "
                        '{"answer": "<your final answer>"}')

        # **Finish the answer the loop never produced.** A run that ends on a
        # repeated command or on the step cap used to call complete_task with
        # whatever `answer` held -- the empty string. For a question that is
        # `null`, which can never match, even when the value was printed in the
        # transcript. Spend a bounded two calls turning the transcript into the
        # value the verifier compares.
        needs_value = aw_needs_answer if aw_enabled else _goal_needs_answer(task.get("goal"))
        if not answer and needs_value:
            answer = _finalize_answer(messages, task, True, last_output, trace,
                                      step=MAX_STEPS)
            if answer:
                trace.write(json.dumps({"final_answer": answer}) + "\n")

        # **Submit at the boundary the verifier reads.** Writing `answer.txt`
        # is not a submission in AppWorld: the verifier replays the API log and
        # reads the answer from `apis.supervisor.complete_task`. In round 0 the
        # model never called it on 76 of 90 tasks (`<<not_given>>`), and when it
        # did call it on a state-only task it passed prose where the ground truth
        # is `null`. The harness now finishes the world itself, exactly once.
        if aw_enabled and aw_ready:
            final_answer = _coerce_answer(answer) if aw_needs_answer else None
            if final_answer is not None:
                answer = final_answer
            elif not aw_needs_answer:
                # The deliverable is the world state; AppWorld's answer for such
                # a task is null. Mirror that in answer.txt instead of leaving an
                # empty artifact.
                answer = "null"
            # A model that called `complete_task` itself may have recorded a
            # prose description ("Followed 5 artists") instead of the value the
            # verifier compares, while its final `{"answer": ...}` is the value.
            # Re-recording the harness's coerced answer overwrites that; for a
            # state-only task the harness answer is null, which is exactly what
            # the earlier completion already says.
            if (not aw_completed
                    or (aw_needs_answer and final_answer is not None)):
                _appworld_complete(workdir, aw_task_id, final_answer, trace)
            else:
                trace.write(json.dumps({"appworld_complete": "already-recorded"}) + "\n")

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
        trace.write(json.dumps({"usage": dict(USAGE),
                                "mutating_calls": mutations}) + "\n")

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
