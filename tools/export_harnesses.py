#!/usr/bin/env python3
"""Export a run's harness checkpoints into `current_harness/`, so they are visible.

Why this exists: every checkpoint is already stored -- as a git commit in the
run's workspace, which is a *better* store than a directory of copies, because git
is content-addressable. Identical files are stored once regardless of how many
revisions contain them, any revision can be extracted directly without replaying
diffs, and one damaged checkpoint does not invalidate the ones after it.

What is missing is not storage but **sight**. `runs/` is gitignored, so nothing
outside a run can answer "which harnesses did this base evolve into?". This tool
answers that by exporting the finished artifacts under a stable, browsable path.

    python tools/export_harnesses.py runs/<run-id>              # the nominated one
    python tools/export_harnesses.py runs/<run-id> --all        # every checkpoint

A run's workspace is a temporary directory (`/tmp/hg-...`), so for a published run it
is usually gone -- and a store nobody can reach is not a store. The run directory
itself keeps other copies of the same trees, and this tool recovers from them in the
order the console already uses (`console/server.py:harness_candidate_from_round`):

    live workspace  ->  state_after_roundN/  ->  snapshots/round-N.json  ->  patch replay

Each exported tree says which one produced it (`source` in the output and in its
PROVENANCE.md), because "the tree I copied out" and "the tree I reconstructed from a
patch" are different claims. Every recovered tree is then hashed the way the run
committed it and compared with the `harness_sha` on the curve point: a store that holds
a different tree is reported, not exported. A round that no source can yield -- or that
yields a tree the run did not measure -- is reported by name (`round-not-stored`) and
makes the exit nonzero, instead of aborting the whole export.

What is written, per checkpoint: the harness files (full code, not a diff) plus a
PROVENANCE.md recording the run, the commit, the method, the model, the score and the
source. A directory of harnesses with no record of where they came from is a directory
of code nobody can use.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_HARNESS = ROOT / "base_harness" / "loop"

#: Loading `harnessgrad/snapshot.py` by path would otherwise leave a `__pycache__` in the
#: platform tree, which this tool only reads -- and a stray file there is not cosmetic:
#: `harness_sha` is a content address of a tree, and not moving it is part of the job.
sys.dont_write_bytecode = True

#: Where a round's tree came from. `state` / `snapshot` / `replay` are the console's own
#: words for its three recovery paths; `workspace` names the git store this tool had
#: first and the console never had (it reads candidates, not commits).
SOURCE_WORKSPACE = "workspace"
SOURCE_STATE = "state"
SOURCE_SNAPSHOT = "snapshot"
SOURCE_REPLAY = "replay"

#: Why a round produced no tree. The first three are the console's words, reused so that
#: a reader of the tool and a reader of the console see the same vocabulary.
FAIL_NOT_STORED = "round-not-stored"
FAIL_NO_ORIGIN = "no-origin"
FAIL_NO_MANIFEST = "no-manifest"
FAIL_PATCH = "patch-failed"

#: Not harness source: bytecode is a cache of a tree, `.git`/`.state` are the stores the
#: tree was copied out of. The same four patterns the console ignores, and the same ones
#: `harnessgrad/snapshot.py` leaves out of a snapshot.
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".state")


@dataclass(frozen=True)
class Recovered:
    """A round's tree, where it was read from, and what that claim means."""

    tree: Path
    source: str
    detail: str


@dataclass(frozen=True)
class Failed:
    """A round none of the recovery paths could yield, and the reason in a named word."""

    reason: str
    detail: str


def read_curve(run_dir: Path) -> list[dict]:
    path = run_dir / "curve.jsonl"
    if not path.exists():
        raise SystemExit(f"no curve.jsonl in {run_dir}")
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _reset(path: Path) -> Path:
    """An empty scratch directory: each recovery attempt starts from nothing."""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _copy_tree(source: Path, dest: Path) -> None:
    """Copy a store's tree byte-for-byte, skipping caches and the stores themselves."""
    shutil.copytree(source, dest, ignore=_IGNORE, dirs_exist_ok=True)


