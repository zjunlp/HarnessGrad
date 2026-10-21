r"""LiveCodeBench release_v6: 1,055 problems, official **pass@1** by running the released tests.

    python3 data/datasets/livecodebench/prepare.py            # materialize the pool (4.5 GB read)
    python3 data/datasets/livecodebench/control.py --oracle --platform
    python3 tools/dataset_lint.py --dataset livecodebench

This adapter was a **refusal** until the platform's authored-reference contract existed; the
refusal is in the git history and in skill doc round 25. Two things changed and nothing else
did: the owner authorised platform-authored references that the released tests validate, and
the tests were shown to decode offline. The metric was never the obstacle.

Where the data is
-----------------
`livecodebench/code_generation_lite@0fe84c3912ea0c4d4a78037083943e8f0c4dd505`: six shards,
**4,485,994,821** bytes, every sha256 verified against `downloads()`. Measured: the shards are
**disjoint** -- 400 + 111 + 101 + 101 + 167 + 175 = 1,055 rows and 1,055 distinct
`question_id`s, so their union is the pool and the list is not a version history. (An earlier
note in this repository called them "cumulative"; that would have made the pool a subset of the
largest shard, which it is not.)

Decoded, the tests are **9.38 GB**, which is why the pool is two things rather than one:
`pool.jsonl` (2.0 MB) carries each problem's metadata plus its test file's sha256 and size, and
`tests/<question_id>.json` carries `{"inputs": [...], "outputs": [...], "fn_name": ...}` --
exactly the payload upstream's `CodeGenerationProblem.get_evaluation_sample()` builds, public
and private cases concatenated in that order. The platform materializes `VERIFY.inputs` **per
task**, so only one problem's tests ever move; the largest is 199 MB (`abc369_g`) and the median
is 48 KB. Neither the adapter nor any check reads the whole directory.

The metric
----------
Reward is per-problem **pass@1**, computed by upstream's own code path: the pinned
`testing_util.run_test` executes the agent's `solution.py` against every released test, and
`pass_k_utils.compute_metrics_from_results` (`np.all(gen > 0)`) decides correctness. Both files
travel as `VERIFY.inputs` out of the pinned archive, so the executor and the aggregation are
upstream's bytes rather than a transcription of them; the check adds only the process boundary
and the platform's reward file. The check uses upstream's global timeout budget
`(6+1)*n_tests+5`, within the platform's 900 s verifier bound. The reward is one task's
pass@1 rather than a batch mean, because the platform grades one task per check.

What the agent sees
-------------------
The problem statement, the starter code when the release ships one (444 of the 1,055 are
call-based and ship a signature; the other 611 read stdin), and nothing else. The tests are
**not** in `SETUP` -- upstream's prompt does not include them either, and while the public half
is published, the private half is the point of the benchmark.

The oracle is authored, and says so
-----------------------------------
The release ships prompts, starter code and tests, and **no reference solution**, so gate 1's
oracle cannot be derived from the benchmark's data. Under the authored-reference contract this
adapter declares `ORACLE_SOURCE = "authored"`, names what validates the references (the released
tests, run by the pinned executor: a reference is accepted only because the tests accept it),
and reports how much of the pool has one -- `ORACLE_COVERAGE`. The references live in
`data/datasets/livecodebench/references/`, are ours, and are never visible to the agent.
"""
from __future__ import annotations

from data.scaffolds.scorer_input import bind as bind_scorer_input

import base64
import json
import pickle
import zlib
from pathlib import Path

import numpy as np

from data.scaffolds import registry

KIND = "dataset"
#: The reference solutions are **ours** and the released tests validate them; see the module
#: docstring and `data/dataset.schema.json` (`module.oracle_about`).
ORACLE_SOURCE = "authored"

