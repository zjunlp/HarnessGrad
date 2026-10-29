"""The wrapper method: it decides whether a round is worth measuring, and nothing else.

The rule it implements is the platform's own: `harnessgrad/loops/mode_a.py:93`
verifies a step's `decision: "no_change"` claim before anything is reused, and
`methods/lightrsi/run.py` is the producer of that claim.

Three things can go wrong silently here, and they are what these tests pin:

* the wrapper claims "no change" for a tree that *did* change -- the platform checks the
  claim (`mode_a._declared_no_change`), but a wrapper that made the claim wrongly would
  spend every round arguing with that check;
* the saturation rule reads the wrong field and never fires, or fires on a round with an
  unmeasured task, which is not a full score;
* the base method is called on a saturated round, which is the cost the rule exists to
  avoid.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RUN = Path(__file__).resolve().parents[2] / "methods" / "lightrsi" / "run.py"


def _harness(root: Path, *, body: str = "print('agent')\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.json").write_text(json.dumps({"name": "fixture", "version": "0.1.0",
                                                   "entrypoint": "agent.py"}))
    (root / "agent.py").write_text(body)
    return root


def _channel(harness: Path, round_no: int, per_task: dict, **extra) -> None:
    hist = harness / "_harnessgrad" / "history"
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"round-{round_no}.json").write_text(json.dumps(
        {"round": round_no, "score": 1.0, "per_task": per_task, **extra}))


BASE_METHOD = """\
import json, shutil, sys
from pathlib import Path
req = json.loads(sys.stdin.read())
ws = Path(req["workspace"]); ws.mkdir(parents=True, exist_ok=True)
if req.get("marker"):
    Path(req["marker"]).write_text("called")
dest = ws / "candidate"
shutil.copytree(Path(req["base_harness"]), dest)
MUTATE = __MUTATE__
if MUTATE:
    (dest / "agent.py").write_text(MUTATE)
Path(req["trajectory_out"]).write_text(json.dumps({
    "steps": [{"harness_dir": str(dest), "label": "fixture step", "edit_kind": "fixture"}],
    "provenance": {"method": "fixture",
                   "acceptance_rule": {"text": "fixture", "source": "test",
                                       "calibrated": False}},
}))
print(json.dumps({"steps": 1, "changed": True, "generation_tokens": 7}))
"""


def _base_method(tmp: Path, mutate: str | None) -> Path:
    path = tmp / "base_method.py"
    path.write_text(BASE_METHOD.replace("__MUTATE__", repr(mutate)))
    return path


def _run_wrapper(tmp: Path, base_method: Path, request: dict) -> dict:
    out = tmp / "trajectory.json"
    request = {**request, "trajectory_out": str(out)}
    proc = subprocess.run(
        [sys.executable, str(RUN), "--base", str(base_method)],
        input=json.dumps(request), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    reply = json.loads(proc.stdout)
    trajectory = json.loads(out.read_text()) if out.is_file() else None
    return {"reply": reply, "trajectory": trajectory}


def _request(tmp: Path, harness: Path, *, round_index: int = 1) -> dict:
    return {"platform_api_version": "0.1.0", "base_harness": str(harness),
            "workspace": str(tmp / "ws"), "round_index": round_index,
            "task_ids": ["t1"], "trajectory_out": ""}


def test_an_unchanged_candidate_is_declared_no_change(tmp_path):
    harness = _harness(tmp_path / "base")
    _channel(harness, 0, {"t1": 0.0})
    base = _base_method(tmp_path, None)
    out = _run_wrapper(tmp_path, base, _request(tmp_path, harness))
    step = out["trajectory"]["steps"][0]
    assert step["decision"] == "no_change"
    assert step["incumbent_round"] == 0
    assert step["method_reported"]["lightrsi"]["reason"].startswith(
        "the candidate's content is identical")
    assert out["reply"]["declared_no_change"] == 1
    assert out["reply"]["steps"] == 1


def test_a_changed_candidate_is_passed_through(tmp_path):
    harness = _harness(tmp_path / "base")
    _channel(harness, 0, {"t1": 0.0})
    base = _base_method(tmp_path, "print('changed')\n")
    out = _run_wrapper(tmp_path, base, _request(tmp_path, harness))
    step = out["trajectory"]["steps"][0]
    assert "decision" not in step
    assert out["reply"]["declared_no_change"] == 0
    assert out["reply"]["generation_tokens"] == 7


def test_a_saturated_incumbent_is_not_measured_and_does_not_call_the_base(tmp_path):
    harness = _harness(tmp_path / "base")
    _channel(harness, 2, {"t1": 1.0, "t2": 1.0}, n_invalid=0, n_harness_failed=0)
    marker = tmp_path / "called"
    base = _base_method(tmp_path, "print('changed')\n")
    request = {**_request(tmp_path, harness, round_index=3), "marker": str(marker)}
    out = _run_wrapper(tmp_path, base, request)
    step = out["trajectory"]["steps"][0]
    assert step["decision"] == "no_change"
    assert step["incumbent_round"] == 2
    assert step["method_reported"]["lightrsi"]["base_method_called"] is False
    assert not marker.exists(), "the base method must not run on a saturated round"


def test_an_unmeasured_task_means_the_incumbent_is_not_saturated(tmp_path):
    harness = _harness(tmp_path / "base")
    _channel(harness, 0, {"t1": 1.0, "t2": 1.0}, n_invalid=1)
    base = _base_method(tmp_path, "print('changed')\n")
    out = _run_wrapper(tmp_path, base, _request(tmp_path, harness))
    assert "decision" not in out["trajectory"]["steps"][0]
    assert out["trajectory"]["provenance"]["method"].endswith("+LightRSI")


def test_the_provenance_names_the_base_method(tmp_path):
    harness = _harness(tmp_path / "base")
    _channel(harness, 0, {"t1": 0.0})
    base = _base_method(tmp_path, None)
    out = _run_wrapper(tmp_path, base, _request(tmp_path, harness))
    provenance = out["trajectory"]["provenance"]
    assert provenance["method"].endswith("+LightRSI")
    assert provenance["base_provenance"]["method"] == "fixture"


def test_a_failing_base_method_is_reported_not_swallowed(tmp_path):
    harness = _harness(tmp_path / "base")
    _channel(harness, 0, {"t1": 0.0})
    base = tmp_path / "base_method.py"
    base.write_text("raise SystemExit(3)\n")
    out = _run_wrapper(tmp_path, base, _request(tmp_path, harness))
    # Reported through the shared editor: the reason lands on the step the platform reads as
    # "the step did not finish", and the round is not silently turned into a measurement.
    assert "exited 3" in out["reply"]["hypothesis"]
    step = out["trajectory"]["steps"][0]
    assert "exited 3" in step["method_reported"]["error"]
    assert out["reply"]["changed"] is False


def test_the_tree_digest_ignores_the_channel_and_sees_real_edits(tmp_path):
    sys.path.insert(0, str(RUN.parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location("lightrsi_run", RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    harness = _harness(tmp_path / "h")
    first = module.tree_digest(harness)
    _channel(harness, 0, {"t1": 1.0})
    assert module.tree_digest(harness) == first, "the channel is not part of the artifact"
    (harness / "agent.py").write_text("print('edited')\n")
    assert module.tree_digest(harness) != first
