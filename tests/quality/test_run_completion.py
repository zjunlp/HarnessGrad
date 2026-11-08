"""Post-record completion ordering; summary failures never alter measurement facts."""
import json
from pathlib import Path

import pytest

from harnessgrad import records
from harnessgrad.orchestration import run_completion as finish


class Reporter:
    def __init__(self, events): self.events = events
    def note(self, message, *, level): self.events.append(("note", message, level))
    def results(self, curve, *, split): self.events.append(("results", curve, split))
    def artifact(self, name, path): self.events.append(("artifact", name, path))


@pytest.fixture
def state(tmp_path, monkeypatch):
    run = tmp_path / "run"; run.mkdir()
    curve = [{"round": 0, "score": 0, "n_scored": 1, "measured_by_platform": True}]
    path = run / "curve.jsonl"; path.write_text(json.dumps(curve[0]) + "\n")
    (run / "run_meta.json").write_text("{}")
    events = []
    monkeypatch.setattr(records, "_platform_record", lambda: {"content_sha": "authored"})
    real_write = finish._write_run_meta
    def write(directory, **kwargs):
        events.append(("metadata", kwargs))
        real_write(directory, **kwargs)
    monkeypatch.setattr(finish, "_write_run_meta", write)
    def summarise(directory):
        assert "completion" in json.loads((directory / "run_meta.json").read_text())
        events.append(("summarise",))
        return {"authored": True}
    def render(summary, target, *, dry_run, stream):
        assert dry_run is False and isinstance(stream, finish.SummaryWriteNotices)
        events.append(("render", target))
    monkeypatch.setattr(finish.run_summary, "summarise", summarise)
    monkeypatch.setattr(finish.run_summary, "write_summary", render)
    return run, path, curve, events, Reporter(events)


def test_train_completion_persists_before_summary_then_reports_original_artifact_order(state):
    run, path, curve, events, reporter = state
    before = path.read_bytes()
    assert finish.finish_train(finish.TrainFinishOptions(0, True, finish.SummaryOptions()),
        run, path, curve, None, run / "work", reporter) == 0
    assert [row[0] for row in events] == ["metadata", "note", "summarise", "render", "results",
                                         "artifact", "artifact", "artifact", "artifact"]
    assert [row[1] for row in events if row[0] == "artifact"] == ["curve", "summary", "workspace", "events"]
    assert events[0][1]["completion"]["status"] == "complete"
    assert path.read_bytes() == before


@pytest.mark.parametrize("method_stopped,strict,expected,status", [(False, False, 0, "incomplete"),
    (False, True, 2, "incomplete"), (True, True, 0, "early_stop")])
def test_train_strict_exit_and_method_stop_stay_distinct(state, method_stopped, strict, expected, status):
    run, path, curve, events, reporter = state
    (run / "run_meta.json").write_text(json.dumps({"method_stopped": method_stopped}))
    assert finish.finish_train(finish.TrainFinishOptions(1, strict, finish.SummaryOptions(no_summary=True)),
        run, path, curve, None, run / "work", reporter) == expected
    assert events[0][1]["completion"]["status"] == status
    assert not any(row[0] in ("summarise", "render") for row in events)


@pytest.mark.parametrize("stage", ["render", "summarise"])
def test_summary_exception_is_one_warning_and_does_not_change_curve_or_exit(state, monkeypatch, capsys, stage):
    run, path, curve, events, reporter = state
    before = path.read_bytes()
    def fail(*args, **kwargs): raise RuntimeError("authored derived-page failure")
    monkeypatch.setattr(finish.run_summary, "write_summary" if stage == "render" else "summarise", fail)
    assert finish.finish_train(finish.TrainFinishOptions(0, True, finish.SummaryOptions()),
        run, path, curve, None, run / "work", reporter) == 0
    warnings = capsys.readouterr().err.splitlines()
    assert len(warnings) == 1 and "RuntimeError: authored derived-page failure" in warnings[0]
    assert path.read_bytes() == before
    assert [row[1] for row in events if row[0] == "artifact"] == ["curve", "workspace", "events"]


@pytest.mark.parametrize("kind", ["metadata-read", "metadata-write", "summary-cancel"])
def test_fatal_completion_boundaries_and_cancellation_are_not_soft_summary_failures(state, monkeypatch, kind):
    run, path, curve, events, reporter = state
    before = path.read_bytes()
    if kind == "metadata-read":
        (run / "run_meta.json").write_text("{authored invalid JSON")
        error = json.JSONDecodeError
    elif kind == "metadata-write":
        def fail(*args, **kwargs): raise OSError("authored metadata failure")
        monkeypatch.setattr(finish, "_write_run_meta", fail)
        error = OSError
    else:
        def cancel(*args, **kwargs): raise KeyboardInterrupt("authored summary cancellation")
        monkeypatch.setattr(finish.run_summary, "summarise", cancel)
        error = KeyboardInterrupt
    with pytest.raises(error):
        finish.finish_train(finish.TrainFinishOptions(0, True, finish.SummaryOptions()),
            run, path, curve, None, run / "work", reporter)
    assert path.read_bytes() == before
    assert not any(row[0] == "results" for row in events)


@pytest.mark.parametrize("result,strict,expected", [(9, False, 9), (9, True, 2), (0, False, 0)])
def test_eval_keeps_its_own_failure_and_reporting_path(state, result, strict, expected):
    run, path, curve, events, reporter = state
    if result:
        path.write_text("invalid curve; failed eval must not decode it")
    assert finish.finish_eval(finish.EvalFinishOptions(strict, finish.SummaryOptions()), run, result, reporter) == expected
    assert not any(row[0] in ("results", "artifact") for row in events)
    assert [row[0] for row in events] == (["metadata", "note"] if result else ["metadata", "note", "summarise", "render"])
    assert events[0][1]["completion"]["status"] == ("incomplete" if result else "complete")


@pytest.mark.parametrize("options", [finish.SummaryOptions(no_summary=True), finish.SummaryOptions(check_only=True),
                                    finish.SummaryOptions(prepare_only=True)])
def test_summary_early_guards_do_not_construct_invalid_target(state, options):
    from dataclasses import replace
    run, path, _, events, _ = state
    class InvalidTarget:
        def __fspath__(self): raise AssertionError("disabled summary parsed its target")
    assert finish.write_end_summary(replace(options, directory=InvalidTarget()), run, path) is None
    assert not events


def test_notice_buffer_filtering_and_partial_flush_are_preserved(capsys):
    stream = finish.SummaryWriteNotices()
    assert stream.write("wrote fresh page\nreplace") == len("wrote fresh page\nreplace")
    stream.write("d previous summary\npartial notice")
    stream.flush()
    assert capsys.readouterr().err == "replaced previous summary\npartial notice\n"
