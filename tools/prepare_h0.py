#!/usr/bin/env python3
"""Measure the shared H0 for a dataset once, and hand the run id to every method that follows.

**Why this is a tool and not a convention.** `driver.py --rounds N` measures round 0 as the
baseline harness's score inside every run, so a batch that just starts N runs gets N H0s. Those
are not interchangeable: the cross-method comparison is a *paired* one, and "method A beat method
B" computed against two different baselines is a statement about two different questions. The
platform has `--h0-from RUN` to share one point (and its `round0_traces`) across runs, but nothing
required anyone to use it -- measured 2026-10-19: three AppWorld study runs were launched without
it and each re-measured H0, which the operator caught. This tool is the missing step 0.

Usage:

    python3 tools/prepare_h0.py --dataset appworld --side train \
        --study-ids ids.txt --jobs 32            # measure it
    python3 tools/prepare_h0.py --dataset appworld --side train \
        --study-ids ids.txt --dry-run            # print the command, run nothing

Output: the run id, the score, the task count, and the exact `--h0-from <id>` to pass to every
study run. It refuses (rather than measuring again) when a valid H0 for the same configuration is
already on disk: two measurements of one state are two numbers, and picking either is a choice no
record can defend.

The identity of "the same configuration" is (dataset, side, study-id set, agent model, endpoint):
those are the things that make two baselines comparable. A different model or a different study
set is a *different* H0, and the tool measures one instead of reusing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_RUNS = pathlib.Path("/tmp/hg-h0-runs")


def study_ids_from(path: pathlib.Path | None) -> list[str]:
    if path is None:
        return []
    if not path.is_file():
        # Refused loudly: a typo'd path must not quietly become "no study set", because then the
        # baseline would be measured on the dataset's default side and reused for a different one.
        raise SystemExit(f"no such study-ids file: {path}")
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        for token in line.replace("\t", ",").split(","):
            token = token.strip()
            if token and not token.startswith("#"):
                ids.append(token)
    return ids


def configuration(args) -> dict:
    """What makes two H0 measurements the same measurement."""
    ids = study_ids_from(args.study_ids)
    return {
        "dataset": args.dataset,
        "side": args.side,
        "n_study_ids": len(ids),
        # The *file contents*, not the path: two files with the same ids are the same study set.
        "study_ids_sha": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest() if ids else None,
        "agent_model": args.model,
        "endpoint": args.endpoint,
        "trials": args.trials,
    }


def points(path: pathlib.Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def find_existing(config: dict, runs_root: pathlib.Path) -> tuple[pathlib.Path, dict] | None:
    """A valid H0 already recorded for this configuration, or None."""
    for run in sorted(runs_root.glob("h0-" + config["dataset"] + "-*")):
        h0 = run / "h0.json"
        meta = run / "run_meta.json"
        if not (h0.is_file() and meta.is_file()):
            continue
        recorded = json.loads(meta.read_text(encoding="utf-8")).get("h0_configuration")
        if recorded != config:
            continue
        base = (json.loads(h0.read_text(encoding="utf-8")).get("base") or {})
        per_task = base.get("per_task") or {}
        invalid = base.get("invalid") or {}
        if not per_task or invalid:
            continue
        return run, {"score": base.get("score"), "n_tasks": len(per_task)}
    return None


def verify(run: pathlib.Path, args, expected_ids: int) -> dict:
    """Refuse to call something an H0 unless it is a complete, valid measurement."""
    h0 = run / "h0.json"
    if not h0.is_file():
        raise SystemExit(f"{run.name}: no h0.json; the run did not finish its baseline measurement")
    payload = json.loads(h0.read_text(encoding="utf-8"))
    base = payload.get("base") or {}
    per_task = base.get("per_task") or {}
    invalid = base.get("invalid") or {}
    problems = []
    if not per_task:
        problems.append("no per-task verdicts")
    if expected_ids and len(per_task) != expected_ids:
        problems.append(f"{len(per_task)} tasks measured, the study set has {expected_ids}")
    if invalid:
        problems.append(f"{len(invalid)} task(s) invalid -- an H0 with holes is not a baseline")
    if base.get("harness_failed"):
        problems.append(f"{base.get('harness_failed')} harness failure(s)")
    if not payload.get("h0_sha"):
        problems.append("no h0_sha recorded")
    if problems:
        raise SystemExit(f"{run.name}: refusing to publish this as H0: " + "; ".join(problems))
    return {"score": base.get("score"), "n_tasks": len(per_task),
            "h0_sha": payload.get("h0_sha")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--side", default="train")
    ap.add_argument("--study-ids", type=pathlib.Path, default=None,
                    help="file of task ids that defines the study set (optional)")
    ap.add_argument("--jobs", type=int, default=32)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--sampling", default="all")
    ap.add_argument("--model", default=None, help="agent model (default: the platform's)")
    ap.add_argument("--endpoint", default=None, help="agent endpoint (default: the platform's)")
    ap.add_argument("--runs-root", type=pathlib.Path, default=DEFAULT_RUNS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    config = configuration(args)
    args.runs_root.mkdir(parents=True, exist_ok=True)
    existing = find_existing(config, args.runs_root)
    if existing is not None:
        run, summary = existing
        print(f"reusing {run.name}: score {summary['score']:.4f} on {summary['n_tasks']} tasks "
              f"(same dataset, study set, model and endpoint)")
        print(f"\n  --h0-from {run.name}\n")
        return 0

    stamp = __import__("time").strftime("%Y%m%dT%H%M%SZ", __import__("time").gmtime())
    run_id = f"h0-{args.dataset}-{stamp}"
    command = [sys.executable, str(ROOT / "driver.py"),
               "--harness", "loop", "--dataset", args.dataset, "--split", "declared",
               "--side", args.side, "--sampling", args.sampling,
               "--trials", str(args.trials), "--jobs", str(args.jobs),
               "--rounds", "0", "--improver", "none",
               "--run-id", run_id, "--runs-root", str(args.runs_root),
               "--work-root", f"/tmp/hg-h0-work/{run_id}"]
    env = dict(**__import__("os").environ)
    if args.study_ids:
        env["HG_APPWORLD_STUDY_IDS"] = str(args.study_ids)
        env["HG_ALFWORLD_STUDY_IDS"] = str(args.study_ids)
    if args.endpoint:
        env["HG_AGENT_BASE_URL"] = args.endpoint
    print(" ".join(command))
    if args.dry_run:
        return 0
    log = args.runs_root / f"{run_id}.log"
    with log.open("w", encoding="utf-8") as handle:
        code = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT,
                              env=env, cwd=str(ROOT)).returncode
    run = args.runs_root / run_id
    if code != 0:
        raise SystemExit(f"the H0 run failed (rc={code}); see {log}")
    summary = verify(run, args, config["n_study_ids"])
    # Stamp what the baseline *is* on the run's own meta, so the next invocation can recognise it
    # without re-deriving the configuration from paths.
    meta_path = run / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    meta["h0_configuration"] = config
    meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nH0 measured: {run_id}")
    print(f"  score {summary['score']:.4f} on {summary['n_tasks']} tasks, h0_sha {summary['h0_sha'][:12]}")
    print(f"\n  --h0-from {run_id}\n")
    print("pass that to every study run for this dataset; without it each run measures its own H0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