DATASET_ROOT = Path("/mnt/20t/xubuqiang/datasets/livecodebench")
REVISION = "0fe84c3912ea0c4d4a78037083943e8f0c4dd505"
UPSTREAM_COMMIT = "28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24"
#: The compressed upstream source archive, which is what `downloads()` declares.
SOURCE_SHA256 = "56c7c42aee979def86b69547ecd6d5a877a0e4940af8923bf4325ca0defecbaf"
PROBLEMS = 1055
POOL = "pool.jsonl"
POOL_SHA256 = "bbf2bf826856156656568f568e4026f7702f90b5da22a880b733b755a69f4256"
TESTS_DIR = "tests"
#: Inside the extracted archive; both files travel as `VERIFY.inputs`.
UPSTREAM = f"_src/upstream/LiveCodeBench-{UPSTREAM_COMMIT}/lcb_runner/evaluation"
EXECUTOR = f"{UPSTREAM}/testing_util.py"
PASS_K = f"{UPSTREAM}/pass_k_utils.py"
PACKAGE = Path(__file__).resolve().parent / "livecodebench"
CHECK_FILE = PACKAGE / "verify" / "check.py"
REFERENCES = PACKAGE / "references"
CLI = PACKAGE / "cli" / "lcb"

#: Shard name -> sha256, verified against the files on disk at onboarding.
_FILES = {
    "test.jsonl": "2bd02b38beb48e8c46b5b9987095d999ff38cd8efc255ea5d58974317c48f63f",
    "test2.jsonl": "095df7c5daf15f882c51a9deb84085cff1e073495a5dbcf95015a564d485f3a3",
    "test3.jsonl": "28ed26cc83363ce3f1fe2d5fad9f8393077beb1907b167a31bd3b32f80801b79",
    "test4.jsonl": "d711138ddaebfcf5f8ec6a4283ee677298c0f5c5d374a235af92aaf0584510da",
    "test5.jsonl": "7f77571c2a6df0c2a72a3277650309f67e01e0008e18117e624633df53f81214",
    "test6.jsonl": "bb4c364f71921c4495a6ad15abe1a927350b720009f4933e2e71f8af0f6fd1f5",
}


# ---------------------------------------------------------------------------------------
# `lcb_runner/evaluation/pass_k_utils.py` lines 4-53, byte for byte: `estimate_pass_at_k`
# with its inner `estimator`, and `compute_metrics_from_results`. The check calls the shipped
# copy of this file, and the test asserts both that this region is upstream's bytes and that
# the shipped file and this transcription agree -- so the metric is pinned in the two places a
# reader might look.
# ---------------------------------------------------------------------------------------
def estimate_pass_at_k(num_samples, num_correct, k):
    """Estimates pass@k of each problem and returns them in an array."""

    def estimator(n: int, c: int, k: int) -> float:
        """Calculates 1 - comb(n - c, k) / comb(n, k)."""
        if n - c < k:
            return 1.0
        return 1.0 - np.prod(1.0 - k / np.arange(n - c + 1, n + 1))

    import itertools

    if isinstance(num_samples, int):
        num_samples_it = itertools.repeat(num_samples, len(num_correct))
    else:
        assert len(num_samples) == len(num_correct)
        num_samples_it = iter(num_samples)

    return np.array(
        [estimator(int(n), int(c), k) for n, c in zip(num_samples_it, num_correct)]
    )


def compute_metrics_from_results(results, k_list=[1, 5]):
    total = []
    correct = []
    task_ids = []
    for task_id, res in results.items():
        all_correct = []
        for generation in res:
            gen = np.array(generation)
            all_correct.append(np.all(gen > 0))
        task_ids.append(task_id)
        total.append(len(all_correct))
        correct.append(sum(all_correct))
    total = np.array(total)
    correct = np.array(correct)
    ks = k_list
    detail_pass_at_k = {
        f"pass@{k}": estimate_pass_at_k(total, correct, k).tolist()
        for k in ks
        if (total >= k).all()
    }
    pass_at_k = {
        f"pass@{k}": estimate_pass_at_k(total, correct, k).mean()
        for k in ks
        if (total >= k).all()
    }
    detail_metrics = {k: dict(zip(task_ids, v)) for k, v in detail_pass_at_k.items()}
    pass_at_k["detail"] = detail_metrics
    return pass_at_k
