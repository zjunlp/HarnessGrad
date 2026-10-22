"""Reading a staged QA table, shared by QA adapters in `data/datasets/`.

Why a shared reader and not `datasets.load_dataset`
---------------------------------------------------
The platform must be able to import a dataset adapter on a machine that has **not**
provisioned its data (`data/scaffolds/registry.py::published` imports every module to decide what
the chooser offers), so an adapter may not touch its data at import time. It must also
be able to *grade* a task without the HuggingFace `datasets` package, because the check
runs inside the verification namespace where a heavy import is a timeout. `pyarrow`
reads the parquet files the pinned revision ships; `pandas` is not needed and is not
imported.

The pinned revision is the only version there is
-----------------------------------------------
Every file is fetched from `<endpoint>/datasets/<repo>/resolve/<sha>/<file>` and
`downloads()` names that sha. `parquet_rows` takes a path that already encodes it, so
there is no second place a version could be written down.

Empty cells are `None`, not `''`
--------------------------------
A parquet column of lists comes back with `nan` for a missing value, and `nan` is
truthy -- a `if not row["aliases"]` guard would pass and the next line would iterate a
float. `_clean` turns `nan`/`NaT` into `None` at the boundary, once, so no scorer has
to remember.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

#: Rows per arrow batch when `parquet_rows(sample=…)` streams a file. The measured middle
#: of the time/peak-memory curve in that function's docstring.
_SAMPLE_BATCH = 32768


def is_missing(value) -> bool:
    """`None`, `nan`, or `NaT`. `pandas.isna` without importing pandas."""
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    return value.__class__.__name__ in ("NAType", "NaTType")


#: The key `parquet_rows(sample=…)` uses to report where a kept row came from in the file.
#: `QASpec.records()` reads it to build the task id, and that is not a nicety: a sampled
#: list is shorter than the file, so numbering it by position would make `train/7` mean one
#: question under one sample rule and a different question under another -- the id and the
#: run's `split_sha` would both stay the same while the content moved, which is the exact
#: failure the split machinery exists to prevent. Read and removed by `records()`, so a
#: record's `row` is the file's row.
SOURCE_INDEX = "_source_index"


def _clean(value):
    """One level: `nan` -> `None`, and numpy scalars -> Python.

    **Deliberately not recursive**, and measured. The recursive version walked every
    nested structure in every row: on TriviaQA's `rc.nocontext` validation split -- 17,944
    rows whose `answer` carries the full retrieved-page structure -- building the records
    took **32 s** against 2 s for the read itself, and every control run paid it. A dataset
    that needs a nested field cleaned can do it in its own `gold()`; most need one level,
    because a parquet column of lists either arrives as a list or as `nan`.
    """
    if is_missing(value):
        return None
    if hasattr(value, "tolist"):          # numpy scalar / array
        return value.tolist()
    return value


def parquet_rows(path: Path | str,
                 columns: Sequence[str] | None = None,
                 sample: int | None = None) -> list[dict]:
    """Rows of one parquet file, with missing cells normalised to `None`.

    `columns` is a projection, and on a table with a large nested column beside the two
    fields an adapter reads it is not a micro-optimization. Measured on TriviaQA's `train`
    split (138,384 rows, 55 MB): **9.46 s** for every column against **0.33 s** for
    `("question", "answer")`, because `entity_pages` and `search_results` are what the
    decode time is spent on. A projection that names a column the file does not have is an
    error, not an empty cell, so a typo surfaces here.

    `sample=k` keeps `k` rows spread evenly across the file in file order
    (`rows[i * total // k]`), and it is **a systematic sample, not a random one** -- the
    platform's random sampling is `--sampling` (INTERFACE.md §2.1) and is a different
    thing. It exists because some of these tables are ordered by something (TriviaQA's
    `train` is ordered by question source), so a caller that wants fewer rows wants them
    from across the file.

    The sample is taken **in arrow, batch by batch, before any Python object is built**,
    and that is the whole point of it existing here rather than in an adapter. Measured on
    TriviaQA's `train` (138,384 rows, `question` + `answer`):
    `to_pylist()` over the whole table is **3.55 s and ~850 MB** of nested dicts; reading
    the whole table and `take()`-ing 2,000 rows still materialises the full arrow table
    (**+310 MB**, 533 MB peak); streaming with a per-batch `take()` returns the same 2,000
    rows in **0.74 s at 236 MB peak**. The batch size is the measured middle of that curve
    (8,192 -> 1.02 s / 184 MB, 65,536 -> 0.67 s / 290 MB, whole file -> 0.64 s / 358 MB):
    a caller that wants fewer rows should not pay for all of them, and should not hold
    them either.

    Raises `FileNotFoundError` with the path in it: a check that cannot find its table
    is an *unmeasured* task, and that decision is made by the caller, not here.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no parquet file at {path}")
    if sample is not None and sample <= 0:
        raise ValueError(f"sample={sample}: a sample must name at least one row")
    columns_arg = list(columns) if columns else None

    if sample is not None:
        parquet = pq.ParquetFile(path)
        total = parquet.metadata.num_rows
        if sample < total:
            targets = [i * total // sample for i in range(sample)]
            kept: list[dict] = []
            position, start = 0, 0
            for batch in parquet.iter_batches(batch_size=_SAMPLE_BATCH,
                                              columns=columns_arg):
                end = start + batch.num_rows
                local = []
                while position < sample and targets[position] < end:
                    local.append(targets[position] - start)
                    position += 1
                if local:
                    for offset, row in zip(local, batch.take(pa.array(local)).to_pylist()):
                        # The row carries its own file position, so the task id the QA
                        # scaffold builds from it does not move when the sample rule does.
                        kept.append({**row, SOURCE_INDEX: start + offset})
                start = end
            return [{column: _clean(value) for column, value in row.items()}
                    for row in kept]

    # `to_pylist()` on the whole table, not `to_pydict()` per batch with a dict
    # comprehension per row. Measured on TriviaQA's 17,944-row validation split: 23 s for
    # the batch version against 2.4 s for this one -- the per-row `{column: ...}` loop
    # was rebuilding a dict for every column of every row, and `to_pydict()` had already
    # materialised the columns as lists.
    table = pq.read_table(path, columns=columns_arg)
    raw = table.to_pylist()
    return [{column: _clean(value) for column, value in row.items()} for row in raw]


def jsonl_rows(path: Path | str) -> list[dict]:
    """Every record of one JSONL file, with missing cells normalised to `None`."""
    import json

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no JSONL file at {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(_clean(json.loads(line)))
    return rows


def tables(root: Path | str, names: list[str]) -> list[dict]:
    """Concatenate several files under one root, in the order given.

    The order is the dataset's own file order, so two runs that declare the same files
    see the same rows in the same sequence and a `task_id` derived from the row index
    means the same thing on both.
    """
    root = Path(root)
    rows: list[dict] = []
    for name in names:
        path = root / name
        if path.suffix == ".jsonl":
            rows.extend(jsonl_rows(path))
        else:
            rows.extend(parquet_rows(path))
    return rows


__all__ = ["is_missing", "parquet_rows", "jsonl_rows", "tables"]
