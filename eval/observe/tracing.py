"""Canonical trace reader and task-artifact helpers.

Trace interpretation lives here: `first_object` reads the first complete JSON object
from a model reply; `loop_facts` counts harness events; `_last_identity` and `_last_usage`
read the last metadata block of each kind from the JSON-lines trace. These are pure
functions over text, independent of a model, container, task definition or scorer.

The runner also uses the artifact helpers split out of it here: `_materialize` prepares
fixture files, `task_file` creates task-named paths, and `_write_progress` persists partial
results and traces. This module imports nothing from the runner.

`eval.observe.trace` is a compatibility adapter that re-exports this module's reply parser,
loop-fact reader and end-state constants. New callers should import from `eval.observe.tracing`.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
from pathlib import Path

from eval.containers.immutable_runtime import projections


#: How a harness run ended. Named constants because the method-facing channel and the
#: panel both branch on them, and a typo in a string literal would be a silent `unknown`.
ENDED_CRASHED = "crashed"
ENDED_ANSWERED = "answered"
ENDED_PARSE_ERROR = "parse_error"
ENDED_BUDGET = "budget_exhausted"
ENDED_UNKNOWN = "unknown"


def first_object(text: str) -> dict | None:
    """The first complete JSON object in `text`, or None.

    `raw_decode` from the first `{` rather than `json.loads(text)`: the measured model
    emits several objects in one reply (`{"tool": ...}}\\n{"answer": ""}` -- note the stray
    brace), and a parser that insists on the whole string being one object treats that as
    a harness failure. `base_harness/loop/agent.py::_parse` follows the same rule; if the
    two ever disagree, the panel's view of a run stops matching the run.
    """
    if not text:
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def loop_facts(trace_text: str) -> dict:
    """What the harness did, counted from its own trace.

    Every field answers a question a reader of the *score* cannot answer:

        model_calls           the model was asked N times
        commands              N shell commands actually executed (a `command` record)
        answers               N replies carried a final answer
        submit_rejected       the submit gate refused N times (needs `base_harness/loop`)
        missing_at_submit     which paths the task named and the harness had not written
        multi_object_replies  N replies carried more than one JSON object
        parse_errors          N replies could not be parsed at all
        empty_commands        N executed commands were the empty string (nothing ran)
        duplicate_keys        N JSON keys the parser had to repair (the model repeated one)
        context_trims         how many times history was shortened after the model refused
                              a too-long prompt
        output_clipped_events / _chars   how often a command's output was clipped for the
                              model, and how much was hidden
        history_dropped       N old messages dropped from the model's view
        ended                 see the ENDED_* constants

    `commands == 0` with `answers == 1` is the shape that mattered most: the harness
    answered without doing anything, and the platform scored the task 0. Without this
    count, that is indistinguishable from "it worked and failed".

    Two of the fields are read together on purpose:

        multi_object_replies > 0, parse_errors == 0   the parser absorbed it
        multi_object_replies > 0, parse_errors > 0    it did not

    A method that sees only the first number will "fix" a parser that is not broken --
    measured: three consecutive rounds of one method diagnosed a parser bug that had been
    fixed a round earlier.
    """
    steps = commands = answers = rejected = parse_errors = multi_object = 0
    #: **The interface events, counted as their own facts.** Every one of these is a case
    #: where the harness changed what the model did or what it saw, and the summary a method
    #: reads first did not mention any of them. Measured on `local-floor-9`
    #: (`cancel-async-tasks`): the summary said `commands: 13` while **ten of them executed
    #: the empty string** -- the model's real command had been replaced by a duplicated JSON
    #: key -- and a method reading `13 commands, 0 parse errors` sees a healthy trajectory.
    #: Counting them separately is what makes "the harness dropped the actions" a fact a
    #: method can act on instead of something only an operator with grep can find.
    empty_commands = duplicate_keys = context_trims = 0
    clipped_events = clipped_chars = history_dropped = 0
    repeated_commands = repeat_nudges = 0
    seen_commands: set[str] = set()
    missing: list[str] = []
    usage: dict = {}
    for line in str(trace_text or "").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record.get("usage"), dict):
            usage = record["usage"]
        if "reply" in record:
            steps += 1
            reply = str(record["reply"])
            action = first_object(reply)
            if action is not None:
                if "answer" in action:
                    answers += 1
                close = reply.find("}")
                if close != -1 and "{" in reply[close:]:
                    multi_object += 1
        if "command" in record:
            commands += 1
            cmd = str(record.get("command") or "").strip()
            if not cmd:
                empty_commands += 1
            elif cmd in seen_commands:
                # **A command this run had already issued.** The score cannot see this and
                # the existing facts did not count it: measured on the 9B
                # (`large-scale-text-editing`, 2026-10-05), 24 of 31 executed commands were
                # re-issues of earlier ones, the harness's repeat nudge fires only on an
                # *immediately* consecutive repeat, and the round recorded a plain 0.000 --
                # a harness burning its whole step budget in a loop is a harness fact, and
                # the method reading the summary had no number for it.
                repeated_commands += 1
            else:
                seen_commands.add(cmd)
        if "repeat_nudge" in record:
            repeat_nudges += 1
        if record.get("duplicate_keys"):
            duplicate_keys += len(record["duplicate_keys"])
        if record.get("context_trims"):
            context_trims = max(context_trims, int(record["context_trims"]))
        if "output_clipped" in record:
            clipped_events += 1
            clipped_chars += int(record.get("output_clipped") or 0)
        if record.get("history_dropped"):
            history_dropped += int(record["history_dropped"])
        if "parse_error" in record:
            parse_errors += 1
        if "submit_rejected" in record:
            rejected += 1
            missing.extend(record.get("submit_rejected") or [])

    return {
        "model_calls": steps,
        "commands": commands,
        "answers": answers,
        "submit_rejected": rejected,
        "missing_at_submit": sorted(set(missing)),
        "multi_object_replies": multi_object,
        "parse_errors": parse_errors,
        # The harness-side events. `empty_commands` is the one that mattered most: it is a
        # step that ran nothing while reporting success, which the score cannot distinguish
        # from a model that did the wrong thing.
        "empty_commands": empty_commands,
        "duplicate_keys": duplicate_keys,
        # Re-issues of a command this run had already executed, and how often the harness's
        # own repeat nudge fired. Two numbers, because the gap between them *is* the bug:
        # the nudge only sees a repeat that arrives immediately after itself.
        "repeated_commands": repeated_commands,
        "repeat_nudges": repeat_nudges,
        "context_trims": context_trims,
        "output_clipped_events": clipped_events,
        "output_clipped_chars": clipped_chars,
        "history_dropped": history_dropped,
        "tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
        "ended": _ended(usage, answers, parse_errors, commands, steps),
    }


def _ended(usage: dict, answers: int, parse_errors: int, commands: int,
           steps: int) -> str:
    """How the run ended, by evidence strength. The order is the whole content here.

    `crashed` first: the harness writes `usage` as its **last** record, so a missing one
    means it never reached its own end (killed, or an exception out of a model call).
    Measured: a task with 10 calls reported as `budget_exhausted` was in fact an
    `APIConnectionError` -- and "ran out of steps" sends a reader to the wrong place.
    """
    if not usage:
        return ENDED_CRASHED
    if answers:
        return ENDED_ANSWERED
    if parse_errors and not commands:
        return ENDED_PARSE_ERROR
    if steps and commands:
        return ENDED_BUDGET
    return ENDED_UNKNOWN


def task_file(dest: Path, name: str) -> Path:
    """`dest / name`, with the directories `name` implies created.

    A task id **contains slashes by design** -- `data/scaffolds/qa/base.py`'s is
    `<dataset>/<split>/<n>`, and the comment there says so out loud -- so every file named
    after a task is a *nested* path. `dest.mkdir()` is not enough: it creates
    `verdicts/round-0/` and leaves `verdicts/round-0/gsm8k/train/` missing.

    Measured, and it is the whole reason this function exists: the first real run of a QA
    dataset (`gsm8k`, ids `gsm8k/train/0`) died with
    `FileNotFoundError: .../verdicts/round-0/gsm8k/train/0.json`, and the same task's
    progress record was lost to a warning nobody read (`.../progress/round-0/gsm8k/train/
    0.json`). Every writer of a task-named file goes through here, so the next id shape
    with a slash in it does not have to find all five sites again.
    """
    path = dest / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def _last_identity(trace: str) -> dict | None:
    """The harness's own statement of what ran it, if it made one.

    A harness reads its model configuration from wherever it likes -- our `HG_AGENT_*`
    convention, a CLI's own config file, a flag. The platform only knows the first of
    those, so left to itself it would record `HG_AGENT_MODEL` for a harness that
    ignored it. **This project has already shipped that bug once**: a curve point said
    `agent_model: mock` while a live model answered every task.

    So the harness may declare it, and when it does the declaration is what goes on
    the record -- see `INTERFACE.md` §3. A declaration that disagrees with the
    platform's own environment is not resolved silently in either direction; both
    values are recorded.

    Shape, one JSON object per line in the trace:

        {"harness_identity": {"agent_model": "...", "agent_backend": "...",
                              "harness": "claude-code 2.0.1"}}
    """
    found = None
    for line in trace.splitlines():
        line = line.strip()
        if not line or "harness_identity" not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("harness_identity"), dict):
            found = event["harness_identity"]      # last one wins
    return found
def _last_usage(trace: str) -> dict | None:
    """The `usage` block a harness wrote into its trace, if it wrote one.

    Last one wins: a harness may report usage incrementally as it goes, and the final
    line is the total. Absent means absent -- a harness that reports nothing gets no
    token count rather than a zero, because a zero that means "not reported" is
    indistinguishable from a real zero and would silently disable a cost rule.
    """
    found = None
    for line in trace.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("usage"), dict):
            found = record["usage"]
    return found
def _clear_tree(target: Path) -> None:
    """Remove a directory tree that may be **read-only**, so `copytree` can re-write it.

    Measured, and it is a platform bug rather than a dataset's: `_materialize` runs twice
    per task and its whole promise is that the second run **overwrites** what the first
    wrote. `shutil.copytree(..., dirs_exist_ok=True)` cannot do that for a source whose
    files are read-only -- Java runtimes are shipped with `r--r--r--` for legal files --
    and it fails with `[Errno 13] Permission denied` on every one of them. The failure
    looks like the dataset's, exactly like the ALFWorld namespace fork did
    (`docs/dataset-onboarding-skill.md` §1); the fix belongs here because the next dataset
    with a read-only input would hit it too.

    `chmod` first (a directory that cannot be traversed cannot be emptied), then remove.
    """
    if not target.exists():
        return
    for root, dirs, _files in os.walk(target):
        os.chmod(root, 0o755)
        for name in dirs:
            os.chmod(Path(root) / name, 0o755)
    shutil.rmtree(target, ignore_errors=True)


def _materialize(workdir: Path, entries, platform_root: Path,
                 base: Path | None = None) -> list[str]:
    """Write mutable dataset files into `workdir`; return the relative paths written.

    ``immutable: true`` entries are runtime projections, not task state: ``from``
    must be an absolute existing non-symlink directory. Validate, but never copy or
    create their host targets (nor any staging/cache). The sandbox binds the exact
    declared source read-only at ``workdir / path``; unsupported binds fail closed.

    Used twice per task, and the two uses are the point of the whole design:

    * **before** the harness runs, to set the task up;
    * **again before verification**, to overwrite anything the harness changed. A task
      whose grade is "run these tests" must not be gradeable by editing the tests, and
      re-writing them is the only way that holds when the harness was free to write
      into the same directory.

    Paths are refused rather than clamped if they escape `workdir`: a `../` silently
    turning into `.` would set up a task whose inputs are not where the dataset said.
    """
    written: list[str] = []
    for entry in entries or []:
        projections([entry])
        if entry.get("immutable") is True:
            # No pre-verification restoration is necessary: anti-tamper is stronger
            # because the harness cannot modify this tree at all, rather than being
            # allowed to change it and having those changes reverted afterwards.
            continue
        rel = str(entry.get("path") or "")
        if not rel:
            continue
        target = (workdir / rel).resolve()
        if not str(target).startswith(str(workdir.resolve()) + os.sep) \
                and target != workdir.resolve():
            raise ValueError(f"setup path {rel!r} escapes the working directory")
        if "content" in entry and entry["content"] is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(entry["content"]))
        elif "from" in entry:
            source = Path(str(entry["from"]))
            if not source.is_absolute():
                source = (base or platform_root) / source
            if not source.exists():
                raise ValueError(f"setup source {source} does not exist")
            if source.is_dir():
                _clear_tree(target)
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        else:
            continue
        written.append(rel)
    return written
#: Shell convention for a process that ended from a signal: 128 + signum. A sandbox
#: wrapper relays an inner kill this way, so the code arrives **positive** and would
#: otherwise read as an ordinary harness crash.
WRAPPER_SIGNALS = {128 + signal.SIGKILL: "SIGKILL", 128 + signal.SIGTERM: "SIGTERM"}


def process_outcome(exit_code, stderr, wrapper: bool = False) -> str | None:
    """How the harness's process ended, kept apart from what the task scored.

    `INTERFACE.md` §2.2 is explicit that the two are independent: "the harness's exit
    code says whether the *harness* finished; the verifier says whether the *task* was
    completed. A harness that crashed but left working artifacts still scores." So
    this is an **annotation**, not a scoring rule -- a harness that crashes on its own
    is a legitimate zero, and marking it unmeasured would discard a real measurement.

    What it fixes is that the runner read `proc.returncode` (`eval/run/runner.py:536`)
    and then dropped it: the per-task record had no `exit_code` at all, so a task where
    the harness crashed and a task where it ran to completion and failed produced
    byte-identical records. Measured on AgentDojo train: 134 of 601 tasks died in the
    harness and nothing in the records said so; it took reading traces and
    `events.jsonl` to find out.

    The four values are distinguishable from data the runner already has:

    ``completed``          exit 0 -- the harness ran to its end.
    ``harness_crash``      exit > 0 -- the harness died on its own. A score (§2.2).
    ``platform_timeout``   the platform's own `agent_timeout_s` expired. `runner.py`
                           catches `TimeoutExpired` and records it as `-1` with a
                           ``timeout after Ns`` stderr marker, deliberately distinct
                           from a signal. `agent_timeout_s` is a *declared* bound, so
                           exceeding it is the harness failing to finish in budget --
                           also a score, by the same reading as §2.2.
    ``stopped_by_signal``  a negative return code that is not the timeout marker:
                           something outside the measurement killed it -- an operator
                           watchdog, the OOM killer. The platform never graded it as a
                           budget failure, which is what `_cancel` means by "a cancelled
                           task is not a task that scored nothing".
    ``relayed_signal``     a **sandbox wrapper** reported ``128+signal`` (137 or 143).
                           Only reachable when ``wrapper=True``; on the host the same
                           kill arrives negative. The convention says a signal ended the
                           process *inside* the wrapper and says nothing about who sent
                           it: a harness that killed itself and an outside kill are the
                           same number here. The record therefore names the convention and
                           leaves the cause **undetermined** rather than asserting either.

    The signal cases are the only ones that must not be scored when nothing passed, and
    `runner.py` routes them to `unmeasured`. The other three stay scores.

    ``wrapper=True`` means the platform launched a wrapper (a sandbox) as the direct child,
    which is the only situation where the 128+signal convention can appear. It is an
    explicit argument rather than something inferred from the code, because "the wrapper
    relayed a signal" and "the harness exited 137" are indistinguishable from the number
    alone -- and inferring the former from the latter is exactly the mistake this value
    exists to avoid.
    """
    if exit_code is None:
        return None
    if exit_code == 0:
        return "completed"
    if exit_code > 0:
        if wrapper and exit_code in WRAPPER_SIGNALS:
            return "relayed_signal"
        return "harness_crash"
    if "timeout after " in str(stderr or ""):
        return "platform_timeout"
    return "stopped_by_signal"


def _write_progress(progress_dir, round_no: int, tid: str,
                    measured: list[dict], unmeasured: dict | None,
                    *, diagnostic: dict | None = None,
                    enforcement: dict | None = None) -> None:
    """Write one task's result the moment it exists, not at the end of the round.

    **Measured, twice, and the second time cost two hours.** A round's results were
    written only after `evaluate` returned -- every trial, every verdict, every trace --
    so a run that was killed lost all of it. Measured: `loop-terminal_bench-41133` and
    `local-floor-5/6` died to the tamper check and discarded their measurements; and
    `screen-all-1` (51 tasks x 3 trials, 2h05m, five containers still running) was killed
    by the environment at 22:42 and left **two files**: `run_meta.json` and a header line.
    Three separate losses from one design choice, and the choice was "the record is the
    thing you write at the end".

    So each task writes itself here as soon as its last trial finishes:

        <run>/progress/round-<N>/<task>.json      score, per-trial scores, verdict,
                                                  the check's test tally, wall clock
        <run>/progress/round-<N>/<task>.trial<i>.jsonl   that trial's trace

    A separate `progress/` directory rather than the final `round<N>_traces/` and
    `verdicts/`: a partial round must not look like a complete one. The end-of-round
    savers remain the authoritative record; these are what survives a kill.

    Never raises. A progress write that fails must not turn a measured task into a
    failed one -- the same rule the teardown helpers follow, for the same reason.
    """
    if progress_dir is None:
        return
    try:
        dest = Path(progress_dir) / "progress" / f"round-{round_no}"
        dest.mkdir(parents=True, exist_ok=True)
        if unmeasured is not None:
            record = {"task_id": tid, "round": round_no, "invalid": unmeasured,
                      "exit_code": None,
                      "process_outcome": unmeasured.get("outcome")}
            if enforcement is not None:
                record["enforcement"] = enforcement
            if diagnostic is not None:
                record["harness_output"] = {
                    "exit_code": diagnostic.get("exit_code"),
                    "stderr": (diagnostic.get("stderr") or "")[-8000:],
                    "harness_runtime": diagnostic.get("harness_runtime"),
                    "runtime_services": diagnostic.get("runtime_services"),
                    "model_gateway": diagnostic.get("model_gateway"),
                }
                body = diagnostic.get("trace")
                if body:
                    traces = dest / "traces"
                    traces.mkdir(exist_ok=True)
                    task_file(traces, f"{tid}.invalid.jsonl").write_text(
                        body, encoding="utf-8")
            task_file(dest, f"{tid}.json").write_text(json.dumps(
                record,
                indent=1, ensure_ascii=False), encoding="utf-8")
            return
        from eval.verification.isolation import common_class, observed_class
        isolation = common_class(observed_class(r.get("verdict")) for r in measured)
        scores = [float((r.get("verdict") or {}).get(
            "score", 1.0 if (r.get("verdict") or {}).get("passed") else 0.0))
            for r in measured]
        last = (measured[-1].get("verdict") or {}) if measured else {}
        # The benchmark's own difficulty label, when the check reported one. Carried on the
        # per-task record because this is the file a fold joins on (`hg-census/fold_nightly.py`
        # reads `progress/round-N/<task>.json`), so a per-level table can be published
        # downstream without that consumer also having to open `verdicts/`. Reporting only:
        # the label sits beside `score` and never enters it.
        evidence = last.get("evidence")
        level = evidence.get("level") if isinstance(evidence, dict) else None
        # The process's own ending, kept *beside* the score rather than folded into it:
        # §2.2 makes the harness's exit code and the task's score independent, and this
        # is what lets a reader tell a crash-driven zero from a completion-driven one.
        last_exit = measured[-1].get("exit_code") if measured else None
        last_stderr = measured[-1].get("stderr") if measured else None
        # **A substring is not a key.** The trace is JSONL, and a *step* record can carry the
        # text `wall_clock_s` in its command output or its own stats -- matching on the
        # substring then parses that record and indexes a key it does not have. Measured on
        # `dream_rsi` (2026-10-17, round 10): `KeyError: 'wall_clock_s'` killed a nine-round
        # run, and the launcher skipped its exam because a failed train returns before it.
        # So: parse, then *check the shape*, and treat anything unexpected as "no wall clock"
        # rather than as a reason to lose the run.
        wall = None
        for line in str((measured[-1] if measured else {}).get("trace") or "").splitlines():
            if '"wall_clock_s"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            value = record.get("wall_clock_s") if isinstance(record, dict) else None
            if isinstance(value, dict):
                wall = value.get("total")
                break
        task_file(dest, f"{tid}.json").write_text(json.dumps({
            "task_id": tid, "round": round_no,
            **({"runtime_services": [r.get("runtime_services") for r in measured]}
               if any(r.get("runtime_services") for r in measured) else {}),
            "score": sum(scores) / len(scores) if scores else None,
            "scorer_isolation_class": isolation,
            "scorer_isolation_comparable": isolation != "unknown",
            "trial_scorer_isolation": [observed_class(r.get("verdict")) for r in measured],
            "trial_scores": scores, "n_trials": len(scores),
            "passed": last.get("passed"), "kind": last.get("kind"),
            "detail": last.get("detail"),
            "tests": last.get("tests"),
            "wall_clock_s": wall,
            "exit_code": last_exit,
            # `wrapper=` is read from the same record the code came from, so the
            # annotation and the scoring rule cannot disagree about what a 137 was.
            "process_outcome": process_outcome(
                last_exit, last_stderr,
                wrapper=bool((measured[-1] if measured else {}).get("sandbox"))),
            "complete": True,
            **({"level": level} if level is not None else {}),
            **({"enforcement": enforcement} if enforcement is not None else {}),
        }, indent=1, ensure_ascii=False), encoding="utf-8")
        traces = dest / "traces"
        traces.mkdir(exist_ok=True)
        for index, result in enumerate(measured, start=1):
            body = result.get("trace")
            if body:
                task_file(traces, f"{tid}.trial{index}.jsonl").write_text(
                    body, encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:                      # noqa: BLE001
        print(f"warning: could not write the progress record for {tid}: {exc}",
              file=sys.stderr)
