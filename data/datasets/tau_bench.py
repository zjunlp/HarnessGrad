"""tau-bench as a dataset: the benchmark's own split, retail and airline. INTERFACE.md §2.5.9.

    python tools/build_tau_bench_gt.py      # regenerate the committed assets
    driver.py --dataset tau_bench ...

Where the tasks come from
-------------------------
`sierra-research/tau-bench`. The task sets are the ones each domain's own environment
scores, and the division between them is the benchmark's, file for file:

    retail    `tasks_train.TASKS_TRAIN` (500)  official train
              `tasks_dev.TASKS_DEV`     (20)   official dev
              `tasks_test.TASKS_TEST`  (115)   official test
    airline   `tasks_test.TASKS`        (50)   official test -- the only set it ships

685 tasks. The checkout's legacy `tasks.py` files hold a larger, older set (185 / 76)
and are deliberately not used -- `MockRetailDomainEnv`/`MockAirlineDomainEnv` do not
read them either. `tools/build_tau_bench_gt.py` copies each task's ground truth, the
clean database, the policy and the tool implementations into `data/datasets/tau_bench/`;
this module only reads those committed assets, so it does not need the checkout on
the machine that runs the dataset.

A task id is `<domain>_<split>_<index>` (`retail_train_010`), not `<domain>_<index>`:
the three retail files each index from 0, so an id that did not name its split would
name three different tasks.

What a task is
--------------
Tau-bench's task is a *database transaction*, not a conversation: the reward is
`tau_bench.envs.base.Env.calculate_reward()`, which hashes the environment database
after the agent's actions, replays the ground-truth actions on a clean database and
requires the two hashes to be equal -- and it computes that reward only when the
episode is `done`, and additionally requires the agent to have said every string in
the task's `outputs`. The environment here reproduces all three parts:

    the domain's tool implementations, the clean database, the wiki + rules

and the agent's only way to act is the `./tau` CLI
(`list` / `call` / `policy` / `say` / `done`), staged as `./tau`. A task-bound
service owns the database and accepted event log in `/recordings/tau/episode.json`.
The existing pinned image supplies the domain tools, clean data and policies; the
service source is delivered through its declared command, without an image rebuild.

Why SQL/args and not an LLM user simulator
------------------------------------------
There is no scripted user strategy upstream -- only `UserStrategy.HUMAN` and
`UserStrategy.LLM` -- and running an LLM in the environment would make the reward
depend on a second model. It does not have to: the reward compares database states,
and the ground-truth `respond`/read-only steps never touch the database. The user id
is therefore given to the agent in the goal, which removes the need for a simulator
while changing nothing the reward measures. Read-only lookups do not move the hash
either, so an agent that skips the authentication calls is not penalised for it.

How `calculate_reward` is reproduced
------------------------------------
* **Database state.** `verify/check.py` hashes the recorded service database with the hash
  functions extracted verbatim from `tau_bench/envs/base.py` and compares it with the
  hash of a clean database after the ground-truth actions are replayed through the
  same tool classes. Terminating actions are not replayed, exactly as upstream:
  `calculate_reward` skips every ground-truth action whose name is in
  `terminate_tools` (`transfer_to_human_agents`), because it does not touch the
  database.
* **Done is required.** Upstream calls `calculate_reward()` only when `Env.step`
  sets `done`. There is no user simulator here that could send `###STOP###`, so
  `./tau done` or the upstream terminating tool ends the service episode. The check
  reads only through the first termination: no termination, reward `0`, however
  good the database looks. The service refuses subsequent actions.
* **`outputs` is required.** For every string in the task's `outputs`, upstream
  requires some respond content to contain it
  (`output.lower() in content.lower().replace(",", "")`). There is no interactive
  user here, so `./tau say '<text>'` records an utterance in the service log and the
  check applies that rule to the recorded says; one missing string is reward `0`.
  Local `db.json` and `tau_calls.jsonl` cannot supply grading evidence.

The split
---------
The benchmark's own, file for file, and declared as such (`SPLIT_SOURCE`). The platform
runs exactly two sides (INTERFACE.md §2.6), so the three official sets are exposed as two
pools of one declaration:

    default (`HG_TAU_BENCH_POOL` unset, or `=test`)
        train   retail train, 500                        -- study material, and only that
        eval    retail test 115 + airline test 50 = 165  -- the exam
    `HG_TAU_BENCH_POOL=dev`
        train   retail train, 500
        eval    retail dev, 20                           -- the selection measurement

Dev is never study material: a split cannot be both what a method learns from and what
its checkpoints are selected on. It is not a third side because dev and test scored in
one run would be one side, and a study score would then be a dev score. `train` is
refused as a pool: it is the material the study set is drawn from, not an evaluation.

`OFFICIAL_EVAL` is the benchmark's own evaluation set *within this pool*: both test files
under `pool=test`, and nothing under `pool=dev` -- no task of the published evaluation
set is in the dev pool, so `--side eval --split official` refuses there rather than
inventing one.

Tasks that cannot discriminate
------------------------------
Replaying all 685 ground truths with `measured_no_op_tasks()` shows 32 whose final
database is byte-identical to the clean one and which carry no `outputs` requirement:
5 retail train, 1 retail dev, 7 retail test, 19 airline test. (A handful of other no-op
tasks *do* carry an `outputs` requirement and are kept: `say` + `done` still make them
discriminate.) For the 32, after the done gate an agent that calls `./tau done`
immediately is a full-credit solution and the task measures nothing about the database
work. They are listed in `EXCLUDED_TASKS` and `load()` never returns them. They cannot
merely be left off `SPLIT`: `data/scaffolds/registry.py` requires every returned task to be
on exactly one side, so an excluded task has to be absent from `load()` entirely. Their
ground-truth files stay in `gt/` -- only the adapter stops returning them.

Counts: 685 staged, 32 excluded, **653 returned** -- `train` 495 retail, `dev` 19
retail, `eval` 139 (retail test 108 + airline test 50 - 19). The default pool loads
495 + 139 = **634** tasks; `HG_TAU_BENCH_POOL=dev` loads 495 + 19 = **514**. A score on
the eval side is a score on the official test files, which is what the benchmark's
published retail and airline numbers are computed on -- and no airline train or dev set
exists to study, so every airline task is an exam task.
"""


