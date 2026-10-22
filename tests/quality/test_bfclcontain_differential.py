"""Task-independent full-call/name-projection differential; known unsafe parser finding.

Passing means the retained observations reproduce, NOT expression containment,
source approval, handler binding, official scorer execution or H0 admission.
"""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parents[1] / "fixtures/hg_bfclcontain"
spec = importlib.util.spec_from_file_location("bfclcontain_control", HERE / "control.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
CASES = json.loads((HERE / "cases.json").read_text())
OBSERVED = [json.loads(line) for line in (HERE / "observations.jsonl").read_text().splitlines()]


def test_source_inventory_and_sink_frontier_have_not_drifted():
    rows, sinks = control.inventory()
    assert rows == json.loads((HERE / "source-inventory.json").read_text())
    assert sinks == [json.loads(line) for line in (HERE / "sinks.jsonl").read_text().splitlines()]
    reviews = [json.loads(line) for line in (HERE / "boundary-review.jsonl").read_text().splitlines()]
    assert len(reviews) == len(sinks)
    for index, (sink, review) in enumerate(zip(sinks, reviews), 1):
        assert review["site"] == index
        assert review["source"] == f"{sink['path']}:{sink['line']}"
        assert review["sink"] == sink["sink"]
        assert review["function"] == sink["function"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_fabricated_full_call_differential(case):
    actual = control.differential([case])[0]
    expected = next(row for row in OBSERVED if row.get("id") == case["id"])
    assert {"kind": "differential", **actual} == expected


def test_containment_controls_and_uncontained_binop_finding():
    # In-memory canary only. This positively detects execution, rather than
    # treating a happy-path result or exception as proof of a boundary.
    actual = [{"kind": "boundary", **row} for row in control.boundary_controls()]
    assert actual == [row for row in OBSERVED if row["kind"] == "boundary"]
    assert actual[-1]["probe_events"] == ["entered"]
    assert actual[-1]["outcome"].startswith("UNCONTAINED_STOP")


@pytest.mark.parametrize("event,args", [
    ("open", ("/dev/nvidia0", "rb", 0)),
    ("open", ("/fabricated/dataset-row.json", "r", 0)),
    ("socket.connect", (None, ("127.0.0.1", 1))),
    ("subprocess.Popen", ("fabricated-scorer", [], None, None)),
    ("import", ("bfcl_eval.eval_checker.eval_runner", None, [], [], [])),
    ("import", ("torch", None, [], [], [])),
])
def test_external_dependency_tripwire_fails_even_if_callee_swallows(event, args):
    # Emit audit events directly: no socket, device, dataset or process is opened.
    with pytest.raises(AssertionError, match="external dependency attempted"):
        with control.no_external_dependencies():
            try:
                sys.audit(event, *args)
            except RuntimeError:
                pass


def test_model_map_dependent_schema_is_a_loud_refusal():
    case = json.loads(json.dumps(CASES[0]))
    case["schema"][0]["name"] = "unbound.fabricated_echo"
    with pytest.raises(AssertionError, match="model map would be required"):
        control.differential([case])
