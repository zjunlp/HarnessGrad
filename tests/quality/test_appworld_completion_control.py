"""Completion-aware fabricated control: state, completion, recording and replay are separable.

The AppWorld protocol review (`registry/w-appworld-protocol-review.md`) found that the public prompt
pointed the actor at a harness-side final answer while the official checker replays recorded API calls
into a fresh world and evaluates *that*, and it recorded one absent control:

    ABSENT in enumerated synthetic protocol scope: a completion-aware recording/replay/scoring control.

This module is that control. It is **entirely fabricated**: an authored world with one state
requirement and one *separate* completion requirement, an authored HTTP service, and a replay of the
recorded calls into a fresh world. It reads no dataset row, no prompt, no gold answer and no released
task, it calls no official scorer, and it touches no GPU, model or endpoint. It cannot satisfy
`appworld / successful-task-evidence` - only a genuine successful measured actor task can - and it
authorizes nothing. What it can do is make four outcomes distinguishable instead of one number:

1. correct live state, no completion request      -> the actor/guidance boundary
2. completion in the live world but not in the recording -> the recording/verifier wiring
3. correct completion replayed, wrong state       -> a task-state failure
4. both requirements satisfied                    -> a complete fabricated control (NOT a benchmark win)

The observable set is the one the review asked for: printed state readback, the completion
request/response, the local harness submission, and the replayed completion state.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

TASK_ID = "fabricated/control/0"
GOAL = "Set the documented value and then complete the task through the documented API."


def _fresh_world() -> dict:
    """A new world: one authored state slot and one authored completion flag."""
    return {"value": None, "completed": False, "completion_calls": 0, "rejected_calls": 0}


def _apis(world: dict, log: list):
    """The fabricated service API surface, bound to one world and one call log."""

    def show_app_descriptions():
        return [{"name": "synthetic", "description": "Authored control"},
                {"name": "supervisor", "description": "Authored completion surface"}]

    def show_api_descriptions(app_name):
        assert app_name in ("synthetic", "supervisor"), app_name
        if app_name == "synthetic":
            return [{"name": "set_value"}, {"name": "reject"}]
        return [{"name": "complete_task"}]

    def show_api_doc(app_name, api_name):
        if app_name == "synthetic" and api_name == "set_value":
            return {"parameters": [{"name": "value", "type": "string"}]}
        if app_name == "synthetic" and api_name == "reject":
            return {"parameters": [{"name": "reason", "type": "string"}]}
        assert app_name == "supervisor" and api_name == "complete_task", (app_name, api_name)
        return {"parameters": []}

    def set_value(value):
        log.append(("synthetic.set_value", {"value": value}))
        world["value"] = value
        return {"value": value}

    def reject(reason="authored rejection"):
        log.append(("synthetic.reject", {"reason": reason}))
        world["rejected_calls"] += 1
        raise ValueError(reason)

    def complete_task():
        log.append(("supervisor.complete_task", {}))
        world["completed"] = True
        world["completion_calls"] += 1
        return {"completed": True}

    return SimpleNamespace(
        api_docs=SimpleNamespace(show_app_descriptions=show_app_descriptions,
                                 show_api_descriptions=show_api_descriptions,
                                 show_api_doc=show_api_doc),
        synthetic=SimpleNamespace(set_value=set_value, reject=reject),
        supervisor=SimpleNamespace(complete_task=complete_task))


class _Service:
    """Authored HTTP service: `/initialize` opens a fresh world, `/execute` runs one snippet."""

    def __init__(self):
        self.live_world = _fresh_world()
        self.live_log: list = []
        self.recorded: list[dict] = []
        self.readbacks: list[str] = []
        self.harness_submissions: list[str] = []
        self.live_errors: list[str] = []
        self.initialises: list[str] = []

    def handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/initialize":
                    service.initialises.append(payload["task_id"])
                    service.live_world = _fresh_world()
                    service.live_log = []
                    service.live_errors = []
                    service.recorded = []
                    output = {"task_id": payload["task_id"], "goal": GOAL}
                elif self.path == "/execute":
                    code = payload["code"]
                    service.recorded.append({"url": "/execute", "code": code})
                    namespace = {"apis": _apis(service.live_world, service.live_log)}
                    buffer = io.StringIO()
                    error = None
                    with contextlib.redirect_stdout(buffer):
                        try:
                            exec(code, namespace)
                        except Exception as exc:  # noqa: BLE001 - authored control
                            error = f"{type(exc).__name__}: {exc}"
                    output = buffer.getvalue()
                    if error is not None:
                        service.live_errors.append(error)
                    service.readbacks.append(output)
                elif self.path == "/harness_answer":
                    service.harness_submissions.append(payload["answer"])
                    output = {"accepted_locally": True}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps({"output": output}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler

    def replay(self, recorded=None) -> dict:
        """Replay recorded calls into a FRESH world and return that world plus its own log."""
        world, log, errors = _fresh_world(), [], []
        for call in (self.recorded if recorded is None else recorded):
            namespace = {"apis": _apis(world, log)}
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    exec(call["code"], namespace)
                except Exception as exc:  # noqa: BLE001 - authored control
                    errors.append(f"{type(exc).__name__}: {exc}")
        return {"world": world, "log": list(log), "errors": errors}


@pytest.fixture
def service(monkeypatch):
    svc = _Service()
    server = ThreadingHTTPServer(("127.0.0.1", 0), svc.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HG_SVC_APPWORLD_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        yield svc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _evaluate(replay: dict, expected_value: str) -> dict:
    """The authored evaluator: two independent requirements, reported separately."""
    world = replay["world"]
    completed_calls = [name for name, _ in replay["log"] if name == "supervisor.complete_task"]
    return {
        "state_requirement": world["value"] == expected_value,
        "completion_requirement": bool(completed_calls),
        "detail": {"replayed_value": world["value"],
                   "replayed_completions": len(completed_calls)},
    }


def _run(service, snippets: list[str], harness_answer: str | None) -> None:
    """Drive the fabricated service the way the actor CLI does, then submit locally."""
    import urllib.request

    base = __import__("os").environ["HG_SVC_APPWORLD_URL"]

    def post(path, payload):
        request = urllib.request.Request(
            base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    post("/initialize", {"task_id": TASK_ID})
    for code in snippets:
        post("/execute", {"task_id": TASK_ID, "code": code})
    if harness_answer is not None:
        post("/harness_answer", {"answer": harness_answer})


def test_the_control_is_fabricated_and_uses_no_dataset_task_or_gold():
    """Guard the control's own claim: nothing here comes from a released task or an answer key."""
    # Scan only the module body, not this test's own list of forbidden names.
    body = pathlib.Path(__file__).read_text(encoding="utf-8").split(
        "def test_the_control_is_fabricated")[0]
    for forbidden in ("data.datasets", "data/datasets", "ground_truth", "oracle",
                      "load(", "import data"):
        assert forbidden not in body, forbidden
    assert TASK_ID.startswith("fabricated/") and GOAL
    assert set(_fresh_world()) == {"value", "completed", "completion_calls", "rejected_calls"}
    assert "reward" not in body and "official scorer" not in body.replace(
        "it calls no official scorer", "")