from __future__ import annotations


#: See `data/scaffolds/registry.py:KIND_DATASET`. A fixture is platform test material and is
#: deliberately not offered as a choice in the Launch form.
KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"

#: The division this adapter declares is the benchmark's own (`SPLIT_SOURCE == "declared"`).
#: It is read from the four official task files, not from a hash of the pool.
SPLIT_SOURCE = "declared"

import json
import os
import sys
from pathlib import Path


#: The committed assets, written by `tools/build_tau_bench_gt.py`. Kept beside this
#: module: `data/datasets/tau_bench.py` is the adapter, `data/datasets/tau_bench/` is its content.
ASSETS = Path(__file__).resolve().parent / "tau_bench"

GT_DIR = ASSETS / "gt"
DB_DIR = ASSETS / "db"
LIB_DIR = ASSETS / "lib"
CHECK_SCRIPT = ASSETS / "verify" / "check.py"
SERVICE_SCRIPT = ASSETS / "service.py"
CLI_SCRIPT = ASSETS / "image" / "tau_cli.py"
PROVENANCE = ASSETS / "PROVENANCE.json"

#: The image. Built once from `data/datasets/tau_bench/Dockerfile`; the platform does not
#: build or pull during a run (INTERFACE.md §2.5.5), so this must already be local.
IMAGE = os.environ.get("HG_TAU_IMAGE", "harnessgrad/tau-bench@sha256:beda1b01f43ca4912555b44ccdfe25b1fe384b42e77d87a5316302ccf493fb23")

WORKDIR = "/app"

#: Where the check's **inputs** are staged inside its container. Top level on purpose --
#: see the note in `_verifier`. The outputs stay in the reward file's directory.
GT_IN_CONTAINER = "/tau-gt.json"
CLEAN_DB_IN_CONTAINER = "/tau-clean-db.json"
CHECK_IN_CONTAINER = "/tau-check.py"
LIB_IN_CONTAINER = "/tau-lib"
REWARD_FILE = "/verifier/reward.txt"
EVIDENCE_FILE = "/verifier/evidence.json"
AGENT_TIMEOUT_S = 900
VERIFY_TIMEOUT_S = 900

