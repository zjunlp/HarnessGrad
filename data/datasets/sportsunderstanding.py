r"""Sports Understanding as a dataset: is this sports sentence plausible?

    python3 tools/datasets.py --fetch --dataset sportsunderstanding
    python3 data/scaffolds/qa/control.py --dataset sportsunderstanding --oracle
    driver.py --dataset sportsunderstanding ...

Where the tasks come from
-------------------------
`data/bigbench/sports_understanding.csv` in `github.com/billxbf/ReWOO` at commit
`9cd0283...`, 1,000 rows of `input,target`. The input is BIG-bench's own sentence and
already names the two labels (`Determine whether the following statement ... are
plausible or implausible: ...`), so the goal adds no second answer contract.

The metric, and what "official" means here
------------------------------------------
BIG-bench's `sports_understanding/task.json` scores this task with
**`multiple_choice_grade`** against `target_scores` (`{'plausible': 1, 'implausible': 0}`
or the reverse), not with a free-text metric. ReWOO's copy flattened that into a
`target` column holding the winning label, which is what this table carries.

`multiple_choice_grade` for a two-way generative task is "does the response identify the
correct choice". The rule transcribed in the test is the narrowest one that does that
without guessing: the answer must identify the correct label, case-insensitively,
with an immediately negated label interpreted as its complement (see `_chosen_labels`).

**The one honest weakness, stated here and not discovered later.** A response containing
**both** labels (`it could be plausible or implausible`) satisfies a substring rule for
either gold and is therefore counted correct for both. That is the same failure
`data/datasets/fever.py` describes for a `contains` metric, and it is inherent to substring
matching, not an implementation choice. It is reported rather than hidden: this adapter
treats a two-label answer as correct for neither, because an answer that names both
choices did not choose -- and that **differs from a plain substring rule**, so the test
pins both the rule and the divergence.

There is no third option in the table: every target is one of the two labels.
"""
from __future__ import annotations

import csv
import re

from data.scaffolds.qa import base

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"




DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/sportsunderstanding")
CSV_MEMBER = "data/bigbench/sports_understanding.csv"
CSV_PATH = ("ReWOO-9cd0283043ff4be0c9d614fda2789d143ca6ffd1/data/bigbench/"
            "sports_understanding.csv")
N_TOTAL = 1000
LABELS = ("plausible", "implausible")


# ------------------------------------------------------------------ the scorer ---


def substring_choice(prediction: str, label: str) -> bool:
    """Is the label named as a **word** in the answer?

    Not a bare `label in answer`, and the difference is measured: `"implausible"` contains
    `"plausible"` as a substring, so the naive rule scores the answer `implausible` as
    correct for a gold of `plausible` -- a silent 100% on one of the two classes. Word
    boundaries fix it without changing what the rule means.
    """
    pattern = r"(?<![A-Za-z])" + re.escape(str(label).lower()) + r"(?![A-Za-z])"
    return re.search(pattern, str(prediction).lower()) is not None


def names_both(prediction: str) -> bool:
    """Does the answer name both choices? Then it did not choose."""
    return all(substring_choice(prediction, label) for label in LABELS)


def _chosen_labels(prediction: str) -> set[str]:
    """Resolve label words, mapping immediate negation to the complementary label.

    Handle `not plausible`, `no plausible`, `n't plausible`, and contractions such as
    `isn't plausible` / `wasn't plausible` (also curly apostrophes), and the same forms
    before `implausible`. Whitespace may separate the negator and label; matching is
    case-insensitive. Conflicting resolved choices are not a single choice.

    This is not a grammar parser: it does not handle double negation, intervening words
    (`not very plausible`), negation after a label (`plausible it isn't`), synonyms
    (`hardly plausible`), or sarcasm. `names_both` still rejects both literal labels.
    """
    text = str(prediction).lower()
    choices = set()
    for match in re.finditer(r"(?<![A-Za-z])(plausible|implausible)(?![A-Za-z])", text):
        label = match.group(1)
        negated = re.search(r"(?<![A-Za-z])(?:not|no|[A-Za-z]*n['’]t)\s+$",
                            text[:match.start()])
        choices.add(LABELS[1 - LABELS.index(label)] if negated else label)
    return choices


