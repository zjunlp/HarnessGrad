"""Classify task execution outcomes without executing or scoring tasks."""
from __future__ import annotations

from eval.observe.tracing import _last_usage, process_outcome


#: Markers for failures to reach the model from inside the harness.
#:
#: On 2026-10-02 (`loop-terminal_bench-submit-1`), two harnesses died with
#: `RuntimeError: 3 attempts failed, last: Connection error.` but received 0.0.
#: Section 2.5.7 separates tasks that could not be measured from measured zeros.
#:
#: Reproduction details are in `eval/service/modelgate.py`: uvicorn closed idle
#: keep-alive connections after five seconds, the gateway forwarded upstream EOF,
#: and a later harness call reused the dead connection after a lengthy command.
_PROVIDER_FAILURES = (
    "APIConnectionError", "APITimeoutError", "Connection error",
    "RemoteDisconnected", "Connection refused", "Connection reset",
    "connect timeout", "Server disconnected",
    # The OpenCode adapter emits this only for an unhandled provider error event.
    # Authentication, HTTP and malformed-stream failures must not become benchmark 0s.
    "OpenCode provider failure:",
)


def _provider_failure(result: dict) -> dict | None:
    """Identify a harness death caused by an unreachable model endpoint, or return None.

    Deliberately require both a nonzero exit code and client connection/timeout
    markers at the end of stderr. Earlier markers may describe an error that the
    harness recovered from successfully.

    A zero exit code never qualifies: the harness completed and keeps its score.
    """
    if not result.get("exit_code"):            # Neither None (invalid) nor zero qualifies.
        return None
    tail = (result.get("stderr") or "")[-1500:]
    for marker in _PROVIDER_FAILURES:
        if marker in tail:
            # Preserve the matching line so the record identifies the exact error.
            line = next((l.strip() for l in reversed(tail.splitlines())
                         if marker in l), marker)
            return {"marker": marker, "line": line[:240],
                    "exit_code": result.get("exit_code")}
    return None


def _stopped_by_signals(measured: list[dict]) -> dict | None:
    """Was this task killed by something the platform never graded as a task failure?

    `process_outcome` separates five endings. Three are scores under `INTERFACE.md` §2.2 --
    the harness ran, the harness crashed on its own, and the harness blew through the
    `agent_timeout_s` budget it was given (a *declared* bound, so exceeding it is failing to
    finish in budget). Two are not: a negative return code that is not the platform's own
    timeout marker, and the sandbox wrapper's `128+signal` relay of an inner signal. In
    both, no trial got the chance to leave an artifact and nothing graded one.

    The relayed case is deliberately weaker than the negative one. A wrapper exit says a
    signal ended the process inside the wrapper and says nothing about who sent it -- a
    harness that killed itself and an outside kill are the same number -- so the record
    names the convention and leaves the cause undetermined instead of asserting one. Only
    `sandbox=True` records can take this branch; on the host the same kill arrives negative.

    That is the case `_cancel` already names -- "a cancelled task is not a task that
    scored nothing ... rather than given a zero the platform never measured" (§2.5.7).
    Nothing checked the sign of the return code until now, so all endings fell through to
    the verifier and were written as ordinary zeros. Measured on our own AgentDojo passes:
    the tasks an operator watchdog killed (156,157,159,160 and
    294,295,296,297,301,302,303,307, eval 257,259) are recorded as clean `0.0` and are
    the only zeros in that collection that are actually wrong.

    A passing verdict is never overruled, for the same reason the provider rule declines
    to: it is positive evidence that the artifact was correct.
    """
    if any((res.get("verdict") or {}).get("passed") for res in measured or []):
        return None
    for res in reversed(measured or []):
        outcome = process_outcome(res.get("exit_code"), res.get("stderr"),
                                  wrapper=bool(res.get("sandbox")))
        if outcome == "relayed_signal":
            # The sandbox wrapper's account of an inner signal. It says a signal ended the
            # process; it does not say who sent it, and a harness that killed itself is
            # the same number here as an outside kill. The rule the operator chose is to
            # keep the task unmeasured when nothing passed *and say that the cause is
            # undetermined*, rather than pick one reading and hide the other.
            code = res.get("exit_code")
            return {"stage": "harness", "outcome": "relayed_signal",
                    "detail": (f"the sandbox wrapper reported exit {code} "
                               f"({code - 128} = the 128+signal convention) and no trial "
                               f"passed, so no artifact was ever graded; whether the task "
                               f"killed itself or was killed from outside is not "
                               f"distinguishable from a wrapper exit, and this is not a score")}
        if outcome != "stopped_by_signal":
            continue
        code = res.get("exit_code")
        return {"stage": "harness", "outcome": "stopped_by_signal",
                "detail": (f"the task was terminated by signal {-code} (exit code {code}) "
                           f"from outside the measurement, so no artifact was ever "
                           f"graded; this is not a score")}
    return None