def test_correct_state_without_completion_fails_only_the_completion_requirement(service):
    """Outcome 1: the actor/guidance boundary. This is the shape the review hypothesised."""
    _run(service, ["apis.synthetic.set_value('done')\nprint(apis.synthetic.set_value)"],
         harness_answer="I set the value to done.")
    assert service.live_world == {"value": "done", "completed": False,
                                  "completion_calls": 0, "rejected_calls": 0}
    assert service.harness_submissions == ["I set the value to done."]
    result = _evaluate(service.replay(), "done")
    assert result["state_requirement"] is True
    assert result["completion_requirement"] is False
    assert result["detail"] == {"replayed_value": "done", "replayed_completions": 0}


def test_completion_in_the_live_world_but_not_in_the_recording_is_wiring(service):
    """Outcome 2: recording/verifier wiring. Live and replayed worlds disagree on completion only."""
    _run(service, ["apis.supervisor.complete_task()"], harness_answer="completed")
    assert service.live_world["completed"] is True
    # Drop the recorded completion call, as a recording/verifier wiring defect would.
    dropped = [call for call in service.recorded if "complete_task" not in call["code"]]
    assert len(dropped) == len(service.recorded) - 1
    result = _evaluate(service.replay(dropped), None)
    assert result["completion_requirement"] is False, "the replay must show what the recording holds"
    assert service.live_world["completed"] is True, "the live world still holds the completion"


