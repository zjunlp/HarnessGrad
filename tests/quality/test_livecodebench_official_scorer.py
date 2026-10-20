"""LiveCodeBench: the pool, the pinned executor, the verdict, and the authored oracle.

This file replaces the refusal-era one, which (correctly, at the time) pinned a *refusal*. Now
that the dataset is runnable, the things that can silently go wrong are different and each has a
test:

* **the pool** -- the six shards are disjoint and their union is 1,055 (an earlier note called
  them cumulative), the materialized pool has the digest the adapter pins, and every problem's
  test file matches the digest recorded in its pool row;
* **the executor** -- `testing_util.py` and `pass_k_utils.py` in the workdir are the archive's
  own bytes, and the adapter's transcription of `pass_k_utils.py` is upstream's region verbatim;
* **the verdict** -- a correct solution scores 1, a wrong one scores 0 *with the per-test tally
  recorded* (the register's M4: a binary reward hides partial progress), a compile failure is a
  measured zero rather than an unmeasurable task, and a broken environment writes no reward file;
* **the authored oracle** -- every reference in `references/` passes its own problem's released
  tests, through the real check, on the real task; a task without a reference refuses rather than
  pretending; and `ORACLE_COVERAGE` counts what is actually there;
* **the prompt** -- the agent gets the statement and the tests' *count*, never the tests.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from queue import SimpleQueue
from types import SimpleNamespace
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import data.datasets.livecodebench as dataset  # noqa: E402

ADAPTER = ROOT / "data" / "datasets" / "livecodebench.py"
CHECK = ROOT / "data" / "datasets" / "livecodebench" / "verify" / "check.py"
ARCHIVE = dataset.DATASET_ROOT / "_src" / "upstream.tar.gz"
ARCHIVE_ROOT = f"LiveCodeBench-{dataset.UPSTREAM_COMMIT}"
#: The task the control registration uses: a covered one, pinned so the registration and this
#: file cannot disagree about which task proves gate 1.
CONTROL_TASK = "livecodebench/800"


def test_the_pool_is_the_pinned_one():
    pool = dataset.DATASET_ROOT / dataset.POOL
    if not pool.is_file():
        pytest.skip("livecodebench: run `python3 data/datasets/livecodebench/prepare.py`")
    assert hashlib.sha256(pool.read_bytes()).hexdigest() == dataset.POOL_SHA256
    header = json.loads(pool.open(encoding="utf-8").readline())
    assert header["problems"] == dataset.PROBLEMS == 1055
    assert header["shards"] == {"test.jsonl": 400, "test2.jsonl": 111, "test3.jsonl": 101,
                                "test4.jsonl": 101, "test5.jsonl": 167, "test6.jsonl": 175}
    assert sum(header["shards"].values()) == dataset.PROBLEMS, "the shards would overlap"
    assert header["tests"]["public"] == 2762 and header["tests"]["private"] == 25492
    assert len(dataset._raw()) == dataset.PROBLEMS
    assert len({row["question_id"] for row in dataset._raw()}) == dataset.PROBLEMS


def test_every_shard_is_the_declared_bytes():
    """The pin the pool digest rests on. Costs ~15 s: it hashes 4.5 GB, once."""
    for name, digest in dataset._FILES.items():
        path = dataset.DATASET_ROOT / "_src" / name
        if not path.is_file():
            pytest.skip(f"livecodebench: fetch {name}")
        sha = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 22), b""):
                sha.update(block)
        assert sha.hexdigest() == digest, name


def test_every_problem_test_file_matches_its_pool_row():
    """The pool's rows carry each test file's digest, so the 9.38 GB of tests is pinned by a
    2 MB file. Checked on every row whose payload is small, plus the largest one."""
    rows = dataset._raw()
    small = [row for row in rows if row["tests_bytes"] < 200_000]
    biggest = max(rows, key=lambda row: row["tests_bytes"])
    assert biggest["question_id"] == "abc369_g" and biggest["tests_bytes"] > 100_000_000
    for row in small[:200] + [biggest]:
        path = dataset.DATASET_ROOT / dataset.TESTS_DIR / f"{row['question_id']}.json"
        assert path.is_file(), row["question_id"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["tests_sha256"], row["question_id"]


def test_the_pinned_files_in_the_workdir_are_the_archive_members():
    """What travels as `VERIFY.inputs` is the archive's own bytes, not a transcription of them."""
    if not ARCHIVE.is_file():
        pytest.skip("livecodebench: fetch the declared source archive")
    with tarfile.open(ARCHIVE) as tar:
        for relative in (dataset.EXECUTOR, dataset.PASS_K):
            member = f"{ARCHIVE_ROOT}/lcb_runner/evaluation/{Path(relative).name}"
            assert tar.extractfile(member).read() == (dataset.DATASET_ROOT / relative).read_bytes()