def _invalid(task: dict, exc, *, stage: str, service: str | None) -> dict:
    """A task the platform could not measure, as opposed to one it measured as zero.

    INTERFACE.md §2.5.7: the result is **not** a `per_task` score and **not** a zero,
    and it is attributed to the dataset and the service by name. The distinction is the
    entire reason this platform exists — a broken mock API and a weak harness must not
    be the same number — and it is independently attested: JarvisBench reports that
    "harness or provider failures are marked invalid rather than assigned a score of
    zero" (`INTERFACE.md` §2.5.7).
    """
    return {"task_id": task["task_id"], "invalid": {
                "stage": stage, "service": service, "detail": str(exc)},
            "answer": "", "trace": "", "exit_code": None, "stderr": "",
            "env_usage": {}, "model_gateway": None, "identity": None}



def timeout_verdict(reason) -> bool:
    """Is this unmeasured verdict a **timeout**, which is scored 0 rather than excluded?

    The verifier reports `{"outcome": "timeout", "unmeasured": "the verifier timed out after
    900s", ...}`; the process-level path reports the bare string `"timeout"` when the wrapper was
    killed at its budget. Both are the harness's failure to finish, and both are scored 0
    (operator decision 2026-10-23). Everything else -- `"unmeasured"` (the check wrote no reward
    file), `"relayed_signal"`, `"stopped_by_signal"` -- stays `invalid`, because the platform,
    not the method, failed it.
    """
    if isinstance(reason, dict):
        return reason.get("outcome") == "timeout"
    return reason == "timeout"


def _spread(xs: list[float]) -> float:
    """Population standard deviation, with no dependency and no ambiguity.

    The population form because these are *all* the trials this run took, not a sample
    from a larger population of runs -- `statistics.pstdev` is the honest name for it, and
    the difference from `stdev` matters most at small n, which is exactly where a reader
    compares two harnesses.
    """
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5


def _harness_failure(result: dict) -> dict | None:
    """Why a harness left nothing to read, when it left nothing.

    A task can be **measured** and still say nothing about the harness: the check reads
    the artifact, finds it wrong, and records a 0.0 -- and that 0.0 is a real score. But
    if the harness *also* wrote no trace, then no method has anything to read while the
    curve reports a number, and those two situations are indistinguishable on the curve.

    Measured, and the reason this exists: a harness that needed a package the task image
    did not have died on its first model call with `ModuleNotFoundError`, exit code 1.
    The trace was 0 bytes, the score was 0.000, and the run reported *nothing* -- the
    exit code and stderr were computed here and then dropped, so "the method decided not
    to edit" was the only reading available. A harness that cannot start is a fact about
    the run and belongs in the record.

    Every harness the platform ships writes `trace.jsonl`, so an empty one is always
    abnormal; a harness that simply chose to write nothing is worth knowing about too.
    Deliberately **not** folded into `invalid`: the task was measured, so it keeps its
    score. This is the attribution, not a reason to drop the measurement (§2.5.7).

    **The second trigger, and the measured case that forced it.** A non-empty trace is
    not proof that the harness ran. Measured 2026-10-04 on the current platform:
    `--harness cli_agent` (then in `base_harness/`, now a test fixture) with no CLI writes
    exactly one line,
    `{"error": "HG_CLI is unset: no CLI to run"}`, never calls a model, exits 2 -- and the
    curve said `score 0.0, harness_failed: null, invalid: null`. The console warned and
    the verdict file carried the exit code, so the fact existed in two places and not on
    the point, which is where a reader compares runs.

    So: a harness that **exited non-zero without recording a single model call** is a
    harness failure too. "A model call" is read from the `usage` block, which the harness
    contract requires (`INTERFACE.md` §1.2) -- the platform is entitled to rely on it, and
    it is the only schema-independent evidence that a harness talked to a model. A
    scripted harness that legitimately makes no calls (`base_harness/oracle`) exits 0 and
    is not flagged.
    """
    trace = (result.get("trace") or "").strip()
    reason = None
    if not trace:
        reason = "the harness left no trace at all"
    elif result.get("exit_code") and _last_usage(trace) is None:
        # State only what the platform knows. Usage is written after completion,
        # so a crash can hide real calls. On 2026-10-04, three local-model tasks
        # had 15-23 calls and 120-140 KB traces, yet the old message incorrectly
        # reported "left no trace" because their usage blocks were absent.
        reason = (f"the harness exited {result['exit_code']} and its trace has no `usage` "
                  f"block, so the run did not reach its end")
    if reason is None:
        return None
    return {"reason": reason, "exit_code": result.get("exit_code"),
            "stderr": (result.get("stderr") or "")[-1500:]}
