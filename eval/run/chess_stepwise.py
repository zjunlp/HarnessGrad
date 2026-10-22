"""Trusted ChessBench binding; no pool loads or actor-authored transcripts."""
from __future__ import annotations

import json

STEPWISE = "chessbench_puzzles.stepwise.v1"
OFFLINE = "offline strict sequence; not interactive official accuracy"
PUBLIC_FIELDS = frozenset({"contract", "fen", "solver_turn", "status", "verdict"})
COMPARABILITY = "Interactive and offline ChessBench protocols are not comparable."
GOAL = '''Solve the ChessBench puzzle interactively. Connect once to the AF_UNIX
socket in HG_STEPWISE_SOCKET. Read one newline-delimited JSON observation, then send
one newline-delimited JSON answer with exactly one string field, {"uci":"<move>"}.
Choose each move from the current FEN. The trusted controller applies the opponent
response before sending the next board. Stop on status solved or failed; do not
send another answer then. Only contract, fen, solver_turn, status and verdict are
public. This task does not use answer.txt or an actor-authored transcript for scoring.'''


def protocol_of(task: dict) -> str | None:
    chess_task = str(task.get("task_id", "")).startswith("chessbench_puzzles/")
    if "interaction" in task:
        if not chess_task or task["interaction"] != STEPWISE:
            raise ValueError("unsupported per-task interaction contract")
        return STEPWISE
    return OFFLINE if chess_task else None


def session_for(task: dict, verifier: dict | None, *, sandbox: bool, env: dict):
    """Gold is read only from trusted verifier inputs, never materialized for actor."""
    if protocol_of(task) != STEPWISE:
        return None
    if not sandbox or env.get("kind", "files") != "files":
        raise ValueError("stepwise ChessBench requires the native files sandbox")
    from data.datasets.chessbench_puzzles import STEPWISE_CONTRACT, StepwisePuzzle
    if STEPWISE_CONTRACT != STEPWISE:
        raise ValueError("stepwise contract drift")
    inputs = [entry for entry in (verifier or {}).get("inputs", [])
              if entry.get("path") == "gold.json"]
    if len(inputs) != 1 or type(inputs[0].get("content")) is not str:
        raise ValueError("stepwise ChessBench requires inline verifier-owned gold")
    try:
        gold = json.loads(inputs[0]["content"])
        return StepwisePuzzle(gold)
    except Exception:
        # Exception messages can contain gold; do not copy them into public records.
        raise ValueError("invalid trusted ChessBench session input") from None


def finish(relay, exit_code: int) -> dict:
    receipt = relay.receipt()
    receipt["comparability"] = COMPARABILITY
    events = receipt["transport"].get("events", [])
    observation = (json.loads(events[-1]["message"])
                   if receipt["status"] == "terminal" and events else {})
    reason = None
    if exit_code != 0:
        reason = "actor_exit_before_clean_completion"
    elif receipt["status"] != "terminal":
        reason = receipt["transport"].get("reason", "exchange_incomplete")
    elif observation.get("verdict") == "invalid_reference":
        reason = "invalid_reference"
    elif not all(receipt["cleanup"].values()):
        reason = "cleanup_incomplete"
    # The trusted platform controller scores its relay receipt in this process;
    # the actor runs separately. This is in-process, never actor-workdir/pristine.
    verdict = {"kind": "stepwise", "protocol": STEPWISE, "evidence": receipt,
               "scorer_isolation_class": "in-process"}
    if reason:
        receipt.update(status="incomplete", measured=False, reason=reason)
        verdict.update(unmeasured=reason, detail=reason)
        return verdict
    hit = observation["status"] == "solved"
    receipt.update(status="complete", measured=True, metric="ChessBench puzzle accuracy",
                   reward=float(hit), correct=hit, verdict=observation["verdict"])
    verdict.update(score=float(hit), passed=hit, detail=observation["verdict"])
    return verdict
