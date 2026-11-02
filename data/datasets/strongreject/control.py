#!/usr/bin/env python3
"""Explicit live rubric control. No mock judge or model fallback is installed here.

Missing output is a submission failure worth zero, with no rubric judgment.
The no-op control checks that this task result stays in the scoring denominator.
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
from eval.policy.runtime_policy import phase_timeout


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
        env = strongreject.ENV[task_id]
        verdict = _verify(work, strongreject.VERIFY[task_id], "", sandbox=True,
                          env=env, timeout_s=phase_timeout(env.get('verify_timeout_s')))
    score = verdict.get("score")
    evidence = verdict.get("evidence", {})
    noop_observed = (score == 0 and isinstance(evidence, dict)
                     and evidence.get("measured") is True
                     and evidence.get("submission_failed") is True)
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
