"""Portable local scoring for the frozen MemoryAgentBench data sources.

Derived from https://github.com/HUST-AI-HYZ/MemoryAgentBench at commit
538026089d1a8a8eff05121d0db89b388f360eba, utils/eval_other_utils.py,
SHA256 d77976be409298970614d477a9d8003850caddb0510e56a7e821a037d98493a2.
Normalization/parsing: lines 33–170; recommendation: 297–398;
metrics and source dispatch: 409–613. README lines 171–185 specify the
primary metrics. The star in longmemeval_s* is literal.

No downloads, model calls, or implicit working-directory resources. ROUGE
requires installed rouge-score and provisioned NLTK sentence data; recommendation
uses an exact unit-cost Levenshtein implementation. Importing this module requires only the standard library.
Missing dependencies/resources and scorer errors propagate, never become zero.

Adapted under the upstream MIT License:
Copyright (c) 2026 Yuanzhe Hu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from functools import lru_cache
import re
import string

UPSTREAM_REVISION = "538026089d1a8a8eff05121d0db89b388f360eba"
UPSTREAM_SCORER_SHA256 = "d77976be409298970614d477a9d8003850caddb0510e56a7e821a037d98493a2"
PRIMARY_METRICS = {
    "detective_qa": "exact_match",
    "eventqa_131072": "substring_exact_match",
    "eventqa_65536": "substring_exact_match",
    "eventqa_full": "substring_exact_match",
    **{f"factconsolidation_{hop}_{size}": "substring_exact_match"
       for hop in ("mh", "sh") for size in ("6k", "32k", "64k", "262k")},
    **{source: "exact_match" for source in (
        "icl_banking77_5900shot_balance", "icl_clinic150_7050shot_balance",
        "icl_nlu_8296shot_balance", "icl_trec_coarse_6600shot_balance",
        "icl_trec_fine_6400shot_balance")},
    "infbench_sum_eng_shots2": "gpt-4-f1",
    "longmemeval_s*": "Accuracy",
    "recsys_redial_full": "recsys_recall@5",
    "ruler_qa1_197K": "substring_exact_match",
    "ruler_qa2_421K": "substring_exact_match",
}
JUDGE_SOURCES = frozenset(("infbench_sum_eng_shots2", "longmemeval_s*"))


def _validate(source, answers):
    if not isinstance(source, str) or source not in PRIMARY_METRICS:
        raise ValueError(f"unknown MemoryAgentBench source: {source!r}")
    if isinstance(answers, str):
        flat = [answers]
    elif isinstance(answers, list) and answers:
        if all(isinstance(item, list) and item for item in answers):
            flat = [item for group in answers for item in group]
        else:
            flat = answers
    else:
        raise ValueError("answers must be a nonempty string or nonempty answer list")
    if not all(isinstance(item, str) and item.strip() for item in flat):
        raise ValueError("answers must contain nonblank strings, at most one list nesting")
    if source.startswith("eventqa_") or source == "recsys_redial_full":
        if not isinstance(answers, list) or not all(isinstance(item, str) for item in answers):
            raise ValueError("EventQA and recommendation answers must be a flat string list")
    if source == "recsys_redial_full":
        try:
            [int(item.strip()) for item in answers]
        except ValueError as exc:
            raise ValueError("recommendation answers must contain integer movie IDs") from exc
    return flat


def _normalize(text):
    text = ''.join(char for char in text.lower() if char not in string.punctuation)
    return ' '.join(re.sub(r'\b(a|an|the)\b', ' ', text).split())


def _parse(text):
    for pattern in (re.compile(r"(?:Answer:)(.*)(?:\n|$)", re.IGNORECASE),
                    re.compile(r"(?:^)(.*)(?:\n|$)")):
        match = pattern.search(text)
        if match:
            return re.sub(r'^Answer:', '', match[1].strip(), flags=re.IGNORECASE).strip()
    return None


def _f1(prediction, answer):
    predicted, gold = _normalize(prediction), _normalize(answer)
    special = {'yes', 'no', 'noanswer'}
    if (predicted in special or gold in special) and predicted != gold:
        return 0
    predicted_tokens, gold_tokens = predicted.split(), gold.split()
    overlap = sum((Counter(predicted_tokens) & Counter(gold_tokens)).values())
    if not overlap:
        return 0
    precision, recall = overlap / len(predicted_tokens), overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


@lru_cache(maxsize=1)
def _rouge_scorer():
    from rouge_score import rouge_scorer
    return rouge_scorer.RougeScorer(['rougeL', 'rougeLsum'], use_stemmer=True)


def _text_metrics(prediction, answers, *, diagnostics=True):
    normalized = _normalize(prediction)
    metrics = {
        "exact_match": max(normalized == _normalize(answer) for answer in answers),
        "f1": max(_f1(prediction, answer) for answer in answers),
        "substring_exact_match": max(_normalize(answer) in normalized for answer in answers),
    }
    if not diagnostics:
        return metrics
    scorer = _rouge_scorer()
    scores = [scorer.score(target=answer, prediction=prediction) for answer in answers]
    for name in scorer.rouge_types:
        metrics[name + "_f1"] = max(score[name].fmeasure for score in scores)
        metrics[name + "_recall"] = max(score[name].recall for score in scores)
    return metrics


def _movie_name(name):
    name = name.split('/')[-1].replace('_', ' ').replace('-', ' ').replace('>', ' ')
    return re.sub(r"\s+", " ", re.sub(r"\([^()]*\)", "", name)).strip()


def _edit_distance(left, right):
    """Exact unit-cost Levenshtein distance, using bit-parallel dynamic programming.

    Equivalent to upstream editdistance.eval for strings, without a native package
    in the isolated verifier. Differential tests include Unicode and random pairs.
    """
    if not left:
        return len(right)
    masks = {}
    for index, char in enumerate(left):
        masks[char] = masks.get(char, 0) | (1 << index)
    positive, negative, score = ~0, 0, len(left)
    high = 1 << (len(left) - 1)
    for char in right:
        equal = masks.get(char, 0)
        vertical = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        plus = negative | ~(horizontal | positive)
        minus = positive & horizontal
        score += bool(plus & high) - bool(minus & high)
        plus = (plus << 1) | 1
        minus <<= 1
        positive = minus | ~(vertical | plus)
        negative = plus & vertical
    return score


def _recommendation_metrics(prediction, answers, entity2id):
    if not isinstance(entity2id, Mapping) or not entity2id:
        raise ValueError("recommendation scoring requires an explicit nonempty entity2id mapping")
    if not all(isinstance(name, str) and isinstance(movie_id, int)
               and not isinstance(movie_id, bool) for name, movie_id in entity2id.items()):
        raise ValueError("entity2id must map string names to integer IDs")
    # Preserve duplicate IDs' last-name behavior and duplicate cleaned movie names.
    id_to_name = {movie_id: _movie_name(name) for name, movie_id in entity2id.items()}
    ids = [int(item.strip()) for item in answers]
    if any(movie_id not in id_to_name for movie_id in ids):
        raise ValueError("recommendation gold movie ID is missing from entity2id")
    edit_distance = _edit_distance
    # Intentionally preserve upstream set order and first-distance tie selection.
    # Equal-distance results can differ across Python hash seeds (known upstream behavior).
    candidates = list(set(id_to_name.values()))
    recommendation_text = (prediction.split('1.', 1)[1] if '1.' in prediction
                           else prediction.replace(',', '\n'))
    predicted_movies = []
    for item in recommendation_text.split('\n'):
        item = re.sub(r"\([^()]*\)", "", item.strip())
        item = re.sub(r"^(?:\d+[\.\)、]?\s*[\-\—\–]?\s*)?", "", item)
        item = re.sub(r"\s+", " ", item).strip()
        distances = [edit_distance(item.lower(), candidate.lower()) for candidate in candidates]
        predicted_movies.append(candidates[min(range(len(distances)), key=distances.__getitem__)])
    gold_movies = [id_to_name[movie_id] for movie_id in ids]
    return {f"recsys_recall@{k}": sum(movie in predicted_movies[:k] for movie in gold_movies)
            / len(gold_movies) for k in (1, 5, 10)}


def local_metrics(source, prediction, answers, *, entity2id=None, diagnostics=True):
    """Return all official local metrics, including diagnostics for judge sources.

    Invalid source/gold/resource inputs raise ValueError. A prediction must be a
    string: the empty string is submitted output and is scored normally.
    """
    flat_answers = _validate(source, answers)
    if not isinstance(prediction, str):
        raise ValueError("prediction must be a string; use score_question for missing output")
    if source == "recsys_redial_full":
        return _recommendation_metrics(prediction, answers, entity2id)
    parsed = _parse(prediction)
    if source.startswith("icl_") or source.startswith("eventqa_"):
        metrics = _text_metrics(parsed, flat_answers, diagnostics=diagnostics)
    else:
        metrics = _text_metrics(prediction, flat_answers, diagnostics=diagnostics)
        if parsed is not None:
            parsed_metrics = _text_metrics(parsed, flat_answers, diagnostics=diagnostics)
            metrics = {key: max(value, parsed_metrics[key]) for key, value in metrics.items()}
    if source.startswith("eventqa_"):
        recall = sum(answer.lower() in prediction.lower() for answer in answers) / len(answers)
        metrics["eventqa_recall"] = int(recall == 1)
    return metrics


def score_question(source, prediction, answers, *, entity2id=None, diagnostics=False):
    """Return a measured primary metric or an explicit unmeasured record.

    Primary scoring is standard-library-only; optional ROUGE diagnostics require
    provisioned dependencies. Judge sources never substitute local overlap diagnostics for the primary
    metric. Missing output has no numeric score. Execution errors propagate.
    """
    _validate(source, answers)
    metric = PRIMARY_METRICS[source]
    if prediction is None:
        return {"status": "unmeasured", "metric": metric, "reason": "missing_prediction"}
    if not isinstance(prediction, str):
        raise ValueError("prediction must be a string or None")
    if source in JUDGE_SOURCES:
        return {"status": "unmeasured", "metric": metric, "reason": "judge_required"}
    metrics = local_metrics(source, prediction, answers, entity2id=entity2id, diagnostics=diagnostics)
    return {"status": "measured", "metric": metric, "score": float(metrics[metric]),
            "metrics": metrics}