# ---------------------------------------------------------------------------------------


def decode_private_test_cases(value: str) -> list[dict]:
    """The released decode step, from
    `lcb_runner/benchmarks/code_generation.py::CodeGenerationProblem.__post_init__`: plaintext
    JSON when the field already is, base64 + zlib + pickle otherwise.

    Also the step `prepare.py` uses, so the pool and this module cannot drift apart. The pickle
    is trusted because the shards are digest-verified before `prepare.py` runs; nothing here
    ever unpickles agent- or network-supplied bytes.
    """
    try:
        cases = json.loads(value)
    except ValueError:
        cases = json.loads(
            pickle.loads(zlib.decompress(base64.b64decode(value.encode("utf-8")))))
    return cases


# ------------------------------------------------------------------------------ the pool ---

_RAW: list[dict] | None = None


def _raw() -> list[dict]:
    """The pool's rows, read once. 2.0 MB of metadata -- never the tests."""
    global _RAW
    if _RAW is None:
        rows = []
        with (DATASET_ROOT / POOL).open(encoding="utf-8") as handle:
            header = json.loads(handle.readline())
            if header.get("problems") != PROBLEMS:
                raise ValueError(f"{POOL}: header says {header.get('problems')} problems, this "
                                 f"adapter expects {PROBLEMS}")
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        if len(rows) != PROBLEMS:
            raise ValueError(f"{POOL}: {len(rows)} rows, expected {PROBLEMS}")
        _RAW = rows
    return _RAW


def task_id(index: int) -> str:
    return f"livecodebench/{index}"


def _row_for(task: dict) -> dict:
    return _raw()[int(task["task_id"].rsplit("/", 1)[1])]


def _tests_path(row: dict) -> Path:
    return DATASET_ROOT / TESTS_DIR / f"{row['question_id']}.json"


# --- the prompt: upstream's substance, and the platform's delivery channel -----------------

#: `lcb_runner/prompts/code_generation.py::PromptConstants`, transcribed. The one thing that
#: cannot be transcribed is the delivery line: upstream asks for a fenced code block in a chat
#: reply, and this platform's answer channel is a file, so the goal says `solution.py`.
SYSTEM_MESSAGE = ("You are an expert Python programmer. You will be given a question (problem "
                  "specification) and will generate a correct Python program that matches the "
                  "specification and passes all tests.")
WITH_STARTER = "You will use the following starter code to write the solution to the problem."
WITHOUT_STARTER = ("Read the inputs from stdin, solve the problem and write the answer to "
                   "stdout (do not directly test on the sample inputs).")


def _question(row: dict) -> str:
    parts = [SYSTEM_MESSAGE, "", "### Question:", row["question_content"], ""]
    if row["starter_code"]:
        parts += [WITH_STARTER, "", "```python", row["starter_code"], "```", ""]
    else:
        parts += [WITHOUT_STARTER, ""]
    parts += [
        "Write the completed program to `solution.py` in this directory: one Python file, no "
        "markdown fences, nothing else. It is executed against the problem's released tests "
        f"({row['n_public'] + row['n_private']} here) and the reward is the official pass@1 -- "
        "1.0 only if every test passes.",
        "",
        "`python3 lcb/bin/lcb show` prints the task; `python3 lcb/bin/lcb submit` writes "
        "`solution.py` from stdin.",
    ]
    return "\n".join(parts)


def _setup(row: dict) -> dict:
    return {"files": [
        # Metadata a harness may legitimately know: where the problem came from, how it is
        # graded, how big it is. Never the tests.
        {"path": "task.json", "content": json.dumps({
            "task_id": task_id(row["index"]), "question_id": row["question_id"],
            "question_title": row["question_title"], "platform": row["platform"],
            "contest_id": row["contest_id"], "contest_date": row["contest_date"],
            "difficulty": row["difficulty"], "n_public_tests": row["n_public"],
            "n_private_tests": row["n_private"], "call_based": bool(row["starter_code"])},
            ensure_ascii=False)},
        {"path": "lcb/bin/lcb", "from": str(CLI)},
    ]}