def test_wrong_state_with_preserved_completion_is_a_task_state_failure(service):
    """Outcome 3: a genuine task-state failure, distinguishable from both boundary defects."""
    _run(service, ["apis.synthetic.set_value('wrong')\napis.supervisor.complete_task()"],
         harness_answer="done")
    result = _evaluate(service.replay(), "done")
    assert result["state_requirement"] is False
    assert result["completion_requirement"] is True


def test_both_requirements_satisfied_is_still_not_a_benchmark_win(service):
    """Outcome 4: a complete fabricated control - explicitly not a measured dataset result."""
    _run(service, ["apis.synthetic.set_value('done')\napis.supervisor.complete_task()"],
         harness_answer="done")
    result = _evaluate(service.replay(), "done")
    assert result["state_requirement"] is True
    assert result["completion_requirement"] is True
    # The control publishes no score, binds no dataset and asserts no success.
    assert "score" not in result and "reward" not in result
    assert service.live_world["completion_calls"] == 1


def test_discovery_exposes_the_completion_surface_without_the_answer(service):
    """The completion API must be discoverable and must not leak what the answer should be."""
    _run(service, ["print(apis.api_docs.show_api_descriptions('supervisor'))"], harness_answer=None)
    readback = service.readbacks[-1]
    assert "complete_task" in readback
    assert "done" not in readback and "fabricated/control/0" not in readback


# ---------------------------------------------------------------- marker / cwd persistence ---

REAL_CLI = pathlib.Path(__file__).resolve().parents[2] / "data/datasets/appworld/cli/aw"


def _run_real_cli(cwd, task_id, code):
    """Drive the shipped `./aw` CLI, not a model of it, and return (rc, stdout, stderr)."""
    import os
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, str(REAL_CLI), "action", "--task", task_id, "--code", code],
        cwd=cwd, capture_output=True, text=True, timeout=60,
        env={**os.environ, "HG_SVC_APPWORLD_URL": os.environ["HG_SVC_APPWORLD_URL"]})
    return completed.returncode, completed.stdout, completed.stderr


def test_the_marker_is_relative_to_cwd_and_a_cwd_change_silently_reinitialises(service, tmp_path):
    """Outcome 5: the marker/cwd persistence hazard, exercised through the shipped CLI.

    `data/datasets/appworld/cli/aw:11` makes `MARKER` relative to the process working directory and
    `:50-52` implicitly re-initialises whenever the marker is missing or mismatched. The
    discriminating observable is a **countable extra `/initialize` between actions, together with the
    loss of an earlier synthetic value** - not a change in the CLI's exit status, which stays 0.
    """
    here = tmp_path / "here"
    elsewhere = tmp_path / "elsewhere"
    here.mkdir()
    elsewhere.mkdir()

    rc, out, err = _run_real_cli(here, TASK_ID, "apis.synthetic.set_value('kept')\nprint('set')")
    assert rc == 0 and "set" in out, (rc, out, err)
    assert service.initialises == [TASK_ID], "the first call in a fresh cwd must initialise once"
    assert service.live_world["value"] == "kept"

    # Same cwd, marker present and matching: no extra initialise, the world is the same one.
    rc, out, err = _run_real_cli(here, TASK_ID, "print(apis.synthetic.set_value.__name__)")
    assert rc == 0, (rc, out, err)
    assert len(service.initialises) == 1, "a matching marker must not re-initialise"
    assert service.live_world["value"] == "kept", "the same cwd must keep the world"

    # A second cwd has no marker, so the CLI silently builds a new world - and the exit status is
    # still 0, which is exactly why the exit status is not the observable.
    rc, out, err = _run_real_cli(elsewhere, TASK_ID, "print('second cwd')")
    assert rc == 0, (rc, out, err)
    assert len(service.initialises) == 2, "a cwd change must produce a second initialise"
    assert service.live_world["value"] is None, "and the earlier value must be gone"
    assert (here / ".appworld_task").is_file() and (elsewhere / ".appworld_task").is_file()


