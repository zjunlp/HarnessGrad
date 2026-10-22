"""DS-1000 code generation: the slice of the simplified test split this host can execute.

Official source: xlang-ai/DS-1000@b39aab71 (`test_ds1000.py` and `execution.py`).
Data: `xlangai/DS-1000@4416080ac5cb80bdf7576aefb8f9a0b4d5426a44/test.jsonl`, 1000 rows.

The official construction is used unchanged: `code_context + code = repr(candidate)`, then
`test_execution(code)`, plus `test_string(code)` only when the context declares that helper,
each candidate in its own process with the upstream 120-second timeout and the upstream
deliberately-disabled `reliability_guard`. `reference_code` is gold and travels only through
`oracle_files()`; it never enters SETUP. The solver receives the released `prompt`
only (plus solution-file instructions); `code_context` contains private tests and
travels only in VERIFY.inputs.

**926 of the 1000 released rows are offered; 74 are excluded, and every exclusion is a
measurement.** This is a coverage fact, reported rather than hidden. No substitute metric is
published under the benchmark's name.

* **47 rows: the official test code imports `tensorflow`** (45 labelled `Tensorflow`, plus 2
  labelled `Numpy` whose prompts are tensorflow conversions). This interpreter has
  pandas/numpy/scipy/sklearn/matplotlib/seaborn/torch but no `tensorflow`. Measured: under
  `--platform` the verification namespace starts from an empty root and binds only `/usr`,
  the interpreter prefix, the work root and the task directory, so a dataset-local Python
  tree outside the work root is not visible (`FileNotFoundError` on an absolute dataset
  path, `HOME=/tmp`). TensorFlow is therefore reachable only by mutating the shared platform
  environment or by copying a multi-gigabyte tree into every task's workdir; neither is
  acceptable here. This group is recomputed from `code_context`, so it cannot drift.
* **27 rows: the released gold and test code target an older library stack** than the one
  installed. Measured by running every offered row's `reference_code` through the official
  construction on 2026-10-07: **926 passed, 27 failed**, and each failure names its cause in
  `MEASURED_EXCLUSIONS`. They are pandas 1.x APIs removed in pandas 2.2.2 (`DataFrame.append`,
  positional `str.split`/`str.rsplit`/`drop`, `set_axis(inplace=)`, date-format inference,
  dtype comparison, index-insert semantics), scipy 1.13 removals (`dok_matrix._update`,
  `scipy.linalg.triu`), one absent optional package (`xgboost`), and one row whose official
  test fetches over the network (`HTTP 403`). DS-1000 ships an `environment.yml` pinning the
  versions its numbers came from; these rows are where that pin differs from this host.

Two further notes for whoever picks this up. The official tests' only *network* dependency
was 14 Matplotlib rows calling `sns.load_dataset(...)`; those are provisioned offline here
from a pinned copy of `mwaskom/seaborn-data` and are **included**, because caching the same
fixed data does not change what the metric computes. And an included set is only trustworthy
because every included row's gold was actually run: `_src/oracle-sweep.json` (outside git)
holds the per-row result, and `MEASURED_EXCLUSIONS` is derived from it.
"""
from __future__ import annotations

from data.scaffolds.scorer_input import bind as bind_scorer_input

import json
import re
from pathlib import Path

from data.scaffolds import command_control

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"

#: The benchmark's own evaluation set, within this adapter's pool: **every task here belongs
#: to it**. The pool is the official simplified test split **minus** the 74 rows measured as unrunnable on this host; every row that remains is an official evaluation row. The pool is therefore not the whole of the official set, and `None` says only that nothing in it is study material.
#:
#: `None` is the accurate value only because nothing in the pool is study material -- it does
#: not claim the pool is the whole of the benchmark's evaluation set (see `ds1000`, which is a
#: measured subset). `--split official --side eval` is the comparable run: the whole pool, with
#: no study material in it. `--split declared` keeps this adapter's own division.
OFFICIAL_EVAL = None

