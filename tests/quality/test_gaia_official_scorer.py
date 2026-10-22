"""GAIA: the release, the placeholder trap, the pinned scorer, and the two leaks.

Everything here is about one of five things, and each one is a claim the adapter makes:

* **the pool** -- 165 validation rows, the digest `prepare.py` recorded, and the 38 attachments
  matching the sizes and digests the pool header carries;
* **the exclusion** -- the test split's 301 rows carry the placeholder gold `"?"`, which is
  measured here from the declared file rather than asserted in prose. A scorer run over those
  rows would compare a submission against `"?"` and report a number;
* **the scorer** -- `_src/scorer.py` is the pinned leaderboard file (sha256), the check imports
  *that* function rather than a transcription, and the three normalization branches are pinned by
  a differential so a re-implementation cannot drift into agreement on the easy third of the pool;
* **the two leaks** -- `Annotator Metadata` (the annotator's search steps) and `file_path` (an
  internal release path) must not appear anywhere a task shows the agent;
* **the containers** -- both control arms through `eval.run.runner.run_one`, because for an `exec`
  dataset the by-hand arm has no meaningful form (the verifier's paths are container-absolute).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import data.datasets.gaia as dataset  # noqa: E402

CHECK = ROOT / "data" / "datasets" / "gaia" / "verify" / "check.py"
CONTROL_TASK = "gaia/0"


# ------------------------------------------------------------------------ the release ---


def test_the_pool_is_the_pinned_one():
    pool = dataset.DATASET_ROOT / dataset.POOL
    if not pool.is_file():
        pytest.skip("gaia: run `python3 data/datasets/gaia/prepare.py` (needs HF_TOKEN)")
    assert hashlib.sha256(pool.read_bytes()).hexdigest() == dataset.POOL_SHA256
    header = json.loads(pool.open(encoding="utf-8").readline())
    assert header["rows"] == dataset.ROWS == 165
    assert header["levels"] == {"1": 53, "2": 86, "3": 26}
    assert header["attachment_count"] == 38
    assert len(dataset.rows()) == dataset.ROWS
    assert all(row["gold"].strip() not in ("", "?") for row in dataset.rows())


def test_every_attachment_matches_the_pool_header():
    """The pool is 85 KB and pins 10 MB of attachments through their recorded digests, so the
    thing that has to be checked is that the recorded digests are the files'."""
    header = json.loads((dataset.DATASET_ROOT / dataset.POOL).open(encoding="utf-8").readline())
    for name, recorded in header["attachments"].items():
        path = dataset.DATASET_ROOT / dataset.ATTACHMENTS / name
        assert path.is_file(), name
        body = path.read_bytes()
        assert len(body) == recorded["bytes"], name
        assert hashlib.sha256(body).hexdigest() == recorded["sha256"], name


def test_the_test_split_is_excluded_because_its_gold_is_a_placeholder():
    """Measured on the declared file, not on the note that recommended the exclusion."""
    import pyarrow.parquet as pq

    path = dataset.SRC / dataset.TEST_LOCAL
    if not path.is_file():
        pytest.skip("gaia: fetch the test metadata")
    rows = pq.read_table(path).to_pylist()
    assert len(rows) == 301
    assert sum(1 for row in rows if str(row["Final answer"]).strip() == "?") == 301


def test_the_scorer_is_the_pinned_leaderboard_file():
    if not dataset.SCORER.is_file():
        pytest.skip("gaia: fetch the pinned scorer")
    assert hashlib.sha256(dataset.SCORER.read_bytes()).hexdigest() == dataset.SCORER_SHA256
    assert "def question_scorer(" in dataset.SCORER.read_text(encoding="utf-8")


