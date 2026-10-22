"""MuSiQue-Ans v1.0 dev: supplied-context answer and support evaluation.

Trivedi et al., TACL 2022, https://arxiv.org/abs/2108.00573. Official
`evaluate_v1.0.py` reports answer_f1, answer_em and support_f1 separately. The default
reward is answer_f1 * support_f1, an explicitly platform-defined joint policy, not an
official MuSiQue metric. Only `legacy_answer_only` preserves the old answer-only reward.

All released context paragraphs are given in original order. Answers, aliases,
question_decomposition and is_supporting flags stay out of agent-visible data.
The upstream dev split is used as a fixed task pool with **no** division declared: it is
one scored split, so the whole pool is evaluation material (`OFFICIAL_EVAL is None`) and it
is not presented as the benchmark's official hidden test set. The adapter used to impose a
platform hash train/eval partition on it; that invented division was removed on 2026-10-24.

HF canonical access timed out during onboarding. Data was verified through hf-mirror
against the pinned revision's LFS sha256; identity against canonical HF bytes has not
been independently established. Official scorer code was fetched from canonical codeload.
"""
from __future__ import annotations

import collections
import json
import re
import string
from pathlib import Path

from data.scaffolds.qa import base

KIND = "dataset"
EVIDENCE_AWARE = True
REDACT_EVIDENCE = True
DEFAULT_MODE = "answer_support"
LEGACY_MODE = "legacy_answer_only"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"
DATASET_ROOT = Path("/mnt/20t/xubuqiang/datasets/musique")
DATA_FILE = "musique_ans_v1.0_dev.jsonl"
REVISION = "c8f4f8c9465fb69d31a8eae894c3fd509c4ca321"
DATA_SHA256 = "15fa63794d18a94ce12411aca6e2327e65b6e83b0b1490efab3f1962e48abf3b"
OFFICIAL_COMMIT = "922ac98f19a201998dbdae6d7f2887a5258dbdeb"
SOURCE_SHA256 = "c1cc3d121e3f623f9925857559030d48c136142ee2c9cdb52214bb6134c02272"


def normalize_answer(s):
    """Lower text and remove punctuation, articles and extra whitespace."""
    def remove_articles(text):
        return re.sub(r"\b(a|an|the)\b", " ", text, flags=re.UNICODE)
    def remove_punc(text):
        return "".join(ch for ch in text if ch not in set(string.punctuation))
    return " ".join(remove_articles(remove_punc(s.lower())).split())


def compute_exact(a_gold, a_pred):
    return int(normalize_answer(a_gold) == normalize_answer(a_pred))


def compute_f1(a_gold, a_pred):
    gold_toks = normalize_answer(a_gold).split() if a_gold else []
    pred_toks = normalize_answer(a_pred).split() if a_pred else []
    num_same = sum((collections.Counter(gold_toks) & collections.Counter(pred_toks)).values())
    if len(gold_toks) == 0 or len(pred_toks) == 0:
        return int(gold_toks == pred_toks)
    if num_same == 0:
        return 0
    precision = num_same / len(pred_toks)
    recall = num_same / len(gold_toks)
    return (2 * precision * recall) / (precision + recall)


def _check_mode(mode: str) -> None:
    if mode not in (DEFAULT_MODE, LEGACY_MODE):
        raise ValueError("MuSiQue mode must be answer_support or legacy_answer_only")


def compute_support(predicted_indices: list[int], gold_indices: list[int]) -> dict:
    """Pinned metrics/support.py::SupportMetric, including the both-empty case."""
    predicted, expected = set(predicted_indices), set(gold_indices)
    tp = len(predicted & expected)
    fp, fn = len(predicted - expected), len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    if not predicted and not expected:
        f1 = 1.0
    return {"f1": f1, "em": int(fp + fn == 0)}


