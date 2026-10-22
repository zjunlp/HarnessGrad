r"""SpreadsheetBench as a dataset: edit a real workbook, graded against the golden one.

    python3 tools/datasets.py --fetch --dataset spreadsheetbench
    python3 data/datasets/spreadsheetbench/control.py --oracle
    driver.py --dataset spreadsheetbench ...

Where the tasks come from
-------------------------
`RUCKBReasoning/SpreadsheetBench` (NeurIPS 2024 D&B), the **`verified_400`** release, fetched
from the HuggingFace mirror `KAKA22/SpreadsheetBench`
(`spreadsheetbench_verified_400.tar.gz`, 15 MB, revision `ab0b742b…`). It is a flat tarball,
not a git repository, so the pin is the Hub **revision sha** plus the file digest.

Each of the 400 tasks is three files:

    <id>_init.xlsx      the workbook the agent starts from
    <id>_golden.xlsx    the workbook the official scorer compares against
    prompt.txt          the instruction (and `dataset.json` carries it too)

The metric, verbatim
--------------------
`evaluation/evaluation.py::compare_workbooks` in the official repository, transcribed in
`data/datasets/spreadsheetbench/verify/check.py`. It loads both workbooks with
`openpyxl.load_workbook(..., data_only=True)` and compares **the values in the cells named by
`dataset.json`'s `answer_position`** -- a comma-separated list of `Sheet!A1:B2` ranges -- after
running `transform_value` on each side:

    numbers rounded to 2 decimals; `datetime` -> Excel's serial day count (rounded);
    `datetime.time` -> `HH:MM`; a string that parses as a float -> that float;
    `""` and `None` are the same value

`data_only=True` means the scorer reads **cached values**, not formulas. A workbook written by
a program that computes its own values has them; one whose cells hold formulas that were never
evaluated has `None` everywhere. So the check **recalculates the agent's workbook with
LibreOffice** before comparing, which is what the repository's own
`evaluation/open_spreadsheet.py` exists to do. Measured on this host: `soffice` is installed
(`/usr/bin/soffice`) and `--headless --calc --convert-to xlsx` does cache the computed values.

What is deliberately not added, and what is not checked
-------------------------------------------------------
* **Cell fill and font colour are not compared.** The official file has those comparisons
  commented out in `cell_level_compare`; this adapter matches the code that runs.
* **Which of the three test cases an agent solved is not reported.** The official `evaluation`
  runs three variants per task id (`1_<id>_answer.xlsx` etc.) and aggregates them into
  `soft_restriction` / `hard_restriction`. The `verified_400` tarball ships **one** pair per
  task (`1_<id>_init.xlsx` / `1_<id>_golden.xlsx`), so this dataset is 400 tasks on the
  variant that ships, and a score here is not the paper's `soft`/`hard` aggregate. Stated
  because otherwise the two numbers would look comparable.
* **No `done` gate and no step budget**: the artifact is a file.
"""
from __future__ import annotations

from data.scaffolds.scorer_input import bind as bind_scorer_input

import json
import os
from pathlib import Path

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"


#: The benchmark's own evaluation set, within this adapter's pool: **every task here belongs
#: to it**. The pool is the released `verified_400` set, which is the official evaluation set.
#:
#: `None` is the accurate value only because nothing in the pool is study material -- it does
#: not claim the pool is the whole of the benchmark's evaluation set (see `ds1000`, which is a
#: measured subset). `--split official --side eval` is the comparable run: the whole pool, with
#: no study material in it. `--split declared` keeps this adapter's own division.
OFFICIAL_EVAL = None

#: Where the extracted release lives. Outside the platform tree on purpose.
DATASET_ROOT = Path(os.environ.get("HG_SPREADSHEETBENCH_DATA",
                                   "/mnt/20t/xubuqiang/datasets/spreadsheetbench"))

