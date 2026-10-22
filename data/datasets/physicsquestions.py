r"""Physics Questions as a dataset: numeric answers graded by BIG-bench's string metric.

    python3 tools/datasets.py --fetch --dataset physicsquestions
    python3 data/scaffolds/qa/control.py --dataset physicsquestions --oracle
    driver.py --dataset physicsquestions ...

Where the tasks come from
-------------------------
`data/bigbench/physics_question.csv` in `github.com/billxbf/ReWOO` at commit
`9cd0283...`, 53 rows of `input,target`, where the target is BIG-bench's **list** of
acceptable spellings:

    "During a recent morning run, Ken averaged a speed of 5.9 m/s for 14.9 minutes. ..."
    -> Can be one of ['7187 m', '7 km']

**53 rows is a fact about every number here.** The 95% confidence interval on an accuracy
at n=53 is roughly ±13 points -- wider than any difference this dataset could be used to
argue for. It is useful for mechanism attribution (does the harness use a calculator?),
not for ranking.

The metric, verbatim
--------------------
BIG-bench's `physics_questions/task.json` declares `metrics: ["exact_str_match", "bleu"]`
and its examples carry a **list** target. `exact_str_match` is BIG-bench's standard
metric: the prediction, stripped of surrounding whitespace, must equal one of the targets
exactly, after both sides strip trailing periods. So `7 km` and `7187 m` both score
for the row above, and they are **not equal numbers** (7187 m is 7.187 km) -- they are two
roundings of the same quantity, which is why the target is a list rather than a value.

**Two things this adapter deliberately does not do**, because both were tempting and both
change the number:

* it does not compare numerically with a relative tolerance. That is a *different, more
  generous* metric: it would score `7.2 km` correct for a target of `7 km`, where
  `exact_str_match` does not. A tolerance is a defensible scorer for a *different*
  question ("is the physics right?"); it is not this benchmark's number, and reporting it
  under this benchmark's name would be the §1 error.
* it does not accept the whole `Can be one of [...]` string as an answer. The list is
  BIG-bench's encoding of "any of these", not the answer text.

`bleu` is the task's second metric and is not reported: a BLEU score over a three-token
numeric answer measures tokenisation, and its corpus implementation depends on a library
this platform does not pin.
"""
from __future__ import annotations

import csv
import re

from data.scaffolds.qa import base

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"




DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/physicsquestions")
CSV_MEMBER = "data/bigbench/physics_question.csv"
CSV_PATH = ("ReWOO-9cd0283043ff4be0c9d614fda2789d143ca6ffd1/data/bigbench/"
            "physics_question.csv")
N_TOTAL = 53

#: BIG-bench's target encoding: `Can be one of ['7187 m', '7 km']`. The list is parsed
#: rather than compared as a string.
_TARGET = re.compile(r"^Can be one of \[(?P<items>.*)\]\s*$", re.I)
_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def accepted_answers(target: str) -> list[str]:
    """The acceptable spellings in a BIG-bench target.

    **Raises when the encoding is not recognised** instead of falling back to the raw
    string: comparing a prediction with `Can be one of [...]` scores every answer 0 for a
    reason nothing records, and a silent wrong number is worse than an error. Measured:
    every one of the 53 rows uses the encoding.
    """
    match = _TARGET.match(str(target).strip())
    if not match:
        raise ValueError(f"physicsquestions: target {target!r} is not `Can be one of [...]`")
    items = [a or b for a, b in _QUOTED.findall(match.group("items"))]
    if not items:
        raise ValueError(f"physicsquestions: no quoted answers in {target!r}")
    return items


# ------------------------------------------------------------------ the scorer ---


def exact_str_match(prediction: str, ground_truth: str) -> bool:
    """BIG-bench's `exact_str_match`, as its examples define it.

    Both sides strip surrounding whitespace and trailing periods, as the answer contract
    promises. Nothing else is normalised: case and units are part of the answer, which
    is why the dataset lists several spellings.
    """
    return str(prediction).strip().rstrip(".") == str(ground_truth).strip().rstrip(".")


def score(prediction: str, gold: dict) -> dict:
    accepted = list(gold.get("accepted") or [])
    hit = any(exact_str_match(prediction or "", want) for want in accepted)
    return {
        "reward": 1.0 if hit else 0.0,
        "correct": hit,
        "official": {"metric": "exact_str_match over BIG-bench's accepted spellings",
                     "accepted": accepted,
                     "prediction": str(prediction).strip()},
        "why": (f"{str(prediction).strip()!r} "
                f"{'matches' if hit else 'matches none of'} {accepted}"),
    }


# ---------------------------------------------------------------- the adapter ---


