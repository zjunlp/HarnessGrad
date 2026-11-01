r"""GAIA (validation split): 165 web-research questions, graded by the official scorer.

    python3 data/datasets/gaia/prepare.py          # fetch the pool + the 42 attachments (needs HF_TOKEN)
    python3 data/datasets/gaia/control.py --oracle --platform
    python3 tools/dataset_lint.py --dataset gaia

Why this dataset is worth its own docstring: it is the first one on this platform whose tasks
need the **live web**, and the first whose gold is a released answer *and* whose environment is
networked. Both are decisions, not defaults.

The pool
--------
`gaia-benchmark/GAIA@682dd723ee1e1697e00360edccf2366dc8418dd9` -- the official release, gated
(`gated: "auto"`: accepting its terms authorises immediately). Two splits are downloadable and
only one is usable:

* **validation** -- 165 rows, and **165 of them carry a real `Final answer`** (levels 53/86/26),
  38 with an attachment;
* **test** -- 301 rows, and **all 301 carry the placeholder `"?"`** instead of an answer.

`prepare.py` materializes validation into a 165-row `pool.jsonl` plus `attachments/`, and
*proves* the exclusion rather than assuming it: it downloads the test parquet too, counts the
placeholders, and records the count in the pool header. A scorer run over the test split would
compare submissions against `"?"` and report a number; that is the failure this dataset is one
step away from at all times.

What the agent gets, and what it must not
-----------------------------------------
The question, the level, the attachment when the row names one, and a short answer-format
instruction. It does **not** get `Annotator Metadata`, which is not metadata: it is a dict whose
`Steps` field walks through the annotator's own search (the first row names the arXiv query, the
date window and the article to open). Nor does it get `file_path`, which points inside the
release tree. Both exclusions have tests.

The metric
----------
`question_scorer(model_answer, ground_truth) -> bool` from the official leaderboard Space
(`gaia-benchmark/leaderboard`, `scorer.py`, sha256 `0d44c07f3046eec521697c22e3eaca8719cc81e422a8eaf32695c5f22bdac6e2`)
-- the same function the leaderboard calls (`app.py`: `question_scorer(task['model_answer'],
gold_results[...]["Final answer"])`). It travels as a `VERIFY.inputs` entry out of `_src/`, so the
check calls **upstream's bytes** rather than a transcription of them: a float, a comma/semicolon
list, and a string are each normalized differently, and that asymmetry is exactly what a
hand-written "exact match" would get wrong. Reward is 1.0 or 0.0.

The level, which this dataset was shipping and no one was reading
----------------------------------------------------------------
GAIA's published table is overall accuracy **and** Level 1/2/3 accuracy, and the level was
already crossing into the verifier -- `_verify` puts it in the gold record beside the answer --
while `verify/check.py` wrote only `reward 1.0`/`0.0`. So a GAIA mean produced here could not be
set beside the paper's per-level rows, and the level distribution of the *measured* set (as
opposed to the pool's 53/86/26) was not recoverable from anything written.

It is now: `check.py` writes `level` into its evidence, `harnessgrad/records.py::per_level_breakdown`
turns the per-task labels into a `per_level` report on the curve point (measured tasks, tasks
scored 1, and accuracy for each of levels 1/2/3, plus an explicit `unknown` count), and the
progress record the nightly fold reads carries the label per task. **Reporting only** -- reward,
`score` and the headline mean are untouched, and the mean stays the flat `arithmetic_mean` over
measured tasks that `data/aggregation_policies.json` declares.

The environment, and the one thing the oracle does not prove
------------------------------------------------------------
GAIA runs in an `exec` container on the internal `services` network and declares `web` access.
The host gateway publishes selected search/fetch providers as `HG_SVC_WEB_URL`; the task has no
general internet route. Provider names and the SearXNG endpoint are recorded with the run.

The oracle here is the **released** gold answer, so `ORACLE_SOURCE = "released"` and gate 1 is
reachable on all 165 tasks. That is worth stating precisely: submitting the gold text proves the
*check* is reachable, and it proves nothing about whether a harness can *find* the answer on the
web. A score of 0 on a task whose oracle has passed is therefore still uninterpretable in the way
INTERFACE §2.5 means -- the oracle control is cheap here and the interesting work is the harness's.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


KIND = "dataset"
#: The gold answers are the benchmark's own released answers; the platform authored nothing.
ORACLE_SOURCE = "released"

REPO = "gaia-benchmark/GAIA"
REVISION = "682dd723ee1e1697e00360edccf2366dc8418dd9"
SPACE_REPO = "gaia-benchmark/leaderboard"
SPACE_REVISION = "9f133d71362ecb0a1c7ff6b4dcfc2a6b9f8ffd1e"
SCORER_SHA256 = "0d44c07f3046eec521697c22e3eaca8719cc81e422a8eaf32695c5f22bdac6e2"
METADATA = "2023/validation/metadata.parquet"
#: Distinct local names on purpose: the release puts `metadata.parquet` under both `2023/validation`
#: and `2023/test`, and a `dst` taken from the basename made one overwrite the other (measured).
VALIDATION_LOCAL = "validation-metadata.parquet"
TEST_LOCAL = "test-metadata.parquet"
METADATA_SHA256 = "d9141bad0081af438d1ee36d188c5197035dc22a01f64e3f2ee17e45469518e9"
METADATA_TEST = "2023/test/metadata.parquet"
METADATA_TEST_SHA256 = "6fab73b48acadd363b4082d283d542702356177c76a0fc8ea84568978d4181d7"
ROWS = 165

DATASET_ROOT = Path("/mnt/20t/xubuqiang/datasets/gaia")
POOL = "pool.jsonl"
POOL_SHA256 = "eab408ebea22fac494a55320acfc250c4425f01b6fd0860921374773e82ab54e"
ATTACHMENTS = "attachments"
SRC = DATASET_ROOT / "_src"
SCORER = SRC / "scorer.py"
PACKAGE = Path(__file__).resolve().parent / "gaia"
CHECK = PACKAGE / "verify" / "check.py"
CLI = PACKAGE / "cli" / "ask"
#: Built once from `data/datasets/gaia/Dockerfile`; the platform will not build it during a run.
IMAGE = os.environ.get("HG_GAIA_IMAGE", "harnessgrad/gaia@sha256:9cc27f813e596542d3f3447de10fc1166b27d66e282f271b2e057ee8ca0e0e13")
WORKDIR = "/app"
AGENT_TIMEOUT_S = 1800
VERIFY_TIMEOUT_S = 600

ENV_BASE: dict = {
    "kind": "exec",
    "image": IMAGE,
    "workdir": WORKDIR,
    "state": "container",
    # Web access is explicit and published through the platform gateway.
    "network": "services",
    "cpus": 2,
    "memory_mb": 4096,
    "placement": "inside",
    "services": [],
    "web": {"search": True, "fetch": True},
    "agent_timeout_s": AGENT_TIMEOUT_S,
    "verify_timeout_s": VERIFY_TIMEOUT_S,
}
DEFAULT_ENV = ENV_BASE

#: Our own delivery instruction, not upstream's: GAIA publishes no agent prompt, only the
#: scorer, so this says what the platform's answer channel is and what the scorer accepts.
_ANSWER_RULES = (
    "Answer with the shortest correct response.\n"
    "- if the answer is a number, write just the number (no units unless they are part of it)\n"
    "- if the answer is a list, separate the items with a comma\n"
    "- otherwise a few words, no sentence\n"
    "Write it to `answer.txt` in this directory, or submit it with\n"
    '    python3 bin/ask submit "<answer>"\n')


#: Where the container keeps the agent's answer and the check's inputs. Absolute, and at the
#: top level: the platform creates `/verifier` only *after* it starts the check's container.
ANSWER_FILE = f"{WORKDIR}/answer.txt"
GOLD_IN_CONTAINER = "/gold.json"
REWARD_FILE = "/verifier/reward.txt"
EVIDENCE_FILE = "/verifier/evidence.json"


def _raw() -> list[dict]:
    rows = []
    with (DATASET_ROOT / POOL).open(encoding="utf-8") as handle:
        header = json.loads(handle.readline())
        if header.get("rows") != ROWS:
            raise ValueError(f"{POOL}: header says {header.get('rows')} rows, expected {ROWS}")
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    if len(rows) != ROWS:
        raise ValueError(f"{POOL}: {len(rows)} rows, expected {ROWS}")
    if any(row["gold"].strip() in ("", "?") for row in rows):
        raise ValueError("gaia: a row carries the test split's placeholder gold; the pool must "
                         "be the validation split")
    return rows


_RAW: list[dict] | None = None


def rows() -> list[dict]:
    """The 165 validation rows, or an error that says how to materialize them."""
    global _RAW
    if _RAW is None:
        if not (DATASET_ROOT / POOL).is_file():
            raise ValueError(
                f"gaia: no pool at {DATASET_ROOT / POOL}. Materialize it with "
                f"`python3 data/datasets/gaia/prepare.py` (needs HF_TOKEN: the release is gated).")
        _RAW = _raw()
    return _RAW


def task_id(index: int) -> str:
    return f"gaia/{index}"


def _row_for(task: dict) -> dict:
    return rows()[int(task["task_id"].rsplit("/", 1)[1])]


def _attachment_path(row: dict) -> Path | None:
    return (DATASET_ROOT / ATTACHMENTS / row["attachment"]) if row.get("attachment") else None


def _question(row: dict) -> str:
    parts = [f"# {row['question']}", ""]
    if row.get("attachment"):
        parts += [f"The task ships one file: `attachments/{row['attachment']}`.", ""]
    parts += [_ANSWER_RULES]
    return "\n".join(parts)


def _setup(row: dict) -> dict:
    files = [{"path": "TASK.md", "content": _question(row)},
             {"path": "bin/ask", "from": str(CLI)}]
    source = _attachment_path(row)
    if source is not None:
        files.append({"path": f"attachments/{row['attachment']}", "from": str(source)})
    return {"files": files}


def _verify(row: dict) -> dict:
    """Absolute `dst` paths at the top level: the platform creates `/verifier` only *after* it
    starts the check's container, so a nested destination is a measured failure mode
    (`Could not find the file /verifier` -- `data/datasets/tau_bench.py` records the same trap)."""
    return {
        "kind": "command",
        "argv": ["python3", "/check.py", "--answer", ANSWER_FILE, "--gold", GOLD_IN_CONTAINER,
                 "--reward-file", REWARD_FILE, "--evidence-file", EVIDENCE_FILE],
        "inputs": [
            {"dst": "/check.py", "from": str(CHECK)},
            # Upstream's scorer, not a transcription of it.
            {"dst": "/scorer.py", "from": str(SCORER)},
            {"dst": GOLD_IN_CONTAINER, "content": json.dumps(
                {"task_id": row["task_id"], "gold": row["gold"], "level": row["level"]})},
        ],
        "reward_file": REWARD_FILE,
        "evidence_file": EVIDENCE_FILE,
        "timeout_s": VERIFY_TIMEOUT_S,
    }



#: `None`: GAIA has no study split in this pool -- the validation split *is* what the benchmark
#: publishes answers for, and every row in it belongs to the evaluation set. That says nothing
#: about whether the pool is the whole of GAIA: it is 165 of the release's 466 rows, and the
#: other 301 are excluded because their gold is the placeholder `"?"`.
OFFICIAL_EVAL = None

#: `"none"`: the benchmark states no division for this pool, so this adapter declares none and
#: the run covers every task. It used to cut the validation pool with a hash third
#: (`"hash"`), an invented division removed by the operator on 2026-10-24; `--split default`
#: still names the platform's hash rule at run time.
SPLIT_SOURCE = "none"

_BUILT: dict[str, object] = {}


def __getattr__(name: str):
    """`ENV`, `SPLIT`, `SETUP` and `VERIFY`, built on demand.

    **Not at import**, for two reasons that have both already cost this repository a bug. The
    mechanical one: `prepare.py` imports this module before the pool exists, so an import-time
    read made the materializer unable to run (measured: `FileNotFoundError` on
    `pool.jsonl` from `data/datasets/gaia/prepare.py`). The policy one: `data/scaffolds/registry.py::published()`
    imports every module in `data/` to decide what the Launch form offers, and that has to work
    on a machine where no dataset has been provisioned (`data/datasets/spreadsheetbench.py` and
    `data/datasets/bird_minidev.py` carry the same guard).
    """
    if name not in ("ENV", "SPLIT", "SETUP", "VERIFY"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name not in _BUILT:
        rows_ = rows()
        ids = [task_id(row["index"]) for row in rows_]
        _BUILT.update({
            "ENV": {tid: dict(ENV_BASE) for tid in ids},
            #: No division: GAIA's validation file is the pool and the whole of it is
            #: evaluation material, so `SPLIT` is `None` rather than a hash third.
            "SPLIT": None,
            "SETUP": {task_id(row["index"]): _setup(row) for row in rows_},
            "VERIFY": {task_id(row["index"]): _verify(row) for row in rows_},
        })
    try:
        return _BUILT[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def load() -> tuple[list[dict], dict[str, str]]:
    tasks = [{"task_id": task_id(row["index"]), "goal": _question(row), "inputs": []}
             for row in rows()]
    return tasks, {task["task_id"]: rows()[int(task["task_id"].rsplit("/", 1)[1])]["gold"]
                   for task in tasks}


def oracle_files(task: dict) -> dict[str, str]:
    """The released answer, as data -- this dataset needs no authored reference."""
    return {"answer.txt": _row_for(task)["gold"] + "\n"}


def downloads() -> list[dict]:
    base = f"https://hf-mirror.com/datasets/{REPO}/resolve/{REVISION}"
    return [
        {"kind": "http", "name": "GAIA validation metadata", "version": REVISION,
         "url": f"{base}/{METADATA}", "dst": f"_src/{VALIDATION_LOCAL}",
         "size": "116,994 bytes (165 rows)", "sha256": METADATA_SHA256,
         "note": "The scored pool. Gated (`gated: auto`), so `--fetch` needs HF_TOKEN in the environment or in ~/.cache/huggingface/token; the URL goes through hf-mirror.com because huggingface.co is unreachable from this host. `prepare.py` reads it and writes pool.jsonl, whose own digest transitively pins the 42 validation attachments (each carries its size and sha256 in the pool header)."},
        {"kind": "http", "name": "GAIA test metadata (evidence of the exclusion)",
         "version": REVISION, "url": f"{base}/{METADATA_TEST}",
         "dst": f"_src/{TEST_LOCAL}", "size": "75,292 bytes (301 rows)",
         "sha256": METADATA_TEST_SHA256,
         "note": "Not a task source: its 301 rows carry the placeholder gold `\"?\"`. It is declared and digested because the exclusion is a decision this dataset makes, and `prepare.py` counts the placeholders in the real file rather than trusting this note."},
        {"kind": "http", "name": "GAIA official scorer (leaderboard Space)",
         "version": SPACE_REVISION,
         "url": f"https://hf-mirror.com/spaces/{SPACE_REPO}/resolve/main/scorer.py",
         "dst": "_src/scorer.py", "size": "3,212 bytes", "sha256": SCORER_SHA256,
         "note": "`question_scorer` / `normalize_str` / `normalize_number_str` / `split_string`, pinned from the official leaderboard Space at its revision. Copied into the verification namespace as a VERIFY.inputs entry and imported there, so the reward is upstream's function rather than a transcription -- the three branches (float, comma list, string) normalize differently and an exact-match re-implementation gets them wrong. Never enters an agent SETUP."},
    ]


def provision_status() -> dict:
    attachments = DATASET_ROOT / ATTACHMENTS
    return {"root": str(DATASET_ROOT),
            "present": ((DATASET_ROOT / POOL).is_file() and SCORER.is_file()
                        and attachments.is_dir()),
            "pool": str(DATASET_ROOT / POOL),
            "attachments": str(attachments),
            "rows": len(_RAW) if _RAW else 0}


__all__ = ["KIND", "ORACLE_SOURCE", "REPO", "REVISION", "SPACE_REPO", "SPACE_REVISION",
           "VALIDATION_LOCAL", "TEST_LOCAL",
           "SCORER_SHA256", "ROWS", "DATASET_ROOT", "POOL", "POOL_SHA256", "ATTACHMENTS",
           "IMAGE", "WORKDIR", "ANSWER_FILE", "load", "oracle_files", "downloads",
           "provision_status", "rows", "task_id", "SETUP", "VERIFY", "SPLIT",
           "SPLIT_SOURCE", "OFFICIAL_EVAL", "ENV", "DEFAULT_ENV"]