#: The Hub release and its digests.
REVISION = "ab0b742b0fc95b946f212d80ac7771b5531272e4"
TARBALL = "spreadsheetbench_verified_400.tar.gz"
TARBALL_URL = (f"https://hf-mirror.com/datasets/KAKA22/SpreadsheetBench/resolve/"
               f"{REVISION}/{TARBALL}")
TARBALL_SHA256 = "10ef893dd29cb13ab97143ea787e68cdc9574a13873ab9a54e50b31dc03fc949"

#: Inside the tarball.
RELEASE_DIR = "spreadsheetbench_verified_400"
TASKS_JSON = f"{RELEASE_DIR}/dataset.json"
N_TASKS = 400

#: The workbook the agent starts from, the file it must leave behind, and the oracle's copy.
INIT_WORKBOOK = "workbook.xlsx"
RESULT_WORKBOOK = "result.xlsx"

REWARD_FILE = "/verifier/reward.txt"
EVIDENCE_FILE = "/verifier/evidence.json"
REWARD_REL = "verifier/reward.txt"
EVIDENCE_REL = "verifier/evidence.json"

#: `evaluation.py::compare_workbooks` reads the answer's cell names from this field.
ANSWER_POSITION_KEY = "answer_position"


# ------------------------------------------------------------------- the tasks ---


def _tasks() -> list[dict]:
    path = DATASET_ROOT / TASKS_JSON
    if not path.is_file():
        raise FileNotFoundError(f"no {TASKS_JSON} at {path}")
    rows = json.loads(path.read_text())
    if len(rows) != N_TASKS:
        raise ValueError(f"spreadsheetbench: {len(rows)} tasks, expected {N_TASKS}")
    records = []
    for row in rows:
        task_id = str(row["id"])
        # `dataset.json`'s `spreadsheet_path` is relative to the **release directory**, not to
        # the dataset root: the tarball extracts to `spreadsheetbench_verified_400/`. Measured
        # -- joining it to the root found `[]` for every task.
        folder = DATASET_ROOT / RELEASE_DIR / str(row["spreadsheet_path"])
        init = _find_workbook(folder, task_id, "init")
        golden = _find_workbook(folder, task_id, "golden")
        if init is None or golden is None:
            raise FileNotFoundError(
                f"spreadsheetbench: {task_id} is missing its workbook pair under {folder} "
                f"(found {sorted(p.name for p in folder.glob('*.xlsx')) if folder.is_dir() else []})")
        position = str(row.get(ANSWER_POSITION_KEY) or "").strip()
        if not position:
            raise ValueError(f"spreadsheetbench: {task_id} has no {ANSWER_POSITION_KEY}")
        records.append({
            "task_id": task_id,
            "init_name": init.name,
            "golden_name": golden.name,
            "instruction": str(row["instruction"]).strip(),
            "instruction_type": str(row.get("instruction_type") or ""),
            "answer_position": position,
            "answer_sheet": str(row.get("answer_sheet") or ""),
            "init_path": init,
            "golden_path": golden,
        })
    return records


#: The naming the release uses for most tasks, and the variants it uses for a few.
_WORKBOOK_PATTERNS = {
    "init": ("1_{id}_init.xlsx", "initial.xlsx", "*_init.xlsx", "initial*.xlsx"),
    "golden": ("1_{id}_golden.xlsx", "golden.xlsx", "*_golden.xlsx", "golden*.xlsx"),
}


def _find_workbook(folder: Path, task_id: str, kind: str) -> Path | None:
    """The task's workbook, under whichever name this release gave it.

    Measured over the 400 tasks: 394 use `1_<id>_init.xlsx` / `1_<id>_golden.xlsx`, **4** use
    `initial.xlsx` / `golden.xlsx`, and **2** have a typo in one half (`1_42930_init.xlsx` with
    `1_43930_golden.xlsx`). The last pattern is a glob, which is how a typo is tolerated without
    hard-coding the typo -- and the fallback order is deterministic so the same file is chosen
    on every machine.
    """
    if not folder.is_dir():
        return None
    for pattern in _WORKBOOK_PATTERNS[kind]:
        candidate = folder / pattern.format(id=task_id)
        if candidate.is_file():
            return candidate
    for pattern in _WORKBOOK_PATTERNS[kind][2:]:
        matches = sorted(folder.glob(pattern))
        if matches:
            return matches[0]
    return None


