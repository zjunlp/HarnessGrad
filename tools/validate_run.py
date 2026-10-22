#!/usr/bin/env python3
"""Say whether a run's numbers are usable, without executing anything.

    python3 tools/validate_run.py runs/ds-dgm          # one run
    python3 tools/validate_run.py --all                 # every run under runs/
    python3 tools/validate_run.py --all --certified     # only the usable ones

Exit code is 0 when every named run is **valid** (readable: a schema-conformant curve, a
run record, every point measured by the platform) and 1 otherwise. `--certified` narrows
the requirement to the stronger property, in which the record also names the platform that
produced it -- see `harnessgrad/store/schema.py` for why those are two levels.

Why this exists
---------------
Measured 2026-10-05: 82 run directories, **none** carrying the content hash of the code
that decided what its numbers meant, and no program that could answer "is this one
usable?". Answering it meant reading the run by hand. Two runs from that day were not
measurements at all -- one invalidated when a platform file was edited while the driver
ran, one whose "gain" came from a candidate that provably could not change behaviour --
and on disk they looked exactly like the valid ones.

`validate` is the command every evaluation harness of this shape ends up with
(`lm-evaluation-harness` has `lm_eval validate`), for the same reason: a definition you
cannot check without running it is a definition nobody checks.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.observe.console import CONSOLE as console                          # noqa: E402
from harnessgrad.store import certify                                # noqa: E402


def _find_runs(runs_root: Path) -> list[Path]:
    """Every directory that looks like a run, newest first."""
    if not runs_root.is_dir():
        return []
    found = [p for p in runs_root.iterdir()
             if p.is_dir() and ((p / "curve.jsonl").is_file() or (p / "run_meta.json").is_file())]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _print_one(result: dict, *, verbose: bool) -> None:
    mark = "certified" if result["certified"] else ("valid" if result["valid"] else "INVALID")
    score = result["final_score"]
    score_s = "n/a" if score is None else f"{float(score):.3f}"
    print(f"  {mark:>9s}  {result['run_id']:<28s} rounds={len(result['rounds'])} "
          f"points={result['points']} last={score_s}")
    if result["tampered"]:
        for line in result["tampered"][:3]:
            print(f"             tampered: {line}")
    for problem in result["problems"][:8 if verbose else 3]:
        print(f"             problem: {problem}")
    if verbose:
        # Notes are why a point is *old*, not why it is wrong: a validator that failed
        # every run recorded before today is a validator nobody would use.
        for line in result["notes"][:12]:
            print(f"             note: {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="*", help="run directories")
    ap.add_argument("--all", action="store_true",
                    help="every run under --runs-root (default: runs/)")
    ap.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    ap.add_argument("--certified", action="store_true",
                    help="require the stronger property: the record names its platform")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="show every problem and every note")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    targets = [Path(p) for p in args.runs]
    if args.all:
        targets += _find_runs(args.runs_root)
    if not targets:
        print("nothing to validate: pass a run directory or --all", file=sys.stderr)
        return 2

    results = [certify(p) for p in targets]
    if args.json:
        print(json.dumps(results, indent=1))
    else:
        console.note(f"validating {len(results)} run director(ies)")
        for result in results:
            _print_one(result, verbose=args.verbose)
        ok = sum(1 for r in results if (r["certified"] if args.certified else r["valid"]))
        print(f"\n  {ok} of {len(results)} "
              f"{'certified' if args.certified else 'valid'}")
        if args.all and not args.certified:
            # The number that matters most and is easiest to misread: a valid old run is
            # not a run whose numbers can be compared with a new one's.
            n_cert = sum(1 for r in results if r["certified"])
            print(f"  {n_cert} of {len(results)} certified -- the rest predate the "
                  f"platform content hash, so their numbers cannot be compared with a "
                  f"run made by different code")

    want = "certified" if args.certified else "valid"
    return 0 if all(r[want] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
