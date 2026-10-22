#!/usr/bin/env python3
"""Replay an AppWorld API log and run the official task evaluator."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import tempfile
import sys


def unmeasured(message: str, evidence_file: str, metadata: dict) -> "NoReturn":
    print(message, file=sys.stderr)
    print(message)
    target = Path(evidence_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({**metadata, "measured": False, "error": message}), encoding="utf-8")
    raise SystemExit(3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--source-split", choices=("train", "dev", "test_normal", "test_challenge"))
    parser.add_argument("--log", required=True)
    parser.add_argument("--reward-file", default="verifier/reward.txt")
    parser.add_argument("--evidence-file", default="verifier/evidence.json")
    args = parser.parse_args()
    metadata = {"task_id": args.task_id, "source_split": args.source_split,
                "metric": "appworld.task_goal_completion", "metric_name": "TGC"}
    log = Path(args.log)
    if not log.is_file():
        unmeasured(f"no API-call log: {log}", args.evidence_file, metadata)
    try:
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        unmeasured(f"unreadable API-call log: {exc}", args.evidence_file, metadata)
    try:
        if Path("/appworld/data").is_dir():
            runtime_root = Path(tempfile.mkdtemp(prefix="appworld-verify-"))
            (runtime_root / "data").symlink_to("/appworld/data")
            (runtime_root / "experiments").mkdir()
            os.environ["APPWORLD_ROOT"] = str(runtime_root)
        from appworld import AppWorld
        # An actor execute may issue many APIs. This verifier executes one trusted
        # API record at a time; do not truncate replay at the actor's action cap.
        with AppWorld(task_id=args.task_id, experiment_name="verify", raise_on_failure=False,
                      ground_truth_mode="minimal", max_interactions=max(1, len(calls))) as world:
            for call in calls:
                url = str(call["url"]).split("?", 1)[0]
                method = str(call.get("method", "get")).lower()
                if method not in {"get", "post", "put", "patch", "delete"}:
                    unmeasured(f"unknown API method: {method}", args.evidence_file, metadata)
                world.execute(
                    f"requester.{method}({url!r}, data={call.get('data') or {}!r}, "
                    "client=requester.client)"
                )
            tracker = world.evaluate()
    except SystemExit:
        raise
    except Exception as exc:  # an environment/import/data failure is not a task failure
        unmeasured(f"environment failure: {type(exc).__name__}: {exc}", args.evidence_file, metadata)
    evidence = {
        **metadata,
        "measured": True,
        "success": bool(tracker.success),
        "num_tests": tracker.num_tests,
        "passes": tracker.passes,
        "failures": tracker.failures,
    }
    reward = Path(args.reward_file)
    reward.parent.mkdir(parents=True, exist_ok=True)
    reward.write_text("1.0\n" if tracker.success else "0.0\n", encoding="utf-8")
    report = Path(args.evidence_file)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(evidence, default=str), encoding="utf-8")
    print("1.0" if tracker.success else "0.0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