def _goal(record: dict) -> str:
    """The instruction, plus the two things the interface cannot tell the agent.

    The prompt is the dataset's own text. The goal adds where the workbook is, what to leave
    behind, and that the comparison is over computed **values** in named cells -- because a
    harness that leaves formulas unevaluated would otherwise be graded on an empty workbook
    for a reason nothing records.
    """
    return (
        "# Task\n"
        "\n"
        f"{record['instruction']}\n"
        "\n"
        "## The workbook\n"
        "\n"
        f"`{INIT_WORKBOOK}` in your working directory is an Excel workbook. Edit it with any\n"
        "tool available to you (for example `python3` with `openpyxl`, which is installed).\n"
        "\n"
        "## What to leave behind\n"
        "\n"
        f"Save the edited workbook as **`{RESULT_WORKBOOK}`** in your working directory, and\n"
        f"put that filename (nothing else) in `answer.txt`:\n"
        "\n"
        "    python3 -c \"open('answer.txt','w').write('result.xlsx')\"\n"
        "\n"
        "## How it is scored\n"
        "\n"
        "- Your workbook is opened with its **computed values**, and the values in the cells\n"
        "  the task names are compared with the reference workbook's. Formulas are fine: the\n"
        "  values are recalculated before the comparison.\n"
        "- Numbers are compared rounded to 2 decimals, dates as Excel's day count, times to\n"
        "  the minute, and an empty cell equals a missing one.\n"
        "- Everything outside the named cells is ignored, and so are colours and formatting.\n"
    )


def _setup(record: dict) -> dict:
    """The agent receives the starting workbook and the CLI.

    The workbook travels as a `from` reference into the dataset root. It is the *input*; the
    golden workbook is not in the workdir at all, and the check reads its own copy.
    """
    return {"files": [
        {"path": INIT_WORKBOOK, "from": str(record["init_path"])},
        {"path": "qa/bin/qa", "from": str(Path(__file__).resolve().parent.parent
                                            / "scaffolds" / "qa" / "cli" / "qa")},
        {"path": "qa/prediction.py", "from": str(Path(__file__).resolve().parent.parent
                                                   / "scaffolds" / "qa" / "prediction.py")},
    ]}


def _verifier(record: dict) -> dict:
    """The check, with its own trusted copies of the scorer, the golden workbook and the task.

    The golden workbook and `answer_position` are inline/path inputs so the platform
    re-materializes them immediately before grading -- the same rule ALFWorld's game file
    follows, and the reason a dataset with an artifact can be graded honestly.
    """
    spec = {
        "kind": "command",
        "argv": [
            "python3", "check.py",
            "--workdir", ".",
            "--answer", "answer.txt",
            "--result", RESULT_WORKBOOK,
            "--golden", "golden.xlsx",
            "--task", "task.json",
            "--reward-file", REWARD_REL,
            "--evidence-file", EVIDENCE_REL,
        ],
        "inputs": [
            {"path": "check.py",
             "from": str(Path(__file__).resolve().parent / "spreadsheetbench"
                         / "verify" / "check.py")},
            {"path": "golden.xlsx", "from": str(record["golden_path"])},
            {"path": "task.json",
             "content": json.dumps({"task_id": record["task_id"],
                                    "answer_position": record["answer_position"],
                                    "instruction_type": record["instruction_type"]})},
        ],
        "reward_file": REWARD_FILE,
        "evidence_file": EVIDENCE_FILE,
        "timeout_s": 300,
    }
    return bind_scorer_input(spec, record["task_id"], ["golden.xlsx", "task.json"])


