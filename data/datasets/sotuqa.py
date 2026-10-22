r"""SOTU-QA as a dataset -- and the one whose official metric this contract cannot carry.

    python3 tools/datasets.py --fetch --dataset sotuqa
    python3 data/scaffolds/qa/control.py --dataset sotuqa --oracle
    driver.py --dataset sotuqa ...

**Status: refused, with the reason written down.** `load()` raises. The three sentences
below are the whole argument, and they are the reason this file exists rather than a
report of an `em` that measures string overlap.

Where the data comes from
-------------------------
ReWOO's own curated set, 74 rows, `data/SOTU/SOTU_QA.csv` in
`github.com/billxbf/ReWOO` at commit `9cd0283...` (the same tarball
`data/scaffolds/qa/rewoo.py` declares for three BIG-bench tasks). Each row is a question about the
2023 State of the Union address plus a gold answer that is **a whole sentence**:

    Q: Based on State of the Union Address 2023: Is Speaker of the House
       Kevin McCarthy older than Nancy Pelosi?
    A: No, Kevin is 58 while Nancy is 83

Why the official metric is unreachable here
-------------------------------------------
ReWOO's `utils/Evaluator.py::get_metrics` returns three numbers per item:

     em   = normalize_answer(pred) == normalize_answer(label)
     f1   = f1_score(norm_pred, norm_label)
     acc  = llm_accuracy_score(query, pred, label)

and the **headline** is `acc`. `llm_accuracy_score` is
`QAEvalChain.from_llm(OpenAI(temperature=0))` followed by
`1 if graded_outputs[0]['text'] == 'CORRECT' else 0` -- an LLM judge with **no pinned
model**. On the langchain version ReWOO was written against, `OpenAI()` resolves to
`text-davinci-003`, which is retired, so the headline number cannot be reproduced at all,
let alone on this platform. The two numbers that *are* reproducible are `em` and `f1`, and
neither measures the answer: the gold is a sentence, so a correct answer phrased
differently scores `em = 0` -- measured by ReWOO's own author, who added the judge because
`em` on this set is a column of zeros.

That is a **contract gap**, not an unimplemented feature. Reporting `f1` as "SOTU-QA
accuracy" would be the §1 error: a number in the right shape, measuring string overlap,
under the benchmark's name. So `load()` raises and says which field is missing.

What a future version would need
--------------------------------
Either (a) the answer contract carries a judge model pinned in the task, so `acc` is
reproducible, or (b) the dataset is scored with a **stated, pinned, local** rule and the
record says "a rule, not ReWOO's judge". Both are interface decisions. Neither is a
scorer tweak, and neither may be made silently.
"""
from __future__ import annotations

import csv

from data.scaffolds.qa import base

KIND = "dataset"

DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/sotuqa")
CSV_MEMBER = "data/SOTU/SOTU_QA.csv"
CSV_PATH = "ReWOO-9cd0283043ff4be0c9d614fda2789d143ca6ffd1/data/SOTU/SOTU_QA.csv"
N_TOTAL = 74

#: The reason `load()` refuses, also quoted by the test. One string, so the module's
#: prose and the test's assertion cannot drift.
REFUSAL = (
    "sotuqa: the benchmark's headline metric is ReWOO's `acc`, an LLM judge "
    "(`QAEvalChain.from_llm(OpenAI(temperature=0))`) with no pinned model -- its default "
    "`text-davinci-003` is retired, so the number is not reproducible here. The two "
    "reproducible metrics, `em` and `f1`, compare a whole-sentence gold "
    "(`No, Kevin is 58 while Nancy is 83`) with the prediction, so a correct answer "
    "phrased differently scores 0 and the column is all zeros. This contract carries no "
    "judge model, so it cannot produce the official number; see data/datasets/sotuqa.py for the "
    "two interface changes that would make it reachable. Refusing rather than reporting "
    "an `f1` under the benchmark's name."
)


def _rows() -> list[dict]:
    path = DATASET_ROOT / CSV_PATH
    if not path.is_file():
        raise FileNotFoundError(f"no SOTU_QA.csv at {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != N_TOTAL:
        raise ValueError(f"sotuqa: {len(rows)} rows, expected {N_TOTAL}")
    return rows


def score(prediction: str, gold: dict) -> dict:
    """Never reached: `load()` refuses before a task exists.

    Present so the refusal is a *dataset-level* decision rather than a missing function:
    the file is complete apart from the one thing the contract cannot supply.
    """
    raise NotImplementedError(REFUSAL)


#: The ReWOO tarball, pinned by commit sha. Inline rather than imported from a shared
#: module: the check materializes this adapter and the QA scaffold only, so importing
#: another QA module fails inside the verification namespace (measured).
REWOO_COMMIT = "9cd0283043ff4be0c9d614fda2789d143ca6ffd1"
REWOO_TARBALL = f"https://codeload.github.com/billxbf/ReWOO/tar.gz/{REWOO_COMMIT}"
REWOO_SHA256 = "7fe653d18faad40bf10ddcc7d85415782af975d80792bb26401393203b9ce136"


def _rewoo_entry(*, name: str, csv_size: str, note: str) -> dict:
    return {
        "kind": "http",
        "name": name,
        "version": REWOO_COMMIT,
        "url": REWOO_TARBALL,
        "size": csv_size + " of CSV (the whole tarball is ~6 MB)",
        "sha256": REWOO_SHA256,
        "dst": "rewoo.tar.gz",
        "extract_to": ".",
        "note": note,
    }


_SPEC = base.QASpec(
    name="sotuqa",
    root=DATASET_ROOT,
    rows=lambda: {"test": _rows()},
    question=lambda row: str(row["question"]),
    gold=lambda row: {"answer": str(row["answer"])},
    goal=lambda question, gold: question,
    score=score,
    downloads=lambda: [
        _rewoo_entry(
            name="ReWOO tarball (SOTU_QA.csv)",
            csv_size="22 KB",
            note="ReWOO's own curated dataset -- there is no other source, so this is "
                 "the dataset's home repository and not a mirror. Trap: the gold is a "
                 "whole sentence and the headline metric is an LLM judge, which is why "
                 "this adapter refuses to load; see the module docstring."),
        {"kind": "pip", "name": "pyarrow", "package": "pyarrow", "version": "23.0.0",
         "size": "~40 MB",
         "note": "Declared for consistency with the other QA datasets; this one is read "
                 "from CSV and does not need it, but the same adapter shape does."},
    ],
    split="test",
    source="billxbf/ReWOO@9cd0283043ff4be0c9d614fda2789d143ca6ffd1 data/SOTU/SOTU_QA.csv",
)

_BUILT = _SPEC.build()

#: Read by `tools/dataset_lint.py`. A dataset that refuses **on purpose** is not a broken
#: dataset, and the lint says so instead of failing: see the lint's `REFUSED` handling.
REFUSED = REFUSAL


def __getattr__(name: str):
    return base.module_getattr(_SPEC, name)


def load() -> tuple[list[dict], dict[str, str]]:
    raise ValueError(REFUSAL)


def downloads() -> list[dict]:
    return _SPEC._downloads()


def provision_status() -> dict:
    return _SPEC.provision_status()


__all__ = ["KIND", "DATASET_ROOT", "REFUSAL", "load", "downloads", "score"]