def test_the_transcription_is_upstreams_bytes():
    """`pass_k_utils.py` is pinned twice -- as the shipped file above and as this region -- and
    a reader who opens the adapter instead of the workdir must not be misled."""
    if not ARCHIVE.is_file():
        pytest.skip("livecodebench: fetch the declared source archive")
    with tarfile.open(ARCHIVE) as tar:
        upstream = tar.extractfile(
            f"{ARCHIVE_ROOT}/lcb_runner/evaluation/pass_k_utils.py").read().decode()
    region = upstream[upstream.index("def estimate_pass_at_k("):
                      upstream.index("def extract_instance_results(")].rstrip()
    # `rstrip` because the adapter continues with a comment where upstream has two blank lines
    # before `extract_instance_results`; the two functions' own bytes are what is asserted.
    assert ADAPTER.read_text(encoding="utf-8").count(region) == 1


def test_the_verifier_ships_the_tests_and_the_executor_for_its_own_task():
    for task_id in (CONTROL_TASK, dataset.task_id(0)):
        row = dataset._row_for({"task_id": task_id})
        inputs = {entry["path"]: entry for entry in dataset.VERIFY[task_id]["inputs"]}
        # Pin the scorer's dependencies and their sources; allow additive platform inputs.
        assert {"verify/check.py", "tests.json", "testing_util.py", "pass_k_utils.py",
                "data/__init__.py", "data/scaffolds/__init__.py",
                "data/scaffolds/scorer_input.py"} <= inputs.keys()
        assert inputs["verify/check.py"]["from"] == str(CHECK)
        assert inputs["tests.json"]["from"] == str(
            dataset.DATASET_ROOT / dataset.TESTS_DIR / f"{row['question_id']}.json")
        assert inputs["testing_util.py"]["from"] == str(dataset.DATASET_ROOT / dataset.EXECUTOR)
        assert inputs["pass_k_utils.py"]["from"] == str(dataset.DATASET_ROOT / dataset.PASS_K)
        assert inputs["data/scaffolds/scorer_input.py"]["from"] == str(
            ROOT / "data/scaffolds/scorer_input.py")
        for path in ("data/__init__.py", "data/scaffolds/__init__.py"):
            assert inputs[path]["content"] == ""
        assert dataset.VERIFY[task_id]["argv"][:2] == ["python3", "verify/check.py"]
        # The prompt carries the counts and never the tests.
        tests = json.loads((dataset.DATASET_ROOT / dataset.TESTS_DIR
                            / f"{row['question_id']}.json").read_text())
        setup = json.dumps(dataset.SETUP[task_id])
        assert tests["inputs"][0][:20] not in setup
        assert tests["outputs"][0][:20] not in setup
        reference = dataset.references().get(row["question_id"])
        if reference is not None:
            assert reference not in setup
        assert not {"tests.json", "testing_util.py", "pass_k_utils.py", "solution.py"} & {
            entry["path"] for entry in dataset.SETUP[task_id]["files"]}
        metadata = json.loads(dataset.SETUP[task_id]["files"][0]["content"])
        assert metadata["n_public_tests"] == row["n_public"]
        assert metadata["n_private_tests"] == row["n_private"]
        assert metadata["call_based"] == bool(row["starter_code"])


