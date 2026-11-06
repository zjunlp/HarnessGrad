"""Offline security regressions for materialising a round as a candidate."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN = "private-recovery-fixture"
MANIFEST = {"name": "loop", "version": "1", "entrypoint": "agent.py", "backend": "files"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    run = runs / RUN
    run.mkdir(parents=True)
    candidates = tmp_path / "candidates"
    monkeypatch.setenv("HG_CONSOLE_RUNS", str(runs))
    monkeypatch.setenv("HG_CONSOLE_CANDIDATES", str(candidates))
    monkeypatch.setenv("HG_CONSOLE_PLATFORM", str(ROOT))
    spec = importlib.util.spec_from_file_location("round_private_server", ROOT / "console/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    return server, run, candidates


def tree(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "harness.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (path / "agent.py").write_text("synthetic = True\n", encoding="utf-8")


@pytest.mark.parametrize("force_replay", [False, True])
def test_patches_never_execute(env, monkeypatch, force_replay):
    server, run, candidates = env
    tree(run / "state_after_round0")
    diffs = run / "diffs"
    diffs.mkdir()
    (diffs / "round-1.patch").write_text("synthetic patch\n", encoding="utf-8")
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("recovery must not invoke patch or any subprocess")
    monkeypatch.setattr(server.subprocess, "run", forbidden)
    code, payload = server.harness_candidate_from_round(RUN, 1, replay=force_replay)
    assert code >= 400, payload
    assert not calls
    assert not candidates.exists() or not server.harness_candidates()["rows"]


@pytest.mark.parametrize("link_location", ["root", "manifest", "nested"])
def test_state_symlinks_are_denied_without_overwriting(env, tmp_path, link_location):
    server, run, candidates = env
    outside = tmp_path / "outside"
    tree(outside)
    state = run / "state_after_round1"
    if link_location == "root":
        state.symlink_to(outside, target_is_directory=True)
    else:
        tree(state)
        if link_location == "manifest":
            (state / "harness.json").unlink()
            (state / "harness.json").symlink_to(outside / "harness.json")
        else:
            (state / "leak").symlink_to(outside / "agent.py")
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    # An existing, edited candidate must survive a failed recovery untouched.
    existing = candidates / f"{RUN}-r1"
    tree(existing)
    (existing / "agent.py").write_text("user edit\n", encoding="utf-8")
    code, payload = server.harness_candidate_from_round(RUN, 1)
    assert code >= 400, payload
    assert (existing / "agent.py").read_text(encoding="utf-8") == "user edit\n"
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before
    assert not (candidates / f"{RUN}-r1-1").exists()


def test_snapshot_is_not_listed_while_checkout_is_in_progress(env, monkeypatch):
    server, run, candidates = env
    from harnessgrad import snapshot
    source = run / "source"
    tree(source)
    snapshot.write(run, 1, source, run_id=RUN, harness_sha=None)
    materializer = server._round_module("round_snapshot")
    original = materializer.materialize
    observed = []
    def checkout(source_fd, target_fd, **kwargs):
        result = original(source_fd, target_fd, **kwargs)
        # A valid, materialized manifest is still private until publication.
        observed.extend(server.harness_candidates()["rows"])
        return result
    monkeypatch.setattr(materializer, "materialize", checkout)
    code, payload = server.harness_candidate_from_round(RUN, 1)
    assert code == 201, payload
    assert observed == []
    assert [row["dir"] for row in server.harness_candidates()["rows"]] == [payload["candidate"]]
    assert (candidates / payload["candidate"] / "agent.py").is_file()


def test_failed_materialization_cleans_private_stage(env):
    server, run, candidates = env
    state = run / "state_after_round1"
    tree(state)
    (state / "unsafe").symlink_to(state / "agent.py")
    code, _ = server.harness_candidate_from_round(RUN, 1)
    assert code == 409
    assert list((candidates / ".round-staging").iterdir()) == []


@pytest.mark.parametrize("after_rename", [False, True])
def test_pending_publication_is_reconciled_by_identity(tmp_path, monkeypatch, after_rename):
    from console.round_publish import Publisher
    root = tmp_path / "candidates"
    root.mkdir()
    with Publisher(root) as publisher:
        stage = publisher.stage()
        (root / ".round-staging" / stage.name / "file").write_text("owned")
        real_write = publisher._write_ledger
        calls = 0
        def interrupted(ledger):
            nonlocal calls
            calls += 1
            if calls == (2 if after_rename else 1):
                if not after_rename:
                    real_write(ledger)
                raise OSError("simulated interruption")
            real_write(ledger)
        monkeypatch.setattr(publisher, "_write_ledger", interrupted)
        with pytest.raises(OSError):
            publisher.publish(stage, "first", {"source": "fixture"})
        stage.close()
    with Publisher(root) as publisher:
        with publisher.stage() as next_stage:
            assert publisher.publish(next_stage, "next", {}) == "next"
    ledger = json.loads((root / ".sources.json").read_text())
    assert "__round_publish_pending__" not in ledger
    assert ("first" in ledger) == after_rename
    if after_rename:
        assert (root / "first" / "file").read_text() == "owned"


def test_ambiguous_pending_preserves_candidate(tmp_path):
    from console.round_publish import Publisher, PublishError
    root = tmp_path / "candidates"
    root.mkdir()
    with Publisher(root) as publisher:
        with publisher.stage() as stage:
            (root / "victim").mkdir()
            (root / "victim" / "file").write_text("user")
            (root / ".sources.json").write_text(json.dumps({"__round_publish_pending__": {
                "candidate": "victim", "stage": stage.name, "dev": 0, "ino": 0, "provenance": {}}}))
            with pytest.raises(PublishError, match="ambiguous"):
                publisher.publish(stage, "next", {})
    assert (root / "victim" / "file").read_text() == "user"


@pytest.mark.parametrize("manifest", [None, "{", "{}", json.dumps({"name": "x", "version": "1", "entrypoint": "missing.py"})])
def test_invalid_manifest_is_not_published(env, manifest):
    server, run, candidates = env
    state = run / "state_after_round1"
    tree(state)
    existing = candidates / f"{RUN}-r1"
    tree(existing)
    (existing / "agent.py").write_text("user edit")
    if manifest is None:
        (state / "harness.json").unlink()
    else:
        (state / "harness.json").write_text(manifest)
    code, payload = server.harness_candidate_from_round(RUN, 1)
    assert (code, payload["error"]) == (409, "no-manifest")
    assert (existing / "agent.py").read_text() == "user edit"
    assert list((candidates / ".round-staging").iterdir()) == []
    assert [row["dir"] for row in server.harness_candidates()["rows"]] == [existing.name]


def test_manifest_subdirectory_entrypoint(env):
    server, run, candidates = env
    state = run / "state_after_round1"
    tree(state)
    (state / "src").mkdir()
    (state / "src" / "agent.py").write_text("synthetic = True\n")
    (state / "agent.py").unlink()
    (state / "harness.json").write_text(json.dumps({**MANIFEST, "path": "src"}))
    code, result = server.harness_candidate_from_round(RUN, 1)
    assert code == 201, result
    assert (candidates / result["candidate"] / "src" / "agent.py").is_file()


@pytest.mark.parametrize("path", ["../escape", "/tmp", "src\\escape"])
def test_manifest_path_escape_is_denied(env, path):
    server, run, candidates = env
    state = run / "state_after_round1"
    tree(state)
    (state / "harness.json").write_text(json.dumps({**MANIFEST, "path": path}))
    code, result = server.harness_candidate_from_round(RUN, 1)
    assert (code, result["error"]) == (409, "no-manifest")
    assert not server.harness_candidates()["rows"]
    assert list((candidates / ".round-staging").iterdir()) == []


@pytest.mark.parametrize("after_replace", [False, True])
def test_pending_write_failure_cleans_only_unreferenced_stage(tmp_path, monkeypatch, after_replace):
    from console.round_publish import Publisher
    root = tmp_path / "candidates"
    root.mkdir()
    with Publisher(root) as publisher:
        real_write = publisher._write_ledger
        def interrupted(ledger):
            if after_replace:
                real_write(ledger)
            raise OSError("simulated ledger write failure")
        monkeypatch.setattr(publisher, "_write_ledger", interrupted)
        with pytest.raises(OSError):
            with publisher.stage() as stage:
                stage_name = stage.name
                (root / ".round-staging" / stage_name / "file").write_text("private")
                publisher.publish(stage, "candidate", {})
        path = root / ".round-staging" / stage_name
        assert path.exists() == after_replace
        if after_replace:
            pending = json.loads((root / ".sources.json").read_text())["__round_publish_pending__"]
            assert pending["stage"] == stage_name
            assert (path / "file").read_text() == "private"