def _score(answer: str, evidence: list | None, gold: dict, mode: str) -> dict:
    _check_mode(mode)
    answers = [gold["answer"], *gold["aliases"]]
    f1 = max(compute_f1(reference, answer) for reference in answers)
    em = max(compute_exact(reference, answer) for reference in answers)
    official = {"metric": "MuSiQue-Ans", "answer_f1": f1, "answer_em": em,
                "id": gold["id"], "mode": mode,
                "aggregate_policy": "ordered macro mean; official columns rounded to 3 decimals"}
    if mode == LEGACY_MODE:
        official.update(metric="MuSiQue-Ans legacy answer-only (support unmeasured)",
                        support_f1=None, reward_policy="legacy answer_f1 only")
        return {"reward": float(f1), "correct": bool(em), "official": official,
                "why": f"legacy_answer_only: answer_f1={f1:g}, answer_em={em}; support unmeasured"}
    if "supporting_facts" not in gold or not isinstance(gold["supporting_facts"], list):
        raise ValueError("MuSiQue trusted gold is missing supporting_facts")
    # Titles describe public citations; upstream support scoring uses paragraph idx only.
    support = compute_support([pair[1] for pair in (evidence or [])],
                              [pair[1] for pair in gold["supporting_facts"]])
    joint_f1, joint_em = f1 * support["f1"], em * support["em"]
    official.update(support_f1=support["f1"], support_em=support["em"],
                    joint_f1=joint_f1, joint_em=joint_em,
                    reward_policy="platform answer_f1 * support_f1 (not an official metric)",
                    joint_em_policy="platform answer_em * support_em (not an official metric)")
    return {"reward": float(joint_f1), "correct": bool(joint_em), "official": official,
            "why": f"answer_f1={f1:g}, support_f1={support['f1']:g}, answer_em={em}; "
                   f"platform joint_f1={joint_f1:g}"}


def score_prediction(prediction: dict, gold: dict, *, mode: str = DEFAULT_MODE) -> dict:
    from data.scaffolds.qa.prediction import validate_prediction

    value, error = validate_prediction(prediction)
    if error:
        raise ValueError(error)
    return _score(value["answer"], value.get("predicted_evidence"), gold, mode)


def score(prediction: str, gold: dict, *, mode: str = DEFAULT_MODE) -> dict:
    """Text is answer-only input, hence zero support under the default policy."""
    return _score(prediction, None, gold, mode)


def aggregate(results: list[dict]) -> dict:
    """Match upstream ordered accumulation/rounding for the three official columns.

    The platform reward is the unrounded mean of per-item rewards, never the product
    of corpus means. Mixing legacy and default results is refused.
    The declared denominator is the provided results: this helper has no gold pool
    to detect absent rows. Upstream requires complete, identically ordered IDs.
    """
    if not results:
        raise ValueError("cannot aggregate an empty MuSiQue result list")
    from data.scaffolds.aggregation import aggregation_denominator

    denominator = aggregation_denominator("musique", "corpus_metrics",
                                          provided_results=len(results))
    modes = {result["official"]["mode"] for result in results}
    if len(modes) != 1:
        raise ValueError("cannot mix MuSiQue scoring modes")
    mode = modes.pop()
    _check_mode(mode)
    output = {"mode": mode, "count": len(results),
              "reward_policy": results[0]["official"]["reward_policy"],
              "aggregate_policy": "ordered macro mean; official columns rounded to 3 decimals"}
    fields = ["answer_f1", "answer_em"]
    if mode == DEFAULT_MODE:
        fields += ["support_f1", "joint_f1", "joint_em"]
    else:
        output["support_f1"] = None
    for field in [*fields, "reward"]:
        total = 0.0
        for result in results:
            total += result["reward"] if field == "reward" else result["official"][field]
        mean = total / denominator
        output[field] = round(mean, 3) if field in ("answer_f1", "answer_em", "support_f1") else mean
    return output


def _rows():
    with (DATASET_ROOT / DATA_FILE).open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if len(rows) != 2417 or len({r["id"] for r in rows}) != 2417:
        raise ValueError("expected exactly 2417 unique MuSiQue-Ans dev questions")
    if not all(r["answerable"] and r["answer"] for r in rows):
        raise ValueError("this adapter requires answerable MuSiQue-Ans rows with gold")
    return {"dev": rows}


def _question(row):
    context = "\n\n".join(f"[{p['idx']}] {p['title']}\n{p['paragraph_text']}"
                          for p in row["paragraphs"])
    return f"Question: {row['question']}\n\nContext paragraphs:\n{context}"


def _gold(row):
    return {"answer": row["answer"], "aliases": list(row["answer_aliases"]), "id": row["id"],
            "supporting_facts": [[p["title"], p["idx"]] for p in row["paragraphs"]
                                 if p["is_supporting"]]}