DATASET_ROOT = Path("/mnt/20t/xubuqiang/datasets/ds1000")
DATA_FILE = DATASET_ROOT / "_src" / "test.jsonl"
SOURCE_DIR = DATASET_ROOT / "_src" / "DS-1000-b39aab71"
SEABORN_DIR = DATASET_ROOT / "seaborn-data"
ASSETS = Path(__file__).resolve().parent / "ds1000"
CHECK_FILE = ASSETS / "verify" / "check.py"
EXECUTION_FILE = SOURCE_DIR / "execution.py"
REVISION = "4416080ac5cb80bdf7576aefb8f9a0b4d5426a44"
DATA_SHA256 = "26a492838b05f9d030b2f08a5715c6bafecafb601c157da912bf417fd8b0d363"
SOURCE_COMMIT = "b39aab71"
SOURCE_TARBALL_SHA256 = "b14f1a2b0fb20ef55f0693019122f22c82b03ef6647faabf0e6e8ae1272a1dc0"
SEABORN_COMMIT = "71e2436a092d714350de0fc409ca8a8714e7e78f"
SEABORN_TARBALL_SHA256 = "ac7ed3e81e634c8febecac42975456280b089bd5bcae3d54bc6be11d8cec1532"
SEABORN_CSVS = {
    "tips.csv": "e54cc4d2ce1bff65d32ca60b3e4b802e06bde1d7e7caf6f796f6bf7370e863b0",
    "planets.csv": "a6d10044887e17396974525a366f5fa2e4b34df70f491e64eb9943de0e3d3825",
    "penguins.csv": "e07636bd8af74260099ea2f8678e2eabbf35def579940cc76f67061ee16c06c1",
    "exercise.csv": "d67ff5896d7d262bba3ec0bd7a8db410e56afc96a0436e08dfe53016d5f1e4e6",
}

#: Rows whose `reference_code` was *run* through the official construction and failed because
#: the installed library stack is newer than the one DS-1000 pinned. The value is the cause
#: as the runner reported it. Frozen from `_src/oracle-sweep.json`; do not edit by hand --
#: re-run `tools/`-free sweep in `tests/quality/test_ds1000_official_scorer.py` and regenerate.
MEASURED_EXCLUSIONS: dict[str, tuple[int, ...]] = {
    "pandas2-append": (47, 48, 49, 228),
    "pandas2-str-split-positional": (199, 200, 201),
    "pandas2-str-rsplit-positional": (220, 221, 222),
    "pandas2-drop-positional": (195, 196),
    "pandas2-set-axis-inplace": (282, 283),
    "pandas2-date-format": (209, 210, 211),
    "pandas2-dtype-compare": (39, 40),
    "pandas2-index-insert": (924, 925),
    "scipy-dok-update": (796,),
    "scipy-triu-moved": (936, 937),
    "missing-xgboost": (837, 838),
    "network-fetch-403": (819,),
}

#: The largest exclusion group is a *criterion* rather than a frozen table: what the released
#: test code imports. Recomputing it keeps the frozen table from drifting away from the data.
_TF_PATTERN = re.compile(r"\b(import tensorflow|from tensorflow|tf\.)")


def _read() -> list[dict]:
    with DATA_FILE.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if len(rows) != 1000:
        raise ValueError(f"DS-1000 expected 1000 rows, got {len(rows)}")
    if any(not row.get("reference_code") for row in rows):
        raise ValueError("DS-1000 row without reference_code")
    return rows


_RAW: list[dict] | None = None
_INCLUDED: list[int] | None = None


def _raw() -> list[dict]:
    global _RAW
    if _RAW is None:
        _RAW = _read()
    return _RAW


def tensorflow_indices() -> set[int]:
    """Rows whose official test code imports tensorflow, recomputed from the release."""
    return {i for i, row in enumerate(_raw()) if _TF_PATTERN.search(row["code_context"])}


def measured_exclusions() -> dict[int, str]:
    """The frozen per-row version-drift exclusions, as `{row index: cause}`."""
    return {index: cause
            for cause, indices in MEASURED_EXCLUSIONS.items()
            for index in indices}


def excluded_indices() -> set[int]:
    """Every row this adapter does not offer, with the reason recorded in the module."""
    return tensorflow_indices() | set(measured_exclusions())


def included_indices() -> list[int]:
    global _INCLUDED
    if _INCLUDED is None:
        excluded = excluded_indices()
        _INCLUDED = [i for i in range(len(_raw())) if i not in excluded]
    return _INCLUDED


def task_id(index: int) -> str:
    return f"ds1000/{index}"


#: **No division.** DS-1000 ships no train/eval division -- the released `test.jsonl` is one
#: exam-like pool, minus the 74 rows measured as unrunnable on this host -- so the adapter
#: declares none and the whole pool is evaluation material (2026-10-24). It used to cut the
#: pool with a deterministic hash third, which labelled a third of an exam "study material";
#: `--split default` still applies that rule to a run that asks for it.
SPLIT = None
SPLIT_SOURCE = "none"

ENV = {task_id(i): {"kind": "files"} for i in included_indices()}


def task_for(index: int) -> dict:
    """A task dict for any released row, included or not.

    Excluded rows are still buildable so that a test can re-run the official construction
    against them and re-observe the recorded failure. They are never offered by `load()`.
    """
    return {"task_id": task_id(index), "goal": _raw()[index]["prompt"], "inputs": []}


def setup_for(index: int) -> dict:
    row = _raw()[index]
    return {"files": [
        {"path": "task.json", "content": json.dumps(
            {"task_id": task_id(index), "prompt": row["prompt"]}, ensure_ascii=False)},
    ]}