def downloads() -> list[dict]:
    return [
        {
            "kind": "http",
            "name": "SpreadsheetBench verified_400",
            "version": REVISION,
            "url": TARBALL_URL,
            "size": "~15 MB (400 tasks x 2 workbooks + dataset.json)",
            "sha256": TARBALL_SHA256,
            "dst": "_src/" + TARBALL,
            "extract_to": ".",
            "note": "Trap 1: this is a **tarball on the Hub**, not a git repository, so the pin "
                    "is the revision sha plus this digest. Trap 2: the official evaluator reads "
                    "**cached** formula values (`data_only=True`), so a workbook whose formulas "
                    "were never evaluated has no values at all -- the check recalculates with "
                    "LibreOffice, which is what the repository's own `open_spreadsheet.py` "
                    "exists to do, and `soffice` must be installed for any formula-using task. "
                    "Trap 3: the `verified_400` release ships **one** variant per task, while "
                    "the paper's `soft`/`hard` numbers aggregate three; a score here is not "
                    "that aggregate.",
        },
        {
            "kind": "pip",
            "name": "openpyxl",
            "package": "openpyxl",
            "version": "3.1.2",
            "size": "~250 KB",
            "note": "The library the official scorer uses to read both workbooks, and the one "
                    "the agent is expected to edit with. Both sides use the same version so a "
                    "round-trip difference is not a scoring difference.",
        },
    ]


# ------------------------------------------------------------------ the platform ---


def _records_cache() -> list[dict]:
    return _ensure()


_RECORDS: list[dict] = []
_LOAD_ERROR: str | None = None
try:
    _RECORDS = _tasks()
except Exception as _exc:  # noqa: BLE001 - reported by load(), which is where it bites
    _RECORDS = []
    _LOAD_ERROR = f"{type(_exc).__name__}: {_exc}"


def _ensure() -> list[dict]:
    global _RECORDS, _LOAD_ERROR
    if _RECORDS:
        return _RECORDS
    try:
        _RECORDS = _tasks()
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        raise
    return _RECORDS


def provision_status() -> dict:
    tarball = DATASET_ROOT / "_src" / TARBALL
    return {
        "root": str(DATASET_ROOT),
        "present": (DATASET_ROOT / TASKS_JSON).is_file() and tarball.is_file(),
        "split_dir": str(DATASET_ROOT / RELEASE_DIR),
        "tarball": str(tarball) if tarball.is_file() else None,
        "libreoffice": _soffice(),
        "tasks": len(_RECORDS),
    }


def _soffice() -> str | None:
    """The LibreOffice binary, or None. Reported by `provision_status` because every
    formula-using task is unmeasurable without it and that must not look like a 0."""
    import shutil
    for candidate in ("soffice", "libreoffice"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def load() -> tuple[list[dict], dict[str, str]]:
    if _LOAD_ERROR is not None:
        raise ValueError(
            f"spreadsheetbench: cannot read the tasks ({_LOAD_ERROR}). Provision it with "
            f"`python3 tools/datasets.py --fetch --dataset spreadsheetbench`.")
    records = _ensure()
    tasks = [{"task_id": r["task_id"], "goal": _goal(r), "inputs": []} for r in records]
    return tasks, {r["task_id"]: RESULT_WORKBOOK for r in records}


DEFAULT_ENV: dict = {"kind": "files"}


def __getattr__(name: str):
    if name == "ENV":
        return {r["task_id"]: {"kind": "files"} for r in _ensure()}
    if name == "SETUP":
        return {r["task_id"]: _setup(r) for r in _ensure()}
    if name == "VERIFY":
        return {r["task_id"]: _verifier(r) for r in _ensure()}
    if name == "SPLIT_SOURCE":
        return "none"
    if name == "SPLIT":
        # No division: the 400-task release is the benchmark's scored set, so the whole
        # pool is evaluation material (2026-10-24). `HG_SPREADSHEETBENCH_SPLIT=none`
        # selected this state and is gone; `--split default` asks for the hash rule.
        return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["KIND", "DATASET_ROOT", "REVISION", "load", "downloads", "provision_status",
           "DEFAULT_ENV", "INIT_WORKBOOK", "RESULT_WORKBOOK"]