def _extract_git_tree(work: Path, sha: str, dest: Path) -> str | None:
    """Materialise the commit's tree into `dest`; a reason when it cannot, else None.

    `git archive` rather than `git show <sha>:<file>` per path: it keeps a file's bytes
    (the old `write_text` decoded every file and silently dropped the ones that failed)
    and its executable bit, and it fails once, in one place, when the commit is gone.
    """
    done = subprocess.run(["git", "-C", str(work), "archive", "--format=tar", sha],
                          capture_output=True)
    if done.returncode != 0:
        return (done.stderr or b"").decode("utf-8", "replace").strip() or "not a tree here"
    try:
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    # A symlink or a device is not harness source; `git ls-tree` has no
                    # portable content for it either.
                    continue
                target = dest / member.name
                if Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                    return f"unsafe path in the archived tree: {member.name}"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError, OSError) as exc:
        return str(exc)
    return None


def tree_hash(tree: Path) -> str | None:
    """The git tree hash of a directory -- the identity `harness_sha` records.

    `mode_a.py` commits a round with `git add -A` inside the workspace, so hashing a
    recovered tree the same way answers "is this the tree the run measured?" rather than
    "did some file appear?". Measured on the rsi10 archive: all 391 state directories
    whose curve point records a `harness_sha` hash to exactly that value, and so does a
    20-round replay, so a mismatch is a finding and not noise.
    """
    with tempfile.TemporaryDirectory(prefix="hg-tree-") as tmp:
        copy = Path(tmp) / "tree"
        shutil.copytree(tree, copy, ignore=_IGNORE)
        try:
            for command in (["git", "init", "-q"], ["git", "add", "-A"],
                            ["git", "write-tree"]):
                done = subprocess.run(command, cwd=str(copy), capture_output=True,
                                      text=True, errors="replace")
                if done.returncode != 0:
                    return None
        except OSError:
            return None
        return done.stdout.strip()


def _verify(tree: Path, sha: str | None) -> str | None:
    """Why `tree` is not the tree `sha` names, or None when it is (or there is no sha)."""
    if not sha:
        return None
    got = tree_hash(tree)
    if got is None:
        return "its git tree hash could not be computed"
    if got != sha:
        return f"hashes to tree `{got}`, not the recorded `{sha}`"
    return None


def _snapshot_api():
    """`harnessgrad/snapshot.py`, loaded by path so the tool needs no `PYTHONPATH`.

    The console loads it the same way and for the same reason (`_snapshot_api`): the
    tool runs as a script, so `import harnessgrad` is not on `sys.path` unless the
    caller sets it, and a release reader should not have to know that.
    """
    import importlib.util

    source = ROOT / "harnessgrad" / "snapshot.py"
    if not source.is_file():
        return None
    spec = importlib.util.spec_from_file_location("hg_export_snapshot", source)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError):
        return None
    return module


def _check_out_snapshot(snapshot_file: Path, target: Path) -> tuple[str | None, str | None]:
    """Materialise `snapshots/round-N.json`; return `(problem, snapshot_id)`.

    Three calls on purpose. `load` checks the schema, every path and the content id
    before anything is written; `check_out` materialises; `verify` re-reads the tree it
    just wrote and compares it with the stored entries. A snapshot whose id no longer
    matches its content must be reported, never exported as if it were the round.
    """
    api = _snapshot_api()
    if api is None:
        return "this checkout has no harnessgrad/snapshot.py", None
    try:
        stored = api.load(snapshot_file)
        api.check_out(snapshot_file, target)
        api.verify(snapshot_file, target)
    except (api.SnapshotError, OSError) as exc:
        return str(exc), None
    if not (target / "harness.json").is_file():
        return f"{snapshot_file.name} carries no harness.json", None
    return None, stored["id"]


def _spans(numbers: list[int]) -> str:
    """`[1,2,3,7]` as `r1–r3, r7`: the reader must be able to see which round is missing."""
    out, start = [], None
    for index, number in enumerate(numbers):
        if start is None:
            start = number
        if index + 1 == len(numbers) or numbers[index + 1] != number + 1:
            out.append(f"r{start}" if start == number else f"r{start}–r{number}")
            start = None
    return ", ".join(out)


def _stored_patches(run_dir: Path) -> list[int]:
    directory = run_dir / "diffs"
    if not directory.is_dir():
        return []
    rounds = []
    for patch in directory.glob("round-*.patch"):
        number = patch.stem[len("round-"):]
        if number.isdigit():
            rounds.append(int(number))
    return sorted(rounds)


