#!/usr/bin/env python3
"""The controls every plain-text QA dataset shares: is the check reachable, and can it fail?

    python3 data/scaffolds/qa/control.py --dataset gsm8k --oracle
    python3 data/scaffolds/qa/control.py --dataset gsm8k --noop
    python3 data/scaffolds/qa/control.py --dataset gsm8k --oracle --platform
    python3 data/scaffolds/qa/control.py --dataset gsm8k --noop   --platform

A dataset whose oracle scores 0 has an unreachable check, and every harness score on it
is meaningless. A dataset whose do-nothing control scores 1 has a check that cannot
fail. Both failures are quiet -- a plausible number lands in the right place -- so both
are run and both are asserted.

Why one control for eight datasets
----------------------------------
They all grade the same artifact: the bytes of `answer.txt`, interpreted by an official
scorer. So the reference solution is always one string, and the shared oracle harness
(`data/scaffolds/qa/oracle_harness/`) can submit it without knowing which dataset it is running.
What differs per dataset is *where the gold lives*, and that is read here from the
adapter's own records -- the same field the check is handed -- so a control that passes
because both sides are wrong is not possible.

The by-hand form needs nothing but the staged data and the platform's interpreter; the
`--platform` form exercises `SETUP` -> harness -> `VERIFY` through `eval.run.runner.run_one`,
which is where a contract error lives that the check alone cannot reveal.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import data.scaffolds.qa.base as base  # noqa: E402


def _adapter(name: str):
    return importlib.import_module(f"data.datasets.{name}")


def _first_task_id(module) -> str:
    """The task a control runs when the caller names none.

    The eval side when the adapter declares a division, and otherwise the first task of the
    pool: most QA adapters load one scored split and declare **no** division (2026-10-24),
    so there is no `eval` side to index and indexing one used to raise `TypeError`.
    """
    split = getattr(module, "SPLIT", None)
    if split is not None and split.get("eval"):
        return split["eval"][0]
    tasks, _ = module.load()
    if not tasks:
        raise SystemExit(f"no {module.__name__} tasks; provision the data first "
                         f"(python3 tools/datasets.py --fetch --dataset "
                         f"{module.__name__.split('.')[-1]})")
    return tasks[0]["task_id"]


def _record(module, task_id: str | None) -> dict:
    selected_control = getattr(module, "control_task", None)
    if callable(selected_control):
        return selected_control(task_id)
    selected_id = task_id
    if selected_id is None:
        # BFCL identities are stable category/index pairs; choose a single simple item without
        # asking its adapter to construct the mixed train/eval split.
        selected_id = ("simple/0" if callable(getattr(module, "load_selected", None))
                       else _first_task_id(module))
    selected_loader = getattr(module, "load_selected", None)
    if callable(selected_loader):
        selected_loader([selected_id])
    records = module._RECORDS
    if not records:
        records = getattr(module, "_records", lambda _ids: [])([selected_id])
    if not records:
        raise SystemExit(f"no {module.__name__} tasks; provision the data first "
                         f"(python3 tools/datasets.py --fetch --dataset "
                         f"{module.__name__.split('.')[-1]})")
    for record in records:
        if record["task_id"] == selected_id:
            return record
    raise SystemExit(f"unknown task id {selected_id!r}")


def _gold_text(record: dict) -> str:
    """The reference answer, as the string the agent would submit.

    Datasets whose gold is a *set* of acceptable answers (TriviaQA's aliases) submit the
    first one, which is the dataset's own canonical `value` and is by construction in the
    accepted set. A dataset whose gold is a label submits the label.
    """
    gold = record["gold"]
    for key in ("answer", "value", "label"):
        value = gold.get(key)
        if isinstance(value, str) and value.strip():
            return value
    accepted = gold.get("accepted") or gold.get("aliases") or []
    if accepted:
        return str(accepted[0])
    raise SystemExit(f"{record['task_id']}: the gold record has no submit-reachable "
                     f"answer: {sorted(gold)}")


def _oracle_prediction(module, record: dict) -> dict | None:
    if not getattr(module, "EVIDENCE_AWARE", False):
        return None
    gold = record["gold"]
    evidence = gold.get("evidence")
    if module.__name__.endswith("fever"):
        if evidence and evidence[0] and isinstance(evidence[0][0], str):
            evidence = [[item[0], int(item[1])] for item in evidence]
        else:
            evidence = [[item[2], item[3]] for group in (evidence or []) for item in group]
    return {"schema_version": 1, "answer": _gold_text(record),
            "predicted_evidence": evidence if module.__name__.endswith("fever")
            else gold.get("supporting_facts")}


def _oracle_submission(module, record: dict) -> dict | None:
    """The reference answer in the adapter's **own** submission channel, if it declares one.

    `ORACLE_SUBMISSION` is an optional module-level callable taking the gold dict and
    returning the object the dataset's `answer.txt` contract expects; it is submitted with
    `qa submit @submission.json`. 2WikiMultihopQA is the case that needed it: its Joint
    metrics need `supporting_facts` and evidence **triples**, and the shared
    `predicted_evidence` pair schema cannot express them -- the scorer refuses that channel
    by name (`twowiki_prediction_json_pair_schema_unsupported`), so the oracle control
    reported `reward -1` (unmeasured) instead of `1` on every run of it.
    """
    builder = getattr(module, "ORACLE_SUBMISSION", None)
    if not callable(builder):
        return None
    return builder(record["gold"])


def _noop_expected(module, record: dict) -> float:
    """Resolve the adapter's declared do-nothing reward (default 0.0)."""
    expected = getattr(module, "NOOP_EXPECTED", 0.0)
    return float(expected(record) if callable(expected) else expected)


def materialize(module, record: dict, workdir: Path, *, oracle: bool) -> None:
    """Put the task where `SETUP` would, plus the gold when this is the oracle arm."""
    workdir.mkdir(parents=True, exist_ok=True)
    setup_map = (module.setup_for(record) if callable(getattr(module, "setup_for", None))
                 else module.SETUP.get(record["task_id"]) or {})
    for entry in setup_map.get("files", []):
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
            target.chmod(0o755)
    if oracle:
        oracle_dir = workdir / ".oracle"
        oracle_dir.mkdir(parents=True, exist_ok=True)
        submission = _oracle_submission(module, record)
        structured = _oracle_prediction(module, record)
        if submission is not None:
            (oracle_dir / "submission.json").write_text(
                json.dumps(submission, ensure_ascii=False) + "\n")
        elif structured is not None:
            (oracle_dir / "prediction.json").write_text(
                json.dumps(structured, ensure_ascii=False) + "\n")
        else:
            (oracle_dir / "answer.txt").write_text(_gold_text(record) + "\n")


def run_check(module, record: dict, workdir: Path) -> tuple[float, str]:
    """Run the check the way the platform's verifier does, grades and all.

    The verifier's inputs are written here **again**, exactly as
    `eval/verification/verifier.py::_verify` re-materializes them after the harness stops. That is
    what makes the grade independent of anything the agent did to `<workdir>/gold.json`.
    """
    verifier = (module.verify_for(record) if callable(getattr(module, "verify_for", None))
                else module.VERIFY[record["task_id"]])
    for entry in verifier.get("inputs") or []:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    # `argv[0]` is the check's own interpreter name ("python3"); the by-hand path runs
    # it with the *current* interpreter, exactly as the platform's `files` branch would.
    # The check imports its scorer by literal name, so the workdir must be where Python
    # looks first -- the same thing the platform guarantees by running the check with the
    # workdir as its working directory.
    env = dict(os.environ, PYTHONPATH=str(workdir) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    argv = [sys.executable, *verifier["argv"][1:]]
    proc = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, errors="replace", env=env)
    reward_path = workdir / base.REWARD_REL
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = workdir / base.EVIDENCE_REL
    evidence = (json.loads(evidence_path.read_text())
                if evidence_path.is_file() else {})
    detail = {
        "returncode": proc.returncode,
        "reward": reward,
        "why": evidence.get("why") or evidence.get("error"),
        "measured": evidence.get("measured"),
        "official": evidence.get("official"),
        "stdout": proc.stdout.strip(),
    }
    return reward, json.dumps(detail, indent=1, sort_keys=True)


def run_local(module, record: dict, *, oracle: bool, workdir: Path) -> int:
    materialize(module, record, workdir, oracle=oracle)
    if oracle:
        submission = _oracle_submission(module, record)
        structured = _oracle_prediction(module, record)
        if submission is not None:
            cli_args = ["submit", "@.oracle/submission.json"]
        elif structured is not None:
            cli_args = ["submit-json", "@.oracle/prediction.json"]
        else:
            cli_args = ["submit", (workdir / ".oracle" / "answer.txt").read_text(encoding="utf-8")]
        submitted = subprocess.run(
            [sys.executable, f"{base.CLI_REL}/{base.CLI_FILE}", *cli_args],
            cwd=workdir, capture_output=True, text=True, errors="replace")
        if submitted.returncode != 0:
            print(f"the CLI refused the reference answer: {submitted.stderr}",
                  file=sys.stderr)
            return 1
    reward, detail = run_check(module, record, workdir)
    print(f"task    {record['task_id']}")
    print(f"mode    {'oracle' if oracle else 'do-nothing'}")
    print(f"reward  {reward:g}")
    print(detail)
    expected = 1.0 if oracle else _noop_expected(module, record)
    if reward != expected:
        print(f"CONTROL FAILED: expected reward {expected:g}, got {reward:g}",
              file=sys.stderr)
        return 1
    return 0


def run_platform(module, record: dict, *, oracle: bool, workdir: Path,
                 sandbox: bool = True) -> int:
    """The same episode through `eval.run.runner.run_one`, with no Docker and no model."""
    from eval.run.runner import run_one

    tid = record["task_id"]
    if callable(getattr(module, "control_task", None)):
        task_builder = getattr(module, "task_for", None)
        if callable(task_builder):
            task = task_builder(record)
        else:
            tasks, _ = module.load_selected([tid])
            task = next(t for t in tasks if t["task_id"] == tid)
        setup_spec = module.setup_for(record)
        verifier = module.verify_for(record)
        env = module.DEFAULT_ENV
    else:
        task = next(t for t in module.load()[0] if t["task_id"] == tid)
        setup_spec = module.SETUP[tid]
        verifier = module.VERIFY[tid]
        env = module.ENV[tid]
    setup = {"files": list(setup_spec["files"])}
    if oracle:
        submission = _oracle_submission(module, record)
        structured = _oracle_prediction(module, record)
        if submission is not None:
            setup["files"].append({"path": ".oracle/submission.json",
                                   "content": json.dumps(submission, ensure_ascii=False) + "\n"})
        elif structured is not None:
            setup["files"].append({"path": ".oracle/prediction.json",
                                   "content": json.dumps(structured, ensure_ascii=False) + "\n"})
        else:
            setup["files"].append({"path": ".oracle/answer.txt",
                                   "content": _gold_text(record) + "\n"})
    work_root = workdir / "work"
    harness = work_root / "oracle_harness"
    shutil.copytree(base.Path(__file__).resolve().parent / "oracle_harness", harness,
                    dirs_exist_ok=True)
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work_root)
    result = run_one(
        repo=harness,
        task=task,
        setup=setup,
        verifier=verifier,
        env=env,
        sandbox=sandbox,
        run_id=f"{module.__name__.split('.')[-1]}-control",
        run_seed="0",
    )
    verdict = result.get("verdict") or {}
    print(f"task     {tid}")
    print(f"mode     {'oracle' if oracle else 'do-nothing'} (platform run_one, "
          f"sandbox={sandbox})")
    print(f"exit     {result.get('exit_code')}")
    print(f"score    {verdict.get('score')}  passed {verdict.get('passed')}")
    print(f"detail   {verdict.get('detail')}")
    if verdict.get("evidence"):
        print(f"evidence {json.dumps(verdict['evidence'].get('why'), ensure_ascii=False)}")
    expected = 1.0 if oracle else _noop_expected(module, record)
    score = verdict.get("score")
    if score is None:
        # No score is not a zero. The platform refused the task, the scorer failed, or
        # the run produced nothing -- each is unmeasured, and an unmeasured do-nothing
        # arm must never be counted as a passing control. Treating `None` as 0.0 here
        # did exactly that: it turned a refusal into "the noop arm behaved".
        print(f"CONTROL UNMEASURED: no score for {tid}", file=sys.stderr)
        return 2
    if float(score) != expected:
        print(f"CONTROL FAILED: expected score {expected:g}, got {score}",
              file=sys.stderr)
        return 1
    return 0


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true",
                      help="submit the dataset's own reference answer; expect reward 1")
    mode.add_argument("--noop", action="store_true",
                      help="submit nothing; expect reward 0")
    parser.add_argument("--platform", action="store_true",
                        help="run through eval.runner.run_one instead of by hand")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="with --platform, run without the mount namespace")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    module = _adapter(args.dataset)
    refused = getattr(module, "REFUSED", None)
    if refused:
        # A refusal means there is no task and no check, so there is nothing for a control
        # to prove. Saying that plainly beats a traceback that reads like a broken adapter.
        print(f"{args.dataset} refuses to load on purpose; there is no check to control.\n"
              f"\n{refused}\n")
        return 0
    record = _record(module, args.task_id)
    if args.workdir:
        workdir = Path(args.workdir)
        if workdir.exists() and not args.keep:
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        workdir = Path(tempfile.mkdtemp(prefix=f"{args.dataset}-control-"))
        cleanup = True
    try:
        if args.platform:
            return run_platform(module, record, oracle=args.oracle, workdir=workdir,
                                sandbox=not args.no_sandbox)
        return run_local(module, record, oracle=args.oracle, workdir=workdir)
    finally:
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
