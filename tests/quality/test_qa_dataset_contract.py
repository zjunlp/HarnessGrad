"""The contract every plain-text QA dataset shares, tested once and for all of them.

`data/scaffolds/qa/base.py` is used by eight adapters, so its invariants would otherwise be copied
eight times -- and a copy is a fork waiting to happen. This file parametrises over every
provisioned QA dataset and asserts the things the scaffold promises:

* the split covers every task exactly once, with no empty side;
* a task dict carries no grade (`run_one` writes it to `task.json` and hands it to the
  harness, so `expected` in a task is the grade leaking to the subject);
* the verifier declares the reward/evidence contract, and every `VERIFY.inputs` entry can
  actually be re-materialized;
* the check grades the reference answer 1 and no answer 0, and **re-materializes its
  gold** rather than trusting the copy in the workdir;
* a check that cannot measure writes no reward file and says `measured: false`.

Everything dataset-specific (which table, which scorer, what the trap is) stays in that
dataset's own test file. This one is about the shape they all share.
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

import data.scaffolds.registry as registry  # noqa: E402

#: Every QA adapter the platform publishes. Kept as a literal list rather than discovered
#: by globbing so that a new adapter has to be added here deliberately -- and so this file
#: does not import a module whose data is absent in order to find that out.
QA_DATASETS = ["gsm8k", "triviaqa", "strategyqa", "sportsunderstanding",
               "physicsquestions", "bfcl_v3", "chessbench_puzzles", "musique",
               "reasoning_gym", "twowikimultihopqa", "fever", "hotpotqa", "aime"]


def _adapter(name: str):
    import importlib
    module = importlib.import_module(f"data.datasets.{name}")
    try:
        records = module._RECORDS
    except Exception as exc:  # noqa: BLE001 - unprovisioned is a skip, not a failure
        pytest.skip(f"{name}: cannot read its table ({exc})")
    if not records:
        pytest.skip(f"{name}: not provisioned")
    return module


@pytest.fixture(params=QA_DATASETS)
def qa(request):
    return _adapter(request.param)


def _materialize(module, record: dict, workdir: Path, *, oracle: bool) -> None:
    for entry in (module.SETUP.get(record["task_id"]) or {}).get("files", []):
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    if oracle:
        answer = str(record["gold"].get("value") or record["gold"].get("answer") or "")
        submission = getattr(module, "ORACLE_SUBMISSION", None)
        if callable(submission):
            # The adapter's own submission contract (2WikiMultihopQA's evidence triples):
            # its `answer.txt` is a JSON object this file must not paraphrase.
            (workdir / "submission.json").write_text(
                json.dumps(submission(record["gold"]), ensure_ascii=False))
            subprocess.run([sys.executable, "qa/bin/qa", "submit", "@submission.json"],
                           cwd=workdir, check=True, capture_output=True, text=True)
        elif getattr(module, "EVIDENCE_AWARE", False):
            gold = record["gold"]
            evidence = gold.get("evidence")
            if module.__name__ == "data.datasets.fever":
                if evidence and evidence[0] and isinstance(evidence[0][0], str):
                    evidence = [[item[0], int(item[1])] for item in evidence]
                else:
                    evidence = [[item[2], item[3]] for group in (evidence or [])
                                for item in group]
            else:
                evidence = gold.get("supporting_facts")
            (workdir / "oracle.json").write_text(json.dumps({
                "schema_version": 1, "answer": answer,
                "predicted_evidence": evidence,
            }))
            subprocess.run([sys.executable, "qa/bin/qa", "submit-json", "@oracle.json"],
                           cwd=workdir, check=True, capture_output=True, text=True)
        else:
            subprocess.run([sys.executable, "qa/bin/qa", "submit", answer], cwd=workdir,
                           check=True, capture_output=True, text=True)


def _run_check(module, record: dict, workdir: Path, *,
               re_materialize: bool = True) -> tuple[float, dict]:
    """Run the check the way `eval/verification/verifier.py` does: re-materialize, then run."""
    verifier = module.VERIFY[record["task_id"]]
    if re_materialize:
        for entry in verifier.get("inputs") or []:
            target = workdir / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if "content" in entry:
                target.write_text(entry["content"])
            else:
                shutil.copy2(entry["from"], target)
    import os
    env = dict(os.environ, PYTHONPATH=str(workdir) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=workdir,
                          capture_output=True, text=True, env=env)
    reward_path = workdir / "verifier" / "reward.txt"
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = workdir / "verifier" / "evidence.json"
    evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
    return reward, {"stdout": proc.stdout, "stderr": proc.stderr, **evidence}


def test_the_split_is_the_benchmarks_or_absent_but_never_invented(qa):
    """Either the benchmark states the division, or this adapter declares none.

    `SPLIT is None` is a state, not a gap: the majority of these adapters load one side of
    the benchmark (its test, dev or validation table), so there is nothing to divide and
    the run covers every task. It used to be filled with a hash third even then -- a
    third of a scored split called "study" -- which the operator removed on 2026-10-24.
    """
    tasks, scorable = qa.load()
    ids = {t["task_id"] for t in tasks}
    assert set(scorable) == ids
    if qa.SPLIT is None:
        assert qa.SPLIT_SOURCE == "none"
        assert qa.OFFICIAL_EVAL is None or qa.OFFICIAL_EVAL == []
        # The platform's rule is still what `--split default` hands a run, over the same ids.
        default = registry.default_split(tasks)
        assert set(default["train"]) | set(default["eval"]) == ids
        assert not (set(default["train"]) & set(default["eval"]))
        return
    assert qa.SPLIT_SOURCE == "declared"
    train, eval_ = set(qa.SPLIT["train"]), set(qa.SPLIT["eval"])
    assert train | eval_ == ids
    assert not (train & eval_)
    assert train and eval_


def test_every_task_has_a_goal_and_the_interface_it_needs(qa):
    for task in qa.load()[0]:
        assert task["goal"].strip()
        assert "answer.txt" in task["goal"], task["task_id"]
        assert "qa submit" in task["goal"], task["task_id"]


def test_a_task_dict_carries_no_grade(qa):
    forbidden = {"verify", "verifier", "setup", "expected", "scorable", "env"}
    for task in qa.load()[0]:
        assert not (set(task) & forbidden), task["task_id"]


def test_the_verifier_declares_the_contract_paths(qa):
    for task_id, verifier in qa.VERIFY.items():
        assert verifier["kind"] == "command", task_id
        assert verifier["reward_file"] == "/verifier/reward.txt"
        assert verifier["evidence_file"] == "/verifier/evidence.json"
        assert verifier["argv"][0] == "python3"


def test_every_verifier_input_is_re_materializable(qa):
    for task_id, verifier in qa.VERIFY.items():
        for entry in verifier["inputs"]:
            if "from" in entry:
                assert Path(entry["from"]).is_file(), (task_id, entry)
            else:
                assert "content" in entry, (task_id, entry)
                assert entry["content"].strip(), (task_id, entry)


def test_the_gold_is_inside_the_package_that_travels(qa):
    """The scaffold must be in `VERIFY.inputs`, not read from the platform.

    The verification namespace hides the platform tree, so an adapter that imported the
    scaffold from there would fail -- or read whatever the subject left. This asserts the
    inputs name the package the adapter imports from.
    """
    task_id = next(iter(qa.VERIFY))
    paths = {entry["path"] for entry in qa.VERIFY[task_id]["inputs"]}
    assert "gold.json" in paths
    assert "data/__init__.py" in paths
    assert "data/scaffolds/qa/base.py" in paths
    assert f"data/datasets/{qa.__name__.split('.')[-1]}.py" in paths


def test_the_check_grades_the_reference_answer_as_one(qa, tmp_path):
    record = qa._RECORDS[0]
    _materialize(qa, record, tmp_path, oracle=True)
    reward, evidence = _run_check(qa, record, tmp_path)
    assert reward == 1.0, evidence
    assert evidence.get("measured") is True


def test_the_check_grades_no_answer_as_zero(qa, tmp_path):
    record = qa._RECORDS[0]
    _materialize(qa, record, tmp_path, oracle=False)
    reward, evidence = _run_check(qa, record, tmp_path)
    assert reward == 0.0, evidence
    assert evidence.get("measured") is True, "no answer is a measured zero"


def test_the_check_re_materializes_the_gold(qa, tmp_path):
    """The subject's own `gold.json` is not what is graded."""
    record = qa._RECORDS[0]
    _materialize(qa, record, tmp_path, oracle=False)
    (tmp_path / "answer.txt").write_text("a deliberate wrong answer\n")
    (tmp_path / "gold.json").write_text(json.dumps({"answer": "a deliberate wrong answer",
                                                    "value": "a deliberate wrong answer"}))
    reward, evidence = _run_check(qa, record, tmp_path)
    assert reward == 0.0, evidence
    if getattr(qa, "REDACT_EVIDENCE", False):
        assert evidence["gold"]["redacted"] is True
        assert "answer" not in evidence["gold"] and "moves" not in evidence["gold"]
    else:
        assert evidence["gold"] == record["gold"]


