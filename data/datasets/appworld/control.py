#!/usr/bin/env python3
"""AppWorld oracle/noop control, with by-hand and platform arms."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AW_PYTHON = Path("/mnt/20t/xubuqiang/venvs/appworld/bin/python")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import data.datasets.appworld as dataset


def _task(task_id: str) -> dict:
    for task in dataset.load()[0]:
        if task["task_id"] == task_id:
            return task
    raise SystemExit(f"unknown task id: {task_id}")


def _local(task: dict, oracle: bool, workdir: Path) -> int:
    os.environ.setdefault("APPWORLD_ROOT", str(dataset.UNPACKED))
    experiment = f"control-{task['task_id']}-{next(tempfile._get_candidate_names())}"
    code = dataset.oracle_files(task)["reference.py"] if oracle else "print('noop')"
    probe = workdir / "probe.py"
    workdir.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "import os\nfrom appworld import AppWorld\n"
        f"os.environ['APPWORLD_ROOT']={str(dataset.UNPACKED)!r}\n"
        f"with AppWorld(task_id={task['task_id']!r}, experiment_name={experiment!r}, raise_on_failure=False) as world:\n"
        f"    world.execute({code!r})\n"
        "    tracker=world.evaluate()\n"
        "    print(world.output_logs_directory)\n",
        encoding="utf-8",
    )
    proc = subprocess.run([str(AW_PYTHON), str(probe)], cwd=workdir, text=True, errors="replace", capture_output=True)
    if proc.returncode:
        print(proc.stdout + proc.stderr, file=sys.stderr)
        return 1
    log = Path(proc.stdout.strip().splitlines()[-1]) / "api_calls.jsonl"
    check = workdir / "check.py"
    check.write_text((ROOT / "data/datasets/appworld/verify/check.py").read_text(), encoding="utf-8")
    cmd = [str(AW_PYTHON), str(check), "--task-id", task["task_id"], "--log", str(log),
           "--reward-file", str(workdir / "reward.txt"), "--evidence-file", str(workdir / "evidence.json")]
    checked = subprocess.run(cmd, cwd=workdir, text=True, errors="replace", capture_output=True)
    reward = (workdir / "reward.txt").read_text().strip() if (workdir / "reward.txt").is_file() else "missing"
    evidence = json.loads((workdir / "evidence.json").read_text()) if (workdir / "evidence.json").is_file() else {}
    # Keep the shared control harness's integer reward spelling while retaining the
    # verifier's measured float for the AppWorld-specific evidence check.
    reward_display = f"{int(float(reward))}" if reward != "missing" else reward
    print(f"task    {task['task_id']}\nmode    {'oracle' if oracle else 'do-nothing'}\n"
          f"reward  {reward_display} (reward  {reward})")
    print(json.dumps(evidence) if evidence else checked.stderr)
    expected = "1.0" if oracle else "0.0"
    measured = evidence.get("measured") is True and isinstance(evidence.get("num_tests"), int) and evidence["num_tests"] > 0
    return 0 if checked.returncode == 0 and measured and reward == expected else 1


def _platform(task: dict, oracle: bool, workdir: Path, sandbox: bool) -> int:
    from eval.run.runner import run_one
    repo = workdir / "harness"
    shutil.copytree(ROOT / "data/datasets/appworld/oracle_harness", repo, dirs_exist_ok=True)
    setup = {"files": [{"path": "aw", "from": str(ROOT / "data/datasets/appworld/cli/aw")}]}
    if oracle:
        setup["files"].append({"path": ".oracle/reference.py", "content": dataset.oracle_files(task)["reference.py"]})
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(workdir / "work")
    if not oracle:
        os.environ["APPWORLD_NOOP"] = "1"
    else:
        os.environ.pop("APPWORLD_NOOP", None)
    result = run_one(repo=repo, task=task, setup=setup, verifier=dataset.VERIFY[task["task_id"]],
                     env=dataset.ENV[task["task_id"]], sandbox=sandbox,
                     run_id=f"appworld-control-{workdir.name}", run_seed="0",
                     recordings_root=workdir / "recordings")
    verdict = result.get("verdict") or {}
    score = float(verdict.get("score", 1.0 if verdict.get("passed") else 0.0))
    print(f"task     {task['task_id']}\nmode     {'oracle' if oracle else 'do-nothing'} (platform run_one, sandbox={sandbox})\nscore    {score}\ndetail   {verdict.get('detail')}")
    print(f"verdict  {verdict}")
    evidence = verdict.get("evidence")
    measured = isinstance(evidence, dict) and evidence.get("measured") is True
    tests = evidence.get("num_tests") if isinstance(evidence, dict) else None
    if not measured or verdict.get("unmeasured") or not isinstance(tests, int) or tests <= 0:
        print("control refused: verifier did not produce a measured official test result",
              file=sys.stderr)
        return 1
    return 0 if score == (1.0 if oracle else 0.0) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true")
    mode.add_argument("--noop", action="store_true")
    parser.add_argument("--platform", action="store_true")
    parser.add_argument("--no-sandbox", action="store_true")
    parser.add_argument("--task-id", default="e85d92a_1")
    parser.add_argument("--workdir")
    args = parser.parse_args()
    task = _task(args.task_id)
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="appworld-control-"))
    try:
        if args.platform:
            return _platform(task, args.oracle, workdir, not args.no_sandbox)
        return _local(task, args.oracle, workdir)
    finally:
        if not args.workdir:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
