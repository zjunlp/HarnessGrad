"""TriviaQA's study side, and the two decisions it needed.

The benchmark publishes both sides, so this adapter takes TriviaQA's division instead of
the platform's hash: study its `train` split, report on `validation` (the scored one --
`test` withholds its gold). Two measurable facts then decide *how* the `train` side is
loaded, and this file is where both are checked rather than asserted in a comment:

* `train` is 138,384 rows and **ordered by question source**. A prefix would study one
  question writer and be scored on a validation split that has all of them, so the sample
  is an even stride across the file;
* a sampled list is shorter than the file, so the task id has to carry the **file**
  position. Otherwise `triviaqa/train/7` keeps its id and changes its question when the
  sample size changes -- same id, same `split_sha`, different task.

Everything about the scorer stays in `test_triviaqa_official_scorer.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import data.scaffolds.registry as registry  # noqa: E402


@pytest.fixture(scope="module")
def triviaqa():
    import data.datasets.triviaqa as module
    if not module._RECORDS:
        pytest.skip("triviaqa: not provisioned")
    return module


@pytest.fixture(scope="module")
def train_question_ids(triviaqa) -> list[str]:
    """Every `question_id` of the train file, one string column, read independently."""
    from data.scaffolds.qa.read import parquet_rows

    path = triviaqa.DATASET_ROOT / triviaqa.SPLIT_FILES["train"]
    return [row["question_id"] for row in parquet_rows(path, columns=("question_id",))]


def test_the_pool_is_triviaqas_own_division(triviaqa):
    """Both sides, the benchmark's own sizes, and the platform imposing nothing."""
    assert triviaqa.SPLIT_SOURCE == "declared"
    assert len(triviaqa.SPLIT["train"]) == triviaqa.STUDY_ROWS == 2000
    assert len(triviaqa.SPLIT["eval"]) == 17_944, "the whole official validation split"
    assert all("/train/" in t for t in triviaqa.SPLIT["train"])
    assert all("/validation/" in t for t in triviaqa.SPLIT["eval"])


def test_official_eval_is_the_validation_split_and_not_the_sample(triviaqa):
    """`--split official --side train` must study, not silently report on, the sample."""
    assert triviaqa.OFFICIAL_EVAL is not None
    assert set(triviaqa.OFFICIAL_EVAL) == set(triviaqa.SPLIT["eval"])
    assert not (set(triviaqa.OFFICIAL_EVAL) & set(triviaqa.SPLIT["train"]))


def test_the_study_ids_are_positions_in_the_train_file(triviaqa, train_question_ids):
    """The differential check of the sample rule, against an independent computation.

    The ids must be exactly `i * total // 2000`, and each must name the file row whose
    `question` is the one the record carries -- so the id is checkable, not decorative.
    """
    total = len(train_question_ids)
    assert total == 138_384, "the pinned `rc.nocontext` train split"
    want = {i * total // triviaqa.STUDY_ROWS: i for i in range(triviaqa.STUDY_ROWS)}
    got = {int(t.rsplit("/", 1)[1]): t for t in triviaqa.SPLIT["train"]}
    assert got == {i: f"triviaqa/train/{i}" for i in want}, "ids are file positions"

    from data.scaffolds.qa.read import parquet_rows

    path = triviaqa.DATASET_ROOT / triviaqa.SPLIT_FILES["train"]
    questions = {row["question"].strip() for row in
                 parquet_rows(path, columns=("question",), sample=triviaqa.STUDY_ROWS)}
    records = {r["task_id"]: r for r in triviaqa._RECORDS if r["split"] == "train"}
    assert len(records) == triviaqa.STUDY_ROWS
    assert {r["question"] for r in records.values()} == questions


def test_the_sample_spans_the_file_sources_unlike_a_prefix(triviaqa, train_question_ids):
    """The measurement that chose the rule, kept as an assertion about the rule.

    A prefix is not merely uglier here: the first 2,000 rows of this file are **one**
    question source, so a prefix studies one writer's style. This is the test that fails if
    somebody "simplifies" the sample back into a prefix.
    """
    total = len(train_question_ids)
    sampled = {train_question_ids[i * total // triviaqa.STUDY_ROWS][:2]
               for i in range(triviaqa.STUDY_ROWS)}
    prefix = {q[:2] for q in train_question_ids[:triviaqa.STUDY_ROWS]}
    assert len(sampled) >= 8, sorted(sampled)
    assert len(prefix) == 1, "if the file ever stops being source-ordered, revisit this"


def test_the_declared_division_is_not_the_platform_hash_division(triviaqa):
    tasks = triviaqa.load()[0]
    default = registry.default_split(tasks)
    assert registry.split_sha(default) != registry.split_sha(triviaqa.SPLIT)
    official = registry.official_split("triviaqa", tasks)
    assert registry.split_sha(official) == registry.split_sha(triviaqa.SPLIT)


def test_the_unloaded_train_rows_are_stated_where_a_reader_meets_them(triviaqa):
    """The pool is a sample of `train`, and that has to be visible without reading code.

    Both places a reader arrives from are checked -- the module docstring and the provenance
    string the tools print -- because "the study side is the official train split" is the
    natural reading and it would be wrong here.
    """
    assert triviaqa.STUDY_ROWS < 138_384
    assert "2,000 rows by an even" in triviaqa.__doc__
    assert "sampled to 2,000 rows" in triviaqa._SPEC.source
    assert "Why an even spread and not the first 2,000 rows" in triviaqa._rows.__doc__