#: The order tasks are listed in: domain, then the official split. Both come from the
#: ground-truth records, so this only fixes the order `load()` returns them in.
DOMAIN_ORDER = ("retail", "airline")
SPLIT_ORDER = ("train", "dev", "test")

#: Which official sets each run-time pool scores. The study side is `train` in both pools:
#: retail train, and nothing else.
POOLS: dict[str, tuple[str, ...]] = {
    "test": (("retail", "test"), ("airline", "test")),
    "dev": (("retail", "dev"),),
}
#: `HG_TAU_BENCH_POOL`, the same shape as `HG_APPWORLD_POOL`: it chooses which official
#: split is scored, and never grows the study side. `train` is not a choice (see `_sides`).
_POOL = (os.environ.get("HG_TAU_BENCH_POOL") or "test").strip()

#: Ground-truth actions upstream does not replay; they do not touch the database.
#: `Mock{Retail,Airline}DomainEnv.terminate_tools`.
TERMINATE_TOOLS = ("transfer_to_human_agents",)

#: Tasks whose ground-truth replay leaves the database unchanged **and** which carry no
#: `outputs` requirement. Such a task cannot discriminate: the database hash already
#: matches before the agent does anything, the done gate is satisfied by an immediate
#: `./tau done`, and there is no output to say. They are removed from what `load()`
#: returns -- not merely from `SPLIT`, because `data/scaffolds/registry.py` requires every
#: returned task to be on exactly one side -- while their ground-truth files stay in `gt/`
#: for audit. The exclusion is a property of the task, not of the side: a no-op task
#: teaches a method nothing on any side. Measured 2026-10-24 over all 685: 5 retail train,
#: 1 retail dev, 7 retail test, 19 airline test.
#:
#: The list is a measurement, not a guess, and it is kept explicit so `load()` does
#: no replay work. To regenerate it after `tools/build_tau_bench_gt.py` reruns:
#:
#:     python3 -c "import data.datasets.tau_bench as t; print(t.measured_no_op_tasks())"
#:
#: which replays every ground truth through the staged tools and keeps the tasks
#: whose `consistent_hash(to_hashable(db))` is unchanged and whose `outputs` is
#: empty -- exactly the comparison `verify/check.py` makes. The no-op tasks that
#: *do* carry `outputs` are deliberately not here: `say` + `done` still make them measure.
EXCLUDED_TASKS = (
    "retail_train_161", "retail_train_214", "retail_train_229", "retail_train_288",
    "retail_train_349", "retail_dev_017",
    "retail_test_010", "retail_test_012", "retail_test_025", "retail_test_050",
    "retail_test_057", "retail_test_065", "retail_test_106",
    "airline_test_012", "airline_test_013", "airline_test_015", "airline_test_017",
    "airline_test_018", "airline_test_021", "airline_test_024", "airline_test_029",
    "airline_test_035", "airline_test_036", "airline_test_037", "airline_test_038",
    "airline_test_039", "airline_test_040", "airline_test_041", "airline_test_042",
    "airline_test_047", "airline_test_048", "airline_test_049",
)

#: The existing image contains /opt/tau/{lib,pristine,policy}. Supply service code
#: as a declared command so no rebuilt image digest is asserted. SETUP supplies
#: the matching HTTP client; neither service ground truth nor recording is exposed.
ENV_BASE: dict = {
    "kind": "exec",
    "image": IMAGE,
    "workdir": WORKDIR,
    "state": "mount",
    "network": "services",
    "cpus": 2,
    "memory_mb": 4096,
    "placement": "inside",
    "services": [{
        "name": "tau", "image": IMAGE, "port": 8080,
        "command": ["python3", "-c", SERVICE_SCRIPT.read_text()],
        "health": {"argv": ["python3", "-c",
                             "import urllib.request as u; "
                             "u.urlopen('http://localhost:8080/health', timeout=2).read()"],
                   "timeout_s": 60},
        "record": True, "needs_model": False, "needs_seed": False,
        "cpus": 2, "memory_mb": 4096,
    }],
    "agent_timeout_s": AGENT_TIMEOUT_S,
    "verify_timeout_s": VERIFY_TIMEOUT_S,
}
DEFAULT_ENV = ENV_BASE


