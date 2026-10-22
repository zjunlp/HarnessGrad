#!/usr/bin/env python3
"""Run every `tests/quality/test_*.py` file in its own pytest process, and prove the coverage.

**Why this exists.** A chunked sweep - one `pytest file1 file2 ... file12` invocation per batch - is not
a coverage measurement. A pytest **collection error aborts the whole batch**, so every file after the bad
one is silently never run, while the batch's summary line still reads like a clean run. Measured
2026-10-15: a 12-file-per-batch sweep of this suite reported nothing for seven files that were red in
`main`, one of them a production defect (`data/datasets/jericho/control.py` calling the platform verifier
without the selected task id). The per-file pass that replaced it found all seven.

So this tool runs one file per process, gives each its own timeout, and then **compares the set of files
it actually ran against the set that exists on disk**. A sweep that skipped a file fails here rather than
looking green, which is the only property that makes the rest of the report meaningful.

**Not a substitute for judgement.** The run takes many minutes (some files drive real sandboxes and
docker), so it is a session-level instrument, not a pre-commit hook. Files that are known-red for a
recorded reason still show up as red; the point of the tool is that the *set* is complete and the reason
is never "nobody ran it".

Usage:
    python3 tools/quality_sweep.py                     # sequential, 300 s per file
    python3 tools/quality_sweep.py --timeout 600 --json /tmp/sweep.json
    python3 tools/quality_sweep.py --only test_verifier   # run a subset, coverage check still enforced

Exit status: 0 when every discovered file ran and passed; 1 when any file failed, timed out, or did not
run at all.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "quality"


def discover() -> list[Path]:
    """Every quality test file, in a stable order."""
    return sorted(SUITE.glob("test_*.py"))


def summary_line(output: str) -> str:
    """pytest's last non-empty line: the counts, or the collection-error banner."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"


def failure_lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines()
            if line.startswith(("FAILED ", "ERROR "))][:20]


def run_one(path: Path, timeout_s: int, tmp_root: Path) -> dict:
    """Run one file. **Never raises**: a file that could not even be launched is a red result.

    That matters more than it looks. If this dropped an entry on an unexpected error, the loop below
    would still finish and the coverage comparison would be comparing a list against itself - a guard
    that cannot fail. Everything that can go wrong becomes a recorded result instead.
    """
    basetemp = tmp_root / path.stem
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            str(path.relative_to(ROOT)), f"--basetemp={basetemp}"]
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                              errors="replace", timeout=timeout_s)
        output, returncode = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or ""
        output = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        returncode, timed_out = 124, True
    except Exception as exc:  # noqa: BLE001 - a launch failure is a result, not a crash
        output, returncode = f"{type(exc).__name__}: {exc}", 1
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)
    return {"file": str(path.relative_to(ROOT)), "returncode": returncode,
            "timed_out": timed_out, "seconds": round(time.time() - started, 1),
            "summary": summary_line(output), "failures": failure_lines(output)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeout", type=int, default=300, help="seconds allowed per file (default 300)")
    ap.add_argument("--only", default=None, help="only files whose name contains this substring")
    ap.add_argument("--json", default=None, help="also write the full report here")
    args = ap.parse_args(argv)

    discovered = discover()
    if not discovered:
        print(f"no test files found under {SUITE}", file=sys.stderr)
        return 1
    selected = [p for p in discovered if args.only in p.name] if args.only else discovered
    if not selected:
        print(f"--only {args.only!r} matched none of the {len(discovered)} files", file=sys.stderr)
        return 1
    if len(selected) != len(discovered):
        # A subset run is legitimate, but it is not a coverage measurement and must never read as one.
        print(f"NOTE: --only selected {len(selected)} of {len(discovered)} files; this run is NOT "
              f"evidence that the suite is green.", file=sys.stderr)

    tmp_root = Path(tempfile.mkdtemp(prefix="hg-quality-sweep-"))
    results: list[dict] = []
    interrupted = False
    try:
        for index, path in enumerate(selected, 1):
            result = run_one(path, args.timeout, tmp_root)
            results.append(result)
            flag = "ok " if result["returncode"] == 0 else "RED"
            print(f"[{index:3d}/{len(selected)}] {flag} {result['file']} "
                  f"({result['seconds']:6.1f}s) {result['summary']}", flush=True)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    covered = {r["file"] for r in results}
    expected = {str(p.relative_to(ROOT)) for p in selected}
    missing = sorted(expected - covered)
    red = [r for r in results if r["returncode"] != 0]

    print()
    print(f"ran {len(covered)} of {len(expected)} selected files "
          f"({len(discovered)} exist); {len(red)} red")
    if interrupted:
        print(f"INTERRUPTED after {len(covered)} of {len(expected)} files - this is not a sweep result")
    if missing:
        # `run_one` records every outcome, including a launch failure, so this can only trip if that
        # contract changes. It is an assertion about the loop, not speculative error handling.
        print("COVERAGE INCOMPLETE - these files were selected but produced no result:")
        for name in missing:
            print(f"  {name}")
    if red:
        print("red files:")
        for result in red:
            print(f"  {result['file']}: {result['summary']}")
            for line in result["failures"]:
                print(f"      {line}")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"discovered": len(discovered), "selected": len(expected), "ran": len(covered),
             "interrupted": interrupted, "missing": missing, "results": results}, indent=1) + "\n",
            encoding="utf-8")
        print(f"report: {args.json}")

    return 0 if not missing and not red and not interrupted else 1


if __name__ == "__main__":
    raise SystemExit(main())