def _rows() -> list[dict]:
    path = DATASET_ROOT / CSV_PATH
    if not path.is_file():
        raise FileNotFoundError(f"no physics_question.csv at {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if str(row.get("input") or "").strip()]
    # BIG-bench's own task.json has one row with an empty input and an empty target
    # (measured: 54 examples, the first blank); ReWOO's CSV drops it. 53 is the count of
    # real questions, and the adapter asserts it so a re-extraction that changes the count
    # is noticed rather than scored.
    if len(rows) != N_TOTAL:
        raise ValueError(f"physicsquestions: {len(rows)} rows, expected {N_TOTAL}")
    return rows


def _gold(row: dict) -> dict:
    accepted = accepted_answers(str(row["target"]))
    return {"answer": accepted[0], "accepted": accepted}


def _goal(question: str, gold: dict) -> str:
    return (
        "# Task\n"
        "\n"
        f"{question}\n"
        "\n"
        "## How to answer\n"
        "\n"
        "Write the answer -- a number and its unit, for example `12.5 m` -- to\n"
        f"`{base.ANSWER_FILE}` in your working directory, or submit it with\n"
        "\n"
        f"    python3 {base.CLI_PATH} submit \"12.5 m\"\n"
        "\n"
        "Do not explain. Include the unit.\n"
        "\n"
        "## How it is scored\n"
        "\n"
        "- Your answer must equal one of the reference spellings for this question\n"
        "  (BIG-bench's `exact_str_match` for this task), allowing only surrounding\n"
        "  whitespace and a trailing period.\n"
        "- Several equivalent spellings are accepted where the dataset lists them, for\n"
        "  example `7 km` and `7187 m` for the same distance.\n"
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
    name="physicsquestions",
    root=DATASET_ROOT,
    rows=lambda: {"test": _rows()},
    question=lambda row: str(row["input"]),
    gold=_gold,
    goal=_goal,
    score=score,
    downloads=lambda: [
        _rewoo_entry(
            name="ReWOO tarball (physics_question.csv)",
            csv_size="18 KB",
            note="Trap 1: only **53** rows, so its accuracy has a ±13-point confidence "
                 "interval -- useful for mechanism attribution, not for ranking. Trap 2: "
                 "the target is a **list** of acceptable spellings (`Can be one of ['7187 "
                 "m', '7 km']`); comparing it as a string scores every answer 0, and "
                 "comparing numerically with a tolerance is a different, more generous "
                 "metric than BIG-bench's `exact_str_match`."),
        {"kind": "pip", "name": "pyarrow", "package": "pyarrow", "version": "23.0.0",
         "size": "~40 MB",
         "note": "Declared for consistency with the other QA datasets; this table is "
                 "CSV, so the check reads it with `csv`, not pyarrow."},
    ],
    split="test",
    source="billxbf/ReWOO@9cd0283043ff4be0c9d614fda2789d143ca6ffd1 "
           "data/bigbench/physics_question.csv; metric from google/BIG-bench "
           "physics_questions task.json (`exact_str_match`)",
)

_BUILT = _SPEC.build()

DEFAULT_ENV = _BUILT["DEFAULT_ENV"]
ENV = _BUILT["ENV"]
SPLIT = _BUILT["SPLIT"]

#: The benchmark's own evaluation set, within this adapter's pool. The pool is the released 53-row table, which is the whole benchmark.
#:
#: Read from `QASpec.division()`, the one place that decides what this pool's division
#: is: `None` here because the benchmark released no study split for this table, so the
#: pool **is** the scored split and every task in it belongs to the evaluation set. It
#: claims nothing about whether the pool is the whole of that set. `--split official
#: --side eval` scores the whole pool, which is the comparable run; `--split declared`
#: is the same whole pool, because there is no division to declare.
OFFICIAL_EVAL = _BUILT["OFFICIAL_EVAL"]

#: `"none"`: the benchmark states no division for this pool, so this adapter declares none
#: and the run covers every task. It used to fill the slot with the platform's hash third
#: (`"hash"`), an invented division removed by the operator on 2026-10-24; `--split default`
#: still names that rule at run time. See `data/scaffolds/qa/base.py::division`.
SPLIT_SOURCE = _BUILT["SPLIT_SOURCE"]


def __getattr__(name: str):
    return base.module_getattr(_SPEC, name)


def load() -> tuple[list[dict], dict[str, str]]:
    return _SPEC.load()


def downloads() -> list[dict]:
    return _SPEC._downloads()


def provision_status() -> dict:
    return _SPEC.provision_status()


__all__ = ["KIND", "DATASET_ROOT", "load", "downloads", "score",
           "accepted_answers", "exact_str_match", "SPLIT"]
