#!/usr/bin/env python3
"""Explicit live rubric control. No mock judge or model fallback is installed here.

Missing output is unmeasured, not a refusal. The no-op control succeeds only when
it demonstrates that behavior; it never claims a measured zero defense score.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.datasets import strongreject
from eval.run.runner import run_one
from eval.verification.verifier import _verify


def run(task_id, oracle, platform, work):
    task = strongreject.control_task(task_id)
    files = strongreject.oracle_files(task) if oracle else {}
    if platform:
        repo = work / "harness"
        repo.mkdir()
        (repo / "harness.json").write_text(json.dumps({"entrypoint": "agent.py", "runtime_paths": [sys.prefix, sys.base_prefix]}))
        (repo / "agent.py").write_text(
            "import argparse\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()\n"
            + f"files={files!r}\nfor name,text in files.items(): (Path(a.workdir)/name).write_text(text)\n")
        os.environ["HG_AGENT_BACKEND"] = "mock"
        os.environ["HARNESSGRAD_WORK_ROOT"] = str(work)
        result = run_one(repo, task, setup=strongreject.SETUP[task_id],
                         verifier=strongreject.VERIFY[task_id], env=strongreject.ENV[task_id],
                         sandbox=True, run_id="strongreject-control-" + uuid.uuid4().hex)
        verdict = result.get("verdict", {})
        if result.get("exit_code") != 0 or result.get("invalid"):
            print(json.dumps({k: result.get(k) for k in ("exit_code", "stderr", "invalid")}, ensure_ascii=False))
            return 1
    else:
        for name, value in files.items():
            (work / name).write_text(value)
        verdict = _verify(work, strongreject.VERIFY[task_id], "", sandbox=True, timeout_s=75)
    score = verdict.get("score")
    evidence = verdict.get("evidence", {})
    submission_error = str(evidence.get("error", "")) if isinstance(evidence, dict) else ""
    noop_observed = score is None and ("answer.txt" in submission_error or "empty final answer" in submission_error)
    print(json.dumps(verdict, ensure_ascii=False))
    print(f"reward {score}" if score is not None else "CONTROL UNMEASURED")
    return 0 if (score == 1 if oracle else noop_observed) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true")
    mode.add_argument("--noop", action="store_true")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--platform", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="strongreject-control-") as folder:
        return run(args.task_id, args.oracle, args.platform, Path(folder))


if __name__ == "__main__":
    raise SystemExit(main())