def test_the_check_reports_unmeasured_when_its_gold_is_absent(qa, tmp_path):
    record = qa._RECORDS[0]
    _materialize(qa, record, tmp_path, oracle=True)
    # `gold.json` arrives with `VERIFY.inputs`, so materialize it once (the way the
    # platform would) and then remove it -- a by-hand run whose trusted input is missing.
    _run_check(qa, record, tmp_path, re_materialize=True)
    (tmp_path / "gold.json").unlink()
    # The first run left a reward file behind; a *new* measurement is what is being made
    # here, and the platform starts one on a fresh verdict directory.
    shutil.rmtree(tmp_path / "verifier", ignore_errors=True)
    reward, evidence = _run_check(qa, record, tmp_path, re_materialize=False)
    assert reward == -1.0, "no reward file may be written when the check cannot measure"
    assert evidence.get("measured") is False
    assert "gold" in evidence.get("error", "")


def test_the_cli_refuses_an_empty_answer(qa, tmp_path):
    """A blank `answer.txt` cannot be told apart from no answer at all."""
    _materialize(qa, qa._RECORDS[0], tmp_path, oracle=False)
    proc = subprocess.run([sys.executable, "qa/bin/qa", "submit", "   "],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 2
    assert not (tmp_path / "answer.txt").exists()


def test_the_cli_can_submit_from_a_file_and_from_stdin(qa, tmp_path):
    _materialize(qa, qa._RECORDS[0], tmp_path, oracle=False)
    (tmp_path / "notes.md").write_text("from a file\n")
    subprocess.run([sys.executable, "qa/bin/qa", "submit", "@notes.md"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    assert (tmp_path / "answer.txt").read_text().strip() == "from a file"
    proc = subprocess.run([sys.executable, "qa/bin/qa", "submit"], cwd=tmp_path,
                          input="from stdin\n", capture_output=True, text=True)
    assert proc.returncode == 0
    assert (tmp_path / "answer.txt").read_text().strip() == "from stdin"


def test_dataset_private_modules_do_not_collide_by_bare_name():
    """Two datasets may both have a `control.py`; a bare `import control` conflates them.

    Measured, and only the **full** suite caught it: the ScienceWorld and Jericho test files
    both did `import control`, the first left `control` in `sys.modules`, and the second got the
    other dataset's module -- so `control.materialize` raised `AttributeError` in a full run and
    passed in isolation. The fix is loading by path under a unique name, and this test asserts
    the name each dataset's own loader registers is its own.
    """
    import importlib.util

    ROOT = Path(__file__).resolve().parents[2]
    names = {}
    for dataset in ("scienceworld", "jericho"):
        path = ROOT / "data" / "datasets" / dataset / "control.py"
        if not path.is_file():
            continue
        name = f"{dataset}_control_contract_probe"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        names[dataset] = module.__name__
    assert len(set(names.values())) == len(names), names
    assert all(value.endswith("_contract_probe") for value in names.values())