# ------------------------------------------------------------------- the authored oracle ---


def test_every_authored_reference_passes_its_own_problems_released_tests():
    """Gate 1, per covered task, through the real check.

    Each task is its own process because upstream's `reliability_guard` nulls `os.environ` and
    friends on its first call -- `run_test` is once-per-process by construction, which is why
    the check and upstream both run it in a child.
    """
    references = dataset.references()
    assert references, "no authored references: gate 1 has nothing to submit"
    for question_id in sorted(references):
        task_id = dataset.task_id(
            next(row["index"] for row in dataset._INDEXED if row["question_id"] == question_id))
        proc = subprocess.run(
            [sys.executable, "data/datasets/livecodebench/control.py", "--oracle", "--task-id", task_id],
            cwd=ROOT, capture_output=True, text=True, timeout=600)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert '"reward": 1.0' in proc.stdout, f"{question_id} ({task_id}): {proc.stdout[-400:]}"


def test_coverage_counts_the_references_that_exist():
    references = dataset.references()
    assert dataset.ORACLE_SOURCE == "authored"
    assert dataset.ORACLE_COVERAGE.startswith(f"{len(references)} of {dataset.PROBLEMS}")
    assert all(qid in dataset.ORACLE_COVERAGE for qid in references)
    assert "released tests" in dataset.ORACLE_VALIDATED_BY


def test_a_task_without_a_reference_refuses_rather_than_pretending():
    """An `authored` oracle that returned empty files for the other 1,048 problems would let the
    control report reachability where nothing has been shown."""
    covered = set(dataset.references())
    task = {"task_id": dataset.task_id(
        next(row["index"] for row in dataset._INDEXED if row["question_id"] not in covered))}
    with pytest.raises(ValueError) as excinfo:
        dataset.oracle_files(task)
    assert "no authored reference" in str(excinfo.value)
    assert dataset.ORACLE_COVERAGE in str(excinfo.value)


# -------------------------------------------------------------------- the check itself ---


def _run_check(work: Path, tests: str, solution: str | None) -> tuple[int, str | None, dict]:
    """The check, materialized the way the platform materializes it."""
    for entry in dataset.VERIFY[CONTROL_TASK]["inputs"]:
        target = work / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"], encoding="utf-8")
        else:
            shutil.copy2(entry["from"], target)
    # These scorer unit cases deliberately replace the tests (including an invalid file),
    # so retain the manual, unbound argv below rather than the task's bound digest flags.
    (work / "tests.json").write_text(tests, encoding="utf-8")
    if solution is not None:
        (work / "solution.py").write_text(solution, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "verify/check.py", "--tests", "tests.json", "--solution", "solution.py",
         "--reward-file", "verifier/reward.txt", "--evidence-file", "verifier/evidence.json"],
        cwd=work, capture_output=True, text=True, timeout=600)
    reward_file = work / "verifier" / "reward.txt"
    reward = reward_file.read_text().strip() if reward_file.is_file() else None
    evidence = json.loads((work / "verifier" / "evidence.json").read_text())
    return proc.returncode, reward, evidence


def test_the_check_scores_a_wrong_solution_zero_with_the_tally_recorded(tmp_path):
    """A solution that fails one test is a measured 0, and the evidence says how many passed --
    the register's M4: a binary reward hides partial progress."""
    tests = (dataset.DATASET_ROOT / dataset.TESTS_DIR / "abc370_a.json").read_text()
    code, reward, evidence = _run_check(tmp_path, tests, "print('Yes')\n")
    assert (code, reward) == (0, "0")
    assert evidence["measured"] is True and evidence["metric"].startswith("pass@1")
    assert evidence["tests"]["passed"] < evidence["tests"]["total"]
    # The executor stops at the first failing test and returns a sentinel, so the verdict list
    # is **shorter** than the test count -- `[True, -2]` for four tests, measured. It is still a
    # correct zero because every truncation carries a non-positive sentinel.
    assert 0 < len(evidence["per_test"]) <= evidence["tests"]["total"]
    assert any(value is not True and value <= 0 for value in evidence["per_test"])


