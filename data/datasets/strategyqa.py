r"""StrategyQA as a dataset: yes/no questions whose official metric is exact string match.

    python3 tools/datasets.py --fetch --dataset strategyqa
    python3 data/scaffolds/qa/control.py --dataset strategyqa --oracle
    driver.py --dataset strategyqa ...

Where the tasks come from
-------------------------
`data/bigbench/strategy_qa.csv` in `github.com/billxbf/ReWOO` at commit `9cd0283...`,
2,290 rows of `input,target`. The input carries its own answer contract verbatim
(`Answer with 'Yes' or 'No': ...`), so the goal does not add one -- two answer contracts
in one prompt is how a model ends up following the one the scorer is not reading.

**Why not BIG-bench main.** Measured: the task directory is *gone* from
`google/BIG-bench` main (`strategy_qa` was removed). ReWOO's vendored copy is the
reachable one, and it is the copy whose numbers appear in that paper, so using it keeps
the two comparable. the same tarball (pinned by commit sha) serves all four datasets that
take a table from it.

The metric, verbatim
--------------------
The dataset's own home no longer ships a scorer, so the reference is ReWOO's
`utils/Evaluator.py` -- the code that produced the published numbers for this table:

    em = normalize_answer(pred) == normalize_answer(label)

`normalize_answer` is lower-case, strip punctuation, remove articles, white-space fix.
The comparison is therefore **lenient about form and strict about content**: `yes.`,
`Yes` and `YES` all score, while `Yes, because ...` does not -- because the whole string
is compared, not the first token. That is ReWOO's rule and it is transcribed in the test
rather than paraphrased.

A note on the other two numbers in that Evaluator
-------------------------------------------------
It also reports `f1` and an LLM `acc`. `f1` on a one-token gold is a reward for writing
extra words, and `acc` needs an unpinned judge. This adapter computes neither; `em` is the
reproducible, published one.
"""
from __future__ import annotations

import csv
import re
import string

from data.scaffolds.qa import base

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"




DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/strategyqa")
CSV_MEMBER = "data/bigbench/strategy_qa.csv"
CSV_PATH = "ReWOO-9cd0283043ff4be0c9d614fda2789d143ca6ffd1/data/bigbench/strategy_qa.csv"
N_TOTAL = 2290
LABELS = ("Yes", "No")


# ------------------------------------------------------------------ the scorer ---


def normalize_answer(s: str) -> str:
    """ReWOO `utils/Evaluator.py::normalize_answer`, verbatim."""

    def remove_articles(text: str) -> str:
        return re.sub(r"\b(a|an|the)\b", " ", text)

    def white_space_fix(text: str) -> str:
        return " ".join(text.split())

    def remove_punc(text: str) -> str:
        return "".join(ch for ch in text if ch not in set(string.punctuation))

    def lower(text: str) -> str:
        return text.lower()

    return white_space_fix(remove_articles(remove_punc(lower(str(s)))))


def exact_match(prediction: str, ground_truth: str) -> bool:
    """ReWOO's `em`: normalised string equality, over the **whole** answer."""
    return normalize_answer(prediction) == normalize_answer(ground_truth)


def score(prediction: str, gold: dict) -> dict:
    want = str(gold.get("answer", ""))
    hit = exact_match(prediction or "", want)
    return {
        "reward": 1.0 if hit else 0.0,
        "correct": hit,
        "official": {"metric": "exact_match after ReWOO's normalize_answer",
                     "label": want,
                     "normalized_prediction": normalize_answer(prediction or "")},
        "why": (f"{'matches' if hit else 'no match for'} "
                f"{normalize_answer(prediction or '')!r} vs {normalize_answer(want)!r}; "
                f"the whole answer is compared, not its first token"),
    }


# ---------------------------------------------------------------- the adapter ---