def verify_for(index: int) -> dict:
    spec = {
        "kind": "command",
        "argv": ["python3", "verify/check.py",
                 "--reward-file", command_control.REWARD_REL,
                 "--evidence-file", command_control.EVIDENCE_REL],
        "inputs": [
            {"path": "verify/check.py", "from": str(CHECK_FILE)},
            # Official solver input is prompt only. code_context carries private tests;
            # stage it after the harness exits, never in SETUP.
            {"path": "context.json", "content": json.dumps(
                {"code_context": _raw()[index]["code_context"]}, ensure_ascii=False)},
            {"path": "execution.py", "from": str(EXECUTION_FILE)},
            # Removes the official tests' only network fetch; see the module docstring.
            *[{"path": f"seaborn-data/{name}", "from": str(SEABORN_DIR / name)}
              for name in sorted(SEABORN_CSVS)],
        ],
        "reward_file": command_control.REWARD_FILE,
        "evidence_file": command_control.EVIDENCE_FILE,
    }
    return bind_scorer_input(spec, task_id(index),
                             ["context.json", *[f"seaborn-data/{name}"
                                                for name in sorted(SEABORN_CSVS)]])


SETUP = {task_id(i): setup_for(i) for i in included_indices()}
VERIFY = {task_id(i): verify_for(i) for i in included_indices()}


def load():
    tasks = []
    for index in included_indices():
        tasks.append({"task_id": task_id(index), "goal": _raw()[index]["prompt"] + (
            "\n\nWrite your completed Python solution to solution.py in this directory. "
            "Do not include markdown "
            "fences; only the solution body is executed.")})
    return tasks, {task["task_id"]: "code" for task in tasks}


def _row_for_task(task: dict) -> dict:
    return _raw()[int(task["task_id"].rsplit("/", 1)[1])]


def oracle_files(task: dict) -> dict[str, str]:
    return {"solution.py": _row_for_task(task)["reference_code"]}


def downloads():
    return [
        {"kind": "hf-dataset", "name": "DS-1000 simplified test.jsonl",
         "repo": "xlangai/DS-1000", "version": REVISION,
         "size": "3,439,876 bytes (1000 rows)", "files": ["test.jsonl"], "dst": "_src",
         "sha256": {"test.jsonl": DATA_SHA256},
         "note": "Canonical HF simplified format. Trap: the tests live inside code_context "
                 "and reference_code is gold. 926 of the 1000 rows are offered here; the 74 "
                 "exclusions are measured and named in data/datasets/ds1000.py."},
        {"kind": "http", "name": "DS-1000 official source archive", "version": SOURCE_COMMIT,
         "url": "https://codeload.github.com/xlang-ai/DS-1000/tar.gz/b39aab71",
         "size": "787 KB measured archive", "sha256": SOURCE_TARBALL_SHA256,
         "dst": "_src/ds1000-repo.tar.gz", "extract_to": "_src",
         "note": "Pins test_ds1000.py and execution.py for verbatim transcription. The "
                 "official runner deliberately leaves reliability_guard disabled because "
                 "data-science libraries need filesystem operations; each candidate runs in "
                 "its own process with a 120-second timeout."},
        {"kind": "http", "name": "seaborn example datasets (offline)",
         "version": SEABORN_COMMIT,
         "url": "https://codeload.github.com/mwaskom/seaborn-data/tar.gz/" + SEABORN_COMMIT,
         "size": "5,076,306 bytes tarball; 4 CSVs (~72 KB) staged",
         "sha256": SEABORN_TARBALL_SHA256,
         "dst": "_src/seaborn-data.tar.gz",
         "note": "14 official Matplotlib rows call sns.load_dataset(...), which otherwise "
                 "fetches from GitHub at evaluation time (raw.githubusercontent is blocked "
                 "on this machine, so those rows failed with connection reset). The four "
                 "CSVs seaborn needs are staged under seaborn-data/ and shipped in "
                 "VERIFY.inputs; SEABORN_DATA points the official code at them. This is "
                 "provisioning the same fixed data, not changing the metric."},
    ]


def provision_status():
    needed = [DATA_FILE, EXECUTION_FILE, CHECK_FILE,
              SEABORN_DIR / "tips.csv", SEABORN_DIR / "penguins.csv"]
    present = [str(path) for path in needed if path.is_file()]
    return {"root": str(DATASET_ROOT), "present": len(present) == len(needed),
            "split_dir": str(DATASET_ROOT), "files": [str(p) for p in needed],
            "present_files": present,
            "tasks": len(included_indices()),
            "excluded": len(excluded_indices()),
            "excluded_tensorflow": len(tensorflow_indices()),
            "excluded_measured": len(measured_exclusions())}
