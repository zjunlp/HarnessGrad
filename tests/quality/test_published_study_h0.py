"""The bundled release works offline and stays scoped to its original tasks."""
import argparse
from pathlib import Path

import pytest

from harnessgrad.loops.mode_a import _matching_verification_context
from harnessgrad.orchestration import h0
from harnessgrad.provenance import published_h0


def options(**changes):
    return argparse.Namespace(**{
        "h0_comparability": "strict", "h0_subset_view": False, "side": "train",
        "dataset": "demo", "sampling": "diagnostic", "harness": "loop", "harness_path": None,
        "tasks": None, **changes,
    })


def test_release_preset_uses_original_cohort_and_all_feedback():
    args = options()
    cohort_file = published_h0.configure(args, set())
    point = published_h0.point()
    ids = args.tasks.split(",")
    assert args.dataset == "alfworld" and args.sampling == "all"
    assert Path(cohort_file).read_text().strip().split(",") == ids
    assert Path(args.harness_path).name == "loop-study-0.2.0"
    assert set(ids) == set(point["per_task"]) and len(ids) == 100
    assert sum(point["per_task"].values()) == point["n_passed"] == 53
    assert point["score"] == .53
    assert all(published_h0.traces(ids).values())


@pytest.mark.parametrize("field,value", [("dataset", "demo"), ("sampling", "below_0.5"),
                                          ("harness", "oracle"), ("tasks", "different")])
def test_preset_refuses_incompatible_explicit_selection(field, value):
    with pytest.raises(ValueError):
        published_h0.configure(options(**{field: value}), {field})


def test_historical_import_requires_the_harnessgrad_environment():
    point = published_h0.point()
    ids = point["sampling"]["task_ids"]
    with pytest.raises(h0.H0Refused, match="scoring environment"):
        h0.materialize_published_h0(point["identity"]["harness_sha"], {}, None, options(dataset="alfworld"),
            None, [{"task_id": tid} for tid in ids], {tid: {} for tid in ids})
    assert not _matching_verification_context({"shared_from": {"comparability": "published"}}, {})


def test_changed_release_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(published_h0, "RELEASE", tmp_path)
    (tmp_path / "curve.jsonl").write_text("{}")
    with pytest.raises(ValueError, match="pinned release"):
        published_h0.point()