def _read_gt() -> list[dict]:
    if not GT_DIR.is_dir():
        raise ValueError(
            f"no tau-bench ground truth under {GT_DIR}. Generate it with:\n"
            f"    python tools/build_tau_bench_gt.py")
    records = []
    for path in sorted(GT_DIR.glob("*.json")):
        records.append(json.loads(path.read_text()))
    if not records:
        raise ValueError(f"{GT_DIR} is empty; run tools/build_tau_bench_gt.py")
    records.sort(key=lambda r: (DOMAIN_ORDER.index(r["domain"]),
                                SPLIT_ORDER.index(r["split"]), r["index"]))
    return records


#: Read once at import. `load()` is called more than once per run and the content is
#: immutable, so re-reading 685 files per call would be work with no effect.
#: `_ALL_GT` is everything on disk (the excluded tasks' files are kept); `_GT` is every
#: task this dataset can return; `_RUN_GT` is the two sides of this run's pool.
_ALL_GT: list[dict] = _read_gt()
_unknown_excluded = set(EXCLUDED_TASKS) - {record["task_id"] for record in _ALL_GT}
if _unknown_excluded:
    raise ValueError(
        f"tau_bench: EXCLUDED_TASKS names task(s) with no ground truth: "
        f"{sorted(_unknown_excluded)}; regenerate it with measured_no_op_tasks()")
_GT: list[dict] = [record for record in _ALL_GT
                   if record["task_id"] not in EXCLUDED_TASKS]
_BY_ID: dict[str, dict] = {record["task_id"]: record for record in _GT}


def _sides() -> tuple[list[str], list[str], list[str]]:
    """`(study ids, eval ids, official-eval ids)` for this run's pool.

    A bad `HG_TAU_BENCH_POOL` is refused rather than repaired: falling back to the default
    pool would change which official split a score is about without changing anything
    the record says.
    """
    if _POOL not in POOLS:
        raise ValueError(
            f"HG_TAU_BENCH_POOL={_POOL!r} is not an official split of tau-bench; the "
            f"eval pool is one of {' / '.join(POOLS)}. It is not `train`: the study "
            "set is drawn from train, so a score on it is not an evaluation.")
    eval_sets = POOLS[_POOL]
    study = [record["task_id"] for record in _GT
             if (record["domain"], record["split"]) == ("retail", "train")]
    eval_ids = [record["task_id"] for record in _GT
                if (record["domain"], record["split"]) in eval_sets]
    # dev is an official split but not the set the benchmark's published numbers are
    # computed on, so under `pool=dev` nothing in the pool is declared as the benchmark's
    # own evaluation set. `--side eval --split official` then refuses on an empty side.
    official = eval_ids if _POOL == "test" else []
    return study, eval_ids, official


#: A bad knob is reported by `load()` rather than raised here: `published()` hides a module
#: that will not import, and a dataset that vanished from the chooser would say nothing
#: about the environment variable that broke it.
try:
    _STUDY_IDS, _EVAL_IDS, _OFFICIAL_EVAL = _sides()
    _LOAD_ERROR: str | None = None
except Exception as _exc:  # noqa: BLE001 - reported by load(), which is where it bites
    _STUDY_IDS, _EVAL_IDS, _OFFICIAL_EVAL = [], [], []
    _LOAD_ERROR = f"{type(_exc).__name__}: {_exc}"

#: The tasks this run can see: the study side and the eval pool, and nothing else.
_RUN_IDS = set(_STUDY_IDS) | set(_EVAL_IDS)
_RUN_GT: list[dict] = [record for record in _GT if record["task_id"] in _RUN_IDS]


