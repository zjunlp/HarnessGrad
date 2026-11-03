"""`tools/export_harnesses.py` must read a published run, not only a live one.

Why this exists: the exporter was the one tool whose job was "give me the harness each
round measured", and after a run's workspace (a `/tmp` directory) was reaped it could
not read the archive it exists for -- it exited on "cannot find the run's workspace".
The console had already solved the same problem in
`console/server.py:harness_candidate_from_round`, with three stores tried in a fixed
order and the answer labelled `state` / `snapshot` / `replay`; the exporter now uses the
same order and says which store answered. These tests pin, per store:

* the tree is the stored tree, byte for byte -- not "close enough";
* the source is recorded in the manifest the export already writes (`PROVENANCE.md`)
  and printed in the summary line, because a copied tree and a replayed one are
  different claims;
* a round that no store can yield fails by name (`round-not-stored`) with a nonzero
  exit and no traceback -- a release reader must see a refusal, not a stack dump;
* a snapshot whose id no longer matches its content is a failure, never an export.

Every fixture is a synthetic run under `tmp_path`; nothing here reads the real archive,
starts a model or re-measures anything.
"""
from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "export_harnesses.py"
RUN = "export-fixture-20261102T0000Z"

#: The smallest harness the tool will accept. `harness.json` is the manifest every
#: recovery path requires, and it has to parse, so the fixture uses the real shape of
#: `base_harness/loop`.
MANIFEST = {
    "name": "loop",
    "version": "0.1",
    "path": ".",
    "entrypoint": "agent.py",
    "backend": "files",
    "env_kinds": ["files"],
}


def tree(path: Path, agent_text: str) -> Path:
    """A small harness tree with a nested directory: a copy must restore the subtree too."""
    (path / "pkg").mkdir(parents=True, exist_ok=True)
    (path / "harness.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (path / "agent.py").write_text(agent_text, encoding="utf-8")
    (path / "requirements.txt").write_text("", encoding="utf-8")
    (path / "pkg" / "tools.py").write_text("VALUE = 1\n", encoding="utf-8")
    return path


def tree_hash(path: Path) -> str:
    """The git tree hash of a tree: the identity a curve point's `harness_sha` records.

    The exporter refuses a tree whose hash is not the recorded one, so a fixture that
    wants a source to be used has to record the hash of the tree it stored -- exactly
    what a run does when it commits the round. `copy` keeps the fixture tree untouched.
    """
    copy = path.parent / f"{path.name}-hash"
    subprocess.run(["cp", "-a", str(path) + "/.", str(copy)], check=True)
    for command in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "write-tree"]):
        done = subprocess.run(command, cwd=str(copy), capture_output=True, text=True,
                              check=True)
    return done.stdout.strip()


def make_run(root: Path, shas: dict[int, str], *, points: int = 3,
             nominated: int | None = 2, workspace: str = "/gone/hg-workspace") -> Path:
    """A run directory with a curve, a `run_meta.json` and nothing else stored yet.

    `shas` maps a round number to the tree hash its curve point must record; a round
    whose tree is never stored keeps a placeholder, because only the chosen point is
    ever verified.
    """
    run = root / RUN
    run.mkdir(parents=True)
    lines = []
    for index in range(points):
        point = {
            "round": index,
            "score": 0.5,
            "identity": {
                "harness_name": "loop",
                "harness_version": "0.1",
                "harness_sha": shas.get(index, f"{index + 1:040x}"),
                "agent_model": "fixture-model",
                "agent_backend": "openai",
                "method_model": "fixture-method",
            },
            "split": {"train": [], "eval": []},
        }
        lines.append(json.dumps(point))
    (run / "curve.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta = {"harness": "loop", "nominated": nominated, "workspace": workspace}
    (run / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return run


def write_snapshot(run: Path, work: Path, round_no: int, sha: str) -> Path:
    """Store a round's tree the way `mode_a._snapshot_round` does, in the real writer.

    Using the platform's own writer (not a hand-built JSON file) keeps the test honest:
    the exporter has to accept the artifact the run actually produces.
    """
    sys.path.insert(0, str(ROOT))
    from harnessgrad import snapshot

    return snapshot.write(run, round_no, work, run_id=RUN, harness_sha=sha)


def write_patch(old: Path, new: Path, dest: Path) -> None:
    """A `-p1` unified diff between two trees: the shape `diffs/round-N.patch` has.

    `fromfile="a/<rel>"` / `tofile="b/<rel>"` is what makes `patch -p1` (the call the
    console makes and this tool copies) strip exactly one leading component.
    """
    rels = {path.relative_to(old).as_posix() for path in old.rglob("*") if path.is_file()}
    rels |= {path.relative_to(new).as_posix() for path in new.rglob("*") if path.is_file()}
    chunks = []
    for rel in sorted(rels):
        before = old / rel
        after = new / rel
        old_lines = before.read_text(encoding="utf-8").splitlines(keepends=True) \
            if before.is_file() else []
        new_lines = after.read_text(encoding="utf-8").splitlines(keepends=True) \
            if after.is_file() else []
        text = "".join(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{rel}",
                                            tofile=f"b/{rel}", n=3))
        if text:
            chunks.append(text)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(chunks), encoding="utf-8")


