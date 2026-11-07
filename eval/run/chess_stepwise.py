"""Trusted ChessBench binding; no pool loads or actor-authored transcripts."""
from __future__ import annotations

import json
import os
import signal
import threading

from harnessgrad import dataset_runtime, dataset_runtime_identity
from harnessgrad.dataset_provider import DatasetProvider

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
    inputs = [entry for entry in (verifier or {}).get("inputs", [])
              if entry.get("path") == "gold.json"]
    if len(inputs) != 1 or type(inputs[0].get("content")) is not str:
        raise ValueError("stepwise ChessBench requires inline verifier-owned gold")
    try:
        gold = json.loads(inputs[0]["content"])
        selected = bind_verifiers([task], {task['task_id']: verifier})[task['task_id']]['provider_runtime']
        return _Session(selected, gold)
    except dataset_runtime.DatasetRuntimeUnavailable as exc:
        raise ValueError(str(exc)) from None
    except Exception:
        # Exception messages can contain gold; do not copy them into public records.
        raise ValueError("invalid trusted ChessBench session input") from None


def bind_verifiers(tasks, verifiers, provider=None):
    """Freeze board dependencies before cache lookup without changing offline pins."""
    result = dict(verifiers or {})
    for task in tasks:
        if protocol_of(task) != STEPWISE:
            continue
        spec = dict(result.get(task['task_id']) or {})
        if 'provider_runtime' in spec and spec['provider_runtime'] is None:
            raise ValueError('controller provider reference is absent')
        reference = spec.get('provider_runtime', provider)
        if reference is None:
            reference = dataset_runtime_identity.provider_reference(
                'chessbench_puzzles', dataset_runtime.resolve('chessbench_puzzles'))
        reference = dataset_runtime_identity.validate_provider_reference(reference)
        if reference['dataset'] != 'chessbench_puzzles':
            raise ValueError('ChessBench controller requires its declared provider')
        spec['provider_runtime'] = reference
        result[task['task_id']] = spec
    return result


class _Session:
    """Platform-owned history; only board calculation runs in the provider worker."""

    def __init__(self, reference, gold):
        self.lock, self.process, self.stopped = threading.Lock(), None, False
        self.provider_runtime = reference
        self.client = DatasetProvider('chessbench_puzzles', timeout_s=30, freeze_runtime=True)
        self.client.runtime = dataset_runtime.admit_provider('chessbench_puzzles', reference)
        self.client.on_process = self._process_changed
        self.gold, self.answers = gold, []
        self._refresh()

    def _process_changed(self, process):
        with self.lock:
            self.process = process
            if self.stopped and process is not None:
                self._kill(process)

    @staticmethod
    def _kill(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def set_deadline(self, deadline):
        self.client.deadline = deadline

    def stop(self):
        with self.lock:
            self.stopped = True
            if self.process is not None:
                self._kill(self.process)

    def _refresh(self):
        try:
            if self.stopped:
                raise ValueError('controller has stopped')
            result = self.client.stepwise({'contract': STEPWISE, 'gold': self.gold, 'answers': self.answers})
            state = result['observation']
            if set(state) != PUBLIC_FIELDS or state['contract'] != STEPWISE:
                raise ValueError('unexpected public controller state')
            self.state = state
            self.provider_observation = result['provider_observation']
        except Exception:
            raise ValueError('trusted stepwise provider unavailable') from None

    def public_task(self):
        return dict(self.state)

    def submit(self, answer):
        if self.state['status'] != 'awaiting_move':
            raise ValueError('puzzle session is already terminal')
        # Send a serializable invalid answer so the provider can deliver its terminal verdict.
        if (not isinstance(answer, dict) or set(answer) != {'uci'}
                or not isinstance(answer['uci'], str)):
            answer = {}
        self.answers.append(answer)
        self._refresh()
        return self.public_task()


def finish(relay, exit_code: int) -> dict:
    receipt = relay.receipt()
    if isinstance(relay.session, _Session):
        receipt['provider_runtime'] = relay.session.provider_runtime
        receipt['provider_observation'] = relay.session.provider_observation
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