def _goal(record: dict) -> str:
    """The instruction, with a clearly-labelled preamble naming only the interface.

    Nothing here hints at the answer. It states the user id (which the reward makes
    free -- see the module docstring), the policy is readable with `./tau policy`, and
    the CLI is how the task is acted on.
    """
    domain = record["domain"]
    return (
        f"# Task\n"
        f"\n"
        f"You are the {domain} customer-service agent. The user is not simulated: the\n"
        f"conversation has already happened, and your job is to carry out the database\n"
        f"work it calls for by calling the domain's tools.\n"
        f"\n"
        f"## How to act\n"
        f"\n"
        f"- You act on the database by calling tools through the `./tau` command.\n"
        f"- You are acting for the user whose id is `{record['user_id']}`.\n"
        f"- Read the policy you must follow first: `./tau policy`.\n"
        f"- List the available tools and their argument schemas: `./tau list`.\n"
        f"- Call one tool at a time and read its observation:\n"
        f"      ./tau call <tool_name> '<json-arguments>'\n"
        f"- There is no interactive user here. Anything you need to tell the user goes\n"
        f"  through:\n"
        f"      ./tau say '<text>'\n"
        f"  and no reply will come back.\n"
        f"- When the task is finished, call `./tau done`. The task is not treated as\n"
        f"  finished until you do or call a terminating tool.\n"
        f"- The service owns the database and records accepted calls and messages.\n"
        f"  Use `./tau show` to inspect it. Local files do not change service state.\n"
        f"- `./tau done` or `transfer_to_human_agents` ends the episode; later calls\n"
        f"  and messages are refused.\n"
        f"- Every fact about the database (ids, prices, availability, balances) must\n"
        f"  come from a tool observation. Do not invent values.\n"
        f"\n"
        f"## The user's request\n"
        f"\n"
        f"{record['instruction'].strip()}\n"
    )


def _db_path(domain: str) -> Path:
    return DB_DIR / f"{domain}.json"


def measured_no_op_tasks() -> list[str]:
    """Recompute `EXCLUDED_TASKS` by replaying every ground truth. Manual step only.

    Deliberately not called by `load()`: `load()` reads committed assets, and a
    replay at import time would make the task list depend on the checkout's tools at
    the moment of import. Run:

        python3 -c "import data.datasets.tau_bench as t; print(t.measured_no_op_tasks())"

    For each task: replay its non-terminating ground-truth actions through the staged
    tool classes on a fresh copy of the clean database, then keep the task when
    `consistent_hash(to_hashable(db))` is unchanged **and** `outputs` is empty --
    the same hash comparison and the same non-discriminating condition the check and
    the module docstring describe.
    """
    import importlib
    if str(LIB_DIR) not in sys.path:
        sys.path.insert(0, str(LIB_DIR))
    from tau_bench.envs.hash import consistent_hash, to_hashable

    def hash_of(value) -> str:
        return consistent_hash(to_hashable(value))

    tools_by_domain: dict[str, dict] = {}
    for domain in DOMAIN_ORDER:
        module = importlib.import_module(f"tau_bench.envs.{domain}.tools")
        tools_by_domain[domain] = {cls.get_info()["function"]["name"]: cls
                                   for cls in module.ALL_TOOLS}

    # Parse each clean database once and deep-copy it per task: re-reading a 4 MB JSON
    # file 685 times measured at ~0.5 s per task, of which the read was most of it.
    import copy
    clean = {domain: json.loads(_db_path(domain).read_text()) for domain in DOMAIN_ORDER}
    no_op = []
    for record in _ALL_GT:
        domain = record["domain"]
        db = copy.deepcopy(clean[domain])
        before = hash_of(db)
        for action in record["actions"]:
            if action.get("name") in TERMINATE_TOOLS:
                continue
            tool = tools_by_domain[domain].get(action["name"])
            if tool is None:
                continue
            try:
                tool.invoke(db, **action.get("arguments", {}))
            except Exception:  # noqa: BLE001 - a refused action is not a hash change
                pass
        if hash_of(db) == before and not (record.get("outputs") or []):
            no_op.append(record["task_id"])
    return no_op


def _setup(record: dict) -> dict:
    """Stage the matching HTTP client; the database stays inside the service."""
    return {"files": [{"path": "tau", "from": str(CLI_SCRIPT)}]}