def score(prediction: str, gold: dict) -> dict:
    want = str(gold.get("answer", "")).lower()
    both = names_both(prediction or "")
    choices = _chosen_labels(prediction or "")
    hit = (not both) and choices == {want}
    return {
        "reward": 1.0 if hit else 0.0,
        "correct": hit,
        "official": {"metric": "multiple_choice_grade (the correct label identified)",
                     "label": want,
                     "substring_rule": substring_choice(prediction or "", want),
                     "named_both_labels": both,
                     "interpreted_labels": sorted(choices)},
        "why": (
            f"{'identifies' if hit else 'does not identify'} {want!r}"
            + ("; the answer names both labels, so it did not choose" if both else "")
        ),
    }


# ---------------------------------------------------------------- the adapter ---


def _rows() -> list[dict]:
    path = DATASET_ROOT / CSV_PATH
    if not path.is_file():
        raise FileNotFoundError(f"no sports_understanding.csv at {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != N_TOTAL:
        raise ValueError(f"sportsunderstanding: {len(rows)} rows, expected {N_TOTAL}")
    return rows


def _gold(row: dict) -> dict:
    label = str(row["target"]).strip().lower()
    if label not in LABELS:
        raise ValueError(f"sportsunderstanding: target {label!r} is not one of {LABELS}")
    return {"answer": label}


def _goal(question: str, gold: dict) -> str:
    return (
        "# Task\n"
        "\n"
        f"{question}\n"
        "\n"
        "## How to answer\n"
        "\n"
        "Write one word -- `plausible` or `implausible` -- to "
        f"`{base.ANSWER_FILE}` in your working directory, or submit it with\n"
        "\n"
        f"    python3 {base.CLI_PATH} submit plausible\n"
        "\n"
        f"## How it is scored\n"
        "\n"
        "- The answer must name the correct one of the two labels\n"
        "  (BIG-bench's `multiple_choice_grade` for this task).\n"
        "- An answer that names **both** labels did not choose and scores 0.\n"
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
    name="sportsunderstanding",
    root=DATASET_ROOT,
    rows=lambda: {"test": _rows()},
    question=lambda row: str(row["input"]),
    gold=_gold,
    goal=_goal,
    score=score,
    downloads=lambda: [
        _rewoo_entry(
            name="ReWOO tarball (sports_understanding.csv)",
            csv_size="138 KB",
            note="The table comes from ReWOO's vendored copy; the *scorer* is "
                 "BIG-bench's `sports_understanding` task, whose `target_scores` is what "
                 "ReWOO flattened into the `target` column. Trap: a substring rule counts "
                 "an answer naming both labels for either gold, so this adapter refuses "
                 "that case and the test pins the divergence."),
        {"kind": "pip", "name": "pyarrow", "package": "pyarrow", "version": "23.0.0",
         "size": "~40 MB",
         "note": "Declared for consistency with the other QA datasets; this table is "
                 "CSV, so the check reads it with `csv`, not pyarrow."},
    ],
    split="test",
    source="billxbf/ReWOO@9cd0283043ff4be0c9d614fda2789d143ca6ffd1 "
           "data/bigbench/sports_understanding.csv; metric from "
           "google/BIG-bench sports_understanding task.json",
)

_BUILT = _SPEC.build()

DEFAULT_ENV = _BUILT["DEFAULT_ENV"]
ENV = _BUILT["ENV"]
SPLIT = _BUILT["SPLIT"]

#: The benchmark's own evaluation set, within this adapter's pool. The pool is the released 1,000-row table, which is the whole benchmark.
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


__all__ = ["KIND", "DATASET_ROOT", "LABELS", "load", "downloads", "score",
           "substring_choice", "names_both", "SPLIT"]