def _rows() -> list[dict]:
    path = DATASET_ROOT / CSV_PATH
    if not path.is_file():
        raise FileNotFoundError(f"no strategy_qa.csv at {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != N_TOTAL:
        raise ValueError(f"strategyqa: {len(rows)} rows, expected {N_TOTAL}")
    return rows


def _gold(row: dict) -> dict:
    label = str(row["target"]).strip()
    if label not in LABELS:
        raise ValueError(f"strategyqa: target {label!r} is not one of {LABELS}")
    return {"answer": label}


def _goal(question: str, gold: dict) -> str:
    return (
        "# Task\n"
        "\n"
        f"{question}\n"
        "\n"
        "## How to answer\n"
        "\n"
        f"Write your answer to `{base.ANSWER_FILE}` in your working directory, or submit "
        f"it with\n"
        "\n"
        f"    python3 {base.CLI_PATH} submit \"Yes\"      # or \"No\"\n"
        "\n"
        "The question above already states the answer format; follow it exactly. The\n"
        "**whole** answer is compared, so an explanation turns a correct answer into a\n"
        "wrong one.\n"
        "\n"
        "## How it is scored\n"
        "\n"
        "- Your answer and the reference are lower-cased, stripped of punctuation and\n"
        "  articles, and white-space normalised, then compared as whole strings\n"
        "  (ReWOO's `em` for this table).\n"
        "- Nothing else you print is read.\n"
    )


#: The ReWOO tarball these tables come from, pinned by commit sha. Declared inline in
#: each adapter rather than imported from a shared module: the *check* materializes the
#: adapter and the QA scaffold only, so an adapter that imported another QA module would
#: fail inside the verification namespace (measured: `ImportError: cannot import name
#: 'rewoo'` from the materialized `data.scaffolds.qa`). Three constant lines are cheaper than a
#: fourth file in every `VERIFY.inputs`.
REWOO_COMMIT = "9cd0283043ff4be0c9d614fda2789d143ca6ffd1"
REWOO_TARBALL = "https://codeload.github.com/billxbf/ReWOO/tar.gz/9cd0283043ff4be0c9d614fda2789d143ca6ffd1"
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
    name="strategyqa",
    root=DATASET_ROOT,
    rows=lambda: {"test": _rows()},
    question=lambda row: str(row["input"]),
    gold=_gold,
    goal=_goal,
    score=score,
    downloads=lambda: [
        _rewoo_entry(
            name="ReWOO tarball (strategy_qa.csv)",
            csv_size="200 KB",
            note="Trap: `strategy_qa` has been removed from `google/BIG-bench` main "
                 "(measured: the task directory is gone), so ReWOO's vendored copy is the "
                 "reachable table -- and the one its published numbers came from. Pinned "
                 "by the commit sha pinned in this file."),
        {"kind": "pip", "name": "pyarrow", "package": "pyarrow", "version": "23.0.0",
         "size": "~40 MB",
         "note": "Declared for consistency with the other QA datasets; this table is "
                 "CSV, so the check reads it with `csv`, not pyarrow."},
    ],
    split="test",
    source="billxbf/ReWOO@9cd0283043ff4be0c9d614fda2789d143ca6ffd1 "
           "data/bigbench/strategy_qa.csv",
)

_BUILT = _SPEC.build()

DEFAULT_ENV = _BUILT["DEFAULT_ENV"]
ENV = _BUILT["ENV"]
SPLIT = _BUILT["SPLIT"]

#: The benchmark's own evaluation set, within this adapter's pool. The pool is the released 2,290-row test table, which is the whole benchmark.
#:
#: Read from `QASpec.division()`, the one place that decides what this pool's division
#: is: `None` here because the benchmark released no study split for this table, so the
#: pool **is** the scored split and every task in it belongs to the evaluation set. It
#: claims nothing about whether the pool is the whole of that set. `--split official
#: --side eval` scores the whole pool, which is the comparable run; `--split declared`
#: keeps the platform's hash third (`SPLIT_SOURCE == "hash"`).
OFFICIAL_EVAL = _BUILT["OFFICIAL_EVAL"]

#: Which rule produced `SPLIT`: `"hash"` here -- the benchmark published no study split,
#: so the platform imposes one. See `data/scaffolds/qa/base.py::division`.
SPLIT_SOURCE = _BUILT["SPLIT_SOURCE"]


def __getattr__(name: str):
    return base.module_getattr(_SPEC, name)


def load() -> tuple[list[dict], dict[str, str]]:
    return _SPEC.load()


def downloads() -> list[dict]:
    return _SPEC._downloads()


def provision_status() -> dict:
    return _SPEC.provision_status()


__all__ = ["KIND", "DATASET_ROOT", "LABELS", "load", "downloads", "score",
           "normalize_answer", "exact_match", "SPLIT"]
