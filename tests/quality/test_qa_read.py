"""`data/scaffolds/qa/read.py::parquet_rows`: the projection, and the systematic sample.

The sample exists because three facts met on TriviaQA's `train` split: the file is ordered
by question source, it is 138,384 rows, and `to_pylist()` over all of it costs ~850 MB of
nested dicts. So the reader can keep `k` rows spread across the file, **in arrow, batch by
batch**, and report where each kept row came from.

Two things this test file is careful about, because both are silent when wrong:

* the rule is a **stride over the file**, not a prefix. A prefix is the cheap mistake here
  and it is not equivalent whenever the file is ordered by something -- TriviaQA's first
  1,000 rows are all one question source;
* a kept row carries its **original file position**. Without it the task id would be a
  position in the sample, and changing the sample size would re-point every id while the
  id and the run's `split_sha` stayed the same.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds.qa.read import SOURCE_INDEX, parquet_rows  # noqa: E402


@pytest.fixture(scope="module")
def table(tmp_path_factory) -> Path:
    """A parquet whose rows say which row they are, in an order that is not sorted."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path_factory.mktemp("rows") / "rows.parquet"
    # 10,000 rows, grouped like TriviaQA's: "source" changes every 1,000 rows.
    pq.write_table(pa.table({
        "n": list(range(10_000)),
        "source": [f"src{n // 1000}" for n in range(10_000)],
        "payload": [f"row-{n}" for n in range(10_000)],
    }), path, row_group_size=1_000)
    return path


def test_the_sample_is_a_stride_over_the_file_and_not_a_prefix(table):
    rows = parquet_rows(table, sample=10)
    assert [r["n"] for r in rows] == [0, 1000, 2000, 3000, 4000, 5000, 6000, 7000,
                                      8000, 9000]
    # The reason the rule is a stride: a prefix of this file is one source.
    assert len({r["source"] for r in rows}) == 10
    assert len({r["source"] for r in rows[:10]}) == 10
    prefix = parquet_rows(table, columns=("source",), sample=10)[0]["source"]
    assert prefix == "src0"


def test_a_sampled_row_reports_its_position_in_the_file(table):
    """What `QASpec.records()` turns into the task id -- see this module's docstring."""
    rows = parquet_rows(table, columns=("n",), sample=4)
    assert [r[SOURCE_INDEX] for r in rows] == [0, 2500, 5000, 7500]
    assert [r["n"] for r in rows] == [0, 2500, 5000, 7500]


def test_the_sample_agrees_with_the_full_read(table):
    """A second, independent computation of the same rule."""
    everything = parquet_rows(table, columns=("n",))
    total = len(everything)
    for k in (3, 7, 1000):
        kept = parquet_rows(table, columns=("n",), sample=k)
        assert [r["n"] for r in kept] == [everything[i * total // k]["n"]
                                          for i in range(k)]


def test_the_sample_is_deterministic(table):
    first = parquet_rows(table, columns=("n",), sample=13)
    second = parquet_rows(table, columns=("n",), sample=13)
    assert first == second


def test_asking_for_more_rows_than_the_file_has_returns_the_file(table):
    rows = parquet_rows(table, columns=("n",), sample=10_000)
    assert len(rows) == 10_000
    assert SOURCE_INDEX not in rows[0], "no sample was taken, so there is nothing to report"
    assert parquet_rows(table, columns=("n",), sample=20_000) == rows


def test_a_sample_of_nothing_is_refused_rather_than_returned_empty(table):
    """`sample=0` would answer "the file has no rows", which is a different statement."""
    with pytest.raises(ValueError, match="at least one row"):
        parquet_rows(table, sample=0)


def test_the_projection_drops_the_columns_it_was_not_asked_for(table):
    rows = parquet_rows(table, columns=("n",), sample=5)
    assert set(rows[0]) == {"n", SOURCE_INDEX}


def test_a_column_the_file_does_not_have_is_an_error(table):
    """A typo must surface here, not as a missing cell three layers down."""
    with pytest.raises(Exception):
        parquet_rows(table, columns=("n", "nope"))


def test_a_missing_file_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="no parquet file at"):
        parquet_rows(tmp_path / "absent.parquet")
