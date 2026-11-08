"""Trusted ChessBench binding; no pool loads or actor-authored transcripts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from eval.containers.process import run
from harnessgrad import dataset_runtime, dataset_runtime_identity as runtime_identity_mod

STEPWISE = "chessbench_puzzles.stepwise.v1"
OFFLINE = "offline strict sequence; not interactive official accuracy"
PUBLIC_FIELDS = frozenset({"contract", "fen", "solver_turn", "status", "verdict"})
_VERDICTS = {
    "awaiting_move": frozenset({"awaiting_move"}),
    "solved": frozenset({"reference_complete", "checkmate", "alternate_mate_in_one"}),
    "failed": frozenset({"invalid_reference", "invalid_answer", "invalid_move", "illegal_move", "wrong_move"}),
}
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


class _ProviderSession:
    """Private input and accepted answers stay in the trusted platform process."""
    def __init__(self, gold, provider):
        self._gold = gold
        self._provider = provider
        self._history = []
        self._initial = self._replay([])

    def _replay(self, history):
        try:
            runtime = dataset_runtime.admit_provider("chessbench_puzzles", self._provider)
            root = Path(__file__).resolve().parents[2]
            bootstrap = ("import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
                         "runpy.run_module('eval.run.chess_stepwise_worker',run_name='__main__')")
            with tempfile.TemporaryDirectory(prefix="hg-chess-controller-") as temp:
                env = {"PATH": os.defpath, "HOME": temp, "TMPDIR": temp,
                       "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                       "HG_REVIEWED_ENVIRONMENT_ONLY": "1"}
                proc = run([runtime["python"], "-I", "-B", "-c", bootstrap, str(root)],
                           timeout_s=30, cwd=Path(temp), env=env,
                           input_text=json.dumps({"gold": self._gold, "history": history}, allow_nan=False))
            result = json.loads(proc.stdout)
            if (proc.returncode or not isinstance(result, dict) or set(result) != PUBLIC_FIELDS
                    or result.get("contract") != STEPWISE
                    or result.get("status") not in ("awaiting_move", "solved", "failed")
                    or result.get("verdict") not in _VERDICTS.get(result.get("status"), ())
                    or type(result.get("solver_turn")) is not int
                    or not (result.get("fen") is None or isinstance(result.get("fen"), str))):
                raise ValueError("invalid controller response")
            return result
        except Exception:
            raise ValueError("trusted ChessBench controller unavailable or invalid") from None

    def public_task(self):
        return dict(self._initial)

    def submit(self, answer):
        if self._initial["status"] != "awaiting_move":
            raise ValueError("puzzle session is already terminal")
        if len(self._history) >= 256:
            raise ValueError("trusted ChessBench submission limit exceeded")
        try:
            encoded = json.dumps(answer, allow_nan=False)
            if len(encoded.encode("utf-8")) > 4096:
                raise ValueError("answer too large")
            frozen_answer = json.loads(encoded)
        except (TypeError, ValueError, UnicodeError):
            raise ValueError("invalid ChessBench submission") from None
        history = [*self._history, frozen_answer]
        observation = self._replay(history)
        self._history = history
        self._initial = observation
        return dict(observation)


def session_for(task: dict, verifier: dict | None, *, sandbox: bool, env: dict,
                dataset_runtime_identity=None):
    """Use the exact measured provider; gold never enters the actor environment."""
    if protocol_of(task) != STEPWISE:
        return None
    if not sandbox or env.get("kind", "files") != "files":
        raise ValueError("stepwise ChessBench requires the native files sandbox")
    try:
        identity = runtime_identity_mod.validate(dataset_runtime_identity)
        provider = identity["provider"]
        if provider is None or provider["dataset"] != "chessbench_puzzles":
            raise ValueError("missing provider")
    except (ValueError, TypeError, KeyError):
        raise ValueError("stepwise ChessBench requires its bound provider identity") from None
    inputs = [entry for entry in (verifier or {}).get("inputs", [])
              if entry.get("path") == "gold.json"]
    if len(inputs) != 1 or type(inputs[0].get("content")) is not str:
        raise ValueError("stepwise ChessBench requires inline verifier-owned gold")
    try:
        return _ProviderSession(json.loads(inputs[0]["content"]), provider)
    except Exception:
        raise ValueError("invalid trusted ChessBench session or provider") from None


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