def _verify(row: dict) -> dict:
    spec = {
        "kind": "command",
        "argv": ["python3", "verify/check.py", "--tests", "tests.json",
                 "--solution", "solution.py",
                 "--reward-file", "verifier/reward.txt",
                 "--evidence-file", "verifier/evidence.json"],
        "inputs": [
            {"path": "verify/check.py", "from": str(CHECK_FILE)},
            {"path": "tests.json", "from": str(_tests_path(row))},
            # Upstream's executor and its aggregation, materialized from the pinned archive:
            # the check runs *their* code rather than re-implementing the metric.
            {"path": "testing_util.py", "from": str(DATASET_ROOT / EXECUTOR)},
            {"path": "pass_k_utils.py", "from": str(DATASET_ROOT / PASS_K)},
        ],
        "reward_file": "/verifier/reward.txt",
        "evidence_file": "/verifier/evidence.json",
        # Leave room beyond upstream's child budget for check startup and verdict writing.
        "timeout_s": 900,
    }
    return bind_scorer_input(spec, task_id(row["index"]), ["tests.json"])


_INDEXED = [{**row, "index": index} for index, row in enumerate(_raw())]

DEFAULT_ENV = {"kind": "files"}
ENV = {task_id(row["index"]): {"kind": "files"} for row in _INDEXED}
SPLIT = registry.default_split([{"task_id": task_id(row["index"])} for row in _INDEXED])

#: `None`: release_v6 publishes no study split, so the pool **is** the scored split and every
#: task in it belongs to the evaluation set -- which claims nothing about whether the pool is
#: the whole of LiveCodeBench. `--split official --side eval` scores the whole pool.
OFFICIAL_EVAL = None

#: Which rule produced `SPLIT`: `"hash"` -- the release has no study split, so the platform
#: imposes one. See `data/scaffolds/qa/base.py::division` for the QA half of this rule.
SPLIT_SOURCE = "hash"

SETUP = {task_id(row["index"]): _setup(row) for row in _INDEXED}
VERIFY = {task_id(row["index"]): _verify(row) for row in _INDEXED}


# ----------------------------------------------------------------------------- the oracle ---


def references() -> dict[str, str]:
    """`{question_id: solution}` -- the authored references, read from disk.

    Read rather than embedded so that a test can check each one against its own problem's
    tests, and so that adding one is adding a file.
    """
    return {path.stem: path.read_text(encoding="utf-8")
            for path in sorted(REFERENCES.glob("*.py"))}


def _coverage() -> str:
    have = sorted(references())
    known = {row["question_id"] for row in _INDEXED}
    unknown = [qid for qid in have if qid not in known]
    if unknown:
        raise ValueError(f"references/{unknown} do not name a problem in the pool")
    return f"{len(have)} of {PROBLEMS} problems ({', '.join(have) if have else 'none'})"


#: The two attributes the lint requires of an `authored` oracle. Computed rather than written
#: by hand, so that adding a reference cannot leave the coverage claim stale.
ORACLE_VALIDATED_BY = ("the problem's own released tests, executed by the pinned upstream "
                       "testing_util.run_test inside data/datasets/livecodebench/verify/check.py: a "
                       "reference is accepted only because the released tests accept it")
ORACLE_COVERAGE = _coverage()