def _failure_detail(run_dir: Path, round_no: int, tried: list[str]) -> str:
    """One sentence naming every place that was tried, and what it said.

    `round-not-stored` is the console's word for this refusal; the sentence under it is
    what keeps the word checkable -- which store is absent, which is present but holds a
    different tree, and which patch number a replay stopped at.
    """
    rounds = _stored_patches(run_dir)
    stored = (f"It stores {len(rounds)} patch(es): {_spans(rounds)}."
              if rounds else "It has no `diffs/` directory.")
    return (f"round {round_no} cannot be rebuilt as the tree it measured: "
            f"{'; '.join(tried)}. {stored}")


def recover_tree(run_dir: Path, round_no: int, sha: str | None, work: Path | None,
                 base_harness: Path, scratch: Path) -> Recovered | Failed:
    """The tree round `round_no` measured, from the first source that can yield it.

    The order is the console's -- state directory, snapshot, patch replay, with the live
    workspace in front because when it survives it *is* the store the round was committed
    to. What the tool adds is the final step of every attempt: the tree it just read is
    hashed the way `commit_state` hashes one and compared with the `harness_sha` on the
    curve point, so a store that holds a different tree is named rather than exported.
    """
    tried: list[str] = []

    def accept(tree: Path, source: str, detail: str) -> Recovered | None:
        """Accept a tree only when it is the one the curve point records."""
        problem = _verify(tree, sha)
        if problem is not None:
            tried.append(f"the tree from {source} {problem}")
            _reset(tree)
            return None
        if sha:
            detail += f", verified against the tree hash `{sha}` the curve records"
        else:
            detail += " (unverified: the curve point records no harness_sha)"
        return Recovered(tree, source, detail)

    state_dir = run_dir / f"state_after_round{round_no}"
    snapshot_file = run_dir / "snapshots" / f"round-{round_no}.json"

    # 1. The live workspace: the git store the checkpoints were committed to.
    if work is None:
        tried.append("the run's workspace is gone")
    elif not sha:
        tried.append("the curve point records no harness_sha to ask the workspace for")
    else:
        tree = _reset(scratch)
        problem = _extract_git_tree(work, sha, tree)
        if problem is not None or not (tree / "harness.json").is_file():
            tried.append(f"`{work}` does not hold tree `{sha}`"
                         + (f" ({problem})" if problem else ""))
        else:
            found = accept(tree, SOURCE_WORKSPACE,
                           f"git tree `{sha}` of the live workspace `{work}`")
            if found is not None:
                return found

    # 2. The per-round state directory, when the run kept one. The manifest has to be
    #    inside it: a directory without one is not a harness, and the console says so.
    if (state_dir / "harness.json").is_file():
        tree = _reset(scratch)
        _copy_tree(state_dir, tree)
        found = accept(tree, SOURCE_STATE, f"copied from `{state_dir.name}/`")
        if found is not None:
            return found
    elif state_dir.exists():
        tried.append(f"`{state_dir.name}/` exists but has no harness.json")
    else:
        tried.append(f"there is no `{state_dir.name}/`")

    # 3. The self-verifying single-file snapshot. A snapshot that fails its own id check
    #    is a finding, not a source: it is reported, and replay may still answer.
    snapshot_skip: str | None = None
    if snapshot_file.is_file():
        tree = _reset(scratch)
        snapshot_problem, snapshot_id = _check_out_snapshot(snapshot_file, tree)
        if snapshot_problem is not None:
            snapshot_skip = snapshot_problem
            tried.append(f"`snapshots/{snapshot_file.name}` exists but cannot be checked "
                         f"out ({snapshot_problem})")
        else:
            found = accept(tree, SOURCE_SNAPSHOT,
                           f"checked out from `snapshots/{snapshot_file.name}` and verified "
                           f"against its id `{snapshot_id}`")
            if found is not None:
                return found
            snapshot_skip = "the tree it holds is not the one the curve point records"
    else:
        tried.append(f"`snapshots/{snapshot_file.name}` does not exist")

    # 4. Replay the run's own patches onto the base harness. The origin is round 0 when
    #    the run kept it (same lineage), otherwise the base harness the run started from.
    _reset(scratch)
    origin = run_dir / "state_after_round0"
    if (origin / "harness.json").is_file():
        _copy_tree(origin, scratch)
        origin_label = f"`{origin.name}/`"
    elif (base_harness / "harness.json").is_file():
        _copy_tree(base_harness, scratch)
        try:
            origin_label = f"`{base_harness.relative_to(ROOT)}/`"
        except ValueError:
            origin_label = f"`{base_harness}`"
    else:
        tried.append("there is nothing to replay onto: no `state_after_round0/` and no "
                     f"base harness at `{base_harness}`")
        return Failed(FAIL_NO_ORIGIN, _failure_detail(run_dir, round_no, tried))

    applied: list[int] = []
    for number in range(1, round_no + 1):
        patch = run_dir / "diffs" / f"round-{number}.patch"
        if not patch.is_file():
            tried.append(f"replay needs `diffs/round-{number}.patch`, which does not exist")
            _reset(scratch)
            return Failed(FAIL_NOT_STORED, _failure_detail(run_dir, round_no, tried))
        with patch.open("rb") as handle:
            done = subprocess.run(["patch", "-p1", "--batch", "--forward", "--silent"],
                                  cwd=str(scratch), stdin=handle, capture_output=True)
        if done.returncode != 0:
            detail = (done.stdout + done.stderr).decode("utf-8", "replace")[-400:].strip()
            tried.append(f"`diffs/round-{number}.patch` did not apply ({detail})")
            _reset(scratch)
            return Failed(FAIL_PATCH, _failure_detail(run_dir, round_no, tried))
        applied.append(number)
    if not (scratch / "harness.json").is_file():
        tried.append(f"replaying onto {origin_label} produced no harness.json")
        _reset(scratch)
        return Failed(FAIL_NO_MANIFEST, _failure_detail(run_dir, round_no, tried))

    if applied:
        detail = (f"reconstructed by replaying `diffs/round-{applied[0]}.patch`"
                  + (f" through `round-{applied[-1]}.patch`" if len(applied) > 1 else "")
                  + f" onto {origin_label}; this is the patch replay, not the stored tree")
    else:
        detail = f"no patch to replay: this is {origin_label} itself"
    if snapshot_skip:
        detail += f" (`snapshots/round-{round_no}.json` was skipped: {snapshot_skip})"
    found = accept(scratch, SOURCE_REPLAY, detail)
    if found is not None:
        return found
    return Failed(FAIL_NOT_STORED, _failure_detail(run_dir, round_no, tried))


