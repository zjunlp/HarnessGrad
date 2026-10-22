"""Shared control for `command`-verifier datasets (INTERFACE.md §2.4).

A dataset whose verifier is `command` needs three things an `answer` dataset does not:

* a workdir built the way `SETUP` builds it,
* a reference artefact that gets it to reward 1,
* a check run the way `eval/verification/verifier.py` runs it, with `VERIFY.inputs` re-materialized.

Those three are dataset-agnostic. Only the reference artefact is not -- and for
SpreadsheetBench it is a golden workbook, for DS-1000 it is `reference_code`, for a
detector it is a label. So this module owns the three, and a dataset owns the artefact by
declaring one function:

    def oracle_files(task: dict) -> dict[str, str]:
        \"\"\"{relative path: content} that a correct agent would leave in the workdir.\"\"\"

`data/datasets/<name>/control.py` is then a twelve-line shim over `control_main(<name>, argv)`, and
`--oracle` / `--noop` / `--platform` mean the same thing for every `command` dataset. The
generated shim is `tools/new_dataset.py --kind command`.

**This is a control-time convention, not part of the dataset contract.** Nothing in
`data/dataset.schema.json` or `tools/dataset_lint.py` requires `oracle_files`, and a
dataset that never runs its control is still a dataset. What the convention buys is that
the oracle gate costs one line to wire up -- and the oracle gate is the one that separates
an unreachable check from a weak harness (skill doc §4), so making it cheap is the point.

Why the split is worth it, measured: three existing `command` datasets
(`alfworld`, `scienceworld`, `jericho`) carry ~200 lines of near-duplicate control each,
and the duplication is exactly where their differences hide. This module is the fourth
copy that did not get written.
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
import time
from pathlib import Path

#: Where the check writes its grade, workdir-relative and absolute. The platform's
#: `eval/verification/verifier.py` owns these names; a `command` dataset must use them.
REWARD_REL = "verifier/reward.txt"
EVIDENCE_REL = "verifier/evidence.json"
REWARD_FILE = "/verifier/reward.txt"
EVIDENCE_FILE = "/verifier/evidence.json"

#: Rewards the two arms must reach. A control that asserts anything else is not the
#: oracle gate.
EXPECTED = {True: 1.0, False: 0.0}


def adapter(name: str):
    """The dataset module. A literal-templated name, not an opaque argument: the
    boundary check reads what `import_module` is handed (tests/quality/test_boundaries.py)."""
    return importlib.import_module(f"data.datasets.{name}")


def pick(module, task_id: str | None) -> dict:
    """Pick one task; targeted adapters require an explicit ID before any reads."""
    targeted = getattr(module, 'control_task', None)
    if callable(targeted):
        if task_id is None:
            raise SystemExit(f'{module.__name__}: targeted controls require --task-id; '
                             'no implicit eval selection')
        return targeted(task_id)
    tasks, _ = module.load()
    if not tasks:
        raise SystemExit(f"{module.__name__}: load() returned no tasks")
    if task_id is None:
        split = getattr(module, "SPLIT", None) or {}
        task_id = (split.get("eval") or [tasks[0]["task_id"]])[0]
    for task in tasks:
        if task["task_id"] == task_id:
            return task
    raise SystemExit(f"{module.__name__}: unknown task id {task_id!r}")


def materialize(module, task: dict, workdir: Path) -> None:
    """Put the task where `SETUP` would."""
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in (module.SETUP.get(task["task_id"]) or {}).get("files", []):
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            source = Path(entry["from"])
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(source, target)
                target.chmod(0o755)


def oracle_files(module, task: dict) -> dict[str, str]:
    """The dataset's reference artefact, or a message saying it has not declared one."""
    hook = getattr(module, "oracle_files", None)
    if not callable(hook):
        raise SystemExit(
            f"{module.__name__} has no `oracle_files(task)`.\n"
            f"A `command` dataset owns its reference artefact: declare\n"
            f"    def oracle_files(task: dict) -> dict[str, str]:\n"
            f"        \"\"\"{{relative path: content}} a correct agent would leave.\"\"\"\n"
            f"Without it there is no oracle arm, and gate 1 cannot be run.")
    files = hook(task)
    if not files or not all(isinstance(v, str) for v in files.values()):
        raise SystemExit(f"{module.__name__}.oracle_files returned {files!r}; expected a "
                         f"non-empty {{relative path: text content}} mapping")
    return files


