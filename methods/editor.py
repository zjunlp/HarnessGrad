"""The edit half of a method, shared by every method in this directory.

Why this is one module and not eight copies
-------------------------------------------
Every method here ends the same way: either something changes in the harness, or
nothing should. What differs is *what the method knows* and *what it decides
before asking*. So the asking lives here once, and each method supplies its own
framing -- which is exactly the split that makes "is this method's advantage its
rule or its editor?" a question the platform can answer by running the same
editor with and without the rule.

Two defects are fixed structurally here rather than in each method
-----------------------------------------------------------------
1. **Every exit must write a trajectory.** This was violated twice in
   `llm_improver` alone: a provider error and an unusable proposal both exited
   without writing one, so the platform could only report "the method wrote no
   trajectory" and the actual cause -- a 503, a malformed reply -- was discarded.
   A method that exits through `report()` or `fail()` cannot have this bug.

2. **The method reads the channel at the path the contract names.**
   `INTERFACE.md` §4.7 puts `_harnessgrad/` inside the *harness* directory. The
   first version of `llm_improver` read it from `request["workspace"]`, which is
   the method's own empty scratch directory -- so every prompt it ever built said
   `(no traces available)` and it proposed edits from the harness source alone,
   while its docstring claimed it read how the harness behaved. Measured, not
   guessed: with a trace staged at the contract path, the old code produced
   `(no traces available)` and the new code produces the trace. `channel()` is the
   single place that decides where to look.
"""
from __future__ import annotations

import itertools
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol                                             # noqa: E402
from protocol import emit, read_request, require_api        # noqa: E402,F401
# `methods/apply.py`, not `eval/candidate.py`: a method's sandbox hides the platform, so
# an `import eval.*` here fails inside a run (`ModuleNotFoundError: No module named
# 'eval'`) while passing every test that calls the editor directly. Measured, in a real
# run. The platform keeps its own copy in `eval/candidate.py`, and that one is the judge.
import apply as candidate                                    # noqa: E402

#: The framework<->method channel. Gitignored, so committing it cannot mint a sha.
CHANNEL = "_harnessgrad"

#: The one harness file a method may not rewrite: it *names* the harness, and a
#: method that could edit it could rename the thing it is measured as. Everything
#: else in the directory is a program, and changing the program is the job.
PROTECTED = {"harness.json"}

#: Text files larger than this are truncated in the prompt -- head **and tail**. A harness
#: that ships a 5 MB data file should still be improvable; the model does not need all of it.
#:
#: **This number has now been wrong twice, the same way.** It was 12000, and methods hand
#: back whole files, so a file the model had partly seen came back with its end missing:
#: `agent.py` was 14916 characters, the model saw the first 12000, and its candidate was an
#: 11880-character file whose tail -- including `def main()` and the `__main__` guard -- no
#: longer existed. The method's own words were "completing the previously truncated/missing
#: `run` body": it was doing exactly what it was asked, from a view that could not contain
#: the answer.
#:
#: Raising it to 24000 "shows the reference harness whole" was true when it was written and
#: false by 2026-10-05, when `agent.py` was 28942 characters (most of it Chinese commentary,
#: which is why the byte count hid it: 34699 bytes, 28942 characters). The cap then cut the
#: file at line 533 of 636 -- and everything the file *decides* is in the cut part: the
#: repeat nudge (583), the finish nudge (595), the submit gate's rejection path (551), the
#: loop's `_trim_history` call (602). What stayed visible was the seven constants at the top
#: (62-121), each under a comment naming the failure it had been added to fix.
#:
#: Measured across eight runs (`v4`-`v10`, one local 9B, codex as the shell): **seven of the
#: eight edits landed inside those first 533 lines and six of them changed a constant**
#: (`PARSE_RETRIES`, `FINISH_NUDGE_STEPS`, `OUTPUT_LIMIT`, `SUBMIT_RETRIES`,
#: `CONTEXT_CHAR_BUDGET`). Both regressions were constant edits; the one edit that landed in
#: the cut region was the only non-constant change and the only harmless one. The improver
#: edited where the prompt showed it, and the prompt showed it the knobs.
#:
#: So the number is no longer the guard. What protects the prompt now is in `load_sources`:
#: the **entrypoint is never trimmed**, other files are trimmed head-and-tail before it is,
#: and any trim says so in the text the model reads. This constant is only the runaway-file
#: guard it was always described as.
SOURCE_LIMIT = int(os.environ.get("HG_METHOD_SOURCE_LIMIT", "200000"))

#: How much harness source one prompt may carry, across all files.
#:
#: Not a curation number and not about the harness: it is the improver's **context window**
#: and the operating system's one-argument limit (128 KB) that set it. The whole instruction
#: travels as a single argv element, and the model reading it has to hold it plus the traces
#: plus the facts table. 60000 characters is roughly 15-20k tokens, which leaves room in a
#: 32k-token improver -- the local vLLM's window, which is what the reference runs use.
#:
#: When it bites, it bites in the right order: `load_sources` trims the largest
#: **non-entrypoint** files first, head-and-tail, and the entrypoint only if it alone
#: exceeds the cap. A half-shown entrypoint is exactly what misdirected every earlier run.
SOURCE_TOTAL_LIMIT = int(os.environ.get("HG_METHOD_SOURCE_TOTAL", "60000"))
#: How much of the platform's skill reaches a prompt. A skill is prose read by a model with
#: a large context, so the limit bounds a runaway file rather than curating one. Raised from
#: 6000 the moment the platform's own skill outgrew it: it was 8407 characters, and the
#: section a round needed most -- the one written from the *previous* run's failure -- sat
#: at character 5595. Truncation is never silent; a prompt that quietly drops its own
#: guidance is worse than no guidance, because the round is spent and nothing says why.
SKILL_LIMIT = int(os.environ.get("HG_METHOD_SKILL_LIMIT", "24000"))

# Artifact transport is bounded in bytes and never truncates a method's card.
CLI_ARTIFACT_FILE_LIMIT = 256 * 1024
CLI_ARTIFACT_TOTAL_LIMIT = 1024 * 1024


# --------------------------------------------------------------- the channel ---

def channel(base: Path) -> Path:
    """Where the platform staged what this method may read."""
    return Path(base) / CHANNEL


