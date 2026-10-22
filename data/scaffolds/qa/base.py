"""What every plain-text QA dataset in this repository shares, in one place.

The batch this exists for -- `gsm8k`, `triviaqa`, `sotuqa`, `strategyqa`,
`sportsunderstanding`, `physicsquestions`, `fever`, `hotpotqa` -- differs only in the
part a program cannot decide: *which table*, *which field is the question*, and *what
the official scorer says*. Everything else is identical and is written once here:

    the data root is outside the platform tree
    the pin is the revision the files were fetched from
    a task id is stable across machines and stable when a file is re-extracted
    the split is imposed by hash, so adding a task does not reshuffle the others
    the agent answers by writing one file, and the check re-derives the grade from
      the official scorer and a **re-materialized** gold file

The answer contract, and why it is a file
-----------------------------------------
`answer.txt` is the platform's own convention (`eval/run/runner.py` reads it, every base
harness writes it), so a QA task needs no new channel: the goal says to write it, the
harness writes it, and the check reads it. `data/scaffolds/qa/cli/qa` exists so a harness that
only knows how to run shell commands can still answer, and so the format the scorer
expects is reachable from inside the task.

The gold travels as `VERIFY.inputs` content, never to the agent
---------------------------------------------------------------
A task dict is written to `task.json` and handed to the harness, and
`data/scaffolds/registry.py::FORBIDDEN_IN_TASKS` refuses `expected` **in a task** for exactly
this reason. The gold therefore lives in the verifier spec: the platform
re-materializes `VERIFY.inputs` immediately before the check runs, so the file the
check reads is the platform's copy and not anything the agent could have edited. That
is the same mechanism that keeps ALFWorld's game file trustworthy, used here for one
string.

What this module deliberately does not do
-----------------------------------------
It does not decide whether a dataset's official metric is *reachable*. Some in this
batch are not, and a dataset whose contract cannot carry the official number raises
instead of reporting a nearby one -- see `data/datasets/fever.py` and `data/datasets/hotpotqa.py`, and
`docs/dataset-onboarding-sketch` §2 for why dropping a requirement is the dangerous
direction.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable, Sequence

#: Re-exported so an adapter imports one module (`data.scaffolds.qa.base`) for the scaffold and the
#: reader both. `read` itself stays a separate module because the *check* imports it
#: without the scaffold: a check has no use for `QASpec`.
from data.scaffolds.qa.read import (  # noqa: F401
    SOURCE_INDEX,
    is_missing,
    jsonl_rows,
    parquet_rows,
    tables,
)

def normalize_missing(value):
    """`nan` -> `None`, one value, no recursion.

    Re-exported so an adapter that needs to clean a nested field can do it where it knows
    the shape; the reader deliberately does not recurse, for the measured reason in
    `data/scaffolds/qa/read.py::_clean`.
    """
    return None if is_missing(value) else value

#: The repository root: `data/scaffolds/qa/base.py` -> `qa` -> `scaffolds` -> `data` -> root.
ROOT = Path(__file__).resolve().parents[3]

#: The legacy answer file the agent writes and the check reads.
ANSWER_FILE = "answer.txt"
PREDICTION_FILE = "prediction.json"
CLI_FILE = "qa"

#: The container contract's locations. On a `files` environment `eval/verification/verifier.py`
#: resolves the absolute spelling against the workdir; the check's own `argv` carries
#: the relative spelling its working directory understands. Identical to ALFWorld's, and
#: for the same reason: the adapter stays a valid container dataset if one is ever built.
REWARD_FILE = "/verifier/reward.txt"
EVIDENCE_FILE = "/verifier/evidence.json"
REWARD_REL = "verifier/reward.txt"
EVIDENCE_REL = "verifier/evidence.json"

#: Where the re-materialized gold lands in the workdir during verification.
GOLD_FILE = "gold.json"

#: The shared CLI, shipped by every QA dataset's `SETUP` and by `VERIFY` -- the latter is
#: belt and braces: the check does not need it, but a harness that edits its own copy
#: should not be able to change what a later step runs.
QA_DIR = Path(__file__).resolve().parent
CLI_DIR = QA_DIR / "cli"
VERIFY_DIR = QA_DIR / "verify"

#: Where the check and the reader land in the workdir.
#:
#: `qa/bin/` holds the CLI the agent runs and `qa/lib/` holds what the check imports --
#: two different audiences, so two different directories. Learned the hard way: with the
#: CLI at `qa` and the check at `qa/verify/check.py`, materializing the verifier's inputs
#: raised `NotADirectoryError: .../qa/verify` because `qa` was a **file**. The CLI could
#: have been moved to the check directory instead; it is not, because the agent's path
#: should not move when the verification layout changes.
CHECK_DIR = "qa/lib/verify"
CHECK_NAME = "check.py"
CLI_REL = "qa/bin"
#: Public command path; keep CLI_FILE as a basename for existing control callers.
CLI_PATH = f"{CLI_REL}/{CLI_FILE}"

#: The package the adapter is materialized into, and the name prefix the check imports.
#: **The adapter keeps its real module name** (`data/datasets/<name>.py` -> `data.datasets.<name>`): a
#: synthetic name would force the adapter to be importable under two different names,
#: and "the check runs a different copy of the scorer" is the exact ambiguity §1 of the
#: skill warns about. What the check may not do is import a name that arrived in `argv`
#: -- `tests/quality/test_boundaries.py::R1` caught that -- so it validates the dataset
#: name and imports `data.datasets.<name>` literally, the same spelling `data/scaffolds/registry.py` uses.
DATASET_PACKAGE = "data"

#: `task_id` is `<dataset>/<split>/<n>`; a slash is fine in a platform task id (it is a
#: JSON value and a path component under the run directory), and it reads as what it is.
_TASK_ID_RE = re.compile(r"^(?P<dataset>[a-z0-9_]+)/(?P<split>[a-z0-9_.-]+)/(?P<index>\d+)$")


def is_eval(task_id: str) -> bool:
    """A deterministic third of the tasks, by hash of the task id.

    Hashed rather than sliced off the sorted list so that adding or removing a task does
    not reshuffle which side every other task is on -- a split that moves under the
    benchmark is a split whose past scores cannot be compared with its future ones. The
    same rule `data/datasets/alfworld.py` and `data/datasets/terminal_bench.py` use.
    """
    return hashlib.sha256(task_id.encode()).digest()[0] % 3 == 0


class QASpec:
    """One QA dataset's decisions. Everything else is `Scaffold`'s.

    `rows` is a callable, not a list, because a dataset adapter is imported on machines
    where its data was never provisioned (`data/scaffolds/registry.py::published`) and must not
    read a byte at import time.

    The shared QA controls read the adapter module's optional `NOOP_EXPECTED`
    declaration: a float, or a callable taking the record dict and returning a float.
    It sets the expected do-nothing reward in both local and platform controls and
    defaults to 0.0 when absent. The oracle arm always expects 1.0.
    """

    def __init__(
        self,
        *,
        name: str,
        root: Path,
        rows: Callable[[], dict[str, list[dict]]],
        question: Callable[[dict], str],
        gold: Callable[[dict], dict],
        goal: Callable[[dict, dict], str],
        score: Callable[[str, dict], dict],
        downloads: Callable[[], list[dict]],
        split: str,
        source: str,
        extra_setup: Sequence[dict] = (),
        study: Sequence[str] = (),
    ) -> None:
        self.name = name
        self.root = Path(root)
        self._rows = rows
        self._question = question
        self._gold = gold
        self._goal = goal
        self.score = score
        self._downloads = downloads
        self.split = split
        #: Other splits of the same benchmark that the benchmark itself designates as
        #: **study material** -- `gsm8k`'s `train`, for the one dataset where the benchmark
        #: publishes both sides. Empty for every QA adapter whose table is a single scored
        #: split, which is most of them: there the platform's hash rule (`is_eval`) is the
        #: only division that exists and is imposed rather than discovered.
        #:
        #: When this is non-empty the benchmark has stated the division, so the platform
        #: stops imposing one: `build()` derives the two sides from the split each task id
        #: carries, and `OFFICIAL_EVAL` becomes the scored split's ids instead of `None`.
        self.study = tuple(study)
        #: One or two sentences for the module docstring / DOWNLOADS: where this table
        #: came from and what was chosen about it.
        self.source = source
        self.extra_setup = list(extra_setup)
        #: Filled by `build()`. Declared here so an adapter can expose `_RECORDS` to
        #: the control before (or without) the extra call.
        self._records: list[dict] = []
        #: `None`, or the reason `load()` will refuse. Set by `build()` when the data
        #: is not on disk -- see `build()` for why that must not be an import failure.
        self.load_error: str | None = None

    # ---------------------------------------------------------------- the table ---

    def ensure(self) -> list[dict]:
        """`records()`, computed once and remembered.

        **The table is read lazily, and that is load-bearing.** A first version built
        every task's goal and every `VERIFY` entry at import time: measured on triviaqa,
        importing the adapter took **18 s** and every one of the four control runs paid
        it, because `load()` re-derived all 17,944 records each time. The platform only
        needs the task list when it runs or lints the dataset, so the expensive part is
        deferred to `load()`, and `load()` is not re-run per subprocess.
        """
        if self._records:
            return self._records
        if self.load_error is None:
            try:
                self._records = self.records()
            except Exception as exc:  # noqa: BLE001 - carried, not swallowed
                self.load_error = f"{type(exc).__name__}: {exc}"
                self._records = []
        return self._records

    def records(self) -> list[dict]:
        """One record per row of the pool, in file order: the scored split first, then any
        study splits the benchmark designates.

        A record carries the question, the gold **as data** (a dict -- a list of
        aliases, a label, an acceptable-answer set), and the task id. Nothing else: the
        goal text is derived later, so the same record can be rendered for a human
        without recomputing the table.

        The split stays in the task id (`<dataset>/<split>/<n>`), which is what lets
        `build()` read the benchmark's own division back off the ids instead of imposing
        one -- and what keeps a `train` and a `test` row at the same index from colliding.
        """
        try:
            tables = self._rows()
        except FileNotFoundError as exc:
            raise ValueError(
                f"{self.name}: {exc}. Provision it with "
                f"`python3 tools/datasets.py --fetch --dataset {self.name}`") from exc
        records = []
        for split in (self.split, *self.study):
            rows = tables.get(split)
            if rows is None:
                raise ValueError(
                    f"{self.name}: the loader returned splits {sorted(tables)}, and "
                    f"{split!r} is not one of them")
            for index, row in enumerate(rows):
                # A sampled list is shorter than the file it came from, so the task id is
                # the row's **file** position when the reader reports one
                # (`data/scaffolds/qa/read.py::SOURCE_INDEX`). Numbering by position instead would
                # let `train/7` keep its id and change its question the moment the sample
                # rule changed -- same id, same `split_sha`, different task.
                source_index = row.get(SOURCE_INDEX, index) if isinstance(row, dict) else index
                if isinstance(row, dict) and SOURCE_INDEX in row:
                    row = {k: v for k, v in row.items() if k != SOURCE_INDEX}
                gold = self._gold(row)
                records.append({
                    "task_id": f"{self.name}/{split}/{source_index}",
                    "index": source_index,
                    "split": split,
                    "row": row,
                    "question": self._question(row),
                    "gold": gold,
                })
        if not records:
            raise ValueError(f"{self.name}: {self.split} has no rows")
        return records

    # ------------------------------------------------------------- the platform ---

    def load(self) -> tuple[list[dict], dict[str, str]]:
        """`(tasks, scorable)`. Every task is graded by its `VERIFY` entry, so the
        `scorable` map carries the human-readable gold only as a fallback -- it must not
        be empty, because `load()` has no other way to say "this task has a grade"."""
        records = self.ensure()
        if self.load_error:
            raise ValueError(f"{self.name}: cannot read {self.root} ({self.load_error})")
        if not records:
            raise ValueError(f"{self.name}: no tasks under {self.root}")
        # The **declared** division, not the hash rule: once a benchmark states its own
        # two sides, a hash that happens to put every task on one side says nothing about
        # whether the dataset can run. `division()` is the one place that decides which of
        # the two is in force, and this guard reads its answer.
        division = self.division()
        if not division["train"] or not division["eval"]:
            raise ValueError(
                f"{self.name}: the division put every task on one side "
                f"({len(records)} tasks, source={division['source']}); a dataset with "
                f"one empty side cannot run")
        self._goals = {r["task_id"]: self._goal(r["question"], r["gold"]) for r in records}
        tasks = [{"task_id": r["task_id"], "goal": self._goals[r["task_id"]],
                  "inputs": []}
                 for r in records]
        return tasks, {r["task_id"]: str(r["gold"].get("answer", "")) for r in records}

    def division(self) -> dict:
        """The two sides of this pool, and **which of the two rules** produced them.

        `"declared"` means the benchmark publishes both sides and `study` names the study
        one; `"hash"` means the benchmark publishes only a scored split, so the platform
        imposes the hash third (`is_eval`). One function rather than a rule copied into
        `build()` and `load()` separately, because the day those two disagree a dataset
        runs on a division its own `SPLIT` does not describe -- and the failure is silent.
        """
        records = self.ensure()
        ids = [r["task_id"] for r in records]
        if self.study:
            eval_ids = [r["task_id"] for r in records if r["split"] == self.split]
            # `set(eval_ids)` is hoisted out of the comprehension on purpose: written
            # inline it is rebuilt once **per task**, which on TriviaQA (19,944 tasks,
            # 17,944 of them eval) measured **9.75 s** in this one call -- paid at import
            # by `build()`. A set membership test is O(1); building the set is not.
            eval_set = set(eval_ids)
            return {"train": [t for t in ids if t not in eval_set],
                    "eval": eval_ids, "source": "declared", "official_eval": eval_ids}
        return {"train": [t for t in ids if not is_eval(t)],
                "eval": [t for t in ids if is_eval(t)],
                "source": "hash", "official_eval": None}

    def setup(self, record: dict) -> dict:
        return {"files": [
            {"path": CLI_PATH, "from": str(CLI_DIR / CLI_FILE)},
            {"path": "qa/prediction.py", "from": str(QA_DIR / "prediction.py")},
            {"path": "qa/prediction.schema.json", "from": str(QA_DIR / "prediction.schema.json")},
            *self.extra_setup,
        ]}

    def verifier(self, record: dict) -> dict:
        """The grade: a check that re-derives the official metric from `answer.txt`.

        `inputs` use `path` (relative to the workdir) rather than `dst`, because a
        `files` environment has no container to put `/gold` into. The gold is inline
        `content`, so it is re-materialized by the platform before the check runs and
        the copy the check reads is not the one the agent could have found.

        **Everything the check imports travels with it.** That is not tidiness: the
        verification namespace puts a tmpfs over the platform tree (`eval/containers/sandbox.py`
        hides the work root's ancestors), so a check that imported `data.scaffolds.qa.verify.check`
        from the platform would fail to find it -- or, worse, would find whatever the
        subject left at that path. So the check, the parquet reader and the dataset's own
        scorer are copied into `qa/` in the workdir and imported from there. The
        scorer itself must import nothing but the standard library for this to work,
        which the adapters satisfy by keeping their scoring half self-contained.
        """
        gold_content = json.dumps(record["gold"], ensure_ascii=False, sort_keys=True)
        scorer_input = {"task_id": record["task_id"], "path": GOLD_FILE,
                        "sha256": hashlib.sha256(gold_content.encode("utf-8")).hexdigest()}
        return {
            "kind": self.VERIFIER_KIND,
            "scorer_input": scorer_input,
            "argv": [
                "python3", f"{CHECK_DIR}/{CHECK_NAME}",
                "--dataset", self.name,
                "--answer", ANSWER_FILE,
                "--prediction", PREDICTION_FILE,
                "--gold", GOLD_FILE,
                "--task-id", record["task_id"],
                "--gold-sha256", scorer_input["sha256"],
                "--reward-file", REWARD_REL,
                "--evidence-file", EVIDENCE_REL,
            ],
            "inputs": [
                {"path": GOLD_FILE,
                 "content": gold_content},
                {"path": f"{CHECK_DIR}/{CHECK_NAME}",
                 "from": str(VERIFY_DIR / CHECK_NAME)},
                # The scaffold, materialized under the same package names the platform
                # uses (`data.scaffolds.qa`), so the adapter's own `from data.scaffolds.qa import base` is
                # the same line in both contexts. A shim that made the import *different*
                # in the check would be one more thing that can disagree; this is the
                # same module, copied.
                {"path": f"{CHECK_DIR}/read.py", "from": str(QA_DIR / "read.py")},
                # The scaffold, materialized under the package names the platform uses,
                # so the adapter's own `from data.scaffolds.qa import base` is the same line in
                # both contexts. `data/__init__.py` is what makes `data` an ordinary
                # package: without it `data` is a namespace package, and a copy of it in
                # the workdir would *merge* with the platform's rather than shadow it --
                # so the import would resolve into the tree the sandbox hides.
                {"path": f"{DATASET_PACKAGE}/__init__.py",
                 "from": str(ROOT / DATASET_PACKAGE / "__init__.py")},
                {"path": f"{DATASET_PACKAGE}/scaffolds/__init__.py",
                 "from": str(ROOT / "data/scaffolds/__init__.py")},
                {"path": f"{DATASET_PACKAGE}/scaffolds/qa/__init__.py", "from": str(QA_DIR / "__init__.py")},
                {"path": f"{DATASET_PACKAGE}/scaffolds/qa/read.py", "from": str(QA_DIR / "read.py")},
                {"path": f"{DATASET_PACKAGE}/scaffolds/qa/base.py", "from": str(QA_DIR / "base.py")},
                {"path": f"{DATASET_PACKAGE}/scaffolds/qa/evidence_scorers.py",
                 "from": str(QA_DIR / "evidence_scorers.py")},
                {"path": f"{DATASET_PACKAGE}/scaffolds/qa/prediction.py",
                 "from": str(QA_DIR / "prediction.py")},
                {"path": f"{DATASET_PACKAGE}/datasets/__init__.py",
                 "from": str(ROOT / "data/datasets/__init__.py")},
                # The dataset's own adapter, under its real module name.
                {"path": f"{DATASET_PACKAGE}/datasets/{self.name}.py",
                 "from": str(ROOT / DATASET_PACKAGE / "datasets" / f"{self.name}.py")},
            ],
            "reward_file": REWARD_FILE,
            "evidence_file": EVIDENCE_FILE,
            "timeout_s": 3600,
        }

    def provision_status(self) -> dict:
        """What is present locally, for `tools/datasets.py --check`.

        Deliberately does not read the table: this is the answer to "did the
        provisioning run", which has to work on a machine where it did not, and on a
        machine where pyarrow is not installed.
        """
        # What counts as present is the **downloaded artifact**, not the files a loader
        # reads out of it: an `http` entry's `dst` is what `--fetch` writes, and it is the
        # only path checkable without knowing how an archive was extracted. A first
        # version looked for the member names under the root and reported every
        # tarball-backed dataset as unprovisioned, so `--check` said `MISS` and the lint
        # skipped a dataset whose data was right there.
        artifacts: list[Path] = []
        for entry in self._downloads():
            dst = entry.get("dst")
            if dst:
                path = Path(str(dst))
                artifacts.append(path if path.is_absolute() else self.root / path)
        present = [str(p) for p in artifacts if p.exists()]
        return {
            "root": str(self.root),
            "present": bool(artifacts) and len(present) == len(artifacts),
            "split_dir": str(self.root),
            "files": [str(p) for p in artifacts],
            "present_files": present,
            "tasks": len(self.ensure()),
        }

    # ------------------------------------------------------------- the boilerplate ---

    def downloads(self) -> list[dict]:
        return self._downloads()

    def build(self) -> dict:
        """Everything a `data/datasets/<name>.py` module exports, as one dict.

        Returned rather than copied into the caller's globals because the caller has to
        spell each name anyway (`KIND = built["KIND"]`), and a `globals().update(...)`
        that adds `DATASET_ROOT` by accident would put the lint's path rule on a
        variable nobody declared.

        **A missing data root is not an import failure.** `data/scaffolds/registry.py::published`
        imports every module in `data/` to decide what the Launch form offers, and
        `tools/dataset_lint.py` imports one to lint it -- both must work on a machine
        where `--fetch` has not run. So a table that cannot be read is recorded in
        `load_error` and re-raised by `load()`, which is the call that actually needs the
        bytes. This is the same split `data/datasets/alfworld.py` makes with `_LOAD_ERROR`.
        """
        records = self.ensure()
        ids = [r["task_id"] for r in records]
        division = self.division()
        exported = {
            "KIND": "dataset",
            "DATASET_ROOT": self.root,
            "DEFAULT_ENV": {"kind": "files"},
            "ENV": {tid: {"kind": "files"} for tid in ids},
            "SPLIT": {"train": division["train"], "eval": division["eval"]},
            # The benchmark's own evaluation set, in the one place this scaffold can know
            # it: the scored split's ids when the benchmark publishes a study split too
            # (`gsm8k`), and `None` when the pool *is* the scored split (`triviaqa` and the
            # rest). Never absent -- `data/scaffolds/registry.py::official_split` refuses silence.
            "OFFICIAL_EVAL": division["official_eval"],
            #: Which rule produced `SPLIT`: `"declared"` (the benchmark said so) or
            #: `"hash"` (the platform imposed it). Read by `load()`'s emptiness guard and
            #: by `tests/quality/test_qaspec_division.py`, which is the test that fails if
            #: the two rules are ever computed in two different places again.
            "SPLIT_SOURCE": division["source"],
        }
        # `SETUP` and `VERIFY` are **not** built here. Each is a dict with one entry per
        # task and each entry renders the goal or serialises the gold, so building them at
        # import made importing triviaqa cost 18 s. The caller that needs them (the
        # platform's `run_one`, or the lint) asks for them by name; see the adapter's
        # module `__getattr__`.
        # The one-side-empty check is made at load time, not here: with no data both
        # sides are empty, and that is a provisioning problem, not a split problem.
        return exported

    def setup_map(self) -> dict:
        """`SETUP`: one entry per task. Built on demand -- see `build()`."""
        return {r["task_id"]: self.setup(r) for r in self.ensure()}

    #: The `kind` every entry of `VERIFY` carries, declared once. `verify_map()`
    #: materializes one entry per task on purpose (building it at import cost 18 s on
    #: triviaqa), so a caller that only needs the *set* of kinds must not be forced to
    #: pay for 949 identical dicts -- that turned a taxonomy report into a 19-second
    #: command and made the dataset-classification suite take fifteen minutes.
    #: `tests/quality/test_dataset_classes.py` pins this against a materialized map so
    #: the declaration cannot drift away from what `verifier()` actually builds.
    VERIFIER_KIND = "command"

    def verify_map(self) -> dict:
        """`VERIFY`: one entry per task. Built on demand -- see `build()`."""
        return {r["task_id"]: self.verifier(r) for r in self.ensure()}

    def control_dir(self) -> Path:
        return Path(__file__).resolve().parent / "oracle_harness"


def module_getattr(spec: "QASpec", name: str):
    """The adapter's module-level `__getattr__`, shared by every QA dataset.

    Returns `SETUP`, `VERIFY`, `_RECORDS` or `_LOAD_ERROR` on demand and raises
    `AttributeError` for anything else, which is what a module-level `__getattr__` must
    do. The two maps are built lazily because each holds one entry per task and building
    them at import cost 18 s on triviaqa (`QASpec.ensure`); `_RECORDS` is exposed because
    the control and the tests read it, and it is the same cached list `load()` uses.
    """
    if name == "SETUP":
        return spec.setup_map()
    if name == "VERIFY":
        return spec.verify_map()
    if name == "_RECORDS":
        return spec.ensure()
    if name == "_LOAD_ERROR":
        return spec.load_error
    raise AttributeError(f"module {spec.name!r} has no attribute {name!r}")


def split_env_override(name: str) -> str | None:
    """`HG_<NAME>_SPLIT=none` runs every task as eval, as ALFWorld's adapter allows.

    Present for the same reason: the benchmark has no train/eval division and the one
    here is imposed, so a reader who wants the whole set scored must be able to say so
    without editing the module.
    """
    return os.environ.get(f"HG_{name.upper()}_SPLIT")


__all__ = ["QASpec", "module_getattr", "ANSWER_FILE", "PREDICTION_FILE", "CLI_FILE", "CLI_PATH", "GOLD_FILE",
           "REWARD_FILE", "EVIDENCE_FILE", "REWARD_REL", "EVIDENCE_REL",
           "is_eval", "split_env_override", "ROOT", "Path"]