def solve(module, task: dict, workdir: Path) -> None:
    """The oracle arm: leave the reference artefact exactly where a correct agent would."""
    for rel, content in oracle_files(module, task).items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def run_check(module, task: dict, workdir: Path) -> tuple[float, str]:
    """Run the check the way `eval/verification/verifier.py` does, re-materializing its inputs.

    The verifier's inputs are written **again** here, as the platform does after the
    harness stops. That is what makes the grade independent of anything the agent did to
    the copies it could see.
    """
    verifier = module.VERIFY[task["task_id"]]
    for entry in verifier.get("inputs") or []:
        rel = entry.get("path") or entry.get("dst")
        target = workdir / rel.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        elif "from" in entry:
            source = Path(entry["from"])
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
    env = dict(os.environ, PYTHONPATH=str(workdir) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=workdir,
                          capture_output=True, text=True, errors="replace", env=env)
    reward_path = workdir / REWARD_REL
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = workdir / EVIDENCE_REL
    evidence = (json.loads(evidence_path.read_text())
                if evidence_path.is_file() else {})
    detail = {
        "returncode": proc.returncode,
        "reward": reward,
        "measured": evidence.get("measured"),
        "why": evidence.get("why") or evidence.get("error"),
        "stderr": proc.stderr[-400:],
    }
    return reward, json.dumps(detail, indent=1, sort_keys=True)


def run_local(module, task: dict, *, oracle: bool, workdir: Path) -> int:
    materialize(module, task, workdir)
    if oracle:
        solve(module, task, workdir)
    reward, detail = run_check(module, task, workdir)
    print(f"task    {task['task_id']}")
    print(f"mode    {'oracle' if oracle else 'do-nothing'}")
    print(f"reward  {reward:g}")
    print(detail)
    return _verdict(oracle, reward)


def run_platform(module, task: dict, *, oracle: bool, workdir: Path,
                 sandbox: bool = True) -> int:
    from eval.run.runner import run_one

    tid = task["task_id"]
    setup = {"files": list(module.SETUP[tid]["files"])}
    if oracle:
        # The harness copies these into place and names them -- see
        # `data/scaffolds/command_oracle_harness/`. They arrive under `.oracle/` so the check's
        # workdir never contains the answer while the agent is running.
        for rel, content in oracle_files(module, task).items():
            setup["files"].append({"path": f".oracle/{rel}", "content": content})
    work_root = workdir / "work"
    harness = work_root / "oracle_harness"
    shutil.copytree(ORACLE_HARNESS, harness, dirs_exist_ok=True)
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work_root)
    started = time.time()
    result = run_one(
        repo=harness,
        task=task,
        setup=setup,
        verifier=module.VERIFY[tid],
        env=module.ENV[tid],
        sandbox=sandbox,
        run_id=f"{module.__name__.split('.')[-1]}-control",
        run_seed="0",
    )
    verdict = result.get("verdict") or {}
    # A missing score stays missing. `float(... or 0.0)` made a refused or unmeasured
    # run indistinguishable from a genuine do-nothing zero, so the noop arm passed on
    # the strength of a run that never produced a verdict.
    raw_score = verdict.get("score")
    print(f"task     {tid}")
    print(f"mode     {'oracle' if oracle else 'do-nothing'} (platform run_one, "
          f"sandbox={sandbox})")
    print(f"exit     {result.get('exit_code')}   wall {time.time() - started:.1f}s")
    print(f"score    {verdict.get('score')}  passed {verdict.get('passed')}")
    print(f"detail   {verdict.get('detail')}")
    if raw_score is None:
        print(f"CONTROL UNMEASURED: no score for {tid}", file=sys.stderr)
        return 2
    return _verdict(oracle, float(raw_score))


#: The generated oracle harness ships beside this module so that every `command` dataset
#: gets the same one, rather than a copy that drifts.
ORACLE_HARNESS = Path(__file__).resolve().parent / "command_oracle_harness"


def _verdict(oracle: bool, reward: float) -> int:
    expected = EXPECTED[oracle]
    if reward != expected:
        print(f"CONTROL FAILED: expected reward {expected:g}, got {reward:g}",
              file=sys.stderr)
        return 1
    return 0


def control_main(module_name: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"control.py ({module_name})",
        description=f"Run {module_name}'s oracle and do-nothing controls.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true", help="expect reward 1")
    mode.add_argument("--noop", action="store_true", help="do nothing; expect 0")
    parser.add_argument("--platform", action="store_true",
                        help="through eval.runner.run_one rather than by hand")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="with --platform, run without the mount namespace")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    module = adapter(module_name)
    task = pick(module, args.task_id)
    if args.workdir:
        workdir = Path(args.workdir)
        if workdir.exists() and not args.keep:
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        workdir = Path(tempfile.mkdtemp(prefix=f"{module_name}-control-"))
        cleanup = True
    try:
        if args.platform:
            return run_platform(module, task, oracle=args.oracle, workdir=workdir,
                                sandbox=not args.no_sandbox)
        return run_local(module, task, oracle=args.oracle, workdir=workdir)
    finally:
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit("import this from a dataset's control.py, which names the dataset")