def test_the_three_normalization_branches_are_upstreams():
    """The differential: import upstream's function from the pinned file and pin the behaviour a
    hand-written exact match would get wrong.

    The comma case is the interesting one and it corrected *this test's* first expectation:
    `question_scorer("34689", "34,689")` is **False**, because a gold containing a comma takes
    the list branch and `["34689"]` is not `["34", "689"]`. A "compare after stripping
    punctuation" implementation returns True there and looks right on every other row.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gaia_upstream_scorer", dataset.SCORER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [
        ("egalitarian", "egalitarian", True), ("Egalitarian.", "egalitarian", True),
        ("  egal itarian ", "egalitarian", True), ("$41", "41", True), ("41.0", "41", True),
        ("41", "41.5", False), ("a, b", "a,b", True), ("a; b", "a,b", True),
        ("a, b, c", "a,b", False), ("34689", "34,689", False), ("34, 689", "34,689", True),
    ]
    for answer, gold, expected in cases:
        assert bool(module.question_scorer(answer, gold)) is expected, (answer, gold)


# -------------------------------------------------------------------- the check ---


def _run_check(work: Path, answer: str | None, gold: str | None,
               *, placeholder: bool = False) -> tuple[int, str | None, dict]:
    """The check, materialized the way the platform materializes it (`/scorer.py` beside it)."""
    (work / "scorer.py").write_text(dataset.SCORER.read_text(encoding="utf-8"), encoding="utf-8")
    (work / "check.py").write_text(CHECK.read_text(encoding="utf-8"), encoding="utf-8")
    (work / "answer.txt").write_text("" if answer is None else answer, encoding="utf-8")
    (work / "gold.json").write_text(json.dumps(
        {"task_id": "gaia/0", "gold": "?" if placeholder else (gold or ""), "level": "1"}),
        encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "check.py", "--answer", "answer.txt", "--gold", "gold.json",
         "--reward-file", "verifier/reward.txt", "--evidence-file", "verifier/evidence.json"],
        cwd=work, capture_output=True, text=True, timeout=120)
    reward_file = work / "verifier" / "reward.txt"
    reward = reward_file.read_text().strip() if reward_file.is_file() else None
    evidence = json.loads((work / "verifier" / "evidence.json").read_text())
    return proc.returncode, reward, evidence


def test_the_check_scores_the_released_answer_and_a_wrong_one(tmp_path):
    code, reward, evidence = _run_check(tmp_path, "Egalitarian.", "egalitarian")
    assert (code, reward, evidence["measured"], evidence["reward"]) == (0, "1", True, 1.0)
    assert "exact match" in evidence["why"] and evidence["scorer_said"]
    other = tmp_path / "wrong"
    other.mkdir()
    code, reward, evidence = _run_check(other, "somebody else", "egalitarian")
    assert (code, reward, evidence["measured"], evidence["reward"]) == (0, "0", True, 0.0)


def test_a_missing_answer_is_a_measured_zero_and_a_placeholder_gold_is_not(tmp_path):
    code, reward, evidence = _run_check(tmp_path, None, "egalitarian")
    assert (code, reward, evidence["measured"]) == (0, "0", True)
    assert "no answer" in evidence["why"]

    empty = tmp_path / "placeholder"
    empty.mkdir()
    code, reward, evidence = _run_check(empty, "anything", None, placeholder=True)
    assert code == 3 and reward is None
    assert evidence["measured"] is False and "placeholder" in evidence["error"]


# ------------------------------------------------------------------- the prompt ---


def test_the_prompt_carries_neither_the_annotator_steps_nor_internal_paths():
    """`Annotator Metadata` is the solution path, and `file_path` is a release-internal path.
    Both are dropped by `prepare.py`; this is the assertion that they stay dropped."""
    import pyarrow.parquet as pq

    parquet = dataset.SRC / dataset.VALIDATION_LOCAL
    if not parquet.is_file():
        pytest.skip("gaia: fetch the validation metadata")
    source = pq.read_table(parquet).to_pylist()
    steps = [str(row["Annotator Metadata"]["Steps"]) for row in source
             if row.get("Annotator Metadata")]
    assert steps and all(text.strip() for text in steps), "the annotator steps exist in the release"
    for task_id in (dataset.task_id(0), dataset.task_id(37)):
        row = dataset.rows()[int(task_id.rsplit("/", 1)[1])]
        setup = json.dumps(dataset.SETUP[task_id])
        goal = next(t["goal"] for t in dataset.load()[0] if t["task_id"] == task_id)
        assert "Annotator Metadata" not in setup and "Annotator Metadata" not in goal
        assert row["question"] in setup
        # Not even the *shape* of the release paths, and no step text from any row.
        assert "2023/validation" not in setup and str(parquet) not in setup
        assert not any(text[:40] in setup or text[:40] in goal for text in steps)


def test_the_environment_is_networked_and_says_so():
    """GAIA's web capability is explicit and runs on the internal task network."""
    env = dataset.ENV[dataset.task_id(0)]
    assert env["kind"] == "exec" and env["network"] == "services"
    assert env["web"] == {"search": True, "fetch": True}
    assert env["image"] == dataset.IMAGE and env["workdir"] == dataset.WORKDIR
    assert dataset.OFFICIAL_EVAL is None and dataset.SPLIT_SOURCE == "none"
    assert dataset.SPLIT is None, "the pool is GAIA's validation file; it declares no division"
    assert dataset.ORACLE_SOURCE == "released"     # the released answer *is* the oracle


# ------------------------------------------------------------------- the controls ---


def test_platform_control_refuses_regardless_of_arm():
    """Both arms are refused before dispatch: GAIA's policy needs an observer it has none of.

    This test used to assert a numeric container score for `--oracle` and `--noop`. That
    encoded a run the platform no longer calls measured, and the reason is specific
    rather than cautious: GAIA is an `exec` task on a container with a service network,
    and nothing in this checkout can observe the container's cpu/memory/pids limits, the
    service network policy, or writable byte/inode bounds. A number from it would claim
    enforcement that was never verified.

    The refusal is therefore the guarantee, and it is asserted in two places: the hook
    directly (which names the policy fields) and the control CLI (which must produce no
    score for either arm). A real container run belongs to the staged diagnostic path
    once a trusted backend observer exists; it is not a unit test.
    """
    from eval.run import runner

    task = {"task_id": CONTROL_TASK}
    for env in (dataset.ENV[CONTROL_TASK], {"kind": "exec", "state": "container",
                                            "network": "services"}):
        refusal = runner._refuse_unobserved_policies(task, env,
                                                     dataset.VERIFY[CONTROL_TASK])
        assert refusal is not None, "a container/service exec task must be refused"
        receipt = refusal["invalid"]["receipt"]
        assert receipt["reason"] == "backend_observation_unavailable"
        assert receipt["measured"] is False and receipt["production_authorized"] is False
        assert "score" not in refusal
        assert {"env.exec_defaults", "env.state=container"} <= set(receipt["policy_fields"])

    for arm in ("--oracle", "--noop"):
        proc = subprocess.run([sys.executable, "data/datasets/gaia/control.py", "--platform", arm,
                               "--task-id", CONTROL_TASK],
                              cwd=ROOT, capture_output=True, text=True, timeout=900)
        combined = proc.stdout + proc.stderr
        assert "sandbox=True" in combined
        assert "score    None" in combined, (
            f"{arm} produced a score for a task the platform refuses: {combined[-400:]}")
        assert proc.returncode != 0, "a refused control must not report success"