def run_tool(tmp_path: Path, run: Path, *extra: str) -> subprocess.CompletedProcess:
    """Run the exporter as a release reader would: as a command, with an exit code."""
    out = tmp_path / "out"
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["TMPDIR"] = str(scratch)
    return subprocess.run([sys.executable, str(TOOL), str(run), "--out", str(out), *extra],
                          capture_output=True, text=True, cwd=str(ROOT), env=env)


def exported(out: Path) -> Path:
    """The one tree the export wrote, addressed the way the tool names it."""
    rows = sorted(path for path in out.iterdir() if path.is_dir())
    assert len(rows) == 1, rows
    return rows[0]


def files_under(root: Path) -> dict[str, bytes]:
    """Every file's bytes, keyed by relative path, so a comparison is per byte."""
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_state_directory_is_copied_byte_for_byte_and_named_in_the_manifest(tmp_path):
    """The per-round state directory is the first fallback and the strongest claim.

    It is the only store that is a *copy*: no replay, no reconstruction. The export
    must therefore be indistinguishable from it, and the manifest must say so.
    """
    stored = tree(tmp_path / "kept", "print('round 2 from the state directory')\n")
    run = make_run(tmp_path, {2: tree_hash(stored)})
    state = run / "state_after_round2"
    subprocess.run(["cp", "-a", str(stored) + "/.", str(state)], check=True)
    # The premise is that the two harder sources are genuinely absent, not merely unused
    # when this one wins: no snapshot file, no diffs/ directory.
    assert not (run / "snapshots").exists()
    assert not (run / "diffs").exists()

    done = run_tool(tmp_path, run)

    assert done.returncode == 0, done.stderr
    assert "source=state" in done.stdout
    assert "sources   r2=state" in done.stdout
    dest = exported(tmp_path / "out")
    assert dest.name.endswith("__r2")
    written = files_under(dest)
    assert written.pop("PROVENANCE.md")  # the manifest is the only file the export adds
    assert written == files_under(state)
    # The source claim lives in the manifest the tool already writes, not only in stdout:
    # an exported tree can be read months later by someone who never saw the console.
    assert "| source | `state` — copied from `state_after_round2/`" in \
        (dest / "PROVENANCE.md").read_text(encoding="utf-8")


