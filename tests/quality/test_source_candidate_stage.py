"""Source lease / committed-candidate ownership with authored offline harnesses."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from eval.ckpt.git_state import stage_workspace, commit_state, content_sha
from harnessgrad.orchestration import source_lease as sources
from harnessgrad.orchestration import candidate_stage as stages
from tools.validate_harness import source_identity, filesystem_identity


def harness(path, *, managed=False):
    path.mkdir(parents=True)
    manifest = {"name": "authored-source", "version": "1", "entrypoint": "agent.py",
                "backend": "cli", "env_kinds": ["files"]}
    if managed:
        manifest["runtime"] = {"kind": "python", "python": "3.12", "requirements": "requirements.lock"}
        (path / "requirements.lock").write_text("# Empty authored SDK, no registry packages\n")
    (path / "harness.json").write_text(json.dumps(manifest))
    (path / "agent.py").write_text("raise AssertionError('source must never be imported')\n")
    return path


def request(source, tmp_path, *, reviewed=False):
    return sources.SourceRequest("loop", str(source), "train", "authored", str(tmp_path / "runs"),
        str(tmp_path / "work"), None, None,
        source_identity(source)["submission_sha256"] if reviewed else None,
        filesystem_identity(source)["sha256"] if reviewed else None)


def own(source):
    receipt = source_identity(source)
    return stages.OwnedSource(source, {"path": str(source), **receipt}, receipt, None, None, None)


class Journal:
    def __init__(self):
        self.events = []
        self.reporter = self

    def note(self, message, *, level): self.events.append(("note", message, level))
    def source(self, value): self.events.append(("source", dict(value)))
    def reject_source(self, path, error): self.events.append(("reject", path, error))
    def fixed_snapshot(self, work, sha): self.events.append(("snapshot", sha))
    def failed(self, receipt, error): self.events.append(("failed", receipt, error))
    def record(self, value): self.events.append(("record", value))
    def external(self, value): self.events.append(("external", value))


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_reviewed_snapshot_is_held_until_outer_owner_closes(tmp_path, monkeypatch, error_type):
    source = harness(tmp_path / "operator-source")
    before = source_identity(source)
    req = request(source, tmp_path, reviewed=True)
    allocated, events = [], []
    real_copy = sources.external_source_snapshot
    def interrupted_copy(origin, target):
        real_copy(origin, target)
        allocated.append(target)
        raise error_type("authored snapshot interruption")
    monkeypatch.setattr(sources, "external_source_snapshot", interrupted_copy)
    @contextmanager
    def provider():
        events.append("provider-enter")
        try: yield
        finally: events.append(("provider-exit", allocated[0].exists()))
    with ExitStack() as outer:
        outer.enter_context(provider())
        lease = outer.enter_context(sources.SourceLease())
        expected = sources.SourceRefused if error_type is ValueError else KeyboardInterrupt
        with pytest.raises(expected):
            sources.select_source(req, lease, roots=lambda: [], named_source=lambda _: None)
        assert allocated[0].exists(), "review refusal must not close the lease before caller reporting"
        events.append("reported")
    assert not allocated[0].exists()
    assert events == ["provider-enter", "reported", ("provider-exit", False)]
    assert source_identity(source) == before


def test_reviewed_copy_rebases_links_without_changing_the_operator_tree(tmp_path):
    source = harness(tmp_path / "operator")
    (source / "linked.py").symlink_to(source / "agent.py")
    req = request(source, tmp_path, reviewed=True)
    with sources.SourceLease() as lease:
        selected = sources.select_source(req, lease, roots=lambda: [], named_source=lambda _: None)
        snapshot = selected.path
        assert snapshot != source and selected.reviewed_original == source
        assert (snapshot / "linked.py").resolve() == snapshot / "agent.py"
        assert source_identity(snapshot) == selected.receipt
    assert not snapshot.exists()
    assert (source / "linked.py").readlink() == source / "agent.py"


def test_catalogue_roots_are_explicit_and_keep_fixture_and_installed_absence_order(tmp_path):
    platform = tmp_path / "not-the-module-location"
    shipped = platform / "base_harness/same"
    fixture = platform / "tests/fixtures/base_harness/same"
    shipped.mkdir(parents=True); fixture.mkdir(parents=True)
    roots = sources.harness_roots(platform)
    assert sources.harness_by_name("same", roots) == shipped
    shipped.rmdir()
    assert sources.harness_by_name("same", roots) == fixture
    fixture.rmdir()
    assert sources.harness_by_name("same", roots) is None


def test_external_source_does_not_resolve_named_catalogue(tmp_path):
    source = harness(tmp_path / "external")
    def forbidden(*args): pytest.fail("external source consulted named catalogue")
    with sources.SourceLease() as lease:
        assert sources.select_source(request(source, tmp_path), lease, roots=forbidden, named_source=forbidden).path == source


def test_owned_stage_does_not_adopt_git_hooks_and_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    source = harness(tmp_path / "operator")
    marker = tmp_path / "hook-executed"
    git = source / ".git/hooks"; git.mkdir(parents=True)
    (git / "pre-commit").write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    (git / "pre-commit").chmod(0o755)
    before = source_identity(source)
    work = tmp_path / "workspace"
    journal = Journal()
    ops = stages.WorkspaceOps(stage_workspace, commit_state)
    manifest = stages.stage_owned_source(own(source), work, restored=False, ops=ops, journal=journal)
    assert manifest["name"] == "authored-source"
    ops.commit(work, "authored fixed source")
    assert not marker.exists() and source_identity(source) == before
    assert journal.events[0][0] == "source"
    assert journal.events[0][1]["harness_sha"] == content_sha(work)
    assert not list(tmp_path.glob("hg-source-*"))


def test_stage_cancellation_cleans_only_local_stage_and_keeps_source_lease(tmp_path):
    source = harness(tmp_path / "operator")
    req = request(source, tmp_path, reviewed=True)
    seen = []
    with sources.SourceLease() as lease:
        selected = sources.select_source(req, lease, roots=lambda: [], named_source=lambda _: None)
        def cancel(snapshot, work):
            seen.append(snapshot)
            assert snapshot.exists() and selected.path.exists()
            raise KeyboardInterrupt("authored staging cancellation")
        owned = stages.OwnedSource(selected.path, selected.provenance, selected.receipt,
            selected.reviewed_original, req.expected_harness_sha256, req.expected_harness_filesystem_sha256)
        with pytest.raises(KeyboardInterrupt):
            stages.stage_owned_source(owned, tmp_path / "work", restored=False,
                ops=stages.WorkspaceOps(cancel, lambda *a: pytest.fail("commit after cancelled stage")), journal=Journal())
        assert not seen[0].exists() and selected.path.exists()
    assert source.exists()


@pytest.mark.parametrize("managed", [False, True])
def test_stage_failure_keeps_legacy_exception_or_managed_journal(tmp_path, managed):
    source = harness(tmp_path / "source", managed=managed)
    error = TypeError("authored stage refusal")
    def fail(*args): raise error
    journal = Journal()
    owned = stages.OwnedSource(source, None, None, None, None, None)
    expected = stages.CandidateStageRefused if managed else TypeError
    with pytest.raises(expected) as caught:
        stages.stage_owned_source(owned, tmp_path / "work", restored=False,
            ops=stages.WorkspaceOps(fail, lambda *a: None), journal=journal)
    if managed:
        assert caught.value.__cause__ is error and journal.events == [("reject", source, error)]
    else:
        assert caught.value is error and not journal.events


def test_manifest_read_stays_outside_stage_refusal_boundary(tmp_path, monkeypatch):
    error = ValueError("authored manifest race")
    def fail(*args): raise error
    monkeypatch.setattr(stages, "manifest", fail)
    with pytest.raises(ValueError) as caught:
        stages.stage_owned_source(stages.OwnedSource(tmp_path, None, None, None, None, None), tmp_path,
            restored=True, ops=stages.WorkspaceOps(lambda *a: pytest.fail("restored source restaged"), lambda *a: None),
            journal=Journal())
    assert caught.value is error


def options(tmp_path, **kw):
    return replace(stages.PreparationOptions("train", None, None, tmp_path, None, True), **kw)


def test_fixed_commit_snapshot_prepare_admit_record_and_external_binding_order(tmp_path, monkeypatch):
    journal = Journal()
    def commit(work, message): journal.events.append(("commit", message)); return "fixed-sha"
    monkeypatch.setattr(stages, "has_python_runtime", lambda work: True)
    identity = {"authored": "identity"}
    receipt = {"status": "ready", "identity": identity, "seconds": None}
    def prepare(work, **kw):
        assert [event[0] for event in journal.events] == ["commit", "snapshot"]
        journal.events.append(("prepare",)); return receipt
    def admit(work, value, **kw):
        assert value is identity
        journal.events.append(("admit",)); return identity
    monkeypatch.setattr(stages.candidate_runtime, "prepare", prepare)
    monkeypatch.setattr(stages.candidate_runtime, "admit", admit)
    result = stages.prepare_committed_candidate(tmp_path, options(tmp_path), envs={}, setups={}, eval_from=None,
        ops=stages.WorkspaceOps(lambda *a: None, commit), journal=journal)
    assert result.fixed_source_sha == "fixed-sha" and result.binding is identity and result.preparation is receipt
    assert [event[0] for event in journal.events] == ["commit", "snapshot", "prepare", "admit", "record"]
    monkeypatch.setattr(stages.external_runtime, "identities", lambda work: {"authored": "external"})
    assert stages.bind_external_candidate(tmp_path, journal) == {"authored": "external"}
    assert journal.events[-1] == ("external", {"authored": "external"})


@pytest.mark.parametrize("point", ["commit", "prepare"])
def test_cancellation_does_not_become_failed_preparation(tmp_path, monkeypatch, point):
    journal = Journal()
    error = KeyboardInterrupt("authored candidate cancellation")
    def commit(*args):
        journal.events.append(("commit",))
        if point == "commit": raise error
        return "fixed"
    def prepare(*args, **kwargs): raise error
    monkeypatch.setattr(stages, "has_python_runtime", lambda *a: True)
    monkeypatch.setattr(stages.candidate_runtime, "prepare", prepare)
    with pytest.raises(KeyboardInterrupt) as caught:
        stages.prepare_committed_candidate(tmp_path, options(tmp_path), envs={}, setups={}, eval_from=None,
            ops=stages.WorkspaceOps(lambda *a: None, commit), journal=journal)
    assert caught.value is error
    assert [event[0] for event in journal.events] == (["commit"] if point == "commit" else ["commit", "snapshot"])


def test_failed_preparation_preserves_receipt_unknowns_and_stops_before_record(tmp_path, monkeypatch):
    journal = Journal()
    error = ValueError("authored failed preparation")
    error.receipt = {"status": "unavailable", "seconds": None, "installed": False, "authored_unknown": None}
    def fail(*a, **kw): raise error
    monkeypatch.setattr(stages, "has_python_runtime", lambda *a: True)
    monkeypatch.setattr(stages.candidate_runtime, "prepare", fail)
    with pytest.raises(stages.CandidateStageRefused) as caught:
        stages.prepare_committed_candidate(tmp_path, options(tmp_path), envs={}, setups={}, eval_from=None,
            ops=stages.WorkspaceOps(lambda *a: None, lambda *a: "fixed"), journal=journal)
    assert caught.value.__cause__ is error
    assert journal.events[-1] == ("failed", error.receipt, error)
    assert journal.events[-1][1] is error.receipt


def test_source_refusal_journal_never_overwrites_prior_evidence(tmp_path):
    source = harness(tmp_path / "invalid-managed", managed=True)
    run = tmp_path / "run"
    reporter = Journal()
    assert stages.refuse_managed_source(run, source, "first", reporter=reporter) == 2
    first = run / "harness-preparation/round-0/attempt-0001.json"
    first_bytes = first.read_bytes()
    retained = (run / "state_after_round0/agent.py").read_bytes()
    (run / "curve.jsonl").write_text("authored existing evidence\n")
    metadata = (run / "run_meta.json").read_bytes()
    (source / "agent.py").write_text("# second rejected source\n")
    assert stages.refuse_managed_source(run, source, "second", reporter=reporter) == 2
    assert first.read_bytes() == first_bytes
    assert (run / "state_after_round0/agent.py").read_bytes() == retained
    assert (run / "run_meta.json").read_bytes() == metadata
    assert (run / "curve.jsonl").read_text() == "authored existing evidence\n"
    second = run / "harness-preparation/round-0/attempt-0002.source/agent.py"
    assert second.read_bytes() == (source / "agent.py").read_bytes()


def test_real_empty_managed_runtime_is_prepared_only_after_source_commit(tmp_path, monkeypatch, request):
    if not shutil.which("uv") or not shutil.which("bwrap"):
        pytest.skip("authored native preparation requires uv and bubblewrap")
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    monkeypatch.setenv("HG_HARNESS_RUNTIME_ROOT", str(tmp_path / "runtime-store"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_OFFLINE", "1")
    source = harness(tmp_path / "managed-source", managed=True)
    work, run = tmp_path / "workspace", tmp_path / "run"
    run.mkdir()  # The caller already claimed its namespace before staging.
    from harnessgrad.python_artifacts import remove_instance
    request.addfinalizer(lambda: remove_instance(tmp_path / "runtime-store"))
    events = []
    def commit(path, message):
        value = commit_state(path, message)
        events.append(("fixed", value))
        return value
    ops = stages.WorkspaceOps(stage_workspace, commit)
    journal = stages.PreparationJournal(run, "authored")
    stages.stage_owned_source(own(source), work, restored=False, ops=ops, journal=journal)
    real_prepare = stages.candidate_runtime.prepare
    def prepare(path, **kwargs):
        assert events and content_sha(path) == events[0][1]
        assert (run / "state_after_round0").is_dir()
        events.append(("prepare",))
        return real_prepare(path, **kwargs)
    monkeypatch.setattr(stages.candidate_runtime, "prepare", prepare)
    result = stages.prepare_committed_candidate(work, options(tmp_path), envs={}, setups={}, eval_from=None,
        ops=ops, journal=journal)
    journal.publish(result)
    assert events[0][0] == "fixed" and events[1] == ("prepare",)
    assert stages.candidate_runtime.admit(work, result.binding) == result.binding
    assert json.loads((run / "run_meta.json").read_text())["harness_runtime_identity"] == result.binding


@pytest.mark.parametrize("curve", [None, [], [{"harness_runtime_identity": {"kind": "python-placements", "authored": "h0"}}]])
def test_donor_binding_keeps_explicit_then_h0_override_precedence(tmp_path, monkeypatch, curve):
    binding_file = tmp_path / "explicit.json"
    explicit = {"authored": "explicit"}
    binding_file.write_text(json.dumps(explicit))
    monkeypatch.setattr(stages.candidate_runtime, "validate", lambda value: value)
    opts = options(tmp_path, side="eval", from_run="train", binding_file=binding_file, h0_from="h0")
    if curve is not None:
        path = tmp_path / "h0/curve.jsonl"; path.parent.mkdir()
        path.write_text("\n".join(json.dumps(row) for row in curve))
    result = stages.donor_actor_binding(opts, {"harness_runtime_identity": {"authored": "eval"}})
    expected = explicit if curve is None else curve[0]["harness_runtime_identity"] if curve else None
    assert result == expected


@pytest.mark.parametrize("error_type", [OSError, ValueError])
def test_fixed_source_commit_errors_are_not_classified_as_preparation_failures(tmp_path, monkeypatch, error_type):
    error = error_type("authored commit failure")
    def fail(*args): raise error
    monkeypatch.setattr(stages.candidate_runtime, "prepare", lambda *a, **k: pytest.fail("prepared uncommitted source"))
    journal = Journal()
    with pytest.raises(error_type) as caught:
        stages.prepare_committed_candidate(tmp_path, options(tmp_path), envs={}, setups={}, eval_from=None,
            ops=stages.WorkspaceOps(lambda *a: None, fail), journal=journal)
    assert caught.value is error and not journal.events


def test_external_legacy_false_fields_are_passed_without_upgrading_them(tmp_path, monkeypatch):
    identities = {"authored": {"complete": False, "legacy": True, "cacheable": False}}
    monkeypatch.setattr(stages.external_runtime, "identities", lambda work: identities)
    journal = Journal()
    assert stages.bind_external_candidate(tmp_path, journal) is identities
    assert journal.events == [("external", identities)]


def test_external_refusal_and_metadata_error_keep_distinct_boundaries(tmp_path, monkeypatch):
    error = stages.external_runtime.RuntimeUnavailable("authored external bytes changed")
    def fail(*args): raise error
    monkeypatch.setattr(stages.external_runtime, "identities", fail)
    journal = Journal()
    with pytest.raises(stages.CandidateStageRefused) as caught:
        stages.bind_external_candidate(tmp_path, journal)
    assert caught.value.__cause__ is error
    assert journal.events == [("note", "refusing external actor runtime: authored external bytes changed", "error")]
    monkeypatch.setattr(stages.external_runtime, "identities", lambda work: {})
    metadata_error = OSError("authored metadata write failed")
    def failed_write(*args): raise metadata_error
    journal.external = failed_write
    with pytest.raises(OSError) as caught:
        stages.bind_external_candidate(tmp_path, journal)
    assert caught.value is metadata_error