def _verifier(record: dict) -> dict:
    """The check and everything it needs, copied in **after** the agent stops.

    The ground truth is an input rather than an image file: an image is built once,
    before any run, and a `VERIFY.inputs` entry is materialized only at verification
    time, so the answer is not on the filesystem the agent explored. The tool library
    and the clean database travel the same way, which also means the replay cannot be
    steered by anything the agent changed inside the container. The verifier reads
    the service recording mounted read-only by the platform, never agent files.
    """
    tid = record["task_id"]
    return {
        "kind": "command",
        "argv": ["python3", CHECK_IN_CONTAINER,
                 "--recording", "/recordings/tau/episode.json"],
        # **Top level, not `/verifier/...`, and that is not cosmetic.** The platform
        # copies these in *before* it starts the check's container, and it creates the
        # reward file's directory only *after* starting it (see `mkdir_in` in
        # `eval/verification/verifier.py`). `docker cp <file> <ctr>:/a/b/c` therefore fails with
        # `Could not find the file /a/b` when the destination is nested and nothing has
        # made the parent yet. A top-level destination has `/` as its parent, which always
        # exists. Measured 2026-10-06: `/verifier/gt.json` failed the whole run with
        # `ContainerUnavailable: could not copy ... Could not find the file /verifier`.
        # The outputs stay under `/verifier` because by then the platform has created it.
        "inputs": [
            {"dst": GT_IN_CONTAINER, "from": str(GT_DIR / f"{tid}.json")},
            {"dst": CLEAN_DB_IN_CONTAINER,
             "from": str(_db_path(record["domain"]))},
            {"dst": CHECK_IN_CONTAINER, "from": str(CHECK_SCRIPT)},
            {"dst": LIB_IN_CONTAINER, "from": str(LIB_DIR)},
        ],
        "reward_file": REWARD_FILE,
        "evidence_file": EVIDENCE_FILE,
        "timeout_s": VERIFY_TIMEOUT_S,
    }


def load() -> tuple[list[dict], dict[str, str]]:
    """`(tasks, scorable)` for this run's pool. Every task is graded by its `VERIFY`
    entry, so the `scorable` map is present only because the interface asks for it."""
    if _LOAD_ERROR is not None:
        raise ValueError(f"tau_bench: {_LOAD_ERROR}")
    tasks = [{"task_id": record["task_id"],
              "goal": _goal(record),
              "inputs": []}
             for record in _RUN_GT]
    return tasks, {record["task_id"]: "" for record in _RUN_GT}


ENV: dict[str, dict] = {record["task_id"]: dict(ENV_BASE) for record in _RUN_GT}
SETUP: dict[str, dict] = {record["task_id"]: _setup(record) for record in _RUN_GT}
VERIFY: dict[str, dict] = {record["task_id"]: _verifier(record) for record in _RUN_GT}

#: The division, in the benchmark's own terms: the retail train file against this pool's
#: official test (or dev) files. Both sides are exactly the ids `load()` returns.
SPLIT = {"train": list(_STUDY_IDS), "eval": list(_EVAL_IDS)}

#: The benchmark's own evaluation set, within this run's pool. Under the default
#: `pool=test` that is the two official test files; `pool=dev` declares nothing, because
#: no task of the published evaluation set is in the dev pool, and `--side eval --split
#: official` then refuses on an empty side rather than inventing one.
OFFICIAL_EVAL = list(_OFFICIAL_EVAL)


def downloads() -> list[dict]:
    """What a reader must fetch to rebuild this dataset's committed assets.

    Tau-bench's ground truth is **committed** under `data/datasets/tau_bench/` -- the adapter
    reads that, not the checkout -- so this dataset runs on a machine with nothing
    fetched at all. The entry below describes the upstream source the committed bytes
    were derived from, pinned to the commit recorded in `PROVENANCE.json`, and
    `tools/build_tau_bench_gt.py` is what turns it back into `gt/`, `db/`, `policy/`
    and `lib/`. `tools/datasets.py --write` renders it into `DOWNLOADS.md` from here,
    so there is one list rather than a document that can drift from the loader.
    """
    try:
        commit = json.loads(PROVENANCE.read_text()).get("checkout_commit") or "unknown"
    except (OSError, ValueError):
        commit = "unknown"
    return [{
        "kind": "http",
        "name": "tau-bench checkout (regenerates the committed assets)",
        "version": commit,
        "url": f"https://github.com/sierra-research/tau-bench/archive/{commit}.tar.gz",
        "size": "~67 MB unpacked",
        "dst": "/mnt/20t/xubuqiang/datasets/tau-bench",
        "extract_to": ".",
        "note": "Only needed to REGENERATE `data/datasets/tau_bench/`: the adapter reads the "
                "committed assets, which are already in git. Pinned to the commit in "
                "PROVENANCE.json -- never a branch.",
    }]
