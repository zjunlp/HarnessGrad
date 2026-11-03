"""The portable per-round snapshot: round-trip, verification, immutability, safety.

The artifact exists because a run keeps every round's diff but only sometimes its tree,
so "open round N" could fail with `round-not-stored`. What these tests hold it to:
a checked-out snapshot is byte-identical to the tree (including the executable bit and
nested directories), it verifies and then notices a single changed byte, it cannot be
silently replaced, and it refuses every path that would escape its root. The last test
pins the wiring: mode A must call the writer *inside its round loop*, and a writer that
fails must not take the run down.

The checks are per-file and per-byte because a snapshot that is almost right is worse
than a missing one: it is trusted.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import harnessgrad.snapshot as snapshot  # noqa: E402
from harnessgrad.loops import mode_a  # noqa: E402


def _tree(root: Path) -> Path:
    """A small harness tree with a nested directory, an executable and an empty file."""
    (root / "pkg" / "nested").mkdir(parents=True)
    (root / "agent.py").write_bytes(b"print('hello')\n")
    (root / "empty.txt").write_bytes(b"")
    (root / "unicode.txt").write_bytes("héllo → 世界\n".encode("utf-8"))
    script = root / "pkg" / "nested" / "run.sh"
    script.write_bytes(b"#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    return root


def _stored(tmp_path: Path) -> tuple[Path, Path]:
    """A tree plus the snapshot written from it."""
    work = _tree(tmp_path / "work")
    stored = snapshot.write(tmp_path / "runs", 1, work, run_id="run-1",
                            harness_sha="sha-1")
    return work, stored


def _resign(payload: dict) -> dict:
    """Recompute the id by the published rule, so a crafted file passes the id check.

    The point of the crafted-input tests is that the *path* is refused, not that a naive
    edit left a stale id -- an attacker can recompute the digest trivially.
    """
    body = {key: value for key, value in payload.items() if key != "id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return dict(body, id=hashlib.sha256(canonical.encode("utf-8")).hexdigest())


def test_round_trip_is_byte_identical_including_executable_and_nested_dirs(tmp_path):
    work = _tree(tmp_path / "work")
    stored = snapshot.write(tmp_path / "runs", 3, work, run_id="run-1",
                            harness_sha="abc123")
    assert stored == tmp_path / "runs" / "snapshots" / "round-3.json"

    payload = snapshot.load(stored)
    assert payload["schema"] == snapshot.SCHEMA
    assert payload["run_id"] == "run-1"
    assert payload["round"] == 3
    assert payload["harness_sha"] == "abc123"
    assert payload["parent_id"] is None
    assert set(payload["files"]) == {"agent.py", "empty.txt", "unicode.txt",
                                     "pkg/nested/run.sh"}
    # The id is the canonical digest of every field but itself -- the published rule.
    body = {key: value for key, value in payload.items() if key != "id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    assert payload["id"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    dest = tmp_path / "checkout"
    snapshot.check_out(stored, dest)
    for rel, entry in payload["files"].items():
        assert (dest / rel).read_bytes() == (work / rel).read_bytes(), rel
        assert (dest / rel).stat().st_mode & 0o111 == (0o111 if entry["executable"] else 0)
    assert os.access(dest / "pkg" / "nested" / "run.sh", os.X_OK)
    assert not os.access(dest / "agent.py", os.X_OK)
    snapshot.verify(stored, dest)  # the materialised tree is the recorded tree


def test_verify_passes_on_the_source_and_raises_after_one_byte_changes(tmp_path):
    work, stored = _stored(tmp_path)
    snapshot.verify(stored, work)

    victim = work / "agent.py"
    original = victim.read_bytes()
    victim.write_bytes(bytes([original[0] ^ 0x01]) + original[1:])
    with pytest.raises(snapshot.SnapshotInvalid, match="agent.py"):
        snapshot.verify(stored, work)


def test_repeated_write_is_idempotent_and_a_different_id_conflicts(tmp_path):
    work = _tree(tmp_path / "work")
    runs = tmp_path / "runs"
    first = snapshot.write(runs, 1, work, run_id="run-1", harness_sha="sha-1")
    before = first.stat()
    first_bytes = first.read_bytes()

    second = snapshot.write(runs, 1, work, run_id="run-1", harness_sha="sha-1")
    assert second == first
    after = first.stat()
    assert first.read_bytes() == first_bytes
    # No overwrite: the same inode and the same mtime, not a re-written file.
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)

    with pytest.raises(snapshot.SnapshotConflict):
        snapshot.write(runs, 1, work, run_id="another-run", harness_sha="sha-1")
    assert first.read_bytes() == first_bytes
    assert snapshot.load(first)["run_id"] == "run-1"


def test_parent_id_chains_rounds_and_last_id_finds_the_newest(tmp_path):
    work = _tree(tmp_path / "work")
    runs = tmp_path / "runs"
    assert snapshot.last_id(runs) is None

    first = snapshot.write(runs, 0, work, run_id="run-1", harness_sha="sha-0")
    first_id = snapshot.load(first)["id"]
    assert snapshot.load(first)["parent_id"] is None
    assert snapshot.last_id(runs) == first_id

    second = snapshot.write(runs, 1, work, run_id="run-1", harness_sha="sha-1",
                            parent_id=first_id)
    assert snapshot.load(second)["parent_id"] == first_id
    assert snapshot.last_id(runs) == snapshot.load(second)["id"]


def test_a_symlink_in_the_tree_is_refused(tmp_path):
    work = _tree(tmp_path / "work")
    os.symlink(work / "agent.py", work / "leak.py")
    with pytest.raises(snapshot.SnapshotInvalid, match="symlink"):
        snapshot.collect(work)
    with pytest.raises(snapshot.SnapshotInvalid, match="symlink"):
        snapshot.write(tmp_path / "runs", 1, work, run_id="run-1", harness_sha="sha-1")


def test_a_filename_that_is_not_utf8_is_refused(tmp_path):
    work = _tree(tmp_path / "work")
    try:
        (work / os.fsdecode(b"bad-\xff.txt")).write_text("x", encoding="utf-8")
    except OSError:  # pragma: no cover -- a filesystem that cannot hold such a name
        pytest.skip("this filesystem cannot hold a non-UTF-8 filename")
    with pytest.raises(snapshot.SnapshotInvalid, match="UTF-8"):
        snapshot.collect(work)


@pytest.mark.parametrize("bad", ["../escape", "/etc/passwd", "pkg/../../escape"])
def test_an_artifact_with_an_unsafe_path_is_refused(tmp_path, bad):
    _, stored = _stored(tmp_path)
    payload = json.loads(stored.read_text(encoding="utf-8"))
    payload["files"][bad] = {"content_b64": "", "executable": False}
    crafted = tmp_path / "crafted.json"
    crafted.write_text(json.dumps(_resign(payload)), encoding="utf-8")

    with pytest.raises(snapshot.SnapshotInvalid, match="path"):
        snapshot.load(crafted)
    with pytest.raises(snapshot.SnapshotInvalid, match="path"):
        snapshot.check_out(crafted, tmp_path / "checkout")
    assert not (tmp_path / "checkout").exists()


def test_check_out_refuses_to_write_through_a_symlink(tmp_path):
    work = _tree(tmp_path / "work")
    stored = snapshot.write(tmp_path / "runs", 1, work, run_id="run-1",
                            harness_sha="sha-1")
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    os.symlink(outside, dest / "pkg")

    with pytest.raises(snapshot.SnapshotInvalid, match="symlink"):
        snapshot.check_out(stored, dest)
    assert not (outside / "nested").exists()


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    found = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == name]
    assert len(found) == 1, f"expected exactly one top-level `{name}`"
    return found[0]


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [call for call in ast.walk(node) if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name) and call.func.id == name]


def test_mode_a_calls_the_snapshot_writer_inside_its_round_loop():
    tree = ast.parse((ROOT / "harnessgrad" / "loops" / "mode_a.py")
                     .read_text(encoding="utf-8"))

    # The wrapper is where the writer is called, and it is called with the documented
    # arguments -- an AST check, because a string match would pass on a comment.
    wrapper = _function(tree, "_snapshot_round")
    writes = [call for call in ast.walk(wrapper) if isinstance(call, ast.Call)
              and isinstance(call.func, ast.Attribute) and call.func.attr == "write"
              and isinstance(call.func.value, ast.Name) and call.func.value.id == "snapshot"]
    assert writes, "`_snapshot_round` does not call `snapshot.write`"
    assert {keyword.arg for keyword in writes[0].keywords} >= {
        "run_id", "harness_sha", "parent_id"}, ast.unparse(writes[0])

    rounds = _function(tree, "_run_mode_a")
    loops = [node for node in ast.walk(rounds) if isinstance(node, ast.While)]
    assert loops, "`_run_mode_a` has no round loop"
    calls = [call for loop in loops for call in _calls(loop, "_snapshot_round")]
    assert calls, ("`_run_mode_a` never calls `_snapshot_round` inside a `while` loop: "
                   "the per-round snapshot is not wired to the round loop")
    call = calls[0]
    assert [ast.unparse(argument) for argument in call.args[:2]] == ["run_dir", "r"]
    assert {keyword.arg for keyword in call.keywords} >= {
        "run_id", "harness_sha", "parent_id"}, ast.unparse(call)


def test_the_wrapper_returns_the_written_id(tmp_path):
    work = _tree(tmp_path / "work")
    ident = mode_a._snapshot_round(tmp_path / "runs", 1, work, run_id="run-1",
                                   harness_sha="sha-1")
    stored = tmp_path / "runs" / "snapshots" / "round-1.json"
    assert ident == snapshot.load(stored)["id"]


def test_the_wrapper_never_lets_a_snapshot_failure_abort_the_round(tmp_path, monkeypatch,
                                                                 capsys):
    work = _tree(tmp_path / "work")

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(mode_a.snapshot, "write", explode)
    result = mode_a._snapshot_round(tmp_path / "runs", 2, work, run_id="run-9",
                                    harness_sha="sha-9")
    assert result is None
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1, lines
    assert "run-9" in lines[0] and "round 2" in lines[0] and "disk full" in lines[0]
    assert not (tmp_path / "runs").exists()