def load_history(base: Path) -> list[dict]:
    """Every previous curve point, oldest first.

    This is what makes a whole family of methods implementable: several published
    ones choose *which* previous state to build on, and that information is
    inherently across rounds. A method that only ever sees the current round can
    only ever hill-climb.
    """
    out = []
    hist = channel(base) / "history"
    if hist.is_dir():
        for f in sorted(hist.glob("round-*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
    out.sort(key=lambda p: p.get("round", 0))
    return out


def render_trace(text: str, max_events: int = 14, width: int = 220) -> str:
    """One task's trace, as lines a model can read.

    Deliberately tolerant about the event schema. The platform requires a harness
    to write a `usage` block and nothing else, so a renderer that assumed the
    reference harness's `command`/`reply` keys would silently show nothing for
    any other harness -- a method would then be diagnosing a blank page and would
    look like a bad method.
    """
    lines = []
    for raw in text.splitlines()[:max_events]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            lines.append(f"  {raw[:width]}")
            continue
        if not isinstance(event, dict):
            lines.append(f"  {str(event)[:width]}")
        elif "usage" in event and len(event) <= 2:
            continue                                    # accounting, not behaviour
        elif "command" in event:
            ran = str(event.get("command") or "")
            if not ran.strip():
                # The harness executed the empty string and the shell exited 0, so the model
                # was told `exit=0` for a step that did nothing. Measured on
                # `cancel-async-tasks`: ten of thirteen commands were this, and the task
                # scored 0.0 with a trace that reads as a working harness.
                lines.append(f"  ran: (EMPTY COMMAND -- nothing ran)"
                             f"  (exit {event.get('exit')})")
            else:
                lines.append(f"  ran: {ran[:width]}  (exit {event.get('exit')})")
            # `output` first, because that is what the reference harness writes
            # (`loop/agent.py`: `{"command": ..., "exit": ..., "output": ...}`). This
            # looked only for `stdout`/`stderr`, so **every** command's output was
            # dropped -- measured 0 of 441 bytes reaching the prompt. An improver was
            # shown `exit 100` with no idea why, which is precisely the feedback it
            # needs; it is also why "the install failed" and "the search found the
            # package" were both invisible.
            for key in ("output", "stdout", "stderr"):
                if event.get(key):
                    label = "" if key == "output" else f"{key}: "
                    # Indent continuation lines too. A command's output is usually
                    # multi-line, and leaving the tail flush against the margin makes
                    # the next event look like part of it.
                    body = "\n    ".join(
                        ln[:width] for ln in str(event[key]).strip().splitlines()[:40])
                    lines.append(f"    {label}{body}")
        elif "reply" in event:
            # **Head *and* tail.** A reply's tail is where the malformation is: measured on
            # `local-floor-9`, the real command was in the first 799 characters and the
            # duplicate `, "command": ""` that replaced it was in the last 40 -- and the
            # renderer cut every line at `width`, so the one thing that explained the empty
            # command was the one thing thrown away. A reply longer than the width now shows
            # both ends with the count of what is between.
            body = str(event["reply"])
            if len(body) <= width:
                lines.append(f"  said: {body}")
            else:
                half = max(40, (width - 40) // 2)
                lines.append(f"  said: {body[:half]}\n"
                             f"    ... [{len(body) - 2 * half} characters omitted] ...\n"
                             f"    ...{body[-half:]}")
        elif "parse_error" in event:
            # A bare JSON blob reads as noise; it is not noise. It is the harness
            # discarding its own tool call, which is a harness behaviour and the single
            # most actionable line a trace can carry. Measured: `loop` threw away a step
            # on `Extra data: line 1 column 56` for three different tasks, because the
            # model emitted trailing braces -- and the improver was shown only the raw
            # object, with nothing saying what it meant.
            lines.append(f"  the harness could not read its own reply and dropped the "
                         f"step ({str(event['parse_error'])[:width]})")
        elif "duplicate_keys" in event:
            lines.append(f"  ! the reply repeated the key(s) "
                         f"{', '.join(map(str, event['duplicate_keys']))}; the parser kept "
                         f"the FIRST value. The model's action may not be the one that ran.")
        elif "context_trims" in event:
            lines.append(f"  ! the model refused the prompt for length; history was "
                         f"shortened to {event.get('history_kept')} messages and retried "
                         f"({event['context_trims']} so far)")
        elif "output_clipped" in event:
            lines.append(f"  ! output clipped for the model: {event['output_clipped']} "
                         f"characters, {event.get('shown')} shown")
        elif "history_dropped" in event:
            lines.append(f"  ! {event['history_dropped']} old messages dropped from the "
                         f"model's view")
        elif "harness_identity" in event or "usage" in event or "wall_clock_s" in event:
            # Accounting, not behaviour. It used to be printed as raw JSON, which spent one
            # of the `max_events` slots the method has on a line that says nothing about what
            # the harness did -- and the `harness_identity` record is one this platform added
            # itself.
            continue
        else:
            lines.append(f"  {json.dumps(event, ensure_ascii=False)[:width]}")
    total = len([line for line in str(text or "").splitlines() if line.strip()])
    shown = min(total, max_events)
    if total > shown:
        # Say what was not shown. A silent cap is how a method concludes there was nothing
        # more, and an operator reads the same file and sees the rest.
        lines.append(f"  ... {total - shown} more record(s) not shown (max_events="
                     f"{max_events}); read the trace file for the rest")
    return "\n".join(lines)


def load_traces(base: Path, limit: int = 6, order: list[str] | None = None) -> str:
    """What the method is allowed to see: behaviour, not answers.

    Failing tasks go first, because a method that reads only successes learns
    nothing -- but the trace carries no expected answer, and nothing here can add
    one. The platform withholds them (`runner.py` never writes `scorable` into a
    trace), so a diagnosis can legitimately fail.

    `limit` caps how many tasks are shown. How many a method reads is part of the
    method (one published one reads its worst 30 plus its best 6), so this is a
    default the caller may override, not a platform rule.
    """
    trace_dir = channel(base) / "traces"
    paths = sorted(trace_dir.glob("*.jsonl"))
    if order:
        rank = {tid: i for i, tid in enumerate(order)}
        paths.sort(key=lambda p: rank.get(p.stem, len(rank)))
    if not paths:
        return ("(the platform staged no traces for this round -- either the dataset "
                "declares no split and this round had none, or the method was called "
                "without one)")
    blocks = []
    for path in paths[:limit]:
        try:
            text = path.read_text()
        except OSError:
            continue
        blocks.append(f"### task {path.stem}\n{render_trace(text)}")
    return "\n\n".join(blocks)


def load_sources(base: Path, limit: int = SOURCE_LIMIT,
                 total: int | None = None,
                 entrypoint: str | None = None) -> dict[str, str]:
    """The harness's text files -- **whole**, so the model can see what it is changing.

    Walks the tree rather than naming files: a harness is a directory, and a
    method that could only see `agent.py` could not add a module, which is a
    legitimate and often necessary edit.

    **Truncation was the bug, not a safeguard.** A per-file cap of 24,000 characters cut
    `base_harness/loop/agent.py` (28,942 characters, most of it Chinese commentary) at line
    533 of 636 -- and everything the file *decides* is in the part that was cut: the repeat
    nudge (583), the finish nudge (595), the submit gate's rejection path (551), the loop's
    own `_trim_history` call (602). What stayed visible was the seven constants at the top
    (lines 62-121), each under a comment naming the failure it had been added to fix. The
    note appended in place of the rest said "Do NOT rewrite this file wholesale -- you have
    not seen how it ends", which is advice to stay where the light is.

    Measured across eight runs (`v4`-`v10`, one local 9B, codex as the shell): **seven of the
    eight edits landed inside those first 533 lines, and six of them changed a constant** --
    `PARSE_RETRIES`, `FINISH_NUDGE_STEPS`, `OUTPUT_LIMIT`, `SUBMIT_RETRIES`,
    `CONTEXT_CHAR_BUDGET`. Both regressions (0.200 -> 0.000 and 0.100 -> 0.000) were constant
    edits; the single edit that landed in the hidden region was the only non-constant change
    and the only harmless one. The improver edited where the prompt showed it, and the prompt
    showed it the knobs.

    So the file is shown whole. The only cap left is `SOURCE_TOTAL_LIMIT`, which is not about
    the harness but about the operating system: the whole instruction travels as **one argv
    element**, and Linux refuses one longer than 128 KB. When the tree is large enough to
    approach that, the *largest files other than the entrypoint* are trimmed head-and-tail
    first, and the entrypoint is trimmed only if it alone exceeds the cap -- because a
    half-shown entrypoint is precisely what misdirected every earlier run.
    """
    if total is None:
        total = SOURCE_TOTAL_LIMIT
    out: dict[str, str] = {}
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        # **Relative parts, not absolute.** The platform stages each earlier round's
        # harness *inside the channel* (`<base_harness>/_harnessgrad/states/round-N/`), so
        # when `base` is one of those states every absolute path contains `_harnessgrad` by
        # construction and this check rejected every file. The prompt's
        # "## The parent's sources" section was therefore present and **empty**: the
        # improver was asked to improve a harness whose code it had not been shown.
        #
        # Measured (`v3-dgm`, 2026-10-05): DGM's instruction to codex was 20,313 characters
        # with that section blank, and codex proposed nothing. The earlier 625-line rewrite
        # is the same bug with a different improver: a chat model told "propose one change
        # to this parent" with no source shown invents an entire harness -- and one that
        # reads `sys.argv[1]` where the platform passes `--task`.
        if CHANNEL in rel.parts:
            continue
        if path.name in PROTECTED:
            continue
        if path.suffix not in {".py", ".json", ".md", ".txt", ".yaml", ".yml", ".toml"}:
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if len(text) > limit:
            # Head and tail, not head alone: the end of a file is where a loop stops and a
            # program answers, and that was the half the old cap removed.
            head, tail = limit * 2 // 3, limit // 3
            text = (text[:head]
                    + f"\n# [platform] TRUNCATED (this file only): {limit} of {len(text)} "
                      f"characters are shown, head and tail. Read the file from disk for the "
                      f"rest.\n"
                    + text[-tail:])
        out[str(rel)] = text

    size = sum(len(v) for v in out.values())
    if size <= total:
        return out
    # Non-entrypoint files first, largest first; the entrypoint last.
    entry = entrypoint if entrypoint is not None else (_read_manifest(base).get("entrypoint") or "")
    for rel in sorted(out, key=lambda r: (r == entry, -len(out[r]))):
        if size <= total:
            break
        body = out[rel]
        if len(body) <= 1024:
            continue
        room = max(1024, len(body) - (size - total))
        head, tail = room * 2 // 3, room // 3
        out[rel] = (body[:head]
                    + f"\n# [platform] TRUNCATED (to keep the whole prompt inside one argv "
                      f"element): {room} of {len(body)} characters are shown, head and tail. "
                      f"Read the file from disk for the rest.\n"
                    + body[-tail:])
        size = sum(len(v) for v in out.values())
    return out


def evidence_pointer(base: Path) -> str:
    """Where the evidence is: **paths, not content.**

    The content used to be compiled in, and that design has a ceiling that is not about
    politeness. Measured (`v10`, 10 tasks, one harness file of 28,942 characters): 23,146
    characters of rendered traces plus 29,032 of source -- 89.7% of a 58,160-character
    instruction, with the ask and the hard rules sharing the remaining 6.9%. At this
    platform's own intended scale (51 train tasks, a harness of several files) the same
    proportions give roughly 230 KB, and the whole instruction travels as **one argv
    element**, which Linux refuses above 128 KB: the improver would not start at all.

    Nothing has to be compiled, because the content is already on disk next to the tree the
    improver edits: `cli_improver.propose` stages the channel as `evidence/`, which holds the
    round summary, the traces with a byte-offset index per file, the task pages with the
    verifier's verdict, the earlier rounds, the earlier harness trees, and the platform's
    skill. This block names them; a CLI improver reads what its own diagnosis needs, and the
    prompt stops growing with the dataset.
    """
    entrypoint = (_read_manifest(base).get("entrypoint") or "agent.py")
    return (
        "## Where the evidence is -- read what your own diagnosis needs\n"
        f"- the harness you are changing **is the current directory**; its entrypoint is "
        f"`{entrypoint}`\n"
        "- this round, per task and in total: `evidence/round.json`\n"
        "- the incumbent's real conversations, one file per task: "
        "`evidence/traces/<task>.jsonl`\n"
        "  each line is one event -- the model's reply, the command it ran, the output it was\n"
        "  shown. `evidence/traces/<task>.index.json` maps every line to a byte offset and a\n"
        "  one-line summary, so you can find the step you care about without reading it all.\n"
        "- the tasks, their grading, and the verifier's verdict: `evidence/tasks/<task>.json`\n"
        "- earlier rounds: `evidence/history/round-<N>.json`; complete harness trees from\n"
        "  earlier rounds: `evidence/states/round-<N>/`\n"
        "- how to improve a harness on this platform: `evidence/SKILL.md` -- read this first\n"
    )


def evidence_content(base: Path, trace_limit: int = 6) -> str:
    """The same material **inlined**, for an improver that cannot read files.

    The two layers are not the same reader. A CLI improver has a working directory and tools,
    so it gets `evidence_pointer` and reads what it needs. A chat call has neither: whatever
    it is not shown, it does not have. So the endpoint path inlines the sources and the traces
    under `load_sources`' total cap -- the cap is that reader's context window and the
    operating system's one-argument limit, not a curation choice.
    """
    sources = "\n\n".join(f"### {k}\n```\n{v}\n```" for k, v in load_sources(base).items())
    return ("## The harness's sources\n" + (sources or "(no text files)")
            + "\n\n## What the harness did, failing tasks first\n"
            + load_traces(base, limit=trace_limit, order=failing_first(base)))


def failing_first(base: Path) -> list[str]:
    """Task ids ordered worst-first, from this round's curve point if it says.

    Which side the method is *shown* is decided by the record's `score_kind` (§2.6),
    through `protocol.studied_per_task`. Ordering by the side it studies is the honest
    choice: a method reading traces of tasks it is not scored on should still look at
    the ones that are going badly.
    """
    point = {}
    rp = channel(base) / "round.json"
    if rp.exists():
        try:
            point = json.loads(rp.read_text())
        except (OSError, json.JSONDecodeError):
            point = {}
    # 哪一侧是「诊断侧」由记录自己说(§2.6),这条规则只在 protocol 里写一次。
    scores = protocol.studied_per_task(point)
    return [tid for tid, _ in sorted(scores.items(), key=lambda kv: kv[1])]


# ------------------------------------------------------------------ the model ---

DEFAULT_SYSTEM = """You are improving an agent harness.

You will be shown the harness's source, the per-task checks' own reports, and the \
record of running it on a set of tasks. Propose ONE concrete change that is likely to \
make it solve more of them.

Reply with JSON only:

  {"files": [{"path": "<relative path>",
              "find": "<exact text to replace, copied from the file>",
              "replace": "<the text that goes in its place>"}],
   "hypothesis": "<one sentence: what you changed and why>"}

Rules:
- **Prefer `find`/`replace`.** `find` must appear EXACTLY ONCE in the file -- copy it
  character for character, including indentation and blank lines, and include enough
  surrounding text to make it unique. If it appears zero times or more than once the
  edit is refused, and you will be told which.
- To create a file that does not exist yet, use `{"path": ..., "content": ...}` with the
  whole file. Do not send `content` for an existing file: regenerating a file you were
  shown loses whatever you did not reproduce (comments, docstrings, functions you did
  not mean to touch), and that loss has been measured on this platform.
- `path` is relative to the harness root. `harness.json` may not be changed.
- If the evidence does not support any change, reply {"no_change": "<reason>"}.
  That is a valid and useful answer, and it is better than a speculative edit.
"""


def model_settings() -> tuple[str, str, str]:
    base_url = os.environ.get("HG_METHOD_BASE_URL")
    api_key = os.environ.get("HG_METHOD_API_KEY")
    model = os.environ.get("HG_METHOD_MODEL")
    if not (base_url and api_key and model):
        raise SystemExit(
            "a method needs HG_METHOD_BASE_URL / HG_METHOD_API_KEY / HG_METHOD_MODEL. "
            "A method brings its own credentials; the platform does not supply them.")
    return base_url, api_key, model


class UnparseableReply(RuntimeError):
    """The model answered, but not in a form this platform could read.

    Distinct from `no_change` on purpose. A method that reports "I chose not to
    change anything" and a method whose reply was garbage are different events,
    and collapsing the second into the first puts a decision in the curve that
    nobody made.
    """


def _unfence(text: str) -> str:
    """Strip a ``` or ```json fence if the model wrapped its reply in one."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    parts = text.split("```")
    if len(parts) < 3:
        return text
    body = parts[1]
    return body[4:] if body.lstrip().startswith("json") else body


def repair_json_text(candidate: str) -> tuple[str, list[str], bool, int | None]:
    """Every textual repair, plus what the scanner learned while doing them.

    Returns `(text, unclosed_stack, still_in_string, first_balanced_at)`.

    Split out because it is what has to be inspected when a reply still will not
    parse: "the model's answer could not be read" is not actionable, but "here is
    what we turned it into, and here is where the brackets ended up" is. It is also
    the part worth testing on its own -- the candidate stage below only chooses
    among its outputs.
    """
    valid_escapes = set('"\\/bfnrtu')
    stack: list[str] = []
    repaired: list[str] = []
    in_string = False
    escaped = False
    complete_at: int | None = None
    i = 0
    while i < len(candidate):
        ch = candidate[i]
        if escaped:
            escaped = False
            repaired.append(ch)
        elif ch == "\\" and in_string:
            nxt = candidate[i + 1] if i + 1 < len(candidate) else ""
            if nxt in valid_escapes:
                escaped = True
                repaired.append(ch)
            else:
                # `\\d`, `\\s`, `C:\\data` -- the backslash was meant literally.
                repaired.append("\\\\")
        elif ch == '"':
            if in_string:
                # A quote *inside* a string that the model forgot to escape. The
                # discriminator is structural and reliable: in JSON a string is
                # always followed by `,`, `:`, `}` or `]` (or the document ends).
                # Anything else means this quote is content.
                look = i + 1
                while look < len(candidate) and candidate[look] in " \t\r\n":
                    look += 1
                nxt = candidate[look] if look < len(candidate) else ""
                if nxt and nxt not in ",:}]":
                    repaired.append('\\"')
                    i += 1
                    continue
            in_string = not in_string
            repaired.append(ch)
        elif in_string and ord(ch) < 0x20:
            repaired.append({"\n": "\\n", "\r": "\\r", "\t": "\\t",
                             "\b": "\\b", "\f": "\\f"}.get(ch, "\\u%04x" % ord(ch)))
        else:
            repaired.append(ch)
            if not in_string:
                if ch in "{[":
                    stack.append(ch)
                elif ch in "}]":
                    want = "{" if ch == "}" else "["
                    if stack and stack[-1] == want:
                        stack.pop()
                    elif stack:
                        # **The model closed the wrong kind of bracket.** Measured:
                        # an 11 KB reply ended `...task inputs."}]}`, where that first
                        # `}` was closing a *list* opened 340 characters earlier. It
                        # is neither missing nor extra -- it is a type mismatch, which
                        # `json.loads` rejects while a type-aware scanner sees a
                        # balanced document and therefore repairs nothing. This was
                        # the last hole, and it hid behind exactly that disagreement.
                        repaired[-1] = "}" if stack[-1] == "{" else "]"
                        stack.pop()
                    else:
                        repaired.pop()      # a stray closer with nothing open
                # Only the *first* return to balance. Updating it on every subsequent
                # character would swallow the very extra closers and trailing prose
                # this exists to remove.
                if not stack and complete_at is None:
                    complete_at = len(repaired)
        i += 1
    return "".join(repaired), stack, in_string, complete_at


def loads_tolerant(text: str):
    """Parse a reply, repairing the ways a small model reliably breaks JSON.

    This is not speculative tidiness; it is the difference between a working method
    and a dead one on a small local model. Every repair comes from a measured
    failure on this host:

    * missing trailing closers -- a 9B model returned a complete 8.8 KB edit and
      omitted the final `]}`;
    * an extra trailing closer -- `{...}}`, the reply that broke the `loop` base
      harness (`base_harness/loop/agent.py:143` has the same flaw);
    * **a bracket of the wrong type** -- `]` where `}` belonged, which a type-aware
      scanner silently ignores and `json.loads` rejects;
    * trailing prose after the JSON;
    * raw newlines, tabs and other control characters inside a string;
    * **invalid escape sequences** -- code containing `\\d`, `\\s` or `C:\\data`;
    * an **unescaped `"` inside a string**, the hardest of these, distinguishable
      from a real terminator only by what follows it;
    * a trailing comma before `}` or `]`.

    Before this existed, every one of these was silently reported as `no_change` --
    a method decision that nobody made. A reply that is wrong in a way not listed
    here still fails, and still gets reported as a failure rather than as a choice.
    """
    body = _unfence(text)
    start = body.find("{")
    if start < 0:
        return None

    repaired, stack, in_string, complete_at = repair_json_text(body[start:])

    # The scanner's bracket stack is a *guess* at how the document should close, and
    # a hand-rolled scanner can still disagree with the real parser. So the guess is
    # not load-bearing: build every plausible ending and return the first that
    # actually parses.
    candidates: list[str] = []
    if complete_at is not None:
        # **Not** conditioned on the final stack being empty. Measured: a reply that
        # opened with prose containing one balanced `{...}` and then the real payload
        # has its first balanced point inside the prose, while the document as a whole
        # never balances. Requiring `not stack` threw that candidate away and the
        # whole reply was reported unreadable -- even though the platform had already
        # located a prefix that parses.
        candidates.append(repaired[:complete_at])
    candidates.append(repaired)
    closers = "".join("}" if c == "{" else "]" for c in reversed(stack))
    if in_string:
        candidates.append(repaired + '"')
        candidates.append(repaired + '"' + closers)
    if closers:
        candidates.append(repaired + closers)

    # ...plus truncations at the last few closing brackets, which is what handles a
    # reply with one (or more) closers too many. Bounded, so a pathological reply
    # cannot make this quadratic.
    for idx in range(len(repaired) - 1, max(0, len(repaired) - 2000), -1):
        if repaired[idx] in "}]":
            candidates.append(repaired[:idx + 1])

    for cand in candidates:
        try:
            return json.loads(_drop_trailing_commas(cand))
        except json.JSONDecodeError:
            continue
    return None


def _drop_trailing_commas(text: str) -> str:
    """Remove `,` immediately before `}` or `]`, outside strings.

    String-aware rather than a regex, because `,}` inside a string literal is
    content. A model writing a file appends a trailing comma often enough that it
    is worth one pass.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for idx, ch in enumerate(text):
        if escaped:
            escaped = False
        elif ch == "\\" and in_string:
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string and ch == ",":
            if text[idx + 1:].lstrip()[:1] in ("}", "]"):
                continue
        out.append(ch)
    return "".join(out)


def run_facts(base: Path) -> str:
    """How this run is configured, in the form a model can act on.

    Why this exists, measured rather than assumed: a method was asked to improve a
    harness whose source contains a `_call_mock` branch, and the model concluded
    from that branch that "the mock backend is a feature, not a bug" and declined to
    edit anything -- on a run that had in fact gone to a real model. The trace it was
    shown could not settle it either: a trace records replies and usage, not which
    backend produced them.

    The platform knows. It records `agent_backend` and `agent_model` on every curve
    point, and the point is in this channel. Leaving the method to infer a fact the
    platform already holds is how a model talks itself out of a real improvement.

    Returns "" when the point says nothing, so a caller can concatenate blindly.
    """
    point = {}
    rp = channel(base) / "round.json"
    if rp.exists():
        try:
            point = json.loads(rp.read_text())
        except (OSError, json.JSONDecodeError):
            point = {}
    if not point:
        return ""
    identity = point.get("identity") or {}
    backend = identity.get("agent_backend") or "unknown"
    model = identity.get("agent_model") or "(not recorded)"
    split = point.get("split") or {}
    lines = [
        "## How this run is configured (from the platform's own record)",
        f"- the harness runs against backend `{backend}`, model `{model}`"
        + ("" if backend != "mock" else "  <- THIS IS A MOCK RUN; scores are not real"),
        f"- the harness is shown {len(protocol.studied_per_task(point))} task(s) and "
        f"scored on {point.get('n_scored', '?')}",
        f"- its score on the tasks it is shown: "
        f"{protocol.studied_score(point) if protocol.studied_score(point) is not None else 'n/a'}",
    ]
    if split:
        lines.append(f"- split: train {len(split.get('train') or [])} / "
                     f"eval {len(split.get('eval') or [])} (the eval side's per-task "
                     f"results are withheld from you on purpose)")
    return "\n".join(lines)


def loop_facts_block(base: Path) -> str:
    """What the harness did on each task, counted by the platform, one line per task.

    The platform computes this (`eval/tracing.py:loop_facts`) and stages it in
    `round.json` under `loop_summary.per_task`, and until now nothing put it in front of
    the improver: the endpoint path prepended `run_facts` (configuration only) and the CLI
    path prepended nothing at all. So a method's first job -- "what is wrong with this
    harness" -- had to be done by reading tens of kilobytes of JSONL, and the two failure
    modes this session measured are exactly the ones a table answers at a glance:

    * `large-scale-text-editing`: 31 commands, of which **24 re-issued an earlier one**, and
      the harness's repeat nudge fires only on an immediately consecutive repeat;
    * `cobol-modernization` and `fix-code-vulnerability`: 32 model calls, `ended step-cap`,
      no answer -- the budget is what ran out, not the model's ideas.

    Columns are the ones that decided those cases, and the legend says what each means, so a
    method does not have to guess at a header. Returns "" when the point carries no summary.
    """
    try:
        point = json.loads((channel(base) / "round.json").read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    per_task = ((point.get("loop_summary") or {}).get("per_task") or {})
    if not per_task:
        return ""
    # **The outcome, beside the process.** Without this column the table is a list of ways a
    # run *ended*, and `ended` is not the objective: measured (`v5-dgm`, 2026-10-05) round 0
    # had `cobol-modernization` ending `budget_exhausted` with 3/3 checks passed and
    # `fix-git` ending `crashed` with 2/2 -- both scored 1.0, because the platform scores
    # the artifact, not the exit. The improver was handed the process columns alone, saw
    # "5 of 10 ended budget_exhausted", doubled the wrap-up warning (`FINISH_NUDGE_STEPS`
    # 4 -> 8) to make runs end `answered` instead, and took the score from 0.200 to
    # **0.000**: the two passing tasks were the ones that needed the whole budget to finish
    # their work. A facts table a method can optimize against has to carry the number the
    # method is paid in.
    scores = protocol.studied_per_task(point)
    head = ("## What the harness did, counted from its own traces\n"
            "task                          score calls cmds ans  ended             repeats "
            "nudges empty  clip  perr\n"
            "  **score is the platform's verdict on the artifact; `ended` is only how the run\n"
            "  stopped. A run that ended `budget_exhausted` or `crashed` can still score 1.0 --\n"
            "  do not trade a passing task's budget for a tidier ending.**\n"
            "  calls=model calls, cmds=commands executed, ans=replies with a final answer,\n"
            "  repeats=commands re-issued that this run had already run, nudges=the harness's\n"
            "  own repeat warnings, empty=commands that executed nothing (a repaired reply),\n"
            "  clip=outputs shortened before the model saw them, perr=unparseable replies\n")
    rows = []
    for tid, f in sorted(per_task.items()):
        score = scores.get(tid)
        # `ended` is not truncated: `budget_exhausted` is the value that matters most and
        # it is exactly the one a 12-character column turns into `budget_exhau`.
        rows.append(f"  {tid[:28]:28s} "
                    f"{('  n/a' if score is None else f'{float(score):5.2f}')} "
                    f"{f.get('model_calls', 0):5d} "
                    f"{f.get('commands', 0):4d} {f.get('answers', 0):3d}  "
                    f"{str(f.get('ended') or '?')[:16]:16s} "
                    f"{f.get('repeated_commands', 0):7d} {f.get('repeat_nudges', 0):6d} "
                    f"{f.get('empty_commands', 0):5d} {f.get('output_clipped_events', 0):5d} "
                    f"{f.get('parse_errors', 0):5d}")
    return head + "\n".join(rows)


def hypothesis_of(reply: dict) -> str:
    """The model's one-line rationale, wherever it decided to put it.

    Measured: asked for a top-level `hypothesis`, a 9B model nested it inside the
    file object instead. The label then came out empty, so a real 8 KB edit was
    recorded on the curve with no stated reason at all. Reading both places costs
    nothing and a missing rationale is a worse record than a slightly loose reading.
    """
    text = reply.get("hypothesis")
    if not text:
        for entry in (reply.get("files") or []):
            if isinstance(entry, dict) and entry.get("hypothesis"):
                text = entry["hypothesis"]
                break
    return str(text or "")[:300]


#: Preferred reply format, appended to whatever system prompt a method supplies.
#:
#: A whole file inside a JSON string is where nearly every parse failure came from:
#: the model must escape every quote, newline and backslash in an 8 KB program, and
#: a 9B model does not. Measured across this host: missing closers, extra closers,
#: mismatched bracket types, raw control characters, invalid `\\d` escapes and
#: unescaped quotes -- all of them, in the end, are one problem, which is that the
#: file's own quoting has to survive a second encoding.
#:
#: A fenced block has no second encoding. The file is written verbatim, and JSON is
#: still accepted as a fallback, so nothing that worked before breaks.
FORMAT_HINT = """

## How to reply
Prefer a fenced block per file -- it needs no escaping, so it cannot be mangled:

```file:<relative path>
<the complete new file contents, written verbatim>
```

HYPOTHESIS: <one sentence: what you changed and why>

If the evidence supports no change, reply exactly `NO_CHANGE: <reason>`.
JSON (`{"files": [{"path": ..., "content": ...}], "hypothesis": ...}`) is also
accepted if you prefer it, but every quote and newline in the file must then be
escaped. One of these two, and nothing else on stdout.
"""


def parse_reply(text: str) -> dict:
    """Read a model reply in either accepted format.

    Fenced `file:` blocks first, because they are the format that cannot be broken
    by the file's own quoting, then JSON with the repairs in `loads_tolerant`.
    Returns `{}` when neither yields anything, so the caller reports a failure
    rather than inventing a decision.
    """
    # Scanned on the **raw** text: `_unfence` strips a leading ``` fence, which is
    # exactly the fence a `file:` block begins with, so unfencing first destroyed
    # the format this function exists to read. Found by its own test.
    blocks: list[dict] = []
    lines = text.splitlines()
    i = 0
    hypothesis = ""
    no_change = None
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```file:"):
            rel = stripped[len("```file:"):].strip()
            i += 1
            chunk: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                chunk.append(lines[i])
                i += 1
            blocks.append({"path": rel, "content": "\n".join(chunk) + "\n"})
        elif stripped.upper().startswith("HYPOTHESIS:"):
            hypothesis = stripped[len("HYPOTHESIS:"):].strip()
        elif stripped.upper().startswith("NO_CHANGE:"):
            no_change = stripped[len("NO_CHANGE:"):].strip()
        i += 1
    if blocks:
        return {"files": blocks, "hypothesis": hypothesis}
    if no_change is not None:
        return {"no_change": no_change}

    parsed = loads_tolerant(text)
    return parsed if isinstance(parsed, dict) else {}


# ------------------------------------------------------------ what it cost ---

#: The model calls **this method invocation** has made, in tokens.
#:
#: Module-level because the process *is* the invocation: the driver spawns one method
#: process per round and reads its reply once. Before this existed, every method had to
#: remember to carry a number from wherever it called the model down to `report`, and the
#: measured result was that none of them did -- `cost.method_generation_tokens` was 0 on
#: every round of every run, including rounds where the improver answered six model calls
#: over a 24 KB prompt. A cost-aware acceptance rule was therefore reading a constant, and
#: a constant is a rule that permits everything.
_SPENT: dict = {"input": 0, "output": 0, "calls": 0, "model": None,
                # Prompt tokens the provider served from its cache. Kept beside `input`
                # because the provider reports them as disjoint counts: `input` already
                # excludes them, so adding this into the total would double-bill the cheap
                # half and make the expensive half look smaller than it is.
                "cache_read": 0,
                # **Whether the spend is known at all.** A CLI improver is an agent
                # process whose provider is built in: it prints prose, not a usage
                # record, so nothing reports tokens. Before this field the sum stayed 0
                # and was recorded as `generation_tokens: 0` -- a claim that the improver
                # spent nothing, when the truth is "nobody counted". `None` is the honest
                # value and the platform already distinguishes it from zero.
                "source": None}


#: The last CLI improver run's own account, attached to whichever step the method
#: reports next. Module-level for the same reason `_SPENT` is: one process is one method
#: invocation, so there is exactly one improver run to describe.
_LAST_IMPROVER: dict = {}


#: The line a usage-reporting improver CLI prints. `dsh` was patched to emit it on stderr:
#: `packages/bundle/headless/src/index.ts` used to receive the provider's usage chunk and
#: `return` without printing it, so a CLI-driven round had no token count to report at all.
DASH_USAGE_PREFIX = "dsh-usage "


def parse_usage_lines(transcript: str) -> dict | None:
    """Sum every `dsh-usage {json}` line a CLI wrote, or `None` if it wrote none.

    A CLI round makes many model calls, and the CLI prints one line per call, so the round's
    spend is the sum. `None` -- not a zero dict -- because "this CLI does not report usage"
    and "this CLI spent nothing" are different facts, and only one of them is plausible.
    """
    total = {"input": 0, "output": 0, "cache_read": 0, "calls": 0}
    found = False
    for line in (transcript or "").splitlines():
        line = line.strip()
        if not line.startswith(DASH_USAGE_PREFIX):
            continue
        try:
            payload = json.loads(line[len(DASH_USAGE_PREFIX):])
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        for key, field in (("input_tokens", "input"), ("output_tokens", "output"),
                           ("cache_read_tokens", "cache_read")):
            value = payload.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            total[field] += value
        total["calls"] += 1
        found = True
    return total if found else None


def improver_run() -> dict:
    """What the last CLI improver reported about itself (empty for endpoint improvers)."""
    return dict(_LAST_IMPROVER)


def spend_source() -> str | None:
    """Who reported the spend: `None` (nobody yet) or `cli_unreported`.

    Kept out of `spent()` on purpose: that mapping is the four numbers and the model, and
    a caller comparing it to the numbers it recorded should not have to filter a string
    out of it.
    """
    return _SPENT.get("source")


def spent() -> dict:
    """A copy of what this process has spent so far.

    `calls` is the field that keeps an unreported spend from looking like no spend: an
    OpenAI-compatible endpoint that omits `usage` leaves the token sums at 0, and a reader
    otherwise cannot tell "the improver did nothing" from "the improver's spend was not
    reported".
    """
    return {k: _SPENT[k] for k in ("input", "output", "calls", "model")}


def cache_read_tokens() -> int | None:
    """Prompt tokens the provider served from cache, or `None` when nobody reported any."""
    if _SPENT.get("source") == "cli_unreported":
        return None
    return _SPENT["cache_read"]


def spent_tokens() -> int | None:
    """Input + output, or **`None` when nothing reported them**.

    A CLI improver cannot report its spend: the provider lives inside the agent process
    and its output is a transcript. Summing to 0 would record "the improver used no
    tokens", which is a different and false statement -- and one a cost-aware rule would
    act on.
    """
    if _SPENT.get("source") == "cli_unreported":
        return None
    return _SPENT["input"] + _SPENT["output"]


def reset_spent() -> None:
    """Start a fresh accounting window inside one process.

    A method process is one invocation and calls `report` once, so no method needs this.
    It exists for a caller that wants the spend of one *phase* rather than of the process
    -- the two-stage loops that ask a proposer and then a critic -- and for tests, which
    share an interpreter and would otherwise read each other's numbers.
    """
    # `cache_read` resets with the rest: a per-phase window that kept the previous phase's
    # cache reads would report a split that belongs to a different call.
    _SPENT.update(input=0, output=0, calls=0, model=None, source=None, cache_read=0)


def _cached_prompt_tokens(usage) -> int:
    """Prompt tokens the provider says it served from cache, in any of the shapes it uses.

    Three spellings exist in the wild and a client sees different ones depending on how it
    parses the body: OpenAI's `prompt_tokens_details.cached_tokens`, DeepSeek's native
    `prompt_cache_hit_tokens`, and whatever the SDK left in `model_extra`. Reading all
    three is the difference between an endpoint row that can be compared with a CLI row and
    one that silently reports zero cache hits.
    """
    def as_int(value) -> int:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0

    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if isinstance(cached, int) and not isinstance(cached, bool):
            return cached
        if isinstance(details, dict):
            cached = details.get("cached_tokens")
            if isinstance(cached, int) and not isinstance(cached, bool):
                return cached
    for name in ("prompt_cache_hit_tokens", "cache_read_tokens", "cached_tokens"):
        cached = getattr(usage, name, None)
        if isinstance(cached, int) and not isinstance(cached, bool):
            return cached
    extra = getattr(usage, "model_extra", None) or {}
    if isinstance(extra, dict):
        for name in ("prompt_cache_hit_tokens", "cache_read_tokens", "cached_tokens"):
            cached = extra.get(name)
            if isinstance(cached, int) and not isinstance(cached, bool):
                return cached
    return 0


def _record_usage(usage, model: str | None) -> None:
    """Add one reply's usage. One call is counted whether or not the provider reported it."""
    _SPENT["calls"] += 1
    # An endpoint reply that arrives means the spend *is* being counted, whatever an
    # earlier CLI call in the same process concluded. Without this the flag set by
    # `_ask_cli` outlives the call it described and turns a later, reported spend into
    # `None`.
    _SPENT["source"] = "endpoint"
    _SPENT["model"] = getattr(usage, "model", None) or model or _SPENT["model"]
    if usage is None:
        return
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    cached = _cached_prompt_tokens(usage)
    # **Cache reads are reported here too, and subtracted from the input count.** The
    # provider gives a single `prompt_tokens` that includes whatever it served from its
    # cache, while the CLI channel reports the two *disjoint* (its `inputTokens` already
    # excludes cache reads). Keeping one convention is what makes a mixed method's two
    # rows add up: input is the tokens that were actually priced as fresh, cache_read is
    # the cheap remainder, and neither contains the other.
    _SPENT["input"] += max(0, prompt - cached)
    _SPENT["cache_read"] += cached
    _SPENT["output"] += int(getattr(usage, "completion_tokens", 0) or 0)


def skill_block(base: Path | None) -> str:
    """The platform's skill, as the prompt block every method inherits by default.

    **Why this is here and not in each method.** The platform stages `improvers/skill.md`
    as `_harnessgrad/SKILL.md` for every method (`harnessgrad/channel.py`), and
    `identity.skill_sha` records it on every point -- but until this function existed only
    four of twelve methods read it, each with its own copy of the path, the header and the
    truncation. That is the same defect the record already had once: a point that names a
    skill it cannot prove was in the prompt. With the block injected here, `skill_sha`
    means "the skill this prompt carried", and a method that wants a different or no skill
    says so once, explicitly, at its call site -- `ask(..., skill=False)`.

    `None`/empty when nothing was staged: a run with no skill gets no block, and the point
    omits `skill_sha` for the same reason (`harnessgrad/identity.py:_skill_sha`).
    """
    if base is None:
        return ""
    path = channel(base) / "SKILL.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) > SKILL_LIMIT:
        text = (text[:SKILL_LIMIT]
                + f"\n\n[truncated: the skill is {len(text)} chars long, "
                  f"limit {SKILL_LIMIT}] ...\n")
    return ("## How to improve a harness (the platform's skill)\n" + text)



def cli_improver_identity(base: Path | None) -> dict | None:
    """The CLI improver this run resolved, if it is one.

    Read from the staged `round.json`, which is the same resolution the curve point names --
    so a method cannot drive a different improver than the record says. `HG_IMPROVER` is the
    operator's override and wins, because "run *this* tool" is a deliberate act.

    **Two shapes of evidence, because one of them was missing.** This asked only for
    `kind == "cli"`, and `kind` was not among the fields the platform copied into
    `round.json` (`harnessgrad/identity.py:IMPROVER_IDENTITY_FIELDS`), so the answer was
    always `None` and every method silently used the chat endpoint instead of the CLI it had
    resolved. The rule `cli_improver.applies` states, and has always stated, is the one that
    survives a record written by an older platform: a CLI carries a `path` and no `base_url`,
    while an endpoint resolution sets **both** to its base URL
    (`tools/improver.py:resolve`). Reading either keeps the dispatch and the record in
    agreement, and `kind` is checked first because it is the field that means it.
    """
    if os.environ.get("HG_IMPROVER"):
        return {"name": os.environ["HG_IMPROVER"], "kind": "cli", "path": ""}
    if base is None:
        return None
    try:
        summary = json.loads((channel(base) / "round.json").read_text())
    except (OSError, ValueError):
        return None
    improver = (summary.get("identity") or {}).get("improver") or {}
    if improver.get("kind") == "cli":
        return improver
    # The fallback for a record written before `kind` was recorded: a CLI carries a `path`
    # and **no `base_url`**, while an endpoint carries both, with `path` set to the URL
    # itself (`tools/improver.py:resolve`). This only works because `base_url` is now one of
    # the fields the platform copies onto the record -- without it, `path` alone made every
    # endpoint look like a command, and `--improver deepseek` died in the sandbox trying to
    # `Path()` a URL. Measured: `ds-dgm` round 1.
    if improver.get("path") and not improver.get("base_url"):
        return improver
    return None


def _method_workspace() -> Path:
    """The directory the platform copies a method's own `*.log` files out of.

    It is this process's working directory: `harnessgrad/loops/mode_a.py` runs the method with
    `cwd=method_ws` and then calls `records._keep_method_logs(method_ws, run_dir, round)`,
    which copies the `*.log` files at that directory's root into
    `<run>/method_logs/round-N/`. Kept as a function rather than an inline `Path.cwd()` so a
    test can point it somewhere and so the coupling is named in one place.
    """
    return Path.cwd()


def _keep_improver_transcript(result: dict, improver_ws: Path) -> list[str]:
    """Copy what the CLI improver left behind to where the platform will keep it.

    **Both files the improver writes are destroyed, and both are evidence.** `propose` writes
    the transcript -- up to 200 kB of what it read, what it tried, and why it changed nothing
    -- to `<improver_ws>/improver.log`, and the launcher writes the model pin to
    `<improver_ws>/.dsh-model-pin.yml`. Both statements about that contract are in the tree
    (`methods/cli_improver.py`'s module docstring and `harnessgrad/records.py:642`) and both
    were false for every sandboxed round:

      * `improver_ws` is a `tempfile.mkdtemp` under `/tmp`, and
        `eval/containers/sandbox.py` mounts an **empty tmpfs** on `/tmp` ("a private /tmp, so
        two runs cannot read each other's task directories") -- so it is gone with the
        namespace, not merely un-copied;
      * `_keep_method_logs` scans the *method workspace*, which is a different directory and a
        host path bound into the namespace.

    Measured: twelve surviving `hg-cli-ask-*` directories on the host, every one of them
    empty, and every one of them from a test run -- a test stubs `propose`, so it leaves the
    directory and nothing in it, while a real round leaves nothing at all. The question this
    costs is the one a comparison of *methods* is mostly about: did the improver read the
    traces, or did it guess?

    Written as `*.log` at the method workspace root, which is the convention
    `_keep_method_logs` documents, so no new path convention is invented and the platform
    still does not need to know which method it is. Returns the names written.
    """
    written: list[str] = []
    destination = _method_workspace()
    if not destination.is_dir():
        return written
    transcript = str(result.get("transcript") or "")
    payloads = [(f"improver-{improver_ws.name}.log", transcript[-200_000:])]
    pin = improver_ws / ".dsh-model-pin.yml"
    if pin.is_file():
        # The pin is the record of which model the improver was *actually* given, which is not
        # always the registry's value: `HG_IMPROVER_MODEL` overrides it, and without this the
        # override would be invisible in the record.
        payloads.append((f"improver-{improver_ws.name}-model-pin.log",
                         pin.read_text(errors="replace")[:4_000]))
    for name, text in payloads:
        if not text:
            continue
        try:
            (destination / name).write_text(text, encoding="utf-8")
        except OSError:
            continue
        written.append(name)
    return written


def artifact_path_error(name) -> str | None:
    """Artifact keys are relative to artifacts/, with no parent traversal."""
    if (not isinstance(name, str) or not name or "\x00" in name
            or Path(name).is_absolute() or PureWindowsPath(name).anchor
            or ".." in Path(name).parts or ".." in PureWindowsPath(name).parts
            or "\\" in name or name == "."):
        return f"unsafe artifact path: {name!r}"
    return None


def _read_cli_artifacts(workspace: Path) -> dict[str, str]:
    """Read only artifacts/, without following links, under byte limits.

    TTHE text_to_sql/proposer.py:150-164,372-388 writes and reads a JSON
    proposal card on disk. This bridge transports that file unchanged instead
    of requiring a CLI's prose response to contain structured method fields.
    Directory-relative opens with O_NOFOLLOW keep even replaced links from
    redirecting a read outside the artifact directory. Non-text files fail
    explicitly: replacing bad bytes or truncating would alter a method's card.
    """
    artifacts: dict[str, str] = {}
    total = 0
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def collect(directory: int, prefix: str = "") -> None:
        nonlocal total
        for name in sorted(os.listdir(directory)):
            rel = f"{prefix}{name}"
            problem = artifact_path_error(rel)
            if problem:
                raise UnparseableReply(problem)
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise UnparseableReply(f"artifact symlink is forbidden: {rel!r}")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, directory_flags, dir_fd=directory)
                try:
                    collect(child, rel + "/")
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise UnparseableReply(f"artifact is not a regular file: {rel!r}")
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise UnparseableReply(f"artifact is not a regular file: {rel!r}")
                remaining = CLI_ARTIFACT_TOTAL_LIMIT - total
                data = stream.read(min(CLI_ARTIFACT_FILE_LIMIT, remaining) + 1)
            if len(data) > CLI_ARTIFACT_FILE_LIMIT:
                raise UnparseableReply(f"artifact {rel!r} exceeds per-file limit "
                                       f"({CLI_ARTIFACT_FILE_LIMIT} bytes)")
            if len(data) > remaining:
                raise UnparseableReply(f"artifact {rel!r} exceeds total limit "
                                       f"({CLI_ARTIFACT_TOTAL_LIMIT} bytes)")
            try:
                artifacts[rel] = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise UnparseableReply(f"artifact {rel!r} must be UTF-8 text") from exc
            total += len(data)

    try:
        if (workspace / "artifacts").is_symlink():
            raise UnparseableReply("artifact directory symlink is forbidden: 'artifacts'")
        root = os.open(workspace / "artifacts", directory_flags)
        try:
            collect(root)
        finally:
            os.close(root)
    except OSError as exc:
        raise UnparseableReply(f"artifact read failed: {exc}") from exc
    return artifacts


def _ask_cli(prompt: str, base: Path, parent: Path, improver: dict) -> dict:
    """Propose through a **CLI** improver and return the result as an edit sequence.

    The two improver layers have different shapes: an endpoint returns edits that the method
    applies, a CLI edits a tree it was given. This bridges them in one place, so a rule
    written against `ask` -- RRSI's band, DGM's archive, TTHE's gate -- runs on either
    without knowing which: the CLI is given the **parent tree**, and the difference between
    what it produced and that parent comes back as `content` edits, which the rule's own
    `apply_edits` then reproduces onto its candidate path.

    Giving it the parent (not the incumbent) is what keeps a parent-selection rule real: DGM
    choosing round 3 must have the improver edit round 3's tree.
    """
    import cli_improver                                            # noqa: PLC0415
    # **The same facts the endpoint path gets.** `ask` prepends `run_facts` before it
    # dispatches, and the CLI branch returns before that line -- so a CLI improver was handed
    # a bare task and had to open `evidence/round.json` itself to learn the backend, the
    # model, the score, and what the harness did on each task. The numbers are one line per
    # task and they are what a hypothesis is made of (`loop_facts_block`).
    facts = "\n\n".join(b for b in (run_facts(base), loop_facts_block(base)) if b)
    # **The evidence is named, not compiled in.** `propose` has staged the whole channel as
    # `evidence/` beside the tree the improver edits, so the paths are enough and the prompt
    # stops growing with the dataset (`evidence_pointer` states the ceiling this removes).
    workspace = Path(tempfile.mkdtemp(prefix="hg-cli-ask-"))
    artifact_dir = workspace / "artifacts"
    artifact_dir.mkdir()
    prompt += (
        "\n\n## Method artifacts\n"
        f"If this method requests artifacts/<path>, write it under `{artifact_dir}` "
        "(relative to your harness working directory: `../artifacts/<path>`). "
        "Artifacts are separate from harness edits. Only UTF-8 files in that directory "
        f"are returned, unchanged, with relative keys; limits: {CLI_ARTIFACT_FILE_LIMIT} "
        f"bytes per file, {CLI_ARTIFACT_TOTAL_LIMIT} bytes total. "
        "Absolute artifact keys, parent traversal and symlinks are forbidden.\n")
    req = {"platform_api_version": "0.1.0", "workspace": str(workspace),
           "base_harness": str(parent), "candidate": str(workspace / "candidate"),
           "improver": improver, "channel": str(channel(base)),
           "trajectory_out": "", "task_ids": [], "train_task_ids": []}
    result = cli_improver.propose(req, parent, prompt=prompt, facts=facts,
                                   evidence=evidence_pointer(base))
    _keep_improver_transcript(result, workspace)
    artifacts = _read_cli_artifacts(workspace)
    # **What the improver itself cost, as far as anyone can see.** `propose` returns a
    # `reported` block -- exit code, wall-clock seconds, which files moved, and what the
    # environment boundary withheld -- and `_ask_cli` used to drop it on the floor, so a
    # nine-method batch could not answer "how long did the improver take per round".
    # Tokens are *not* in it: the CLI's provider is built in, so the honest record is the
    # wall clock plus the prompt size we constructed ourselves.
    reported = result.get("reported") or {}
    # What the provider said it spent, if this CLI reports it at all.
    usage = parse_usage_lines(result.get("transcript") or "")
    if usage:
        _SPENT["source"] = "cli_reported"
        _SPENT["input"] += usage["input"]
        _SPENT["output"] += usage["output"]
        _SPENT["cache_read"] += usage["cache_read"]
        _SPENT["calls"] += usage["calls"]
    _LAST_IMPROVER.clear()
    _LAST_IMPROVER.update({
        "exit_code": reported.get("exit_code"),
        "seconds": reported.get("seconds"),
        "files_changed": len(result.get("files") or []),
        "env_withheld": reported.get("env_withheld"),
        "prompt_chars": len(prompt),
        # A token count we can defend: the prompt is ours, so its length is exact and
        # the usual characters-per-token ratio makes the estimate reproducible. Label
        # it as an estimate in the name, not in a comment nobody reads.
        "prompt_tokens_estimate": round(len(prompt) / 4),
        "spend_reported": bool(usage),
        **({"usage": usage} if usage else {}),
    })
    if not usage:
        _SPENT["source"] = "cli_unreported"
    edits: list[dict] = []
    for rel in result["files"]:
        path = Path(result["candidate"]) / rel
        if not path.is_file():
            # **A file the improver removed is an edit, not a read failure.** `_changed_files`
            # walks the pristine side and names what the candidate no longer has, so a
            # deletion arrives here -- and this loop used to swallow it in `except OSError:
            # continue`. Two consequences, both silent: a round that deleted one file and
            # edited another measured a tree that still contained the deleted file, and a
            # round that *only* deleted files produced an empty `edits`, fell into the
            # `no_change` return below, and recorded "the improver decided to change nothing"
            # for a change it had actually made. That is the same collapse `UnparseableReply`
            # exists to prevent, one layer down.
            edits.append({"path": rel, "delete": True})
            continue
        edits.append({"path": rel, "content": path.read_text(errors="replace")})
    if not edits:
        # **A crashed CLI improver is not a decision.** `cli_improver.run` already draws this
        # line (`if code != 0 and not changed:` -> `reported["error"]`), and this path did
        # not: it wrapped whatever the improver had printed into `{"no_change": <prose>}`,
        # so a call that never ran became a method saying "I chose not to change anything".
        # Measured (`v12-dgm`, 2026-10-05): the improver was codex, three rounds printed
        # prose and edited nothing, and the curve read "DGM: no child produced" -- a decision
        # nobody made, in the shape `UnparseableReply`'s own docstring forbids.
        #
        # Raised, not returned, because the two callers already do the right thing with an
        # exception: `ask_for_json` retries it with the failure fed back, and a method that
        # calls `ask` directly records it as an outage rather than as a proposal it declined.
        if result["exit_code"] != 0:
            raise UnparseableReply(
                f"the CLI improver exited {result['exit_code']} and changed nothing: "
                f"{(result['note'] or '').strip()[:300]}")
        # Exit 0 with no files is a genuine decline, and `cli_improver`'s prompt makes it name
        # a mechanism, so the note *is* the reason. Kept as `no_change` on purpose.
        return {"no_change": result["note"] or (
            "the CLI improver exited 0 and changed nothing"), "artifacts": artifacts}
    return {"files": edits,
            "hypothesis": result["note"] or f"{len(edits)} file(s) edited by the CLI",
            "artifacts": artifacts}


#: One counter for endpoint call records, so two calls in the same second cannot collide.
_CALL_SEQ = itertools.count(1)


def _record_endpoint_call(prompt: str, system: str, reply: str, model: str,
                          resp: object) -> None:
    """Keep one endpoint conversation on disk, in the run's own `method_logs` tree.

    **The CLI transcript already keeps its reasoning; this is the other half.** When a
    method's proposal goes through the chat endpoint instead of the CLI improver -- an
    `endpoint_only=True` design call, or a run with no CLI improver at all -- the prompt
    and the reply existed only in memory. Measured on 2026-10-17: `meta_harness`'s design
    step and six of `dream_rsi`'s nine rounds were endpoint calls, so the only surviving
    evidence of *why* those harnesses were proposed was the parsed edit itself.

    The directory comes from `HG_CALL_LOG_DIR`, which the driver sets per round. Without
    it (a method invoked by hand) nothing is written: guessing a path would scatter files
    somewhere nobody looks.

    Never raises. A record that fails must not turn a measured call into a failed one.
    """
    directory = os.environ.get("HG_CALL_LOG_DIR")
    if not directory:
        return
    usage = getattr(resp, "usage", None)
    try:
        dest = Path(directory) / "endpoint"
        dest.mkdir(parents=True, exist_ok=True)
        record = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": model,
            "system": system,
            "prompt": prompt,
            "reply": reply,
            "prompt_chars": len(prompt),
            "reply_chars": len(reply),
            "usage": None if usage is None else {
                "input": getattr(usage, "prompt_tokens", None),
                "output": getattr(usage, "completion_tokens", None),
                "cached": ((getattr(usage, "prompt_tokens_details", None) or None)
                           and getattr(usage.prompt_tokens_details, "cached_tokens", None)),
            },
        }
        name = (f"{time.strftime('%H%M%S', time.gmtime())}-{os.getpid()}-"
                f"{next(_CALL_SEQ):04d}.json")
        (dest / name).write_text(json.dumps(record, ensure_ascii=False, indent=1))
    except (OSError, TypeError, ValueError):
        return


def ask(prompt: str, system: str = DEFAULT_SYSTEM, base: Path | None = None,
        skill: bool = True, parent: Path | None = None,
        endpoint_only: bool = False) -> dict:
    """One model call, with a bounded retry when the reply cannot be read.

    `base` adds the run's configuration block ahead of the method's own prompt. It is
    passed explicitly rather than inferred, because the prompt a method builds is its
    own business and silently rewriting it would make every method's prompt
    unreproducible from its source. The same `base` also supplies the platform's **skill**
    (`skill_block`), injected by default because it is staged for every method and named on
    every point; `skill=False` is how a method says it wants none.

    **One dispatch point for the improver layer.** When the run resolved a *CLI* improver
    (codex), this call drives it instead of a chat endpoint -- on the tree named by `parent`
    (the one the rule chose to build on) -- and returns its edit as an ordinary edit
    sequence. A rule therefore runs on either layer without knowing which it got, which is
    what makes "the RSI rule is the rule; codex is the shell" true rather than aspirational.
    `parent=None` means "build on the incumbent", which is what most A-shaped rules want.

    **`endpoint_only=True` refuses that dispatch.** The caller that needs it is the one
    asking a *schema* question rather than for an edit: a CLI improver answers by editing a
    tree and printing a paragraph, so it cannot return `{"verdict": ...}`. Measured
    (`v4-rrsi`, 2026-10-05): RRSI's leakage critic was routed to codex, codex replied by
    editing `.gitignore` and quoting the intended verdict inside its prose, the critic's
    `accept()` predicate found no top-level `verdict`, and the critic rejected **every**
    candidate in all three rounds -- the A-shaped arm measured no candidate at all, and the
    reason read on the curve like a weak proposer rather than a mis-routed call.

    Retries because a small model's malformed JSON is intermittent, so a second
    sample usually parses -- but only a bounded number of times, and then it is
    reported. Silently returning `no_change` on a parse failure is the one thing
    that must not happen: it turns a broken call into a method decision.
    """
    from openai import OpenAI

    # **One dispatch point for the improver layer.** When this run resolved a *CLI* improver
    # (codex), drive it rather than a chat endpoint -- on the tree the rule chose to build on
    # (`parent`, defaulting to the incumbent) -- and hand the result back as an ordinary edit
    # sequence. A rule thus runs on either layer without knowing which it got, which is what
    # makes "the RSI rule is the rule; codex is the shell" true rather than aspirational.
    #
    # **The CLI needs no endpoint, so it must not be made to require one.** `model_settings()`
    # below demands `HG_METHOD_BASE_URL / _API_KEY / _MODEL` and raises when any is missing,
    # and a run whose improver is a CLI injects none of them -- `harnessgrad/methods.py` only
    # writes them for an improver that carries a `base_url`. Called first, as it was, it
    # refused every CLI run with "a method brings its own credentials": the one layer with no
    # endpoint would have been the only one that could not start without one.
    cli = None if endpoint_only else cli_improver_identity(base)
    if cli is not None:
        tree = Path(parent) if parent is not None else Path(base)
        return _ask_cli(prompt, Path(base) if base is not None else tree, tree, cli)

    base_url, api_key, model = model_settings()
    client = OpenAI(base_url=base_url, api_key=api_key,
                    timeout=float(os.environ.get("HG_METHOD_TIMEOUT_S", "180")),
                    max_retries=2)
    attempts = max(1, int(os.environ.get("HG_METHOD_PARSE_RETRIES", "2")))
    system = system + FORMAT_HINT
    if base is not None:
        # The endpoint layer cannot read `evidence/`, so it gets the content inlined; and it
        # never received the platform's ask or its hard rules before this -- those lived only
        # in `cli_improver`, which this branch does not go through.
        import cli_improver                                          # noqa: PLC0415
        # **The evidence is about the tree the edits land on.** A CLI improver is driven on
        # `parent` (above), so a chat endpoint shown the *incumbent's* sources would be
        # answering a question about a different tree than the one its answer is applied
        # to. Latent while every caller passes the incumbent as `parent`; wrong the moment
        # one selects an older state, which is the case the parameter exists for.
        source = Path(parent) if parent is not None else Path(base)
        facts = "\n\n".join(
            b for b in (run_facts(base), loop_facts_block(base),
                        evidence_content(source)) if b)
        prompt = "\n\n".join(
            x for x in (cli_improver.CHANGE_REQUEST, cli_improver.HARD_RULES, facts, prompt) if x)
        if skill:
            block = skill_block(base)
            if block:
                prompt = block + "\n\n" + prompt
    last = ""
    for _ in range(attempts):
        resp = client.chat.completions.create(
            model=model, temperature=0.0,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
        )
        last = resp.choices[0].message.content or ""
        # Counted before the reply is judged. A retry that produced unparseable JSON is
        # still a call the method paid for, and a parse failure is exactly when someone
        # asks what the round cost.
        _record_usage(getattr(resp, "usage", None), getattr(resp, "model", None) or model)
        _record_endpoint_call(prompt, system, last, getattr(resp, "model", None) or model, resp)
        # Debug affordance: `HG_METHOD_DUMP=<path>` writes the raw reply out. A model
        # reply that will not parse is the hardest failure to diagnose from the
        # outside -- the platform only ever sees "unparseable" and 200 characters of
        # tail -- so the raw text has to be obtainable without editing the method.
        dump = os.environ.get("HG_METHOD_DUMP")
        if dump:
            try:
                Path(dump).write_text(last)
            except OSError:
                pass
        parsed = parse_reply(last)
        if parsed:
            return parsed
    raise UnparseableReply(
        f"the model replied {len(last)} chars but no attempt parsed as JSON; "
        f"tail: {last[-200:]!r}")


# ------------------------------------------------------------- the candidate ---

class CandidateInvalid(Exception):
    """The candidate is not a harness the platform can measure. Raised by `report`.

    Raised rather than returned because it is not a method *decision* -- the method
    wanted to hand over a harness and what it has is not one. A method that catches this
    can re-propose; a method that does not gets the `fail()` path, which records the
    reason instead of a score.
    """


def apply_edits(base: Path, dest: Path, edits: list) -> tuple[bool, list[str], str]:
    """Apply an edit sequence onto a copy of `base`, then check the result is a harness.

    The applying itself lives in `eval/candidate.py`, which is the platform's definition
    of what it is about to measure. It used to live here, which meant every port carried
    its own idea of "applied", and the one thing the platform could not do was check the
    claim independently.

    **Step 2 of the two-step protocol.** Step 1 is the model proposing the sequence; this
    is where it is guaranteed to apply. Returns `(changed, paths, problems)` -- a
    non-empty third value is a *reported* failure the caller is expected to act on, not a
    silent `False`.
    """
    applied, problems = candidate.apply(base, dest, edits)
    if not problems:
        problems = candidate.validate(dest)
    return (not problems and bool(applied)), applied, "; ".join(problems)


def _read_manifest(root: Path) -> dict:
    try:
        man = json.loads((Path(root) / "harness.json").read_text(encoding="utf-8"))
        return man if isinstance(man, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def repair_block(problems: str, *, expect: str) -> str:
    """Append a precise apply/format failure to the method's next prompt."""
    if not problems:
        return ""
    return ("\n\n## Your previous reply could not be used\n\n"
            f"{problems}\n\n{expect}")


EDIT_EXPECT = ("Return the complete corrected edit sequence as JSON, with the complete "
               "contents of every file it names, and nothing else.")


def ask_for_json(prompt_for, *, system, base=None, attempts: int = 3,
                 parent=None, endpoint_only: bool = False,
                 accept=None, what: str = "a JSON reply",
                 skill: bool = True) -> tuple[dict, str]:
    """Retry a JSON-producing model call with the failure fed back to the model.

    `accept(reply)` returns an empty string when the reply is usable, or the text that
    should be placed in the next prompt. This is deliberately shared by proposer and
    critic: repeating the exact same payload after a formatting failure is three copies
    of the same failure, not a repair.

    **`parent` used to be accepted and then dropped.** The parameter was in this signature
    for the CLI dispatch and never reached `ask`, so every `propose_and_apply(..., parent=...)`
    drove the improver on `base` -- the *channel* -- instead of the tree the rule chose.
    Passed through now.

    `endpoint_only` goes with it and says the other half of the same thing: a CLI improver
    is an editor, so a call that must come back as `{"verdict": ...}` cannot be served by
    one (`ask` documents the measured case).
    """
    problems = ""
    reply: dict = {}
    for _ in range(max(1, attempts)):
        try:
            call = {"system": system, "base": base, "skill": skill, "parent": parent}
            # Passed only when set: the stub `ask` in the tests is the seam every rule is
            # tested through, and a keyword it has never heard of turns a formatting
            # question into a `TypeError` in the test that was asking it.
            if endpoint_only:
                call["endpoint_only"] = True
            reply = ask(prompt_for(problems), **call)
        except UnparseableReply as exc:
            problems = (f"your reply could not be read as {what}: {exc}. "
                        "Answer with the JSON envelope only, and nothing else.")
            continue
        if accept is None:
            return reply, ""
        problem = accept(reply)
        if not problem:
            return reply, ""
        problems = problem
    return reply, problems


def propose_and_apply(base: Path, req: dict, system: str, prompt_for, *,
                      parent=None,
                      attempts: int = 3, skill: bool = True) -> tuple[dict, Path, str]:
    """Two phases: propose an edit sequence, then apply and validate it.

    **`parent` is the tree the edits land on.** It is what the improver was driven against
    (`ask_for_json(..., parent=parent)` gives a CLI improver that tree to edit), so
    applying the returned sequence anywhere else silently produces a different candidate
    than the one the rule asked for -- `incumbent + parent's files` rather than `parent`.
    Latent rather than observed: no caller passes a non-incumbent `parent` today, and
    `methods/base.py` applies onto `rnd.parent` itself instead of coming through here.
    Fixed anyway, because the next rule that selects an older state is the one this
    parameter exists for.
    """
    dest = candidate_path(req)
    tree = Path(parent) if parent is not None else base

    def accept(reply: dict) -> str:
        if isinstance(reply, dict) and "no_change" in reply:
            return ""
        _, _, problems = apply_edits(tree, dest, reply.get("files"))
        return problems

    reply, problems = ask_for_json(
        prompt_for, system=system, base=base, attempts=attempts, parent=parent,
        accept=accept, what="an edit sequence", skill=skill)
    return reply, dest, problems


def report(req: dict, *, method: str, harness_dir: Path, changed: bool,
           files: list[str] | None = None, hypothesis: str = "",
           edit_kind: str | None = None, method_reported: dict | None = None,
           acceptance_rule: str = "", tokens: int | None = None, label: str = "",
           extra: dict | None = None, stop: bool | None = None,
           note: str | None = None) -> int:
    """Write the trajectory and emit the reply. Every exit path goes through here.

    Centralised because "the method wrote no trajectory" is the least diagnosable
    failure this platform has: the driver can only report the absence, and the
    cause -- a provider error, an unparseable reply, a rejected path -- is lost at
    exactly the moment someone needs it.

    **A candidate that does not load is refused here, not measured.** `report` validates
    `harness_dir` and raises `CandidateInvalid` when it is not a harness -- so "reported a
    candidate" and "the edit sequence applied" are the same statement, and a method cannot
    hand over a tree that the platform will score as a weak harness. `changed=False` with
    the pristine base as `harness_dir` is still allowed and still means "I chose not to
    change anything"; that is the one case a reader is entitled to read as a decision.

    `stop` separates two things `changed: false` used to mean at once. A method
    that *rejects* a candidate -- RRSI's leakage critic, TTHE's rollback gate,
    AHE's HARMFUL verdict -- is mid-loop and wants its next round; a method that
    has nothing left to say is finished. Left unset, `stop` defaults to
    `not changed`, which is the behaviour every method had before the field
    existed. A filter sets `stop=False` and keeps its budget.

    `tokens=None` means **what this process spent**, not zero: the numbers come from
    `spent()` (see `_SPENT`). A method may still pass an explicit count, and the one that
    passes 0 is a method that made no model call at all (`echo_base`), which is a fact
    rather than a default.
    """
    spent_now = spent()
    if tokens is None:
        tokens = spent_tokens()
    if changed:
        problems = candidate.validate(Path(harness_dir))
        if problems:
            raise CandidateInvalid(
                "refusing to report a candidate that is not a harness: "
                + "; ".join(problems))

    trajectory = {
        "steps": [{
            "harness_dir": str(harness_dir),
            "label": label or hypothesis[:80] or ("edit" if changed else "no change"),
            "edit_kind": edit_kind or ("none" if not changed else "harness_source"),
            # `generation_tokens` is the contract field and stays the single number a
            # cost-aware rule reads. The split is kept beside it because it is the part
            # that says *why* the number is large: measured on our own improver, one
            # round is ~24 KB of harness sources in and ~200 tokens out, so a rising
            # improver cost is a source-reading cost, not a thinking cost.
            "claimed_cost": {"generation_tokens": tokens,
                             "method_input_tokens": spent_now["input"] or None,
                             "method_output_tokens": spent_now["output"] or None,
                             "method_model_calls": spent_now["calls"] or None,
                             "method_model": spent_now["model"],
                             # Null tokens above mean "nobody counted" only if this says
                             # so; without it a reader cannot tell a free round from an
                             # invisible one.
                             "method_spend_source": spend_source(),
                             "method_cache_read_tokens": cache_read_tokens()},
            "method_reported": {"score": None, **_reported_with_improver(method_reported)},
        }],
        "trajectory_shape": "sequence",
        # Mode A: the platform owns the schedule, so nobody nominates a step.
        "nominated": 0,
        "provenance": {
            "method": method,
            "acceptance_rule": {"text": acceptance_rule,
                                "source": f"methods/{method}/run.py",
                                "calibrated": False},
            **(extra or {}),
        },
    }
    # `Path("")` is `PosixPath('.')`, which is truthy, so the old `if out:` guard let a
    # request with no `trajectory_out` through and then wrote the trajectory *onto a
    # directory*. The driver always sets the key, so this only ever hit a method invoked
    # by hand -- which is exactly how the next person will invoke it.
    raw_out = req.get("trajectory_out")
    out = Path(raw_out) if raw_out else None
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(trajectory, indent=1))

    response = {"modified": bool(changed), "changed": bool(changed),
                "files": list(files or []), "hypothesis": hypothesis,
                "generation_tokens": tokens,
                # What the process itself saw the provider report. Kept apart from
                # `generation_tokens` on purpose: the claim and the observation are two
                # statements, and a method that counts its own calls by hand is exactly
                # the case where they can disagree.
                "method_usage": spent_now,
                "method_reported": _reported_with_improver(method_reported)}
    if stop is not None:
        response["stop"] = bool(stop)
    # The method's own one-line account of this round. The driver falls back to a
    # neutral default, because it cannot tell a rejected candidate from a declined
    # proposal -- and guessing makes the log say something the method did not.
    if note is not None:
        response["note"] = str(note)[:160]
    emit(response)
    return 0



#: "Attach whatever this process last saw", for callers that cannot be wrong about it.
#:
#: `report` is such a caller: one process is one method call, so the improver run recorded
#: at the end of it *is* the one that produced this candidate. `report_many` is not, and the
#: difference is the whole reason this sentinel exists -- see `_step_payload`.
_PROCESS_IMPROVER = object()


def _step_payload(*, harness_dir, label, edit_kind, changed, tokens_now,
                  method_reported, incumbent_round=None,
                  improver=_PROCESS_IMPROVER) -> dict:
    """One step of a trajectory, built the same way however many of them there are.

    `incumbent_round` is the **selection** half of a rule: `score` on a curve point is the
    *candidate's* measurement, so a method that decides "keep the state from round 3" has to
    say so, or the record shows only what it tried and never what it kept. Omitted by
    default, which means "this candidate becomes the base for my next step".

    **The spend split comes from the process, and it used to be hard-coded to `None` here.**
    That was not a neutral omission, because this function is the *batch* path and every
    batched method goes through it: measured 2026-10-18 on the nine-method batch, every
    curve point of every method recorded `method_generation_input: null`,
    `method_output: null`, `method_calls: null` and `method_spend_source: null` -- including
    the rounds where the improver's provider had reported a usage block. The single-candidate
    path (`report`) did read it, so the two halves of one platform disagreed about the same
    call, and `records._cost_fields` had nothing to read. The same three lines as `report`
    now, from the same place.

    `improver` is separate because *attribution* is a different question from *measurement*.
    `_LAST_IMPROVER` is one process-level slot holding the last CLI run, so a batch of k
    steps cannot say which step that run produced -- and filling all k in says it produced
    all k, which is false for a batch whose earlier candidates came from endpoint calls. So
    a k>1 batch passes `None` and the account is recorded once at the envelope, marked
    call-scoped. A one-step submission is unambiguous and passes the run itself.
    """
    spend_now = spent()
    step = {
        "harness_dir": str(harness_dir),
        "label": label or ("edit" if changed else "no change"),
        "edit_kind": edit_kind or ("none" if not changed else "harness_source"),
        # `generation_tokens` is the contract field and stays the single number a
        # cost-aware rule reads. The split is kept beside it because it is the part that
        # says *why* the number is large: measured on our own improver, one round is
        # ~24 KB of harness sources in and ~200 tokens out, so a rising improver cost is a
        # source-reading cost, not a thinking cost.
        "claimed_cost": {"generation_tokens": tokens_now,
                         "method_input_tokens": spend_now["input"] or None,
                         "method_output_tokens": spend_now["output"] or None,
                         "method_model_calls": spend_now["calls"] or None,
                         "method_model": spend_now["model"],
                         # Null tokens above mean "nobody counted" only if this says so;
                         # without it a reader cannot tell a free round from an invisible
                         # one. It is also what makes the nulls above *provable* rather
                         # than a gap in the record.
                         "method_spend_source": spend_source(),
                         "method_cache_read_tokens": cache_read_tokens()},
        "method_reported": {"score": None,
                            **_reported_with_improver(method_reported,
                                                      improver=improver)},
    }
    if incumbent_round is not None:
        step["incumbent_round"] = int(incumbent_round)
    return step


def _reported_with_improver(method_reported: dict | None,
                            improver=_PROCESS_IMPROVER) -> dict:
    """The method's own report plus the improver's account of itself, under its own key.

    Kept apart rather than merged so a method's keys cannot collide with the improver's,
    and omitted entirely for endpoint improvers -- an empty block would say "the improver
    reported nothing", which is not the same as "there was no CLI improver".

    `improver=None` means **do not attach**: the caller cannot attribute the process's last
    CLI run to this record (see `_step_payload`). Passing the run explicitly is how a caller
    that *can* attribute it says so.
    """
    merged = dict(method_reported or {})
    run = improver_run() if improver is _PROCESS_IMPROVER else improver
    if run:
        merged["improver_run"] = dict(run)
    return merged


def _batch_reported(steps: list[dict], payloads: list[dict]) -> dict:
    """The envelope-level record for a batch submission (see `report_many`)."""
    envelope: dict = {"steps": len(payloads)}
    if len(steps) == 1:
        envelope.update(_reported_with_improver(steps[0].get("method_reported")))
    else:
        per_step = [e.get("method_reported") or {} for e in steps]
        if any(per_step):
            envelope["per_step"] = per_step
        run = improver_run()
        if run:
            envelope["improver_run"] = run
            # **Not per step, and the record says so.** One CLI run bought this batch as a
            # whole; a reader who found it inside `per_step[i]` would be entitled to count
            # it k times, and nothing on the record said not to.
            envelope["improver_run_scope"] = "call"
    return envelope


def report_many(req: dict, *, method: str, steps: list[dict],
                acceptance_rule: str = "", tokens: int | None = None,
                extra: dict | None = None, stop: bool | None = True,
                note: str | None = None, hypothesis: str = "") -> int:
    """Report **several** candidate states from one call -- a B-shaped submission.

    One engine now reads `--rounds N` as a budget of *measured states*, so a method may
    hand over a whole batch: every step is measured, in order, and the platform does not
    call the method again while the batch lasts (and not at all if `stop`). That is what a
    method whose algorithm *is* a multi-candidate search needs -- DGM's archive draws k
    children from sampled parents, HyperAgents' population samples several, Meta-Harness's
    frontier proposes a set -- and it is the shape our ports did not have: they were written
    one-candidate-per-round to fit mode A, so their search rule was never really run.

    Each entry of `steps` is a dict with `harness_dir` and optionally `changed`, `files`,
    `label`, `edit_kind`, `hypothesis`, `method_reported`, `incumbent_round`. Every step
    that says `changed` is validated here, exactly as `report` validates its one candidate: a
    batch must not be a way to hand over a tree that is not a harness.

    `tokens` is the whole call's spend (default: what this process spent), and the
    platform divides it across the states the call bought -- charging it to each would
    multiply the method's cost by the batch size, and a cost-aware rule reading a number k
    times too large is worse than reading none.
    """
    spent_now = spent()
    if tokens is None:
        tokens = spent_tokens()
    if not steps:
        raise SystemExit("report_many needs at least one step; use report for a single one")

    payloads: list[dict] = []
    for index, entry in enumerate(steps):
        if not isinstance(entry, dict) or not entry.get("harness_dir"):
            raise SystemExit(f"step {index} names no harness_dir")
        changed = bool(entry.get("changed", True))
        if changed:
            problems = candidate.validate(Path(entry["harness_dir"]))
            if problems:
                raise CandidateInvalid(
                    f"step {index}: refusing to report a candidate that is not a harness: "
                    + "; ".join(problems))
        payloads.append(_step_payload(
            harness_dir=entry["harness_dir"], label=entry.get("label") or "",
            edit_kind=entry.get("edit_kind"), changed=changed,
            tokens_now=tokens, method_reported=entry.get("method_reported"),
            incumbent_round=entry.get("incumbent_round"),
            # One step, one candidate: the process's last CLI run *is* the run that
            # produced it, so it is attributable. k>1 is not, and `_step_payload` records
            # why the account then lives on the envelope instead.
            improver=improver_run() if len(steps) == 1 else None))

    trajectory = {
        "steps": payloads,
        "trajectory_shape": "sequence",
        # A batch nominates nothing: there is no single state to point at, and pretending
        # there is would make `tools/export_harnesses.py` pick one at random.
        "nominated": 0,
        "selection_pool_size": len(payloads),
        "provenance": {
            "method": method,
            "acceptance_rule": {"text": acceptance_rule,
                                "source": f"methods/{method}/run.py",
                                "calibrated": False},
            **(extra or {}),
        },
    }
    raw_out = req.get("trajectory_out")
    out = Path(raw_out) if raw_out else None
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(trajectory, indent=1))

    if not hypothesis:
        # **The children's own words.** A batch has `k` hypotheses and the reply has one
        # field, so the honest value is what they said -- deduplicated, because a batch that
        # keeps proposing the same thing is saying one thing. A caller may still pass its own
        # summary, but a summary of what the model said is worse than what it said: measured
        # by a test that wanted `make it print more` and got "N candidates from the frontier
        # base".
        seen: list[str] = []
        for entry in steps:
            said = str(entry.get("hypothesis") or "").strip()
            if said and said not in seen:
                seen.append(said)
        hypothesis = " | ".join(seen)[:400]
    changed_any = any(bool(e.get("changed", True)) for e in steps)
    # Deduplicated, because it is a *set of paths that changed*: three children that each
    # edited `agent.py` changed one file, and `["agent.py", "agent.py", "agent.py"]` reads
    # as three. Order preserved so the caller can still report "the first one it touched".
    files: list[str] = []
    for entry in steps:
        for name in (entry.get("files") or []):
            if name not in files:
                files.append(name)
    response = {"modified": changed_any, "changed": changed_any,
                "files": files, "hypothesis": hypothesis, "steps": len(payloads),
                "generation_tokens": tokens, "method_usage": spent_now,
                # **The method's own record, at the level the curve point reads.** This
                # envelope used to say only `{"steps": n}`, so a method that submits its
                # batch through `report_many` -- DGM, HyperAgents, Meta-Harness -- had its
                # rule record written into the step payloads and nowhere the point kept.
                # One step is unambiguous and merges; several are listed per step so
                # nothing is silently chosen between them.
                "method_reported": _batch_reported(steps, payloads)}
    if stop is not None:
        response["stop"] = bool(stop)
    if note is not None:
        response["note"] = str(note)[:160]
    emit(response)
    return 0


def fail(req: dict, *, method: str, exc: BaseException | str, base: Path,
         state: dict | None = None, stop: bool = False) -> int:
    """A trajectory for the error path, and the reason on stderr where it is read.

    An unreadable failure is worse than a reported one: it looks like the method
    did nothing.

    `state` carries the method's own record through the failure. Several methods
    keep their *cross-round memory* there -- DGM's `dgm_parent` is the lineage the
    child-count term reads, AHE's `ahe_manifest` is the pre-registration the next
    round grades, RRSI's `rrsi_rule` holds the pruned components -- so a failure
    that dropped it would silently erase that memory. Measured: five of the seven
    ports lost their record on the error path while `tthe` and `harnessx`, which
    report instead of failing, kept theirs. Same event, two different records.

    `stop` defaults to **False**, because a caught exception here is a runtime call
    failure -- a 500, a timeout, an unreadable reply -- and not a decision by the
    method. Structural failures (missing credentials, a wrong API version) raise
    `SystemExit` and are re-raised by the callers rather than caught, so they never
    reach this function. Ending a run on one transient blip would cost a method the
    rest of its budget, which is the same unfairness the `stop` field exists to
    remove.
    """
    why = exc if isinstance(exc, str) else f"{type(exc).__name__}: {str(exc)[:300]}"
    print(f"{method}: {why}", file=sys.stderr)
    return report(req, method=method, harness_dir=base, changed=False, stop=stop,
                  hypothesis=why, edit_kind="none",
                  method_reported={"error": why, **(state or {})},
                  acceptance_rule="not reached -- the method failed before deciding",
                  label="method error")


def candidate_path(req: dict, name: str = "candidate") -> Path:
    """Where a method writes its candidate harness."""
    return Path(req["workspace"]) / name
