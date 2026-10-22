"""Validation for the optional structured QA prediction artifact.

The artifact is deliberately small and closed: a schema version, a final answer, and
optional model-selected evidence. Gold answers and arbitrary metadata never belong in it.
"""
from __future__ import annotations

SCHEMA_VERSION = 1
ALLOWED_KEYS = {"schema_version", "answer", "predicted_evidence"}


def validate_prediction(value: object) -> tuple[dict | None, str | None]:
    if not isinstance(value, dict):
        return None, "prediction must be a JSON object"
    if set(value) - ALLOWED_KEYS:
        return None, "prediction contains unsupported properties"
    if value.get("schema_version") != SCHEMA_VERSION:
        return None, f"schema_version must be {SCHEMA_VERSION}"
    answer = value.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return None, "answer must be a nonempty string"
    evidence = value.get("predicted_evidence")
    if evidence is None:
        return dict(value), None
    if not isinstance(evidence, list):
        return None, "predicted_evidence must be a list of pairs"
    for index, pair in enumerate(evidence):
        if (not isinstance(pair, list) or len(pair) != 2
                or not isinstance(pair[0], str) or not pair[0].strip()
                or isinstance(pair[1], bool) or not isinstance(pair[1], int)
                or pair[1] < 0):
            return None, (
                f"predicted_evidence[{index}] must be [nonempty page/title, "
                "nonnegative integer]")
    return dict(value), None


__all__ = ["SCHEMA_VERSION", "validate_prediction"]