def provenance(point: dict, run_dir: Path, result: Recovered) -> str:
    i = point.get("identity") or {}
    state = "measured by the platform" if point.get("measured_by_platform") else \
            "NOT measured by the platform"
    sp = point.get("split") or {}
    side = point.get("side")
    kind = point.get("score_kind")
    score_side = ("training" if side == "train" else "eval" if side == "eval" else
                  "training" if kind == "training" else "eval" if kind == "exam" else "unknown side")
    train = point.get("train_score")
    # The train side is a diagnostic, not a result -- and it is written here
    # because an exported harness leaves this run directory. A checkpoint that
    # carries one score and no indication of which side produced it is exactly
    # the ambiguity the split exists to remove.
    train_row = (f"| train score | `{train:.3f}` — training-side diagnostic; "
                 "tasks are eligible for method feedback; "
                 "score does not establish held-out performance |" if isinstance(train, (int, float))
                 else "| train score | `(not recorded — no split, or not measured)` |")
    lines = [
        f"# {run_dir.name} — round {point.get('round')}",
        "",
        "Exported from a run's workspace. The harness files beside this file are the",
        "complete code of that checkpoint, taken from the commit named below.",
        "",
        "| | |",
        "| --- | --- |",
        f"| harness | `{i.get('harness_name')}` {i.get('harness_version')} |",
        f"| commit | `{i.get('harness_sha')}` |",
        # Which of the recovery sources produced these bytes. A copied tree and a tree
        # rebuilt from patches are different claims, so the claim is recorded, not implied.
        f"| source | `{result.source}` — {result.detail} |",
        f"| harness model | `{i.get('agent_model') or '(not recorded)'}` via `{i.get('agent_backend') or '?'}` |",
        f"| method model | `{i.get('method_model') or '(not declared)'}` |",
        f"| score ({score_side}) | `{point.get('score'):.3f}` — {state} |",
        train_row,
        f"| split | train `{len(sp.get('train') or [])}` / eval `{len(sp.get('eval') or [])}` |",
        f"| edit kind | `{point.get('edit_kind') or '(not declared)'}` |",
        f"| files touched | {point.get('editable_surface_touched') or '[]'} |",
        f"| trajectory shape | `{(json.loads((run_dir / 'run_meta.json').read_text()).get('trajectory_shape') if (run_dir / 'run_meta.json').exists() else 'sequence')}` |",
        "",
    ]
    if result.source == SOURCE_REPLAY:
        lines += [
            "## This tree is a reconstruction",
            "",
            "The round's own directory was not stored. These files were rebuilt by applying",
            "the run's recorded patches to the base harness named in the `source` row above.",
            "That is the same code when the patches applied cleanly, and it is not the bytes",
            "the run measured; a snapshot or a state directory from the same round would be",
            "the stronger artifact.",
            "",
        ]
    if score_side == "training":
        lines += [
            "This is a training-side diagnostic. Its tasks are eligible for method feedback.",
            "The score does not establish held-out performance.",
            "",
        ]
    mr = point.get("method_reported") or {}
    if mr:
        lines += ["## What the method said", "", "```json",
                  json.dumps(mr, indent=1)[:1200], "```", ""]
    if not point.get("measured_by_platform"):
        lines += [
            "## This score is not the platform's",
            "",
            "The number above was reported by the method. The platform could not run",
            "this state, so it did not produce a score of its own. Comparing it with a",
            "platform-measured number is not valid.",
            "",
        ]
    return "\n".join(lines)


