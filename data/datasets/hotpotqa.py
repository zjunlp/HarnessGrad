r"""HotpotQA fullwiki validation adapter reporting answer F1, not official joint F1.

`prediction.json` carries the answer and optional `(title, sentence_index)` pairs.
Reward uses answer F1; correctness uses answer EM, recorded alongside it. This
question-only configuration supplies neither context nor a retrieval service, so this
reward must not be presented as the official HotpotQA joint metric. Supporting-fact
and joint EM/F1 remain diagnostic scores; support facts use exact set semantics.
Missing evidence remains a valid prediction and produces support/joint zero without
changing the answer reward. The goal still asks for supporting facts.
The adapter reads only the pinned validation split, not test gold.
"""
from __future__ import annotations

from data.scaffolds.qa import base
from data.scaffolds.qa.evidence_scorers import (hotpot_exact_match, hotpot_f1_score,
                                      SpScore, hotpot_joint, hotpot_update_sp, MissingEvidence)

KIND = "dataset"
METRIC_NAME = "HotpotQA answer F1 (not official joint)"

DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/hotpotqa")
REVISION = "1908d6afbbead072334abe2965f91bd2709910ab"
CONFIG = "fullwiki"
VALIDATION = f"{CONFIG}/validation-00000-of-00001.parquet"
TEST = f"{CONFIG}/test-00000-of-00001.parquet"


def _gold(row: dict) -> dict:
    facts = row.get("supporting_facts")
    if isinstance(facts, dict):
        titles = facts.get("title") or []
        indices = facts.get("sent_id") or []
        if len(titles) != len(indices):
            raise ValueError("hotpotqa: supporting_facts title/sent_id lengths differ")
        facts = [[title, index] for title, index in zip(titles, indices)]
    return {"answer": str(row["answer"]), "supporting_facts": facts}

EVIDENCE_AWARE = True
ORACLE_SOURCE = "released"


def score_prediction(prediction: dict, gold: dict) -> dict:
    answer = prediction["answer"]
    em = hotpot_exact_match(answer, gold["answer"])
    f1, precision, recall = hotpot_f1_score(answer, gold["answer"])
    try:
        sp = hotpot_update_sp(prediction.get("predicted_evidence"),
                              gold.get("supporting_facts"))
    except MissingEvidence:
        sp = SpScore(em=0.0, f1=0.0, prec=0.0, recall=0.0)
    joint_em, joint_f1 = hotpot_joint(em, precision, recall, sp)
    return {"reward": f1, "correct": bool(em == 1.0),
            "official": {"metric": METRIC_NAME, "reward_metric": "answer_f1",
                          "joint_metrics_role": "diagnostic_only", "em": em, "f1": f1,
                          "sp_em": sp.em, "sp_f1": sp.f1,
                          "joint_em": joint_em, "joint_f1": joint_f1},
            "why": f"reward=answer_f1={f1:g}, answer_em={em:g}; not official joint "
                   f"(joint_em={joint_em:g}, joint_f1={joint_f1:g})"}


def score(prediction: str, gold: dict) -> dict:
    return score_prediction({"answer": prediction}, gold)


def _rows() -> list[dict]:
    path = DATASET_ROOT / VALIDATION
    if not path.is_file():
        raise FileNotFoundError(f"no {VALIDATION} at {path}")
    return base.parquet_rows(path)


_SPEC = base.QASpec(
    name="hotpotqa",
    root=DATASET_ROOT,
    rows=lambda: {"validation": _rows()},
    question=lambda row: str(row["question"]),
    gold=_gold,
    goal=lambda question, gold: (
        f"Question: {question}\n\nWrite the answer to `answer.txt` with `qa submit`, or "
        "write `prediction.json` with `qa submit-json @file`: "
        "{\"schema_version\":1,\"answer\":\"...\",\"predicted_evidence\":["
        "[title,sentence_index]]}. Supporting facts use exact set semantics and are "
        "required for a nonzero joint score."),
    score=score,
    downloads=lambda: [
        {
            "kind": "hf-dataset",
            "name": "hotpot_qa (fullwiki)",
            "repo": "hotpot_qa",
            "version": REVISION,
            "size": "~28 MB (fullwiki validation; the full repository is ~750 MB)",
            "files": [VALIDATION],
            "sha256": {
                VALIDATION: "78933c0a31a5f7b420d4effdf4cd4eed573b28c6a3da6179dcf7a02b39e51d03",
            },
            "dst": ".",
            "note": "Trap 1: `hotpot_qa` has two configs and they are different "
                    "benchmarks -- `distractor` ships ten candidate paragraphs per "
                    "question, `fullwiki` ships the whole-Wikipedia setting. Trap 2: only "
                    "the `fullwiki/validation` file is fetched; `fullwiki/train` alone is "
                    "330 MB and the `distractor` config another 360 MB, none of it scored. "
                    "Trap 3: the `supporting_facts` column is the gold evidence a future "
                    "answer contract would need -- it is here, the answer channel is what "
                    "does not exist.",
        },
    ],
    split="validation",
    source="hotpot_qa@1908d6afbbead072334abe2965f91bd2709910ab (config `fullwiki`)",
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


__all__ = ["KIND", "DATASET_ROOT", "REFUSED", "load", "downloads"]
