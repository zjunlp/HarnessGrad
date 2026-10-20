#!/usr/bin/env python3
"""Score immutable relay submissions using selected verifier-only gold."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.protocol import METRIC, CODE_REVISION, REVISION
from lib.scoring import score_answer


def verify(gold, record, *, task_id, input_sha256, relay_sha256):
    if (gold.get("schema") != "personamem-v2-gold-v1" or gold.get("task_id") != task_id
            or gold.get("metric") != METRIC or gold.get("input_sha256") != input_sha256):
        raise ValueError("gold identity mismatch")
    binding = record.get("binding", {})
    payload = record.get("payload", {})
    if (record.get("schema") != "harnessgrad.interaction-record.v1"
            or binding.get("task_id") != task_id or binding.get("input_sha256") != relay_sha256
            or not binding.get("run_id") or not binding.get("trial_id")
            or payload.get("schema") != "personamem-v2-record-v1"
            or payload.get("task_id") != task_id or payload.get("input_sha256") != input_sha256):
        raise ValueError("trusted record identity mismatch")
    qids = [q["id"] for q in gold["questions"]]
    answers = payload.get("answers")
    if (len(set(qids)) != len(qids) or payload.get("question_ids") != qids
            or payload.get("chunk_count") != gold["chunk_count"]
            or type(payload.get("acked_chunks")) is not int
            or not 0 <= payload["acked_chunks"] <= gold["chunk_count"]
            or not isinstance(answers, list) or len(answers) > len(qids)
            or [a.get("question_id") for a in answers] != qids[:len(answers)]
            or any(set(a) != {"question_id", "answer"} or not isinstance(a["answer"], str) for a in answers)
            or (answers and payload["acked_chunks"] != gold["chunk_count"])):
        raise ValueError("record progress or answer sequence mismatch")
    complete = payload["acked_chunks"] == gold["chunk_count"] and len(answers) == len(qids)
    if type(payload.get("complete")) is not bool or payload["complete"] != complete:
        raise ValueError("record completeness mismatch")
    result = {"schema": "personamem-v2-verdict-v1", "task_id": task_id, "metric": METRIC,
              "split": gold["split"], "persona_id": gold["persona_id"], "binding": binding,
              "measured": complete, "expected_questions": len(qids), "answered_questions": len(answers),
              "artifacts": {"dataset_revision": REVISION, "code_revision": CODE_REVISION,
                            "source_manifest_sha256": gold["source_manifest_sha256"],
                            "input_sha256": input_sha256, "history_sha256": gold["history_sha256"]}}
    if gold.get("source_errors"):
        result.update(measured=False, reason="source_data_error", source_errors=gold["source_errors"])
        return result
    if not complete:
        result["reason"] = "incomplete_episode"
        return result
    scores = [{"question_id": q["id"], "score": score_answer(a["answer"], q["correct_letter"]),
               "pref_type": q["pref_type"], "updated": q["updated"],
               "sensitive_info": q["sensitive_info"]}
              for q, a in zip(gold["questions"], answers)]
    result.update(score=sum(q["score"] for q in scores) / len(scores), questions=scores)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gold", "gold-sha256", "task-id", "input-sha256", "relay-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--record", default="trusted_episode/episode.json")
    parser.add_argument("--reward-file", default="verifier/reward.txt")
    parser.add_argument("--evidence-file", default="verifier/evidence.json")
    args = parser.parse_args()
    reward, output = Path(args.reward_file), Path(args.evidence_file)
    reward.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    reward.unlink(missing_ok=True)
    try:
        raw = Path(args.gold).read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.gold_sha256:
            raise ValueError("gold checksum mismatch")
        evidence = verify(json.loads(raw), json.loads(Path(args.record).read_text()),
                          task_id=args.task_id, input_sha256=args.input_sha256,
                          relay_sha256=args.relay_sha256)
    except Exception as exc:
        evidence = {"schema": "personamem-v2-verdict-v1", "task_id": args.task_id,
                    "measured": False, "reason": "scorer_error", "detail": f"{type(exc).__name__}: {exc}"}
    output.write_text(json.dumps(evidence, ensure_ascii=False) + "\n")
    if evidence["measured"]:
        reward.write_text(str(evidence["score"]) + "\n")
    return 3 if evidence.get("reason") == "scorer_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
