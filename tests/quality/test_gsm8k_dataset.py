"""The gsm8k adapter's contract: what it returns, what it refuses, and what it grades.

The judgement lessons live in `docs/dataset-onboarding-skill.md`; this file is the part a
program can decide. The one with the most weight is
`test_the_check_re_materializes_the_gold`: the check is handed the gold through
`VERIFY.inputs`, and the platform writes its copy immediately before the check runs. This
proves that by editing `<workdir>/gold.json` to the answer the agent did *not* give and
asserting the grade does not move.

All rows are synthetic, including the rows labelled `test`. Verifier subprocesses
use an empty table reader so grading cannot read any installed benchmark split.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def gsm8k(synthetic_gsm8k):
    return synthetic_gsm8k


def _materialize(module, record: dict, workdir: Path, *, oracle: bool) -> None:
    """What `SETUP` + `VERIFY.inputs` put in the workdir, as `run_one` does it."""
    for entry in (module.SETUP.get(record["task_id"]) or {}).get("files", []):
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    verifier = module.VERIFY[record["task_id"]]
    for entry in verifier.get("inputs") or []:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    if oracle:
        # The oracle arm submits the reference answer through the CLI, not by writing
        # `answer.txt` itself: that is what a harness would do.
        answer = str(module._RECORDS[record["index"]]["gold"]["answer"])
        subprocess.run([sys.executable, "qa/bin/qa", "submit", answer], cwd=workdir,
                       check=True, capture_output=True, text=True)


def _run_check(module, record: dict, workdir: Path, *,
               re_materialize: bool = True) -> tuple[float, dict]:
    """Run the check the way `eval/verification/verifier.py` does: re-materialize, then run.

    The re-materialization is the part that matters. It is what makes the grade
    independent of the workdir's contents, and a test that skipped it would pass while
    proving the opposite of what it claims -- measured: the first version of
    `test_the_check_re_materializes_the_gold` did exactly that.
    """
    verifier = module.VERIFY[record["task_id"]]
    if re_materialize:
        for entry in verifier.get("inputs") or []:
            target = workdir / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if "content" in entry:
                target.write_text(entry["content"])
            else:
                shutil.copy2(entry["from"], target)
    # The copied production scorer imports its adapter, whose build normally reads
    # both tables. Simulate an unprovisioned verifier without accessing either split.
    bootstrap = (
        "import runpy, sys; from data.scaffolds.qa import base; "
        "base.parquet_rows = lambda path: []; "
        "check_path = sys.argv.pop(1); "
        "runpy.run_path(check_path, run_name='__main__')"
    )
    proc = subprocess.run([sys.executable, "-c", bootstrap, *verifier["argv"][1:]], cwd=workdir,
                          capture_output=True, text=True)
    reward_path = workdir / "verifier" / "reward.txt"
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = workdir / "verifier" / "evidence.json"
    evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
    return reward, {"stdout": proc.stdout, "stderr": proc.stderr, **evidence}


@pytest.fixture
def record(gsm8k):
    return gsm8k._RECORDS[0]


def test_load_preserves_the_declared_split_labels_on_synthetic_rows(gsm8k):
    """Three synthetic test rows and five train rows retain their declared sides."""
    tasks, scorable = gsm8k.load()
    assert len(tasks) == 8
    assert set(gsm8k.SPLIT["train"]) == {f"gsm8k/train/{i}" for i in range(5)}
    assert set(gsm8k.SPLIT["eval"]) == {f"gsm8k/test/{i}" for i in range(3)}
    assert gsm8k.SPLIT_SOURCE == "declared", "GSM8K publishes both sides"
    assert all(t["task_id"] and t["goal"] for t in tasks)
    assert set(scorable) == {t["task_id"] for t in tasks}


def test_the_official_evaluation_set_is_exactly_the_test_split(gsm8k):
    """What `--split official` means here, and it must not drift into the train side."""
    assert gsm8k.OFFICIAL_EVAL is not None
    assert set(gsm8k.OFFICIAL_EVAL) == set(gsm8k.SPLIT["eval"])
    assert all("/test/" in tid for tid in gsm8k.OFFICIAL_EVAL)
    assert not (set(gsm8k.OFFICIAL_EVAL) & set(gsm8k.SPLIT["train"]))


def test_the_declared_division_is_not_the_platform_hash_division(gsm8k):
    """The two rules must be distinguishable, or `split_sha` on a run means nothing.

    `--split declared` is GSM8K's own division; `--split default` is the platform's hash
    third of the same pool. Synthetic rows exercise the distinction without reading
    the released benchmark.
    """
    import data.scaffolds.registry as registry

    tasks = gsm8k.load()[0]
    default = registry.default_split(tasks)
    assert registry.split_sha(default) != registry.split_sha(gsm8k.SPLIT)
    assert default != gsm8k.SPLIT


def test_the_split_covers_every_task_exactly_once(gsm8k):
    ids = {t["task_id"] for t in gsm8k.load()[0]}
    train, eval_ = set(gsm8k.SPLIT["train"]), set(gsm8k.SPLIT["eval"])
    assert train | eval_ == ids
    assert not (train & eval_)
    assert train and eval_


def test_the_goal_carries_the_question_and_no_answer(gsm8k):
    """The goal is the question plus the interface; the gold is nowhere in it.

    Two things make a naive `gold not in goal` check wrong here, and both are worth
    knowing: the reference answer is a bare number that can legitimately occur inside the
    question text (`18` appears in the Janet problem), and `scorable` carries the gold for
    the platform's fallback path. So the check is on the *record fields* -- `gold` and
    `verbatim` -- rather than on a substring of the rendered goal.
    """
    records = {r["task_id"]: r for r in gsm8k._RECORDS}
    for task in gsm8k.load()[0][:50]:
        record = records[task["task_id"]]
        assert record["question"] in task["goal"]
        assert "answer.txt" in task["goal"]
        assert "write `#### 18`, including the space" in task["goal"]
        assert record["gold"]["verbatim"] not in task["goal"]


def test_goal_is_independent_of_gold_despite_answer_format_markers(gsm8k):
    question = "Synthetic question with 18 units; compute a value."
    first = {"answer": "987654321",
             "verbatim": "PRIVATE solution alpha\n#### 987654321"}
    second = {"answer": "-123456789.5",
              "verbatim": "PRIVATE solution beta\n#### -123456789.5"}
    goal = gsm8k._goal(question, first)
    assert goal == gsm8k._goal(question, second) == gsm8k._goal(question, {})
    assert question in goal
    assert "write `#### 18`, including the space" in goal
    for gold in (first, second):
        assert gold["answer"] not in goal
        assert gold["verbatim"] not in goal


def test_the_task_dict_carries_no_grade(gsm8k):
    """`run_one` writes every task dict to `task.json` and hands it to the harness."""
    forbidden = {"verify", "verifier", "setup", "expected", "scorable", "env"}
    for task in gsm8k.load()[0][:20]:
        assert not (set(task) & forbidden), task["task_id"]


def test_the_verifier_declares_the_contract_paths(gsm8k, record):
    verifier = gsm8k.VERIFY[record["task_id"]]
    assert verifier["kind"] == "command"
    assert verifier["reward_file"] == "/verifier/reward.txt"
    assert verifier["evidence_file"] == "/verifier/evidence.json"
    assert verifier["argv"][0] == "python3"
    paths = {entry["path"] for entry in verifier["inputs"]}
    assert {"gold.json", "qa/lib/verify/check.py", "data/datasets/gsm8k.py"} <= paths


def test_every_verifier_input_is_re_materializable(gsm8k):
    """No `from` may point at a path that is missing -- the platform copies these in."""
    for task_id, verifier in gsm8k.VERIFY.items():
        for entry in verifier["inputs"]:
            if "from" in entry:
                assert Path(entry["from"]).is_file(), (task_id, entry)
            else:
                assert "content" in entry


def test_the_check_grades_the_reference_answer_as_one(gsm8k, record, tmp_path):
    _materialize(gsm8k, record, tmp_path, oracle=True)
    reward, evidence = _run_check(gsm8k, record, tmp_path)
    assert reward == 1.0, evidence
    assert evidence["measured"] is True
    assert evidence["official"]["filter"] == "decimal-aware-extract"


def test_the_check_preserves_decimal_extraction_and_metric_identity(gsm8k, tmp_path):
    record = next(r for r in gsm8k._RECORDS if r["gold"]["answer"] == "3.5")
    _materialize(gsm8k, record, tmp_path, oracle=True)
    reward, evidence = _run_check(gsm8k, record, tmp_path)
    assert reward == 1.0, evidence
    assert evidence["measured"] is True
    assert evidence["official"]["filter"] == "decimal-aware-extract"
    assert evidence["official"]["flexible_extract"] == "3.5"


def test_the_check_grades_no_answer_as_zero(gsm8k, record, tmp_path):
    _materialize(gsm8k, record, tmp_path, oracle=False)
    reward, evidence = _run_check(gsm8k, record, tmp_path)
    assert reward == 0.0, evidence
    assert evidence["measured"] is True, "no answer is a measured zero, not unmeasured"


def test_the_check_grades_a_wrong_answer_as_zero(gsm8k, record, tmp_path):
    _materialize(gsm8k, record, tmp_path, oracle=False)
    (tmp_path / "answer.txt").write_text("999999\n")
    reward, _ = _run_check(gsm8k, record, tmp_path)
    assert reward == 0.0


def test_the_check_re_materializes_the_gold(gsm8k, record, tmp_path):
    """The agent's own `gold.json` is not what is graded.

    The workdir is writable by the subject, so a check that trusted the copy it found
    would let a harness grade itself. Here the check's copy is rewritten after the
    harness would have stopped, exactly as `eval/verification/verifier.py` does, and the planted gold
    (the agent's answer) does not move the reward.
    """
    _materialize(gsm8k, record, tmp_path, oracle=False)
    (tmp_path / "answer.txt").write_text("999999\n")
    verifier = gsm8k.VERIFY[record["task_id"]]
    for entry in verifier["inputs"]:
        if entry["path"] == "gold.json":
            (tmp_path / "gold.json").write_text(json.dumps({"answer": "999999"}))
    reward, evidence = _run_check(gsm8k, record, tmp_path)
    assert reward == 0.0, evidence
    assert evidence["gold"]["answer"] == str(record["gold"]["answer"])


def test_the_check_reports_unmeasured_when_the_gold_is_absent(gsm8k, record, tmp_path):
    """No gold means no measurement -- not the agent's zero.

    `eval/verification/verifier.py` reads `evidence["measured"] is False` and marks the task invalid,
    which is a different outcome from a 0 the agent earned.
    """
    _materialize(gsm8k, record, tmp_path, oracle=True)
    (tmp_path / "gold.json").unlink()
    # **No** re-materialization here: this test is about what the check does when its
    # trusted input is missing on a by-hand run.
    reward, evidence = _run_check(gsm8k, record, tmp_path, re_materialize=False)
    assert reward == -1.0, "no reward file is written when the check cannot measure"
    assert evidence["measured"] is False
    assert "gold" in evidence["error"]


def test_the_cli_refuses_an_empty_answer(gsm8k, tmp_path):
    """A blank `answer.txt` cannot be told from no answer at all."""
    _materialize(gsm8k, gsm8k._RECORDS[0], tmp_path, oracle=False)
    proc = subprocess.run([sys.executable, "qa/bin/qa", "submit", "   "],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 2
    assert not (tmp_path / "answer.txt").exists()


def test_a_quoted_answer_is_unwrapped_before_scoring(gsm8k):
    """`'18'` and `18` are the same answer; the quotes are a formatting artefact.

    Measured on the first smoke run of another dataset in this batch, where a shell
    snippet submitted a quoted value: an exact-match scorer would have failed it for a
    reason the record could not show.
    """
    check = _load_check()
    assert check.unwrap('"18"') == "18"
    assert check.unwrap("'Paris'") == "Paris"
    assert check.unwrap("18") == "18"
    assert check.unwrap('He said "18"') == 'He said "18"'


def _load_check():
    import importlib.util
    path = ROOT / "data" / "scaffolds" / "qa" / "verify" / "check.py"
    spec = importlib.util.spec_from_file_location("qa_check_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
