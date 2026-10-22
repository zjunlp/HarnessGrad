#!/usr/bin/env python3
"""Official KUMO consistency score from the final answer and pinned private case."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scoring import METRIC, PROTOCOL, environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--case-sha256", required=True)
    args = parser.parse_args()
    reward = Path("verifier/reward.txt")
    evidence_file = Path("verifier/evidence.json")
    reward.parent.mkdir(parents=True, exist_ok=True)
    reward.unlink(missing_ok=True)
    evidence = {"task_id": args.task_id, "metric": METRIC, "protocol": PROTOCOL, "measured": False}
    code = 3
    try:
        case_path = Path("kumo_verify/private.json")
        raw = case_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        case = json.loads(raw)
        if (digest != args.case_sha256 or case["task_id"] != args.task_id
                or case["schema"] != "kumo.case.v1"):
            raise ValueError("case identity mismatch")
        answer_path = Path("answer.txt")
        answer = answer_path.read_text().strip() if answer_path.exists() else ""
        correct = bool(environment(case).evaluate_answer(answer))
        evidence.update(measured=True, reward=float(correct),
                        source_revision=case["source_revision"],
                        scorer_input={"task_id": args.task_id, "path": str(case_path),
                                      "sha256": digest, "expected_sha256": args.case_sha256,
                                      "verified": True})
        reward.write_text(f"{int(correct)}\n")
        code = 0
    except Exception as error:
        evidence["error"] = f"{type(error).__name__}: {error}"
    evidence_file.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
