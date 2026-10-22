r"""FEVER exported-row conjunctive strict@5, not classification Macro-F1.

The adapter reads the pinned `copenlu/fever_gold_evidence` validation split and keeps
that provenance explicit; it does not claim test gold access. Reward follows the
operator's recorded choice in `registry/w-fevobj-receipts.md`: binary correctness of
the label AND the whole exported evidence group within the first five predicted pairs,
with NOT ENOUGH INFO evidence-exempt. This retains the strict predicate while naming
the exported-row denominator honestly; it is not an original-claim FEVER aggregate.
Each flat list is one conjunction in retained source order. Omitted alternative groups,
annotation IDs, and original claim weighting cannot be recovered from that row.

The version's paper metric, classification Macro-F1 over LABELS, is separate: sum the
per-task per-label TP/FP/FN counts in `official.classification`, then compute
(1/3) * sum(2*TP_c / (2*TP_c + FP_c + FN_c)); use 0 for a zero denominator.
Do not average per-task F1 or call label accuracy Macro-F1. Label comparison only
uppercases, matching the strict scorer. Macro-F1 is not the task reward because it
requires an aggregate and the operator already chose the exported-row strict metric.

`prediction.json` carries the label and optional `(page, line)` pairs. Missing required
evidence gives strict reward 0 even for a right label; its classification counts still
record that right label. The prompt supplies only the claim, not evidence text or a
retrieval corpus. This scoring repair does not establish evidence retrieval readiness.
"""
from __future__ import annotations

from data.scaffolds.qa import base
from data.scaffolds.qa.evidence_scorers import MissingEvidence, fever_is_strictly_correct

KIND = "dataset"

DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/fever")
REVISION = "a6b8d891d393e97a4efac791afffb2d7de5e57c6"
VALID = "valid.jsonl"
TEST = "test.jsonl"
TRAIN = "train.jsonl"
LABELS = ("SUPPORTS", "REFUTES", "NOT ENOUGH INFO")
REWARD_METRIC = "fever_exported_row_conjunctive_strict_at_5"

EVIDENCE_AWARE = True
ORACLE_SOURCE = "released"


def score_prediction(prediction: dict, gold: dict) -> dict:
    answer = prediction["answer"]
    predicted_evidence = prediction.get("predicted_evidence")
    try:
        correct = fever_is_strictly_correct(
            gold["answer"], answer, gold.get("evidence"), predicted_evidence)
    except MissingEvidence:
        correct = False
    label = str(gold["answer"]).upper()
    predicted_label = str(answer).upper()
    classification = {
        "metric": "classification_macro_f1",
        "labels": list(LABELS),
        "gold_label": label,
        "predicted_label": predicted_label,
        "per_label": {
            name: {"tp": int(label == name and predicted_label == name),
                   "fp": int(label != name and predicted_label == name),
                   "fn": int(label == name and predicted_label != name)}
            for name in LABELS
        },
    }
    return {"reward": float(correct), "correct": correct,
            "official": {"metric": REWARD_METRIC, "strict_score": float(correct),
                         "max_evidence": 5, "classification": classification},
            "why": "strict label and evidence match" if correct else
                  "label or required evidence did not match"}


def score(prediction: str, gold: dict) -> dict:
    # Legacy answer.txt remains scoreable; omitted evidence is an incorrect prediction.
    return score_prediction({"answer": prediction}, gold)


def _rows() -> list[dict]:
    path = DATASET_ROOT / VALID
    if not path.is_file():
        raise FileNotFoundError(f"no {VALID} at {path}")
    return base.jsonl_rows(path)


_SPEC = base.QASpec(
    name="fever",
    root=DATASET_ROOT,
    rows=lambda: {"validation": _rows()},
    question=lambda row: str(row["claim"]),
    gold=lambda row: {"answer": str(row["label"]), "evidence": row.get("evidence")},
    goal=lambda question, gold: (
        f"Claim: {question}\n\nReward: FEVER exported-row conjunctive strict@5. "
        "Return the FEVER label in `answer.txt` or with `qa submit`, "
        "and include evidence in prediction.json "
        "using `qa submit-json @file`. Schema: {\"schema_version\":1,\"answer\":"
        "\"SUPPORTS|REFUTES|NOT ENOUGH INFO\",\"predicted_evidence\":[[page,line]]}. "
        "Evidence is optional in the artifact; non-NEI claims without it score incorrect. "
        "NEI is evidence-exempt. All pairs in the exported evidence group must occur "
        "within the first five predicted pairs."),
    score=score,
    downloads=lambda: [
        {
            "kind": "hf-dataset",
            "name": "copenlu/fever_gold_evidence",
            "repo": "copenlu/fever_gold_evidence",
            "version": REVISION,
            "size": "~110 MB (test 6.6 MB + valid 6.5 MB + train 97 MB)",
            # `dst` must be the **path `--fetch` writes**, which for `hf-dataset` entries
            # is built by appending the file to `dst`. A first version of this entry set
            # `dst` to the file name as well, which produced `.../valid.jsonl/valid.jsonl`
            # and a 404 -- a duplicate key in one dict, and the fetch is where it showed.
            "files": [VALID],
            "dst": ".",
            "note": "The exported-row conjunctive strict@5 reward requires predicted "
                    "evidence for non-NEI claims. The paper's classification Macro-F1 "
                    "can be aggregated separately from per-task label counts. The "
                    "repository also ships `test.jsonl` and a 97 MB `train.jsonl`; the "
                    "adapter reads `valid.jsonl` and declares only it, so `--check` does "
                    "not report a missing 97 MB file as an error. `sha256` is left null "
                    "because the HuggingFace JSONL export is regenerated by the Hub and "
                    "the pinned revision is the guarantee; the lint requires a pinned "
                    "version, not a checksum.",
        },
    ],
    split="validation",
    source="copenlu/fever_gold_evidence@a6b8d891d393e97a4efac791afffb2d7de5e57c6 (validation split)",
)

_BUILT = _SPEC.build()
DEFAULT_ENV = _BUILT["DEFAULT_ENV"]
ENV = _BUILT["ENV"]
SPLIT = _BUILT["SPLIT"]
OFFICIAL_EVAL = _BUILT["OFFICIAL_EVAL"]
SPLIT_SOURCE = _BUILT["SPLIT_SOURCE"]


def __getattr__(name: str):
    return base.module_getattr(_SPEC, name)


def load() -> tuple[list[dict], dict[str, str]]:
    return _SPEC.load()


def downloads() -> list[dict]:
    return _SPEC._downloads()


def provision_status() -> dict:
    return _SPEC.provision_status()


__all__ = ["KIND", "DATASET_ROOT", "REFUSED", "LABELS", "load", "downloads"]
