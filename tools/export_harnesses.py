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

What is written, per checkpoint: the harness files (full code, not a diff) plus a
PROVENANCE.md recording the run, the commit, the method, the model and the score.
A directory of harnesses with no record of where they came from is a directory of
code nobody can use.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, errors="replace").stdout


def read_curve(run_dir: Path) -> list[dict]:
    path = run_dir / "curve.jsonl"
    if not path.exists():
        raise SystemExit(f"no curve.jsonl in {run_dir}")
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def provenance(point: dict, run_dir: Path) -> str:
    i = point.get("identity") or {}
    state = "measured by the platform" if point.get("measured_by_platform") else \
            "NOT measured by the platform"
    sp = point.get("split") or {}
    train = point.get("train_score")
    # The train side is a diagnostic, not a result -- and it is written here
    # because an exported harness leaves this run directory. A checkpoint that
    # carries one score and no indication of which side produced it is exactly
    # the ambiguity the split exists to remove.
    train_row = (f"| train score | `{train:.3f}` — diagnostics only; the method "
                 f"was shown these traces |" if isinstance(train, (int, float))
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
        f"| harness model | `{i.get('agent_model') or '(not recorded)'}` via `{i.get('agent_backend') or '?'}` |",
        f"| method model | `{i.get('method_model') or '(not declared)'}` |",
        f"| score (eval) | `{point.get('score'):.3f}` — {state} |",
        train_row,
        f"| split | train `{len(sp.get('train') or [])}` / eval `{len(sp.get('eval') or [])}` |",
        f"| edit kind | `{point.get('edit_kind') or '(not declared)'}` |",
        f"| files touched | {point.get('editable_surface_touched') or '[]'} |",
        f"| trajectory shape | `{(json.loads((run_dir / 'run_meta.json').read_text()).get('trajectory_shape') if (run_dir / 'run_meta.json').exists() else 'sequence')}` |",
        "",
    ]
    if not sp.get("train"):
        lines += [
            "This run's dataset declared no train/eval split, so the eval and train",
            "sides are the same tasks and the method was shown the traces of every task",
            "it was scored on. The score is still a measurement; it is not evidence",
            "about generalisation.",
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


def export_point(run_dir: Path, point: dict, dest_root: Path, name: str,
                 work: Path) -> Path | None:
    sha = (point.get("identity") or {}).get("harness_sha")
    if not sha or not work.exists():
        return None

    listing = git(work, "ls-tree", "-r", "--name-only", sha).strip()
    if not listing:
        return None

    dest = dest_root / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    for rel in listing.splitlines():
        blob = subprocess.run(["git", "-C", str(work), "show", f"{sha}:{rel}"],
                              capture_output=True, text=True, errors="replace")
        if blob.returncode != 0:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(blob.stdout)

    (dest / "PROVENANCE.md").write_text(provenance(point, run_dir))
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
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
    if work is None:
        raise SystemExit(
            f"cannot find the run's workspace: run_meta.json names "
            f"{run_meta.get('workspace')!r} and there is no legacy "
            f"{run_dir / 'workspace'}. The checkpoints are git commits inside "
            f"it, so without it there is nothing to export.")

    # Which point is "the" harness this run produced. The method nominates one;
    # without a nomination the last is taken, and PROVENANCE says which rule fired.
    if args.all:
        chosen = list(enumerate(curve, start=1))
        rule = "all checkpoints (--all)"
    elif isinstance(nominated, int) and 1 <= nominated < len(curve):
        # `nominated` counts *steps*, 1-based, matching `driver.py`'s `[nominated]`
        # marker and INTERFACE.md's example. `curve[0]` is the H0 baseline, so step N
        # is `curve[N]` -- there is one more curve point than there are steps.
        #
        # The real defect here was elsewhere: `nominated` was read from the trajectory
        # and never written to `run_meta.json`, so this branch never ran and the last
        # checkpoint was always exported. Verified end to end after the fix.
        chosen = [(nominated, curve[nominated])]
        rule = "nominated by the method"
    elif isinstance(nominated, int):
        print(f"note: the method nominated step {nominated}, which is outside "
              f"1..{len(curve)}; falling back to the last checkpoint", file=sys.stderr)
        chosen = [(len(curve), curve[-1])]
        rule = f"last checkpoint (nomination {nominated} out of range)"
    else:
        chosen = [(len(curve), curve[-1])]
        rule = "last checkpoint (the method nominated none)"

    base = ".".join(p for p in (
        (curve[0].get("identity") or {}).get("harness_name"),
        run_meta.get("method_entrypoint") and Path(
            run_meta["method_entrypoint"][-1]).parent.name,
    ) if p) or "harness"
    stamp = run_dir.name.split("-")[-1] if "-" in run_dir.name else run_dir.name

    dest_root = Path(args.out)
    written = []
    for round_no, point in chosen:
        name = f"{base}__{run_dir.name}__r{round_no}"
        path = export_point(run_dir, point, dest_root, name, work)
        if path:
            written.append(path)

    print(f"rule      {rule}")
    print(f"workspace {work}")
    for p in written:
        try:
            shown = p.relative_to(ROOT)
        except ValueError:
            shown = p  # --out outside the platform is allowed
        print(f"exported  {shown}")
    if not written:
        print("nothing exported: the run's workspace or commits are missing",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