def test_snapshot_is_checked_out_and_its_id_is_recorded(tmp_path):
    """A snapshot-only run exports, and the manifest names the artifact it was read from."""
    work = tree(tmp_path / "snapshotted", "print('round 2 from the snapshot')\n")
    sha = tree_hash(work)
    run = make_run(tmp_path, {2: sha})
    stored = write_snapshot(run, work, 2, sha)
    assert not (run / "state_after_round2").exists()
    assert not (run / "diffs").exists()

    done = run_tool(tmp_path, run)

    assert done.returncode == 0, done.stderr
    assert "sources   r2=snapshot" in done.stdout
    dest = exported(tmp_path / "out")
    assert files_under(dest)["agent.py"] == \
        (work / "agent.py").read_bytes()
    manifest = (dest / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "| source | `snapshot` — checked out from `snapshots/round-2.json`" in manifest
    # The snapshot's own id is quoted, so a reader can tell *which* artifact was read
    # instead of trusting the round number alone.
    stored_id = json.loads(stored.read_text(encoding="utf-8"))["id"]
    assert stored_id in manifest


def test_patch_replay_reconstructs_a_round_that_was_not_stored(tmp_path):
    """With only round 0 and the patches kept, the export is the replayed round-2 tree.

    This is the archive's common shape: `state_after_round0/` plus one patch per round.
    The export must be the tree the patches produce, and it must be labelled as a
    reconstruction rather than passed off as a copy.
    """
    round0 = tree(tmp_path / "r0", "print('round 0')\n")
    round1 = tree(tmp_path / "r1", "print('round 1')\n")
    round2 = tree(tmp_path / "r2", "print('round 2')\n")
    run = make_run(tmp_path, {0: tree_hash(round0), 2: tree_hash(round2)})
    subprocess.run(["cp", "-a", str(round0) + "/.", str(run / "state_after_round0")],
                   check=True)
    write_patch(round0, round1, run / "diffs" / "round-1.patch")
    write_patch(round1, round2, run / "diffs" / "round-2.patch")
    # Premise: round 2 itself is not stored, so the answer can only come from replay.
    assert not (run / "state_after_round2").exists()
    assert not (run / "snapshots").exists()

    done = run_tool(tmp_path, run)

    assert done.returncode == 0, done.stderr
    assert "sources   r2=replay" in done.stdout
    dest = exported(tmp_path / "out")
    written = files_under(dest)
    written.pop("PROVENANCE.md")
    assert written == files_under(round2)
    manifest = (dest / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "## This tree is a reconstruction" in manifest
    assert "diffs/round-1.patch" in manifest and "round-2.patch" in manifest


def test_the_live_workspace_wins_over_a_stale_state_directory(tmp_path):
    """When the workspace still exists it is the store, so it is asked first.

    The state directory here holds *different* content, which is what makes the
    precedence visible: if the tool fell back to it, the bytes would differ.
    """
    workspace = tree(tmp_path / "workspace", "print('from the live workspace')\n")
    for command in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.email=t@example.invalid",
                     "-c", "user.name=t", "commit", "-q", "-m", "round 2"]):
        subprocess.run(command, cwd=str(workspace), check=True, capture_output=True)
    sha = tree_hash(workspace)
    run = make_run(tmp_path, {2: sha}, workspace=str(workspace))
    stale = tree(run / "state_after_round2", "print('a stale copy')\n")
    assert (stale / "agent.py").read_text(encoding="utf-8") == "print('a stale copy')\n"

    done = run_tool(tmp_path, run)

    assert done.returncode == 0, done.stderr
    assert "sources   r2=workspace" in done.stdout
    dest = exported(tmp_path / "out")
    assert files_under(dest)["agent.py"] == \
        b"print('from the live workspace')\n"


def test_a_run_with_no_store_fails_by_name_and_not_with_a_traceback(tmp_path):
    """Nothing stored: the reason is the console's word, the exit is nonzero, no crash.

    The workspace is gone and there is no state directory, snapshot or patch, so there
    is no tree to export -- and the reader has to be told which three places were tried.
    """
    run = make_run(tmp_path, {})

    done = run_tool(tmp_path, run)

    assert done.returncode != 0
    assert "round-not-stored" in done.stdout
    # A named refusal, not an exception: a stack dump is not a reason a release reader
    # can act on, and the old tool exited with one when the workspace was missing.
    assert "Traceback" not in done.stderr
    detail = done.stdout
    assert "state_after_round2" in detail
    assert "snapshots/round-2.json" in detail
    assert "diffs/round-1.patch" in detail
    # A failed round leaves no half-written tree behind for someone to pick up later.
    assert not (tmp_path / "out").exists()


