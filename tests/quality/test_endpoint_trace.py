"""Preserve historical endpoint receipts during round archival."""
import json


def test_the_records_travel_into_the_run_after_the_call(tmp_path):
    """The method can only write where its sandbox allows; the platform moves the record.

    Measured 2026-10-18: pointing the variable at `runs/...` produced zero records, because
    the method's sandbox does not mount the run directory -- the trajectory takes the same
    detour through `method_root`.
    """
    from harnessgrad.loops.mode_a import _keep_endpoint_records

    source = tmp_path / "round1_endpoint" / "endpoint"
    source.mkdir(parents=True)
    original = {"a.json": {"prompt": "p", "reply": '{"verdict":"accept"}'},
                "b.json": {"prompt": "q", "reply": "an unparseable reply"}}
    for name, record in original.items():
        (source / name).write_text(json.dumps(record))
    run_dir = tmp_path / "runs" / "rid"

    _keep_endpoint_records(source.parent, run_dir, 1)

    dest = run_dir / "method_logs" / "round-1" / "endpoint"
    assert {p.name: json.loads(p.read_bytes()) for p in dest.glob("*.json")} == original
    assert not source.exists(), "the temp copy is removed once it is in the record"


def test_a_record_that_cannot_be_kept_does_not_fail_the_round(tmp_path):
    from harnessgrad.loops.mode_a import _keep_endpoint_records

    source = tmp_path / "round2_endpoint"
    source.mkdir()
    (source / "a.json").write_text("{}")
    run_dir = tmp_path / "runs" / "rid"
    (run_dir / "method_logs").mkdir(parents=True)
    (run_dir / "method_logs" / "round-2").write_text("")     # a file where a dir belongs

    _keep_endpoint_records(source, run_dir, 2)               # must not raise