def find_workspace(run_dir: Path, run_meta: dict) -> Path | None:
    """Where the run's git history actually is.

    Not `run_dir / "workspace"` any more: the working tree moved outside the
    platform tree once it became clear that a harness sitting two `..` from the
    driver could read it. The path is recorded in `run_meta.json`, and the
    fallback exists only for runs made before that was recorded.
    """
    recorded = run_meta.get("workspace")
    if recorded and Path(recorded).is_dir():
        return Path(recorded)
    legacy = run_dir / "workspace"
    return legacy if legacy.is_dir() else None


def base_harness_for(run_meta: dict) -> Path:
    """The harness the run starts from, as `run_meta.json` records it.

    The console's replay hard-codes `base_harness/loop`. Every one of the 781
    `run_meta.json` in the rsi10 archive names `loop`, so the two agree on that corpus;
    reading the recorded name keeps a run based on another harness from being replayed
    onto the wrong tree.
    """
    recorded = run_meta.get("harness")
    if isinstance(recorded, str) and recorded:
        candidate = ROOT / "base_harness" / Path(recorded).name
        if (candidate / "harness.json").is_file():
            return candidate
    return BASE_HARNESS


def round_numbers(curve: list[dict]) -> list[int]:
    """Each curve point's round number: its own `round`, else its index.

    This is the key the recovery paths are asked for -- `state_after_roundN/`,
    `snapshots/round-N.json`, `diffs/round-N.patch` -- so it must be the point's own
    round and not its position in the file. Verified on all 698 curves in the rsi10
    archive: `point["round"] == index` in every point. Reporting the index instead of
    the round is how an `--all` export used to label round 20 as `r21`.
    """
    numbers = []
    for index, point in enumerate(curve):
        number = point.get("round")
        if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
            numbers.append(number)
        else:
            numbers.append(index)
    return numbers


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export a run's harness checkpoints, recovering them from the run "
                    "directory when its workspace is gone.",
        epilog="Sources, tried in this order and named per round in the output and in each "
               "PROVENANCE.md: the live workspace, `state_after_roundN/`, "
               "`snapshots/round-N.json`, then `diffs/round-N.patch` replayed onto the base "
               "harness. A round none of them can yield is reported as `round-not-stored` "
               "and makes the exit nonzero.")
    ap.add_argument("run")
    ap.add_argument("--all", action="store_true",
                    help="export every checkpoint, not only the nominated one")
    ap.add_argument("--out", default=str(ROOT / "current_harness"))
    args = ap.parse_args()

    run_dir = Path(args.run)
    curve = read_curve(run_dir)
    if not curve:
        raise SystemExit(f"{run_dir} has no curve points")

    run_meta = {}
    rm = run_dir / "run_meta.json"
    if rm.exists():
        run_meta = json.loads(rm.read_text())
    nominated = run_meta.get("nominated")

    work = find_workspace(run_dir, run_meta)
    base_harness = base_harness_for(run_meta)
    numbers = round_numbers(curve)

    # Which point is "the" harness this run produced. The method nominates one;
    # without a nomination the last is taken, and PROVENANCE says which rule fired.
    if args.all:
        chosen = list(zip(numbers, curve))
        rule = "all checkpoints (--all)"
    elif isinstance(nominated, int) and not isinstance(nominated, bool) \
            and 1 <= nominated < len(curve):
        # `nominated` counts *steps*, 1-based, matching `driver.py`'s `[nominated]`
        # marker and INTERFACE.md's example. `curve[0]` is the H0 baseline, so step N
        # is `curve[N]` -- there is one more curve point than there are steps.
        #
        # The real defect here was elsewhere: `nominated` was read from the trajectory
        # and never written to `run_meta.json`, so this branch never ran and the last
        # checkpoint was always exported. Verified end to end after the fix.
        chosen = [(numbers[nominated], curve[nominated])]
        rule = "nominated by the method"
    elif isinstance(nominated, int) and not isinstance(nominated, bool) and nominated > 0:
        # 0 is "the method nominated none" (INTERFACE.md `nominated`), not an out-of-range
        # step; only a positive step that is too large is a fallback worth reporting.
        print(f"note: the method nominated step {nominated}, which is outside "
              f"1..{len(curve) - 1}; falling back to the last checkpoint", file=sys.stderr)
        chosen = [(numbers[-1], curve[-1])]
        rule = f"last checkpoint (nomination {nominated} out of range)"
    else:
        chosen = [(numbers[-1], curve[-1])]
        rule = "last checkpoint (the method nominated none)"

    base = ".".join(p for p in (
        (curve[0].get("identity") or {}).get("harness_name"),
        run_meta.get("method_entrypoint") and Path(
            run_meta["method_entrypoint"][-1]).parent.name,
    ) if p) or "harness"

    dest_root = Path(args.out)
    written: list[tuple[int, Path, Recovered]] = []
    failed: list[tuple[int, Failed]] = []
    scratch_root = Path(tempfile.mkdtemp(prefix="hg-export-"))
    try:
        for round_no, point in chosen:
            sha = (point.get("identity") or {}).get("harness_sha")
            result = recover_tree(run_dir, round_no, sha, work, base_harness,
                                  scratch_root / f"round-{round_no}")
            if isinstance(result, Failed):
                failed.append((round_no, result))
                print(f"failed    r{round_no} {result.reason}: {result.detail}")
                continue
            dest = dest_root / f"{base}__{run_dir.name}__r{round_no}"
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            _copy_tree(result.tree, dest)
            (dest / "PROVENANCE.md").write_text(provenance(point, run_dir, result))
            written.append((round_no, dest, result))
            try:
                shown = dest.relative_to(ROOT)
            except ValueError:
                shown = dest  # --out outside the platform is allowed
            print(f"exported  {shown}  source={result.source}")
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    print(f"rule      {rule}")
    print(f"workspace {work}" if work else
          "workspace (gone: recovering from the run directory)")
    for round_no, dest, result in written:
        print(f"  r{round_no:<3} {result.source:<9} {result.detail}")
    # The one line a release reader needs: per round, which source produced the tree.
    summary = [f"r{round_no}={result.source}" for round_no, _d, result in written]
    summary += [f"r{round_no}={result.reason}" for round_no, result in failed]
    print(f"sources   {' '.join(summary)}")
    if not written:
        print("nothing exported: no round's tree could be recovered", file=sys.stderr)
    if failed:
        print(f"{len(failed)} of {len(chosen)} round(s) could not be recovered", file=sys.stderr)
    return 0 if written and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