def test_a_state_directory_without_a_manifest_is_not_a_source(tmp_path):
    """The console requires `harness.json` inside the state directory; so does the tool."""
    run = make_run(tmp_path, {})
    (run / "state_after_round2").mkdir()
    (run / "state_after_round2" / "agent.py").write_text("print('not a harness')\n",
                                                        encoding="utf-8")

    done = run_tool(tmp_path, run)

    assert done.returncode != 0
    assert "round-not-stored" in done.stdout
    assert "`state_after_round2/` exists but has no harness.json" in done.stdout


def test_a_snapshot_that_fails_its_id_check_is_reported_not_exported(tmp_path):
    """A tampered snapshot is a finding. It must not become an exported harness."""
    work = tree(tmp_path / "snapshotted", "print('the real round 2')\n")
    sha = tree_hash(work)
    run = make_run(tmp_path, {2: sha})
    stored = write_snapshot(run, work, 2, sha)
    payload = json.loads(stored.read_text(encoding="utf-8"))
    payload["files"]["agent.py"]["content_b64"] = "cHJpbnQoJ3RhbXBlcmVkIikkKQo="
    stored.write_text(json.dumps(payload), encoding="utf-8")  # id no longer matches

    done = run_tool(tmp_path, run)

    assert done.returncode != 0
    assert "round-not-stored" in done.stdout
    assert "exists but cannot be checked out" in done.stdout
    assert "snapshot id does not match its content" in done.stdout
    assert not (tmp_path / "out").exists()


def test_all_labels_each_point_with_its_own_round(tmp_path):
    """`--all` keys the stores by the point's round, not by its position in the file.

    The curve has one more point than the run has steps (`curve[0]` is the H0 baseline),
    so an off-by-one here asks `state_after_round2/` for a point that is round 1 -- and
    the verification would reject it. The round number is the point's, so it is right.
    """
    round0 = tree(tmp_path / "r0", "print('round 0')\n")
    round1 = tree(tmp_path / "r1", "print('round 1')\n")
    run = make_run(tmp_path, {0: tree_hash(round0), 1: tree_hash(round1)}, points=2,
                   nominated=1)
    subprocess.run(["cp", "-a", str(round0) + "/.", str(run / "state_after_round0")],
                   check=True)
    subprocess.run(["cp", "-a", str(round1) + "/.", str(run / "state_after_round1")],
                   check=True)

    done = run_tool(tmp_path, run, "--all")

    assert done.returncode == 0, done.stderr
    assert "sources   r0=state r1=state" in done.stdout
    names = sorted(path.name for path in (tmp_path / "out").iterdir() if path.is_dir())
    assert [name.rsplit("__", 1)[-1] for name in names] == ["r0", "r1"]


def test_a_point_without_a_harness_sha_exports_but_is_marked_unverified(tmp_path):
    """The 2026-10 runs predate `harness_sha`; a missing identity is not a failed one.

    Refusing those would make a stored tree unreadable for a reason the run cannot help,
    so the tool exports the state directory and says in the manifest that no content
    address existed to check it against.
    """
    stored = tree(tmp_path / "kept", "print('a round from before harness_sha')\n")
    run = make_run(tmp_path, {}, points=1, nominated=None)
    point = json.loads((run / "curve.jsonl").read_text(encoding="utf-8"))
    point["identity"]["harness_sha"] = None
    (run / "curve.jsonl").write_text(json.dumps(point) + "\n", encoding="utf-8")
    subprocess.run(["cp", "-a", str(stored) + "/.", str(run / "state_after_round0")],
                   check=True)

    done = run_tool(tmp_path, run)

    assert done.returncode == 0, done.stderr
    assert "sources   r0=state" in done.stdout
    manifest = (exported(tmp_path / "out") / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "unverified: the curve point records no harness_sha" in manifest
