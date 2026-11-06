"""Synthetic path-boundary regressions for the legacy operations layer."""
from unittest.mock import Mock

import pytest

from tools import serve_ui


@pytest.fixture
def roots(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    (tmp_path / "base_harness").mkdir()
    monkeypatch.setattr(serve_ui, "ROOT", tmp_path)
    monkeypatch.setattr(serve_ui, "RUNS", runs)
    monkeypatch.setattr(serve_ui, "WORK_ROOTS", [tmp_path / "work"])
    return tmp_path, runs


@pytest.mark.parametrize("bad", [".", "..", "../outside", "/tmp/outside", "ok\n"])
def test_direct_promotion_rejects_bad_names_without_deletion(roots, monkeypatch, bad):
    root, runs = roots
    state = runs / "good" / "state_after_round1"
    state.mkdir(parents=True)
    (state / "harness.json").write_text("{}")
    remove = Mock(side_effect=AssertionError("must not delete"))
    monkeypatch.setattr(serve_ui.shutil, "rmtree", remove)
    assert "error" in serve_ui.promote("good", 1, bad, True)
    assert "error" in serve_ui.promote(bad, 1, "good", True)
    remove.assert_not_called()


def test_promotion_rejects_symlink_escape(roots, monkeypatch):
    root, runs = roots
    outside = root / "outside"
    outside.mkdir()
    (outside / "harness.json").write_text("{}")
    (runs / "evil").symlink_to(outside, target_is_directory=True)
    state = runs / "good" / "state_after_round1"
    state.mkdir(parents=True)
    (state / "harness.json").write_text("{}")
    (root / "base_harness" / "evil").symlink_to(outside, target_is_directory=True)
    remove = Mock(side_effect=AssertionError("must not delete"))
    monkeypatch.setattr(serve_ui.shutil, "rmtree", remove)
    assert "error" in serve_ui.promote("evil", 1, "new", True)
    assert "error" in serve_ui.promote("good", 1, "evil", True)
    remove.assert_not_called()
    assert (outside / "harness.json").read_text() == "{}"


def test_promotion_rejects_symlinked_base_root(roots, monkeypatch):
    root, runs = roots
    state = runs / "good" / "state_after_round1"
    state.mkdir(parents=True)
    (state / "harness.json").write_text("{}")
    (root / "base_harness").rmdir()
    outside = root.parent / (root.name + "-outside")
    outside.mkdir()
    (root / "base_harness").symlink_to(outside, target_is_directory=True)
    remove = Mock(side_effect=AssertionError("must not delete"))
    monkeypatch.setattr(serve_ui.shutil, "rmtree", remove)
    assert "error" in serve_ui.promote("good", 1, "new", True)
    remove.assert_not_called()
    assert not (outside / "new").exists()


@pytest.mark.parametrize("bad", [".", "..", "../outside", "/tmp/outside", "good\n"])
def test_legacy_read_helpers_reject_traversal(roots, bad):
    assert serve_ui.run_detail(bad) is None
    assert not serve_ui.task_detail(bad, "task")["found"]
    assert not serve_ui.trace_for(bad, "task")["found"]
    assert serve_ui.trace_paths(bad, "task") == []
    assert "error" in serve_ui.step_diff(bad, 1)
    assert "error" in serve_ui.job_status(bad)


def test_trace_symlinks_cannot_expose_external_files(roots):
    root, runs = roots
    outside = root / "outside"
    outside.mkdir()
    (outside / "task.jsonl").write_text('{"command":"secret"}\n')
    (runs / "linked").symlink_to(outside, target_is_directory=True)
    assert not serve_ui.trace_for("linked", "task")["found"]
    traces = runs / "good" / "round1_traces"
    traces.mkdir(parents=True)
    (traces / "task.jsonl").symlink_to(outside / "task.jsonl")
    assert not serve_ui.trace_for("good", "task")["found"]
