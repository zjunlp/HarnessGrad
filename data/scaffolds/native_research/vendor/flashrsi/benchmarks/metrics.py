"""Reusable exact-match and token-F1 metrics for English question answering."""

from __future__ import annotations

import string
import unicodedata
from collections import Counter
from collections.abc import Sequence


def normalize_answer(value: str) -> str:
    """Normalize Unicode, case, punctuation, English articles, and whitespace."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(
        character
        for character in value
        if character not in string.punctuation
        and not unicodedata.category(character).startswith("P")
    )
    return " ".join(token for token in value.split() if token not in {"a", "an", "the"})


def exact_match(prediction: str, answer: str) -> float:
    """Return 1.0 when the normalized prediction and reference are identical."""
    return float(normalize_answer(prediction) == normalize_answer(answer))


def token_f1(prediction: str, answer: str) -> float:
    """Score normalized token overlap, counting repeated tokens separately."""
    return _normalized_token_f1(normalize_answer(prediction), normalize_answer(answer))


def qa_metrics(prediction: str, answers: Sequence[str]) -> dict[str, float]:
    """Return the best EM/F1 over references; blank predictions score zero.

    ``answers`` must contain at least one reference. A nonblank answer that
    becomes empty after normalization (for example, "The") can still match.
    """
    if not answers:
        raise ValueError("QA metrics require at least one reference answer")
    if not prediction.strip():
        return {"exact_match": 0.0, "f1": 0.0}
    prediction = normalize_answer(prediction)
    normalized_answers = tuple(normalize_answer(answer) for answer in answers)
    return {
        "exact_match": float(prediction in normalized_answers),
        "f1": max(
            _normalized_token_f1(prediction, answer) for answer in normalized_answers
        ),
    }


def _normalized_token_f1(prediction: str, answer: str) -> float:
    predicted = prediction.split()
    expected = answer.split()
    if not predicted or not expected:
        return float(predicted == expected)
    overlap = sum((Counter(predicted) & Counter(expected)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted)
    recall = overlap / len(expected)
    return 2 * precision * recall / (precision + recall)


__all__ = ["exact_match", "normalize_answer", "qa_metrics", "token_f1"]