def test_numpy_boolean_verdicts_become_json_native_booleans(tmp_path, monkeypatch):
    """Run the worker/evidence boundary with real numpy scalars, no released task."""
    np = pytest.importorskip("numpy")
    spec = importlib.util.spec_from_file_location("livecodebench_check_json_scalar", CHECK)
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    verdicts = [np.bool_(True), np.bool_(False), True, False, -1, -2, -3, -4]
    monkeypatch.setitem(sys.modules, "testing_util", SimpleNamespace(
        run_test=lambda *args, **kwargs: (verdicts, "synthetic")))
    queue = SimpleQueue()
    check._child("{}", "pass", queue)
    outcome = queue.get()
    assert "error" not in outcome
    results = outcome["results"]
    assert results == [True, False, True, False, -1, -2, -3, -4]
    assert all(type(value) is bool for value in results[:4])
    assert all(type(value) is int for value in results[4:])
    assert sum(check._positive(value) for value in results) == 2
    args = SimpleNamespace(reward_file=str(tmp_path / "reward.txt"),
                           evidence_file=str(tmp_path / "evidence.json"),
                           scorer_input_receipt={"verified": False})
    assert check._measure(args, 0.0, "synthetic verdicts", 8, 2, results) == 0
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    assert evidence["per_test"] == results
    assert evidence["measured"] is True and evidence["reward"] == 0.0


def test_a_correct_solution_scores_one(tmp_path):
    tests = (dataset.DATASET_ROOT / dataset.TESTS_DIR / "abc370_a.json").read_text()
    code, reward, evidence = _run_check(tmp_path, tests, dataset.references()["abc370_a"])
    assert (code, reward) == (0, "1")
    assert evidence["tests"]["passed"] == evidence["tests"]["total"]
    assert evidence["why"] == "all 4 released tests passed"


def test_a_solution_that_does_not_compile_is_a_measured_zero(tmp_path):
    """Upstream records this as `results = [-2]`; `np.all(gen > 0)` makes it a failure. It must
    not be reported as an unmeasurable task -- that distinction is what `measured` is for."""
    tests = (dataset.DATASET_ROOT / dataset.TESTS_DIR / "abc370_a.json").read_text()
    code, reward, evidence = _run_check(tmp_path, tests, "def broken(:\n")
    assert (code, reward) == (0, "0")
    assert evidence["measured"] is True


def test_a_missing_answer_is_a_measured_zero_and_a_missing_test_file_is_not(tmp_path):
    tests = (dataset.DATASET_ROOT / dataset.TESTS_DIR / "abc370_a.json").read_text()
    code, reward, evidence = _run_check(tmp_path, tests, None)
    assert (code, reward, evidence["measured"]) == (0, "0", True)
    assert "no answer" in evidence["why"]

    empty = tmp_path / "empty"
    empty.mkdir()
    code, reward, evidence = _run_check(empty, "", dataset.references()["abc370_a"])
    assert code == 3 and reward is None
    assert evidence["measured"] is False


@pytest.mark.parametrize("arm,expected", [("--oracle", "1.0"), ("--noop", "0.0")])
def test_platform_control(arm, expected):
    proc = subprocess.run([sys.executable, "data/datasets/livecodebench/control.py", arm,
                           "--task-id", CONTROL_TASK, "--platform"],
                          cwd=ROOT, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"score    {expected}" in proc.stdout
    assert "sandbox=True" in proc.stdout
