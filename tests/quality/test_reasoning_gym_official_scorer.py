"""Reasoning Gym's official scorer: a **call**, pinned, plus the rule that chose the families.

Most datasets in this repository need a scorer transcribed verbatim from upstream, because the
only released form of the metric is source code that has to be copied into
`data/datasets/<name>/verify/`. Reasoning Gym is the other case and it is worth naming: the metric **is**
a method on the library's own dataset object (`Dataset.score_answer(answer, entry) -> float`),
so the faithful port is not a copy but a call -- which is what `data/datasets/chessbench_puzzles.py`
does with the official replay loop, and for the same reason: a copy can drift from the source,
and this repository has already paid for one drift (skill doc §15.5, the two GSM8K filters).

So this file pins four things, each of which fails on its own if the pin moves:

* **the pin** -- `reasoning-gym==0.1.25`, one seed, twenty items per family;
* **the pool** -- its digest, and that regenerating a sample reproduces the stored rows;
* **the call** -- `score()` compared against the library called independently here, so a
  reimplementation would have to agree with the source to pass;
* **the family rule** -- one task per family, reference answer to 1.0 and empty answer to 0.0.
  That rule is what selected the 89 families out of 106 (see `prepare.py`), and it is what
  keeps gate 2 honest: a family whose scorer accepted an empty answer would hand the do-nothing
  control reward 1.

The reward is **not always 0 or 1** -- several families return partial credit -- so the
comparisons are on floats.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data" / "datasets" / "reasoning_gym"))

import prepare  # noqa: E402
from data.datasets import reasoning_gym as rg  # noqa: E402


@pytest.fixture(scope="module")
def rows() -> dict[tuple[str, int], dict]:
    """`{(family, index): row}` -- the pool's own coordinate, which is what a pool row
    carries and what `prepare._entry(family, index)` takes.

    Deliberately **not** keyed by the platform's task id. The platform numbers the pool by
    position (`reasoning_gym/eval/<n>`, from `QASpec.records()`, which sets `index` to the
    row's file position because this reader reports no `SOURCE_INDEX`); the first version of
    this file keyed by a `task_id` field that the pool rows used to carry, and that field
    disagreed with the platform (`reasoning_gym/<family>/<index>`), so the lookup raised
    `KeyError` for every sample. The field is gone -- one id in play, and it is the
    platform's.

    A **row** here is what `_rows()` yields, not the record: `records()` wraps each row in
    `{task_id, index, split, row, question, gold}`, and `_question`/`_gold` are written
    against the inner row.
    """
    if not (rg.DATASET_ROOT / rg.POOL).is_file():
        pytest.skip("reasoning_gym: no pool; run `python3 data/datasets/reasoning_gym/prepare.py`")
    return {(r["row"]["family"], r["row"]["index"]): r["row"] for r in rg._SPEC.ensure()}


# ------------------------------------------------------------------- the pin ---


def test_the_installed_version_is_the_pin():
    """A scorer change in a different release is a different dataset."""
    assert importlib.metadata.version(rg.PACKAGE) == rg.VERSION


def test_the_pool_matches_its_recorded_digest():
    """A byte of drift in the pool is a different task set under the same ids."""
    body = (rg.DATASET_ROOT / rg.POOL).read_bytes()
    assert hashlib.sha256(body).hexdigest() == rg.POOL_SHA256


def test_the_pool_regenerates_identically_on_a_sample(rows):
    """Determinism across processes. The whole pool costs 15.6 s to regenerate -- the very
    measurement that put it on disk -- so a sample is what a test can afford, and the digest
    above is what covers the remaining 1,778 rows."""
    for family in ("chain_sum", "letter_counting", "maze"):
        for index in (0, rg.PER_FAMILY - 1):
            fresh = prepare._entry(family, index)
            assert fresh == rows[(family, index)], f"{family}/{index} regenerated differently"


def test_the_pool_is_the_declared_size(rows):
    """89 families × 20: a silent shrink would be invisible in the ids alone."""
    assert len(prepare.FAMILIES) == 89
    assert len(rows) == len(prepare.FAMILIES) * prepare.PER_FAMILY
    # The platform's id is the pool **position**, and the pool is family-major, so the
    # id of `(family, index)` is arithmetic. Pinned because the fixture above no longer
    # reads an id out of the rows: if `records()` ever stopped numbering by file position,
    # this is where it would show up rather than in a task that silently changed meaning.
    records = rg._SPEC.ensure()
    for family, index in (("chain_sum", 0), ("maze", rg.PER_FAMILY - 1)):
        position = list(prepare.FAMILIES).index(family) * rg.PER_FAMILY + index
        assert records[position]["task_id"] == f"reasoning_gym/eval/{position}"
        assert (records[position]["row"]["family"], records[position]["row"]["index"]) \
            == (family, index)


def test_the_pool_declares_no_division_and_is_not_an_evaluation_set():
    """A generated training corpus, declared as one: no split, and no official eval set.

    The adapter used to declare a hash third of it as the exam, which made a training
    corpus look like a benchmark with a held-out split. Removed 2026-10-24.
    """
    assert rg.SPLIT is None and rg.SPLIT_SOURCE == "none"
    assert rg.OFFICIAL_EVAL == []


# ---------------------------------------------------------------- the scorer ---


def test_the_adapter_calls_the_library_scorer_and_not_a_copy(rows):
    """Compared against the library, called independently here."""
    from reasoning_gym import create_dataset

    for coord in list(rows)[:3]:
        gold = rg._gold(rows[coord])
        dataset = create_dataset(gold["family"], size=rg.PER_FAMILY, seed=rg.SEED)
        for prediction in (gold["answer"], "", "certainly not the answer"):
            expected = dataset.score_answer(answer=prediction, entry=gold["entry"])
            assert rg.score(prediction, gold)["reward"] == pytest.approx(float(expected)), \
                (coord, prediction)


def test_a_gold_with_no_family_is_reported_rather_than_scored():
    """A gold that did not come from this pool must not be scored as if it had."""
    verdict = rg.score("anything", {"answer": "x"})
    assert verdict["reward"] == 0.0
    assert "no family" in verdict["why"]


@pytest.mark.parametrize("family", sorted(prepare.FAMILIES))
def test_every_declared_family_scores_its_reference_answer_and_the_empty_one(family, rows):
    """The selection rule, re-checked against the pin for every family it admitted.

    Reference to 1.0 and empty to 0.0 is what makes gate 1 (oracle -> 1) and gate 2 (do
    nothing -> 0) true here. A family that failed either was excluded in `prepare.py`; this
    test is what notices if a later pin changes one of them.
    """
    from reasoning_gym import create_dataset

    gold = rg._gold(next(r for r in rows.values() if r["family"] == family))
    dataset = create_dataset(family, size=rg.PER_FAMILY, seed=rg.SEED)
    assert float(dataset.score_answer(answer=gold["answer"], entry=gold["entry"])) == 1.0
    assert float(dataset.score_answer(answer="", entry=gold["entry"])) == 0.0
