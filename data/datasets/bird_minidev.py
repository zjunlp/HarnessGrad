"""BIRD Mini-Dev (SQLite): 500 text-to-SQL tasks, the official **EX** metric.

    python3 tools/datasets.py --fetch --dataset bird_minidev
    python3 data/datasets/bird_minidev/control.py --oracle --platform
    python3 tools/dataset_lint.py --dataset bird_minidev

Where the data is
-----------------
`minidev.zip` (800,943,648 bytes) from the official OSS mirror, pinned by sha256 *and* by
the object's ETag/Last-Modified (`2024-06-20/7BEB6DAB…`), unpacked to
`_src/unpacked/minidev/MINIDEV/`:

* `mini_dev_sqlite.json` -- 500 questions with `question_id`, `db_id`, `question`,
  `evidence`, `difficulty` and a `SQL` field;
* `mini_dev_sqlite_gold.sql` -- the same 500 gold queries as `SQL<TAB>db_id` lines, which is
  what the official evaluator reads (`evaluation_utils.py::package_sqls`, mode `"gt"`);
* `dev_databases/<db_id>/<db_id>.sqlite` -- 11 databases, 1.4 GB;
* `dev_tables.json` -- the official schema (tables, columns, types, primary and foreign
  keys) and `database_description/<table>.csv` -- per-column descriptions.

The two released gold channels are checked against each other rather than trusted: the JSON
`SQL` equals the `.sql` line for **all 500** rows, every gold line carries exactly one tab,
and every gold line's `db_id` equals its question's. The adapter reads the `.sql` file,
because that is the file the official evaluator reads.

What the agent sees, and what it does not
----------------------------------------
The question, the released `evidence` hint, and the **official schema** rendered from
`dev_tables.json` plus the per-column descriptions -- the inputs BIRD's own setting gives a
model. It does **not** get the database, for two reasons: BIRD's task is to write SQL from
the schema, and the database contains the rows that answer the question. It does not get the
gold SQL, and `difficulty` travels in the gold rather than the prompt (the benchmark
publishes EX per difficulty, so the platform can too).

The metric, and the two it does not report
------------------------------------------
Reward is per-question **EX**, and the official comparison is transcribed verbatim in
`data/datasets/bird_minidev/verify/check.py` from
`bird-bench/mini_dev@abd11b6 evaluation/evaluation_ex.py` lines 18-22. That file's execution
semantics are upstream's as well (predicted query, then gold, `fetchall()`, set equality,
timeout -> 0), with **one deliberate difference**: upstream scores a *gold* query that fails
as a wrong answer, and this check reports `measured: false` instead, because a broken
environment and a wrong answer must not look the same (`docs/dataset-onboarding-skill.md`
§5). Soft-F1 (`evaluation_f1.py`) and R-VES (`evaluation_ves.py`) are the benchmark's other
two official columns over the same query pair; they are **not** reported, and the evidence
names `EX` so a published number cannot be mistaken for one of them.

No division is declared (`SPLIT is None`, `SPLIT_SOURCE == "none"`, `OFFICIAL_EVAL is None`):
Mini-Dev is one released pool and publishes no study split, so the pool **is** the scored
split, and the run says so rather than implying the pool is the whole benchmark. The adapter
used to declare the platform's hash third here; that invented division was removed on
2026-10-24 and `--split default` is how a run asks for it now.
"""
from __future__ import annotations

from data.scaffolds.scorer_input import bind as bind_scorer_input

import csv
import io
import json
from pathlib import Path


KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"

DATASET_ROOT = Path("/mnt/20t/xubuqiang/datasets/bird_minidev")
RELEASE = "2024-06-20/7BEB6DAB11E65F0FDE563E644A1EA319"
DATA_SHA256 = "cc48ba16838204e4e214512030cb572eeb5f7bcdd999bae4b9b6ff12ec13b92f"
DATA_MD5 = "7beb6dab11e65f0fde563e644a1ea319"
EVALUATION_COMMIT = "abd11b6db92a1c9f809b32f7564c7c71b34d67f0"
EVALUATION_SHA256 = "e70bcf57ed7dcc1ab4a55e38a14fa12d8bf7ff0cc2e66db673e4d55b16f5050b"

