"""Owned deferred resume transactions; real Git trees, authored evidence only."""
from contextlib import ExitStack
from pathlib import Path

import pytest

from eval.ckpt.git_state import stage_workspace, commit_state
from harnessgrad.orchestration import resume
from harnessgrad.orchestration.candidate_stage import WorkspaceOps
from test_resume_train import _run, _ctx


def snapshot(root):
    return {str(path.relative_to(root)): (path.lstat().st_mode,
            str(path.readlink()) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
            for path in root.rglob("*")}


@pytest.fixture
def transaction(tmp_path):
    directory = _run(tmp_path, platform="before")
    work = tmp_path / "work"
    work.mkdir()
    (work / "operator-sentinel").write_bytes(b"unchanged\x00original")
    request = resume.ResumeRequest(3, resume.ResumeIdentities(None, None, _ctx()), across_platforms="authored reviewed move")
    calls = []
    platform = resume.PlatformEvidence(lambda: calls.append("content") or "after",
                                        lambda: calls.append("git") or "authored-git")
    return directory, work, request, platform, calls


def prepare(transaction):
    directory, work, request, platform, _ = transaction
    return resume.prepare_resume_workspace_lease(request, directory, None, work,
        ops=WorkspaceOps(stage_workspace, commit_state), platform=platform)


def test_prepare_and_later_refusal_leave_all_original_evidence_unchanged(transaction):
    directory, work, _, _, calls = transaction
    before_run, before_work = snapshot(directory), snapshot(work)
    with ExitStack() as outer:
        lease = prepare(transaction)
        outer.callback(lease.close)
        assert snapshot(directory) == before_run and snapshot(work) == before_work
        assert Path(lease._temporary.name).exists()
        assert lease.state["last"] == 1 and lease.state["base"]["traces"]["t1"]
        # This is where driver readiness may refuse; no publication is attempted.
    assert snapshot(directory) == before_run and snapshot(work) == before_work
    assert not list(work.parent.glob(".resume-*"))
    assert calls == ["content"] and not (directory / "platform-seams").exists()


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_failed_publication_restores_exact_original_bytes_before_cleanup(transaction, monkeypatch, error_type):
    directory, work, _, _, calls = transaction
    before_run, before_work = snapshot(directory), snapshot(work)
    lease = prepare(transaction)
    staging = Path(lease._temporary.name)
    before_staged = snapshot(staging / "workspace")
    original = Path.rename
    renames = []
    error = error_type("authored second rename refusal")
    def rename(path, target):
        renames.append((path, Path(target)))
        if path == staging / "workspace" and Path(target) == work:
            raise error
        return original(path, target)
    monkeypatch.setattr(Path, "rename", rename)
    try:
        with pytest.raises(error_type) as caught:
            lease.publish()
        assert caught.value is error
        assert renames == [(work, staging / "previous-workspace"), (staging / "workspace", work),
                           (staging / "previous-workspace", work)]
        assert snapshot(work) == before_work and snapshot(directory) == before_run
        assert snapshot(staging / "workspace") == before_staged
        assert not (staging / "previous-workspace").exists()
        assert calls == ["content"]
        with pytest.raises(RuntimeError, match="already attempted"):
            lease.publish()
    finally:
        lease.close()
    assert not staging.exists() and snapshot(work) == before_work


def test_cancellation_before_publish_removes_only_owned_temp(transaction):
    directory, work, _, _, _ = transaction
    before_run, before_work = snapshot(directory), snapshot(work)
    with pytest.raises(KeyboardInterrupt):
        with ExitStack() as outer:
            lease = prepare(transaction)
            outer.callback(lease.close)
            raise KeyboardInterrupt("authored late cancellation")
    assert snapshot(directory) == before_run and snapshot(work) == before_work
    assert not list(work.parent.glob(".resume-*"))
    assert (directory / "state_after_round0").is_dir()


def test_publish_is_single_shot_and_seam_is_deferred_until_rename_success(transaction):
    directory, work, _, _, calls = transaction
    before_curve = (directory / "curve.jsonl").read_bytes()
    lease = prepare(transaction)
    try:
        assert calls == ["content"]
        lease.publish()
        assert calls == ["content", "git"]
        assert not (work / "operator-sentinel").exists()
        assert (work / "agent.py").read_text() == "print('round 1')\n"
        assert (directory / "curve.jsonl").read_bytes() == before_curve
        assert len(list((directory / "platform-seams").glob("*.json"))) == 1
        before = snapshot(directory), snapshot(work)
        with pytest.raises(RuntimeError, match="already attempted"):
            lease.publish()
        assert (snapshot(directory), snapshot(work)) == before
    finally:
        lease.close()


def test_cancel_during_owned_staging_preserves_original_records(transaction):
    directory, work, request, platform, _ = transaction
    before_run, before_work = snapshot(directory), snapshot(work)
    seen = []
    def stage(source, target):
        stage_workspace(source, target)
        seen.append(target)
        raise KeyboardInterrupt("authored stage cancellation")
    with pytest.raises(KeyboardInterrupt):
        resume.prepare_resume_workspace_lease(request, directory, None, work,
            ops=WorkspaceOps(stage, commit_state), platform=platform)
    assert not seen[0].parent.exists()
    assert snapshot(directory) == before_run and snapshot(work) == before_work


def test_nothing_to_do_is_distinct_and_never_allocates_a_workspace(transaction):
    from dataclasses import replace
    directory, work, request, platform, calls = transaction
    before_run, before_work = snapshot(directory), snapshot(work)
    with pytest.raises(resume.ResumeNothingToDo):
        resume.prepare_resume_workspace_lease(replace(request, rounds=1), directory, None, work,
            ops=WorkspaceOps(lambda *a: pytest.fail("staged completed run"), commit_state), platform=platform)
    assert snapshot(directory) == before_run and snapshot(work) == before_work
    assert not list(work.parent.glob(".resume-*")) and calls == ["content"]


def test_driver_initial_metadata_decode_error_stays_outside_admission_catch(tmp_path, monkeypatch):
    import json
    import sys
    import driver
    from harnessgrad import config
    run = tmp_path / "runs/authored"
    run.mkdir(parents=True)
    meta = run / "run_meta.json"
    meta.write_bytes(b"{authored malformed original")
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setattr(resume, "admit_resume_bindings", lambda *a, **k: pytest.fail("admission before metadata decode"))
    monkeypatch.setattr(driver, "_write_run_meta", lambda *a, **k: pytest.fail("rewrote refused metadata"))
    monkeypatch.setattr(sys, "argv", ["driver.py", "--harness", "loop_plain", "--dataset", "demo",
        "--sampling", "all", "--rounds", "0", "--run-id", "authored", "--resume", "--improver", "none",
        "--runs-root", str(tmp_path / "runs"), "--work-root", str(tmp_path / "work")])
    config.reset()
    try:
        with pytest.raises(json.JSONDecodeError):
            driver.main()
    finally:
        config.reset()
    assert meta.read_bytes() == b"{authored malformed original"
    assert not (tmp_path / "work").exists()


@pytest.mark.parametrize("saved,current,error", [
    ({"split_protocol": {}}, {"file_sha256": "x"}, KeyError),
    ({"split_protocol": 7}, {"file_sha256": "x"}, TypeError),
    ({"split_protocol": {}}, {}, KeyError),
    ({"split_protocol": []}, {"file_sha256": "x"}, TypeError),
])
def test_malformed_frozen_protocol_is_not_reclassified_as_a_cli_refusal(saved, current, error):
    with pytest.raises(error):
        resume.frozen_split_changed(saved, current)
