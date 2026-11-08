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
current_spec = importlib.util.spec_from_file_location('bfcl_current_packet', HERE / 'current_packet.py')
current_packet = importlib.util.module_from_spec(current_spec)
current_spec.loader.exec_module(current_packet)

CASES = json.loads((HERE / "cases.json").read_text())
OBSERVED = [json.loads(line) for line in (HERE / "observations.jsonl").read_text().splitlines()]


def test_source_inventory_and_sink_frontier_have_not_drifted():
    rows, sinks = control.inventory()
    packet = current_packet.read_current_packet()
    assert rows == packet['sources']
    assert sinks == packet['sinks']
    reviews = packet['reviews']
    assert len(reviews) == len(sinks)
    for index, (sink, review) in enumerate(zip(sinks, reviews), 1):
        assert review["site"] == index
        assert review["source"] == f"{sink['path']}:{sink['line']}"
        assert review["sink"] == sink["sink"]
        assert review["function"] == sink["function"]


def test_source_inventory_covers_bfcl_verifier_python_inputs():
    from data.datasets import bfcl_v3

    spec = bfcl_v3.verify_for({"task_id": "authored/bfcl-inventory",
                               "gold": {"functions": ["fabricated_echo"], "subset": "simple"}})
    sources = {Path(entry["from"]).relative_to(control.ROOT).as_posix()
               for entry in spec["inputs"]
               if "from" in entry and entry["from"].endswith(".py")}
    inventory = {row["path"] for row in current_packet.read_current_packet()["sources"]}
    assert sources <= inventory, f"unreviewed verifier sources: {sorted(sources - inventory)}"


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


def test_current_packet_keeps_original_integrity_and_explicit_review_delta():
    packet = current_packet.read_current_packet()
    assert len(packet['sources']) == 66 and len(packet['sinks']) == 44
    assert packet['source_gate_satisfied'] is packet['official_scorer_executed'] is False
    assert set(packet['semantic_review']) == {'data/scaffolds/qa/base.py', 'data/scaffolds/qa/control.py'}
    new = [r for r in packet['reviews'] if r['original_site'] is None]
    assert len(new) == 1 and new[0]['function'] == '_oracle_prediction'
    assert new[0]['control_state'] == 'UNKNOWN'


def test_missing_current_packet_is_not_recreated(tmp_path):
    for name in current_packet.ORIGINAL_PACKET_PINS:
        (tmp_path / name).write_bytes((HERE / name).read_bytes())
    with pytest.raises(ValueError, match='current BFCL packet missing; no automatic regeneration'):
        current_packet.read_current_packet(tmp_path)
    assert not (tmp_path / 'current-v2').exists()


def test_original_packet_drift_is_not_hidden_by_current_view(tmp_path):
    import shutil
    for name in current_packet.ORIGINAL_PACKET_PINS:
        (tmp_path / name).write_bytes((HERE / name).read_bytes())
    shutil.copytree(HERE / 'current-v2', tmp_path / 'current-v2')
    (tmp_path / 'sinks.jsonl').write_bytes((tmp_path / 'sinks.jsonl').read_bytes() + b' ')
    with pytest.raises(ValueError, match='original BFCL packet identity changed'):
        current_packet.read_current_packet(tmp_path)
