#!/usr/bin/env python3
"""Report AppWorld TGC/SGC from a selected manifest and saved round verdicts.

Usage: python tools/report_appworld.py --manifest selected.json \
    --verdicts runs/RUN/verdicts/round-0 --trials 1 --out fresh-report.json

The manifest is a JSON list of {task_id, source_split} rows, exported by the
trusted adapter's reporting_manifest(selected_ids). This command imports no
dataset adapter and never executes tasks, evaluates gold or contacts a model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.policy.appworld_reporting import build_report, make_manifest, receipts_from_verdicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path,
                        help="Selected public task_id/source_split JSON manifest.")
    parser.add_argument("--verdicts", required=True, type=Path,
                        help="Saved verdicts/round-N directory; progress scores and events lack TGC evidence.")
    parser.add_argument("--trials", type=int, default=1,
                        help="Planned trial count (default: 1); compacted histories cannot establish SGC.")
    parser.add_argument("--out", required=True, type=Path,
                        help="Fresh report JSON path; existing files are never overwritten.")
    args = parser.parse_args()
    if not args.verdicts.is_dir():
        raise ValueError(f"verdict directory does not exist: {args.verdicts}")
    manifest = make_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    verdicts = []
    # Read only selected receipt paths; no directory scan or mixed-pool import.
    for row in manifest:
        path = args.verdicts / (row["task_id"] + ".json")
        if path.is_file():
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry.get("task_id") != row["task_id"]:
                raise ValueError(f"receipt task ID mismatch: {path}")
            verdicts.append(entry)
    report = build_report(manifest, receipts_from_verdicts(verdicts, trials=args.trials), trials=args.trials)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