def test_deleting_only_the_marker_reinitialises_in_the_same_cwd(service, tmp_path):
    """Negative control for the observable above: it tracks the marker, not the cwd by itself."""
    work = tmp_path / "steady"
    work.mkdir()
    rc, _, err = _run_real_cli(work, TASK_ID, "apis.synthetic.set_value('kept')")
    assert rc == 0, err
    assert len(service.initialises) == 1 and service.live_world["value"] == "kept"
    (work / ".appworld_task").unlink()
    rc, _, err = _run_real_cli(work, TASK_ID, "print('after marker loss')")
    assert rc == 0, err
    assert len(service.initialises) == 2 and service.live_world["value"] is None, \
        "marker loss alone must re-initialise in an unchanged working directory"


def test_a_stable_cwd_keeps_exactly_one_initialize(service, tmp_path):
    """The converse observable: one initialise for N actions argues against the hazard, not for it."""
    work = tmp_path / "stable"
    work.mkdir()
    for step in range(3):
        rc, out, err = _run_real_cli(work, TASK_ID, f"print('step {step}')")
        assert rc == 0 and f"step {step}" in out, (rc, out, err)
    assert service.initialises == [TASK_ID], f"expected one world, saw {service.initialises}"


# ------------------------------------------------ live-versus-replayed recording differential ---

def _differential(live: dict, replayed: dict) -> dict:
    """Per-field difference between the live world and the world the recording reproduces."""
    keys = sorted(set(live) | set(replayed))
    return {key: {"live": live.get(key), "replayed": replayed.get(key)}
            for key in keys if live.get(key) != replayed.get(key)}


def test_a_faithful_recording_reproduces_the_live_world(service):
    """Outcome 6a: nothing to see - the differential is empty, which is what makes 6b mean anything."""
    _run(service, ["apis.synthetic.set_value('v')\napis.supervisor.complete_task()"],
         harness_answer="done")
    replayed = service.replay()
    assert _differential(service.live_world, replayed["world"]) == {}
    assert replayed["errors"] == []


def test_a_recording_that_loses_a_call_is_visible_only_as_a_world_difference(service):
    """Outcome 6b: the recording, not the task, is what the differential indicts."""
    _run(service, ["apis.synthetic.set_value('v')", "apis.supervisor.complete_task()"],
         harness_answer="done")
    assert service.live_world == {"value": "v", "completed": True, "completion_calls": 1,
                                  "rejected_calls": 0}
    replayed = service.replay(service.recorded[:1])          # the recording lost the completion
    assert _differential(service.live_world, replayed["world"]) == {
        "completed": {"live": True, "replayed": False},
        "completion_calls": {"live": 1, "replayed": 0}}


def test_an_http_success_with_a_rejected_api_call_is_not_a_semantic_success(service):
    """Outcome 6c: exit 0 / HTTP 200 must not be read as success (review source boundary).

    `data/datasets/appworld/service.py:48-50` uses `raise_on_failure=False`, so a rejected API call
    still returns a successful HTTP response. The observable that separates them is the service output
    and an unchanged world, not the transport status.
    """
    _run(service, ["apis.synthetic.reject('authored')"], harness_answer="claimed success")
    assert service.live_errors and "authored" in service.live_errors[0]
    assert service.live_world["rejected_calls"] == 1 and service.live_world["value"] is None
    assert service.harness_submissions == ["claimed success"], \
        "the local submission is accepted regardless - which is exactly why it is not evidence"
    assert service.replay()["errors"] == service.live_errors, "the replay must reproduce the refusal"
