#!/usr/bin/env python3
"""Run the official verifier on the private, platform-sealed episode."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source import METRIC, REVISION, bootstrap, verified_case


def with_answer_artifact(config, payload, location):
    """Accept plain actor text only after the trusted relay exposes the final turn."""
    turns = len(config["task"]["question"])
    if (payload["final_answer"] is not None or payload["released_turns"] != turns
            or len(payload["turn_answers"]) != turns - 1):
        return payload
    try:
        fd = os.open(location, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return payload
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            return payload
        limit = 8 * 1024 * 1024
        data = stream.read(limit + 1)
        if len(data) > limit:
            return payload
    return {**payload, "final_answer": data.decode("utf-8", errors="replace"), "complete": True}


def grade(config, payload):
    from ptc_bench.eval_checker.verifiers import verify

    task = config["task"]
    try:
        result = verify(task_id=task["id"], final_answer=payload["final_answer"],
                        final_state=payload["final_state"], initial_config=task["initial_config"],
                        ground_truth=config["ground_truth"], turn_answers=payload["turn_answers"])
    except Exception as exc:
        # The official evaluate_traces.py records verifier errors on valid traces as
        # task failures. Bundle and sealed-record admission remain outside this arm.
        return {"passed": False, "partial_score": 0.0, "verifier_error": type(exc).__name__}
    return {"passed": result.passed, "partial_score": result.score}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--case-sha256", required=True)
    args = parser.parse_args()
    directory = Path("verifier")
    directory.mkdir(exist_ok=True)
    reward = directory / "reward.txt"
    reward.unlink(missing_ok=True)
    evidence = {"task_id": args.task_id, "metric": METRIC, "revision": REVISION, "measured": False}
    rc = 3
    try:
        config = verified_case("ptc_private_case.json", args.case_sha256)
        sealed = json.loads(Path("ptc_trusted/episode.json").read_bytes())
        payload = sealed["payload"]
        if (sealed["schema"] != "harnessgrad.interaction-record.v1"
                or sealed["binding"]["task_id"] != args.task_id
                or config["task_id"] != args.task_id or payload["task_id"] != args.task_id
                or payload["case_sha256"] != args.case_sha256):
            raise ValueError("PTC-Bench trusted record identity mismatch")
        bootstrap("ptc_bundle")
        submitted = with_answer_artifact(config, payload, Path("answer.txt"))
        result = grade(config, submitted)
        evidence.update(measured=True, official=result, api_calls=payload["api_calls"],
                        api_errors=payload["api_errors"], user_turns_complete=submitted["complete"],
                        final_answer_channel=("artifact" if submitted is not payload else
                                              "relay" if payload["final_answer"] is not None else None))
        reward.write_text(f"{int(result['passed'])}\n")
        rc = 0
    except Exception as exc:
        evidence["error"] = type(exc).__name__
        print(f"PTC-Bench verification failed: {type(exc).__name__}", file=sys.stderr)
    (directory / "evidence.json").write_text(json.dumps(evidence))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