UNPACKED = DATASET_ROOT / "_src" / "unpacked" / "minidev" / "MINIDEV"
QUESTIONS = UNPACKED / "mini_dev_sqlite.json"
GOLD = UNPACKED / "mini_dev_sqlite_gold.sql"
TABLES = UNPACKED / "dev_tables.json"
DBS = UNPACKED / "dev_databases"
CLI = Path(__file__).resolve().parent / "bird_minidev" / "cli" / "bird"
EXPECTED_ROWS = 500


# --------------------------------------------------------------------------- the table ---


def _rows() -> list[dict]:
    """One record per released question, with the gold query the official evaluator reads."""
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    golds = [line.split("\t", 1)[0] for line in GOLD.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if len(questions) != EXPECTED_ROWS or len(golds) != EXPECTED_ROWS:
        raise ValueError(f"expected exactly {EXPECTED_ROWS} questions and gold queries, "
                         f"found {len(questions)} and {len(golds)}")
    return [{"task_id": f"bird_minidev/{index}", "index": index,
             "question_id": row["question_id"], "db_id": row["db_id"],
             "question": row["question"], "evidence": (row.get("evidence") or "").strip(),
             "difficulty": row.get("difficulty", ""), "gold_sql": gold}
            for index, (row, gold) in enumerate(zip(questions, golds))]


_RECORDS: list[dict] = []
_LOAD_ERROR: str | None = None
try:
    _RECORDS = _rows()
except Exception as _exc:  # noqa: BLE001 - reported by load(), which is where it bites
    _RECORDS = []
    _LOAD_ERROR = f"{type(_exc).__name__}: {_exc}"


def _ensure() -> list[dict]:
    """The table, or an error that says how to provision it.

    Read at import **inside a guard**, the pattern `data/datasets/spreadsheetbench.py` uses, because
    `data/scaffolds/registry.py::published()` imports every module in `data/` to decide what the
    Launch form offers and that has to work on a machine where `--fetch` has not run. The
    first version read the table unguarded, so an unprovisioned machine silently dropped
    this dataset from `published()` -- 27 datasets became 26 with no error anywhere.
    """
    if _RECORDS:
        return _RECORDS
    raise ValueError(
        f"bird_minidev: cannot read {UNPACKED} ({_LOAD_ERROR}). Provision it with "
        f"`python3 tools/datasets.py --fetch --dataset bird_minidev`")


def _ids() -> list[str]:
    return [record["task_id"] for record in _RECORDS]


# ------------------------------------------------------------------ the agent's world ---


_DESCRIPTION_CACHE: dict[str, dict[str, str]] = {}


def _descriptions(db_id: str) -> dict[str, str]:
    """`{column: note}` from the release's per-database description CSVs.

    Keyed by the *original* column name, because that is the name `dev_tables.json` carries,
    and cached because 500 tasks share 11 databases.

    The encoding ladder is measured, not defensive: **4 of the 75** description files are
    cp1252 rather than UTF-8 (`european_football_2/Player_Attributes.csv` and three others,
    each failing at a `0x95` bullet), and one of them is reached by a task in this pool. A
    reader that assumed UTF-8 made the whole dataset unimportable -- the schema renderer runs
    inside `SETUP` -- so the first version's prompt would have raised for those tasks.
    """
    if db_id not in _DESCRIPTION_CACHE:
        notes: dict[str, str] = {}
        directory = DBS / db_id / "database_description"
        if directory.is_dir():
            for path in sorted(directory.glob("*.csv")):
                text = None
                for encoding in ("utf-8-sig", "cp1252", "latin-1"):
                    try:
                        text = path.read_text(encoding=encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                if text is None:                        # pragma: no cover - latin-1 never fails
                    continue
                for row in csv.DictReader(io.StringIO(text)):
                    column = (row.get("original_column_name") or "").strip()
                    if not column:
                        continue
                    description = (row.get("column_description") or "").strip()
                    values = (row.get("value_description") or "").strip()
                    note = description or (row.get("data_format") or "").strip()
                    if values and values != description:
                        note = f"{note}; values: {values}" if note else f"values: {values}"
                    notes[column] = note
        _DESCRIPTION_CACHE[db_id] = notes
    return _DESCRIPTION_CACHE[db_id]


def schema_text(db_id: str) -> str:
    """The official schema as DDL, from `dev_tables.json` plus the column descriptions.

    Deterministic: the release's own table and column order, so a task's prompt is a
    function of the release rather than of a dict iteration.
    """
    entry = next((row for row in json.loads(TABLES.read_text(encoding="utf-8"))
                  if row["db_id"] == db_id), None)
    if entry is None:
        raise KeyError(f"bird_minidev: {db_id} is not in dev_tables.json")
    names = entry["table_names_original"]
    columns = entry["column_names_original"]          # [[table_index, name], ...]
    types = entry["column_types"]
    notes = _descriptions(db_id)
    by_table: dict[int, list[int]] = {index: [] for index in range(len(names))}
    for index, (table_index, _name) in enumerate(columns):
        if table_index >= 0:
            by_table[table_index].append(index)

    def flatten(values) -> list[int]:
        out: list[int] = []
        for value in values:
            out.extend(value if isinstance(value, (list, tuple)) else [value])
        return out

    primary = flatten(entry["primary_keys"])
    lines = [f"Database: {db_id}", ""]
    for table_index, table in enumerate(names):
        body = []
        for index in by_table[table_index]:
            name, kind = columns[index][1], types[index]
            note = notes.get(name, "")
            body.append(f"    {name} {kind}" + (f"  -- {note}" if note else ""))
        keys = [index for index in primary if columns[index][0] == table_index]
        if keys:
            body.append(f"    PRIMARY KEY ({', '.join(columns[index][1] for index in keys)})")
        for index, target in entry["foreign_keys"]:
            if columns[index][0] == table_index:
                other = columns[target]
                body.append(f"    FOREIGN KEY ({columns[index][1]}) REFERENCES "
                            f"{names[other[0]]}({other[1]})")
        lines.append(f"CREATE TABLE {table} (")
        lines.append(",\n".join(body))
        lines.append(");")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _task_md(record: dict) -> str:
    evidence = record["evidence"] or "none given"
    return (f"# {record['question']}\n\n"
            f"Evidence: {evidence}\n\n"
            f"Write one SQLite query that answers the question, against the schema in\n"
            f"`schema.sql`. Submit it with\n\n"
            f'    python3 bird/bin/bird submit "<SQL>"\n\n'
            f"or write it to `answer.sql` yourself. The reward is the official **EX**: the\n"
            f"result rows of your query must equal the gold query's result rows as a **set**\n"
            f"(duplicates and order do not matter; a query that does not run scores 0).\n")


def _goal(record: dict) -> str:
    return (f"{record['question']}\n\n"
            f"Write one SQLite query answering it into `answer.sql`; the schema is in "
            f"`schema.sql`, and `python3 bird/bin/bird show` prints the task. Reward is the "
            f"official EX: set equality of your query's result rows with the gold query's.\n")


def _setup(record: dict) -> dict:
    return {"files": [
        {"path": "TASK.md", "content": _task_md(record)},
        {"path": "schema.sql", "content": schema_text(record["db_id"])},
        {"path": "bird/bin/bird", "from": str(CLI)},
    ]}


def _verify(record: dict) -> dict:
    database = DBS / record["db_id"] / f"{record['db_id']}.sqlite"
    spec = {
        "kind": "command",
        "argv": ["python3", "check.py", "--db", "database.sqlite", "--gold", "gold.sql",
                 "--pred", "answer.sql", "--reward-file", "verifier/reward.txt",
                 "--evidence-file", "verifier/evidence.json"],
        "reward_file": "/verifier/reward.txt",
        "evidence_file": "/verifier/evidence.json",
        "inputs": [
            {"path": "check.py",
             "from": str(Path(__file__).resolve().parent / "bird_minidev" / "verify" / "check.py")},
            {"path": "gold.sql", "content": record["gold_sql"]},
            {"path": "database.sqlite", "from": str(database)},
        ],
    }
    spec["native_runtime"] = {
        "dataset": "bird_minidev",
        "manifest": "data/runtimes/bird_minidev-native.json",
        "sha256": "bc6f725f5ed371060e8f28f53fe69e339d65f18d97dae2ecc7000d3a4e07fb08",
    }
    return bind_scorer_input(spec, record["task_id"], ["gold.sql", "database.sqlite"])


def setup_map() -> dict:
    return {record["task_id"]: _setup(record) for record in _RECORDS}


def verify_map() -> dict:
    return {record["task_id"]: _verify(record) for record in _RECORDS}


def tasks() -> list[dict]:
    return [{"task_id": record["task_id"], "goal": _goal(record), "inputs": []}
            for record in _RECORDS]


_TASKS_CACHE: list[dict] = []


def __getattr__(name: str):
    """`SETUP`, `VERIFY` and `TASKS`, on demand.

    Each holds one entry per task and each entry renders a schema, so building them at
    import would tax every module that only asks which datasets exist. This mirrors
    `data/scaffolds/qa/base.py::module_getattr`; the control and the lint ask for them by name.
    """
    global _TASKS_CACHE
    if name == "SETUP":
        return setup_map()
    if name == "VERIFY":
        return verify_map()
    if name == "TASKS":
        if not _TASKS_CACHE:
            _TASKS_CACHE = tasks()
        return _TASKS_CACHE
    if name == "_RECORDS":
        return _RECORDS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# -------------------------------------------------------------------- the platform ---

DEFAULT_ENV = {"kind": "files"}
ENV = {task_id: {"kind": "files"} for task_id in _ids()}
SPLIT = None

#: `None`: Mini-Dev publishes no study split, so the pool **is** the scored split. Every task
#: belongs to the evaluation set, which claims nothing about whether the pool is the whole of
#: Mini-Dev or of BIRD. `--split official --side eval` scores the whole pool, the comparable
#: run.
OFFICIAL_EVAL = None

#: `"none"`: the benchmark states no division, so this adapter declares none and the run
#: covers every task. It used to cut its own table with a hash third (`"hash"`); an invented
#: division, removed by the operator on 2026-10-24. `--split default` still names the
#: platform's hash rule at run time.
SPLIT_SOURCE = "none"


def load() -> tuple[list[dict], dict[str, str]]:
    records = _ensure()
    if not records:
        raise ValueError(f"bird_minidev: no tasks under {DATASET_ROOT}")
    return tasks(), {record["task_id"]: record["gold_sql"] for record in records}


def oracle_files(task: dict) -> dict[str, str]:
    """The gold query, as data: the same bytes the official evaluator reads."""
    record = next(record for record in _ensure() if record["task_id"] == task["task_id"])
    return {"answer.sql": record["gold_sql"] + "\n"}


def downloads() -> list[dict]:
    return [
        {"kind": "http", "name": "BIRD Mini-Dev ZIP", "version": RELEASE,
         "url": "https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip",
         "size": "800943648 bytes", "sha256": DATA_SHA256,
         "dst": "_src/minidev.zip",
         "note": "Official Mini-Dev release: 500 questions, the gold SQL, and 11 SQLite databases (1.4 GB). Pinned by sha256 and by the object's ETag/Last-Modified, both re-derived by the test. The agent gets the question, the released evidence hint and the schema rendered from dev_tables.json -- never the database, which holds the answer rows, and never the gold SQL."},
        {"kind": "http", "name": "BIRD Mini-Dev official evaluation code",
         "version": EVALUATION_COMMIT,
         "url": "https://codeload.github.com/bird-bench/mini_dev/tar.gz/" + EVALUATION_COMMIT,
         "size": "3479775 bytes compressed source", "sha256": EVALUATION_SHA256,
         "dst": "_src/evaluation.tar.gz", "extract_to": "_src/evaluation",
         "note": "Pins evaluation/evaluation_ex.py, whose `calculate_ex` is transcribed verbatim in data/datasets/bird_minidev/verify/check.py, plus evaluation_f1.py (Soft-F1) and evaluation_ves.py (R-VES) -- the benchmark's other two official columns, named in the evidence and not reported. The tests read this archive and compare bytes; no run imports it."},
    ]


def provision_status() -> dict:
    return {"root": str(DATASET_ROOT),
            "present": (QUESTIONS.is_file() and GOLD.is_file() and TABLES.is_file()
                        and DBS.is_dir() and any(DBS.glob("*/*.sqlite"))),
            "unpacked": str(UNPACKED),
            "tasks": len(_RECORDS)}


__all__ = ["KIND", "DATASET_ROOT", "RELEASE", "DATA_SHA256", "DATA_MD5",
           "EVALUATION_COMMIT", "EVALUATION_SHA256", "EXPECTED_ROWS", "load", "downloads",
           "oracle_files", "provision_status", "schema_text", "setup_map", "verify_map",
           "tasks", "SPLIT", "SPLIT_SOURCE", "OFFICIAL_EVAL", "ENV", "DEFAULT_ENV"]
