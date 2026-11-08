#!/usr/bin/env python3
"""The ScienceWorld controls: does the check actually reach 1, and actually reach 0?

    python3 data/datasets/scienceworld/control.py --oracle
    python3 data/datasets/scienceworld/control.py --noop
    python3 data/datasets/scienceworld/control.py --oracle --platform
    python3 data/datasets/scienceworld/control.py --noop   --platform

A dataset whose oracle scores 0 has an unreachable check, and every harness score on it is
meaningless. A dataset whose do-nothing control scores 1 has a check that cannot fail. Both
failures are quiet -- a plausible number lands in the right place -- so both are run and both
are asserted.

The oracle is ScienceWorld's own: `get_gold_action_sequence()` after
`load(..., generateGoldPath=True)`, which is the environment's reference solution for the
task (the analogue of ALFWorld's `walkthrough`). It is regenerated here rather than read
from the repository's gold-path archive, and that is deliberate: **the archived paths were
written for a different minor version** -- measured, the archive's `boil/21` path is 85
moves and this package's is 102, so they are not the same sequence -- and the control's job
is to ask whether *this* environment's own solution scores 100, not whether a stored list
does.

Both flag spellings use the same managed path
---------------------------------------------
`--oracle` / `--noop`, with or without the retained `--platform` flag, run through
`eval.run.runner.run_one`, the declared trusted relay, and the original sealed
native verifier. Oracle actions are generated first in an isolated operator-only
worker using the selected managed Python and pinned payload. Platform and actor
Python do not need ScienceWorld. Prepare the execution artifact explicitly with
`python tools/prepare_dataset.py --dataset scienceworld --runtime execution`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import data.datasets.scienceworld as scienceworld  # noqa: E402


def _record(task_id: str | None) -> dict:
    records = scienceworld._RECORDS
    if not records:
        try:
            records = scienceworld._ensure()
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"cannot read the task set: {exc}. Provision it with "
                             f"`python3 tools/datasets.py --fetch --dataset scienceworld`")
    if task_id is None:
        # A task from the pool a run would see -- the eval side of a declared division, or
        # the whole official test fold when the adapter declares none (2026-10-24) -- and
        # the shortest gold, because the control's own runtime is a cost the suite pays.
        split = scienceworld.SPLIT
        pool = set(split["eval"]) if split else {r["task_id"] for r in records}
        candidates = [r for r in records if r["task_id"] in pool]
        task_id = min(candidates, key=lambda r: r["gold_moves"])["task_id"]
    for record in records:
        if record["task_id"] == task_id:
            return record
    raise SystemExit(f"unknown task id {task_id!r}")


def _gold(record: dict, workdir: Path) -> list[str]:
    """Generate references only in the selected isolated operator Python."""
    from eval.containers import python_service
    from harnessgrad.dataset_runtime import bind, resolve_execution, admit_binding
    selected = bind(resolve_execution('scienceworld'), 'service')
    runtime = admit_binding(selected, 'service')
    declaration = scienceworld._setup(record, binding=selected)['tool_relay']
    with tempfile.TemporaryDirectory(prefix='scienceworld-operator-') as name:
        directory = Path(name)
        (directory/'state').mkdir()
        spec = directory/'state/case.json'
        spec.write_text(declaration['content'])
        program = Path(__file__).resolve().parent/'oracle_worker.py'
        argv = [selected['python'], '-I', '-B', str(program), '--case-spec', str(spec)]
        command = python_service.command(argv, directory=directory,
            runtime_paths=runtime['runtime_paths'],
            read_paths=[*declaration['read_paths'], str(program)])
        proc = subprocess.run(command, env=python_service.environment(directory, {}),
                              capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            raise RuntimeError('ScienceWorld trusted reference worker failed; control is unmeasured')
        actions = json.loads(proc.stdout)
        if not isinstance(actions, list) or not actions or any(not isinstance(a, str) for a in actions):
            raise RuntimeError('ScienceWorld trusted reference worker returned no valid reference')
        return actions


def _copy_entry(entry: dict, workdir: Path) -> None:
    """One `SETUP`/`VERIFY` entry into `workdir`, the way the platform does it.

    Directories are copied as trees (that is what the JRE is), and a tree that is already
    there is **removed first**. Measured: the JRE's files are read-only (0664 is not what
    the archive says), so a second `copytree(dirs_exist_ok=True)` over the first attempt
    fails with `[Errno 13] Permission denied` for every file -- which is exactly what a
    rerun of the control does. The platform never hits this because it copies into a fresh
    workdir per run; the control reuses one when `--keep` is passed and its own
    `run_check` re-materializes into the same directory.
    """
    target = workdir / entry["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    source = Path(entry["from"]) if "from" in entry else None
    if source is not None and source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        return
    if "content" in entry:
        target.write_text(entry["content"])
    else:
        shutil.copy2(entry["from"], target)
        if entry["path"] == "scw":
            target.chmod(0o755)


def materialize(record: dict, workdir: Path) -> None:
    """Materialize only public SETUP files; the runner owns the trusted relay."""
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in scienceworld.SETUP[record["task_id"]]["files"]:
        _copy_entry(entry, workdir)


def drive(workdir: Path, actions: list[str], *, budget: int) -> None:
    """Compatibility helper: use the same relay and actor sandbox as the runner."""
    from eval.run.tool_relay import start
    from eval.containers import sandbox
    task = json.loads((workdir/scienceworld.TASK_IN_WORKDIR).read_text())
    record = _record(f"{task['task_name']}/{task['variation']}")
    workdir = workdir.resolve()
    control_root = workdir/'.control'
    control_root.mkdir(exist_ok=True)
    environment = {'PATH': f'{Path(sys.executable).parent}:/usr/bin:/bin',
                   'HOME':'/tmp', 'TMPDIR':'/tmp', 'LANG':'C.UTF-8',
                   'PYTHONNOUSERSITE':'1', 'PYTHONDONTWRITEBYTECODE':'1'}
    with start(scienceworld._setup(record)) as relay:
        for command in [*[['do', action] for action in actions[:budget]], ['done']]:
            proc = sandbox.run([sys.executable, 'scw', *command], cwd=workdir,
                platform=ROOT, work_root=control_root, repo=control_root,
                task_dir=workdir, env=environment, timeout_s=900,
                network='none', tool_socket=relay.path)
            if proc.returncode != 0:
                raise RuntimeError(f'ScienceWorld CLI exited {proc.returncode}; control is unmeasured')
    if relay.fault():
        raise RuntimeError('ScienceWorld relay failed; control is unmeasured')


def run_check(module, record: dict, workdir: Path) -> tuple[float, str]:
    """Compatibility helper: use the original sealed platform verifier."""
    from eval.verification.verifier import _verify
    root = workdir.resolve()/'.control'
    root.mkdir(exist_ok=True)
    previous = os.environ.get('HARNESSGRAD_WORK_ROOT')
    os.environ['HARNESSGRAD_WORK_ROOT'] = str(root)
    try:
        verdict = _verify(workdir.resolve(), module._verifier(record), '', sandbox=True,
                          task_id=record['task_id'], timeout_s=900, env={'kind':'files'})
    finally:
        if previous is None: os.environ.pop('HARNESSGRAD_WORK_ROOT', None)
        else: os.environ['HARNESSGRAD_WORK_ROOT'] = previous
    evidence = verdict.get('evidence') or {}
    reward = (float(verdict['score']) if verdict.get('score') is not None
              and evidence.get('measured') is True and not verdict.get('unmeasured') else -1.0)
    return reward, json.dumps({'reward':reward, 'verdict':verdict}, sort_keys=True)


def run_local(module, record: dict, *, oracle: bool, workdir: Path) -> int:
    """The historical local spelling uses the same managed runner contract."""
    return run_platform(module, record, oracle=oracle, workdir=workdir, sandbox=True)


def run_platform(module, record: dict, *, oracle: bool, workdir: Path,
                 sandbox: bool = True) -> int:
    """The same episode through `eval.run.runner.run_one`, with no Docker and no model."""
    from eval.run.runner import run_one

    tid = record["task_id"]
    task = next(t for t in module.load()[0] if t["task_id"] == tid)
    declared_setup = module._setup(record)
    setup = {**declared_setup, "files": list(declared_setup["files"])}
    if oracle:
        # Reference generation is an operator-only managed subprocess. Its
        # private selected-task recipe never becomes a public relay operation.
        gold = _gold(record, workdir)
        setup["files"].append({"path": ".oracle/actions.json",
                               "content": json.dumps(gold[:module.STEP_BUDGET])})
    work_root = workdir / "work"
    harness = work_root / "oracle_harness"
    shutil.copytree(Path(__file__).resolve().parent / "oracle_harness", harness,
                    dirs_exist_ok=True)
    previous = os.environ.get("HARNESSGRAD_WORK_ROOT")
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work_root)
    started = time.time()
    try:
        result = run_one(
            repo=harness,
            task=task,
            setup=setup,
            verifier=module._verifier(record),
            env={"kind":"files"},
            sandbox=sandbox,
            run_id="scienceworld-control",
            run_seed="0",
        )
    finally:
        if previous is None: os.environ.pop('HARNESSGRAD_WORK_ROOT', None)
        else: os.environ['HARNESSGRAD_WORK_ROOT'] = previous
    verdict = result.get("verdict") or {}
    print(f"task     {tid}")
    print(f"mode     {'oracle' if oracle else 'do-nothing'} (platform run_one, "
          f"sandbox={sandbox})")
    print(f"exit     {result.get('exit_code')}   wall {time.time() - started:.1f}s")
    print(f"score    {verdict.get('score')}  passed {verdict.get('passed')}")
    print(f"detail   {verdict.get('detail')}")
    if verdict.get("evidence"):
        print(f"evidence {json.dumps(verdict['evidence'].get('why'), ensure_ascii=False)}")
    expected = 1.0 if oracle else 0.0
    score = verdict.get("score")
    if (score is None or result.get("exit_code") != 0
            or (verdict.get("evidence") or {}).get("measured") is not True):
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true",
                      help="replay the environment's own gold action sequence; expect 1")
    mode.add_argument("--noop", action="store_true",
                      help="do nothing; expect 0")
    parser.add_argument("--platform", action="store_true",
                        help="compatibility flag; both modes use the managed platform runner")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="with --platform, run without the mount namespace")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    record = _record(args.task_id)
    if args.workdir:
        workdir = Path(args.workdir)
        if workdir.exists() and not args.keep:
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        workdir = Path(tempfile.mkdtemp(prefix="scienceworld-control-"))
        cleanup = True
    started = time.time()
    try:
        return run_platform(scienceworld, record, oracle=args.oracle,
                            workdir=workdir, sandbox=not args.no_sandbox)
    finally:
        print(f"elapsed {time.time() - started:.1f}s")
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