def oracle_files(task: dict) -> dict[str, str]:
    """The authored reference, or an error that says cover is missing for this task.

    Raising matters: returning nothing, or something empty, would let the oracle arm report
    "the check is reachable" on tasks where nothing has been shown -- which is the claim
    `ORACLE_COVERAGE` exists to prevent.
    """
    row = _row_for(task)
    reference = references().get(row["question_id"])
    if reference is None:
        raise ValueError(
            f"livecodebench: no authored reference for {row['question_id']} "
            f"({task['task_id']}); this dataset's ORACLE_COVERAGE is {ORACLE_COVERAGE}. Run "
            f"the control on a task that has one, or write the reference and re-run "
            f"`python3 tools/dataset_lint.py --dataset livecodebench`.")
    return {"solution.py": reference}


def load() -> tuple[list[dict], dict[str, str]]:
    tasks = [{"task_id": task_id(row["index"]), "goal": _question(row), "inputs": [],
              "submission": {"kind": "files", "paths": ["solution.py"]}}
             for row in _INDEXED]
    # The scorable map is the human-readable grade slot. This dataset's grade is a program's
    # behaviour, so the value names the metric rather than pretending to be a gold answer.
    return tasks, {task["task_id"]: "pass@1 over the released tests" for task in tasks}


def downloads() -> list[dict]:
    base = "https://hf-mirror.com/datasets/livecodebench/code_generation_lite/resolve/"
    entries = [{
        "kind": "http", "name": name, "version": REVISION,
        "url": base + REVISION + "/" + name, "dst": "_src/" + name,
        "sha256": digest, "size": "release_v6 shard",
        "note": "One of the six **disjoint** release_v6 shards: 400+111+101+101+167+175 = 1,055 problems, 1,055 distinct ids, every digest verified against the files on disk at onboarding. `prepare.py` decodes them into pool.jsonl (2.0 MB of metadata) plus tests/<question_id>.json (9.38 GB decoded; largest single problem 199 MB), because the platform materializes VERIFY.inputs per task and the tests cannot live in the pool."}
        for name, digest in _FILES.items()]
    entries.append({
        "kind": "http", "name": "LiveCodeBench upstream source", "version": UPSTREAM_COMMIT,
        "url": "https://codeload.github.com/LiveCodeBench/LiveCodeBench/tar.gz/" + UPSTREAM_COMMIT,
        "size": "3,361,935 bytes compressed source", "sha256": SOURCE_SHA256,
        "dst": "_src/upstream.tar.gz", "extract_to": "_src/upstream",
        "note": "Pins `lcb_runner/evaluation/testing_util.py` (the executor, including the reliability_guard upstream warns is not a sandbox -- the platform's verification namespace is) and `pass_k_utils.py` (the aggregation). Both are copied into the verification namespace as VERIFY.inputs, so the metric runs upstream's bytes; `data/datasets/livecodebench.py` also transcribes pass_k_utils.py lines 4-53 verbatim. Never enters an agent SETUP."})
    return entries


def provision_status() -> dict:
    tests = DATASET_ROOT / TESTS_DIR
    return {"root": str(DATASET_ROOT),
            "present": ((DATASET_ROOT / POOL).is_file()
                        and (DATASET_ROOT / EXECUTOR).is_file()
                        and tests.is_dir() and any(tests.glob("*.json"))),
            "pool": str(DATASET_ROOT / POOL),
            "tests": str(tests),
            "tasks": len(_RAW) if _RAW else 0}


__all__ = ["KIND", "ORACLE_SOURCE", "ORACLE_VALIDATED_BY", "ORACLE_COVERAGE",
           "DATASET_ROOT", "REVISION", "UPSTREAM_COMMIT", "SOURCE_SHA256", "PROBLEMS",
           "POOL", "POOL_SHA256", "TESTS_DIR", "SYSTEM_MESSAGE", "WITH_STARTER",
           "WITHOUT_STARTER", "estimate_pass_at_k", "compute_metrics_from_results",
           "decode_private_test_cases", "task_id", "references", "oracle_files", "load",
           "downloads", "provision_status", "SETUP", "VERIFY", "SPLIT", "SPLIT_SOURCE",
           "OFFICIAL_EVAL", "ENV", "DEFAULT_ENV"]
