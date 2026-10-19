"""Scoring of a seal obtained directly from a trusted Episode controller.

The checksum catches corruption; it does not authenticate a harness-provided
file. The verifier obtains this record from the platform-sealed relay output.
This module computes episode scores; reward export and source reporting are separate.
"""
from collections.abc import Mapping
import hashlib
import json

from .scoring import JUDGE_SOURCES, PRIMARY_METRICS, score_question


def _check_seal(record, expected_episode_id):
    fields = {"schema", "episode_id", "status", "expected", "acked_chunk_ids",
              "submitted_question_ids", "missing_chunk_ids", "missing_question_ids",
              "answers", "sha256"}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("invalid seal fields")
    if (record["schema"] != "hg-mab-episode-seal-v1"
            or not isinstance(expected_episode_id, str) or not expected_episode_id.strip()
            or record["episode_id"] != expected_episode_id):
        raise ValueError("seal schema or episode identity mismatch")
    payload = {key: value for key, value in record.items() if key != "sha256"}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False).encode("utf-8")).hexdigest()
    if record["sha256"] != digest:
        raise ValueError("seal checksum mismatch")
    expected = record["expected"]
    if not isinstance(expected, dict) or set(expected) != {"chunk_ids", "question_ids"}:
        raise ValueError("invalid expected IDs")
    for kind in ("chunk", "question"):
        ids = expected[kind + "_ids"]
        accepted = record["acked_chunk_ids" if kind == "chunk" else "submitted_question_ids"]
        missing = record["missing_" + kind + "_ids"]
        if (not isinstance(ids, list)
                or not all(isinstance(item, str) and item.strip() for item in ids)
                or len(set(ids)) != len(ids) or not isinstance(accepted, list)
                or not isinstance(missing, list) or len(accepted) > len(ids)
                or accepted != ids[:len(accepted)] or missing != ids[len(accepted):]):
            raise ValueError("seal IDs must form an ordered prefix and remaining suffix")
    if not expected["question_ids"]:
        raise ValueError("an episode requires questions")
    if record["missing_chunk_ids"] and record["submitted_question_ids"]:
        raise ValueError("questions submitted before all chunks were acknowledged")
    complete = not record["missing_chunk_ids"] and not record["missing_question_ids"]
    if record["status"] != ("complete" if complete else "incomplete"):
        raise ValueError("seal status disagrees with coverage")
    answers = record["answers"]
    if (not isinstance(answers, list)
            or any(not isinstance(item, dict) or set(item) != {"question_id", "answer"}
                   or not isinstance(item["answer"], str) for item in answers)
            or [item["question_id"] for item in answers] != record["submitted_question_ids"]):
        raise ValueError("seal answers disagree with submitted question IDs")


def score_episode(source, seal, gold_by_id, *, expected_episode_id, entity2id=None, judge_metadata_by_id=None,
                  judge_profile=None, complete=None):
    """Score a complete episode, preserving unmeasured outcomes and exceptions.

    Gold is supplied separately, keyed by stable adapter question IDs. The mean
    is over this episode's questions only, not an official benchmark-wide mean.
    Incomplete episodes receive no partial numeric score in this scoring core.
    """
    _check_seal(seal, expected_episode_id)
    question_ids = seal["expected"]["question_ids"]
    if not isinstance(gold_by_id, Mapping) or set(gold_by_id) != set(question_ids):
        raise ValueError("gold IDs must exactly match the episode question IDs")
    # Validate source and every gold even when the episode is incomplete.
    for qid in question_ids:
        score_question(source, None, gold_by_id[qid])
    total, submitted = len(question_ids), len(seal["answers"])
    result = {
        "schema": "hg-mab-episode-score-v1", "episode_id": expected_episode_id,
        "seal_sha256": seal["sha256"], "source": source, "metric": PRIMARY_METRICS[source],
        "coverage": {"expected": total, "submitted": submitted, "missing": total - submitted,
                     "measured": 0, "unmeasured": total},
        "questions": [],
    }
    if seal["status"] != "complete":
        return dict(result, status="unmeasured", reason="incomplete_episode")
    if source in JUDGE_SOURCES and complete is not None:
        if (not isinstance(judge_metadata_by_id, Mapping)
                or set(judge_metadata_by_id) != set(question_ids) or judge_profile is None):
            raise ValueError('judge metadata/profile must cover the selected episode')
        result['judge'] = judge_profile
    for item in seal["answers"]:
        if source in JUDGE_SOURCES and complete is not None:
            from .judge import score_question as judge_question
            score = judge_question(source, item['answer'], gold_by_id[item['question_id']],
                                   judge_metadata_by_id[item['question_id']], complete, judge_profile)
        else:
            score = score_question(source, item["answer"], gold_by_id[item["question_id"]],
                                   entity2id=entity2id)
        result["questions"].append(dict(score, question_id=item["question_id"]))
    measured = sum(item["status"] == "measured" for item in result["questions"])
    result["coverage"].update(measured=measured, unmeasured=total - measured)
    if measured != total:
        return dict(result, status="unmeasured", reason="judge_required")
    return dict(result, status="measured", aggregation="question_mean_within_episode",
                score=sum(item["score"] for item in result["questions"]) / total)