def _goal(question, gold, *, mode=DEFAULT_MODE):
    _check_mode(mode)
    if mode == LEGACY_MODE:
        return (question + "\n\nLEGACY ANSWER-ONLY MODE: reward is answer F1; support is "
                "unmeasured. Write `answer.txt` or use the qa submit command:\n"
                '    python3 qa/bin/qa submit "<answer>"\n')
    return (question + "\n\nSubmit a short answer and the supporting paragraph indices in "
            '`prediction.json`: {"schema_version":1,"answer":"<answer>",'
            '"predicted_evidence":[["<title>",0]]}. Each pair cites a public title and '
            "the [idx] printed above, not its list position or a sentence number. Titles "
            "are descriptive; only indices are scored. Duplicates count once; extra indices "
            "reduce support precision. Missing/null/empty evidence is an empty prediction. "
            "Answer F1, Support F1 and Answer EM are reported separately. Reward is their "
            "platform joint policy: answer_f1 * support_f1 (not an official metric). "
            "All provided paragraphs may be used; their order is unchanged.\n\n"
            "Read `qa/output-contract.md`; submit using:\n"
            "    python3 qa/bin/qa submit-json @prediction.json\n"
            "`answer.txt` / qa submit without evidence gets zero support in this mode.\n")


def _downloads():
    return [
        {"kind": "hf-dataset", "name": "MuSiQue-Ans dev (third-party HF release)",
         "repo": "dgslibisey/MuSiQue", "version": REVISION,
         "size": "30,439,728 bytes (2417 dev rows)", "files": [DATA_FILE], "dst": ".",
         "sha256": {DATA_FILE: DATA_SHA256},
         "note": "Canonical HF timed out; pinned data and LFS digest verified via hf-mirror, not independently against HF. Preserve all paragraphs; strip answer, aliases, decomposition and supporting labels from public inputs. Report official answer_f1, answer_em and support_f1 separately; joint reward is platform-defined."},
        {"kind": "http", "name": "MuSiQue official evaluation code", "version": OFFICIAL_COMMIT,
         "url": f"https://codeload.github.com/StonyBrookNLP/musique/tar.gz/{OFFICIAL_COMMIT}",
         "size": "1,281,842 bytes compressed source",
         "sha256": SOURCE_SHA256, "dst": "_src/musique.tar.gz", "extract_to": "_src",
         "note": "Pinned evaluate_v1.0.py and metrics/answer.py. The evaluator uses each row's answer_aliases, not the repository-global .answer_aliases.json. Code archive never enters agent SETUP."},
    ]


class _MuSiQueSpec(base.QASpec):
    mode = DEFAULT_MODE

    def verifier(self, record):
        spec = super().verifier(record)
        spec["argv"] += ["--score-mode", self.mode, "--redact-gold"]
        return spec


_SPEC = _MuSiQueSpec(name="musique", root=DATASET_ROOT, rows=_rows,
                   question=_question, gold=_gold,
                   goal=lambda question, gold: _goal(question, gold, mode=_SPEC.mode), score=score,
                   downloads=_downloads, split="dev",
                   extra_setup=[{"path": "qa/output-contract.md",
                                 "from": str(base.ROOT / "docs/musique-output-contract.md")}],
                   source=f"MuSiQue-Ans v1.0 dev, dgslibisey/MuSiQue@{REVISION}")
# Do not open a mixed study/eval payload merely to import a scoring function.
DEFAULT_ENV = {"kind": "files"}

#: The benchmark's own evaluation set, within this adapter's pool. The pool is MuSiQue-Ans `dev`, the answerable split the benchmark reports on.
#: `train` exists at the same pinned revision and is not loaded, so this pool holds no study
#: material.
#:
#: Read from `QASpec.division()` -- the one place that decides what this pool's
#: division is -- and `None` because the benchmark released no study split for this
#: table: the pool **is** the scored split, so every task in it belongs to the
#: evaluation set, and that claims nothing about whether the pool is the whole of it.
#: `--split official --side eval` scores the whole pool, which is the comparable run;
#: `--split declared` is the same whole pool, because there is no division to declare.
OFFICIAL_EVAL = None

#: `"none"`: the benchmark states no division, so this adapter declares none and the run
#: covers every task. It used to cut its own pool with a hash third (`"hash"`), an invented
#: division the operator removed on 2026-10-24; `--split default` still names that rule at
#: run time.
SPLIT_SOURCE = "none"


def __getattr__(name):
    if name in ("ENV", "SPLIT"):
        return _SPEC.build()[name]
    return base.module_getattr(_SPEC, name)


def load(*, mode=DEFAULT_MODE):
    _check_mode(mode)
    _SPEC.mode = mode
    return _SPEC.load()


def verify_for(record: dict, *, mode: str = DEFAULT_MODE) -> dict:
    """Build a selected verifier from trusted configuration, without reading the pool."""
    _check_mode(mode)
    spec = base.QASpec.verifier(_SPEC, record)
    spec["argv"] += ["--score-mode", mode, "--redact-gold"]
    return spec


def downloads():
    return _downloads()


def provision_status():
    return _SPEC.provision_status()
