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

Two ways to run each control
----------------------------
`--oracle`/`--noop` drive the CLI by hand and run the check directly. This needs the staged
JRE under the dataset root and nothing else -- no Docker, no platform sandbox.

`--platform` runs the same episode through the platform's own `eval.run.runner.run_one` with the
control harness in `oracle_harness/`, with the mount namespace **on** by default. That
exercises `SETUP` -> harness -> `VERIFY` end to end, which is where a contract error lives
that the check alone cannot reveal -- and it is also what proves the JRE travels into the
task, since the namespace cannot see the dataset root.
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
import scw_env  # noqa: E402


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
    """The environment's reference solution for this task, regenerated live.

    Raises rather than returning an empty list: a control that silently replays nothing
    would score 0 and look like an unreachable check.
    """
    env = scw_env.load_task(workdir, generate_gold=True,
                            step_budget=scienceworld.STEP_BUDGET)
    try:
        actions = list(env.get_gold_action_sequence())
    finally:
        env.close()
    if not actions or str(actions[0]).startswith("ERROR"):
        raise SystemExit(f"{record['task_id']} ships no gold action sequence: "
                         f"{actions[:1]}")
    return [str(a) for a in actions]


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
    """Put the task where `SETUP` would: the task file, the CLI, its library, the JRE."""
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in scienceworld.SETUP[record["task_id"]]["files"]:
        _copy_entry(entry, workdir)
    # The JRE arrives as a prepared tree with the rest of SETUP (`_materialize` copies the
    # directory), so there is nothing to unpack. Kept as a comment rather than a call: the
    # first design unpacked a tarball here and the platform's read-only verification tree
    # made the same call fail during a check.


def drive(workdir: Path, actions: list[str], *, budget: int) -> None:
    """Act through the CLI exactly as an agent would, stopping at the budget."""
    for action in actions[:budget]:
        proc = subprocess.run([sys.executable, "scw", "do", action], cwd=workdir,
                              capture_output=True, text=True, errors="replace")
        if proc.returncode != 0:
            raise SystemExit(f"the CLI refused {action!r}: {proc.stderr[-400:]}")
    subprocess.run([sys.executable, "scw", "done"], cwd=workdir, capture_output=True,
                   text=True, errors="replace")


def run_check(module, record: dict, workdir: Path) -> tuple[float, str]:
    """Run the check the way the platform's verifier does, re-materializing its inputs.

    The re-materialization is the point: it is what makes the grade independent of anything
    the agent did to `check.py`, `scw_env.py`, `scw_task.json` or the JRE in the workdir.
    """
    verifier = module.VERIFY[record["task_id"]]
    for entry in verifier.get("inputs") or []:
        # Same rule as `materialize`, and a shared helper so the two cannot drift: the
        # platform's `_materialize` (`eval/observe/tracing.py`) copies trees as trees, and this
        # mirrors it.
        _copy_entry(entry, workdir)
    argv = [sys.executable, *verifier["argv"][1:]]
    proc = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, errors="replace")
    reward_path = workdir / module.REWARD_REL
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = workdir / module.EVIDENCE_REL
    evidence = (json.loads(evidence_path.read_text())
                if evidence_path.is_file() else {})
    detail = {
        "returncode": proc.returncode,
        "reward": reward,
        "score": evidence.get("score"),
        "measured": evidence.get("measured"),
        "moves": evidence.get("moves"),
        "why": evidence.get("why") or evidence.get("error"),
        "stderr": proc.stderr[-400:],
    }
    return reward, json.dumps(detail, indent=1, sort_keys=True)


def run_local(module, record: dict, *, oracle: bool, workdir: Path) -> int:
    materialize(record, workdir)
    if oracle:
        drive(workdir, _gold(record, workdir), budget=module.STEP_BUDGET)
    reward, detail = run_check(module, record, workdir)
    print(f"task    {record['task_id']}")
    print(f"mode    {'oracle' if oracle else 'do-nothing'}")
    print(f"reward  {reward:g}")
    print(detail)
    expected = 1.0 if oracle else 0.0
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
    task = next(t for t in module.load()[0] if t["task_id"] == tid)
    setup = {"files": list(module.SETUP[tid]["files"])}
    if oracle:
        # The gold is generated **before** the run, and `_gold` builds the world from
        # `scw_task.json` -- so the task has to be materialized first, exactly as the
        # platform will materialize it for the harness. Leaving it out made the control
        # fail with `FileNotFoundError: .../scw_task.json` from the control itself.
        materialize(record, workdir)
        gold = _gold(record, workdir)
        setup["files"].append({"path": ".oracle/actions.json",
                               "content": json.dumps(gold)})
    work_root = workdir / "work"
    harness = work_root / "oracle_harness"
    shutil.copytree(Path(__file__).resolve().parent / "oracle_harness", harness,
                    dirs_exist_ok=True)
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work_root)
    started = time.time()
    result = run_one(
        repo=harness,
        task=task,
        setup=setup,
        verifier=module.VERIFY[tid],
        env=module.ENV[tid],
        sandbox=sandbox,
        run_id="scienceworld-control",
        run_seed="0",
    )
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true",
                      help="replay the environment's own gold action sequence; expect 1")
    mode.add_argument("--noop", action="store_true",
                      help="do nothing; expect 0")
    parser.add_argument("--platform", action="store_true",
                        help="run through eval.runner.run_one instead of by hand")
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
        if args.platform:
            return run_platform(scienceworld, record, oracle=args.oracle,
                                workdir=workdir, sandbox=not args.no_sandbox)
        return run_local(scienceworld, record, oracle=args.oracle, workdir=workdir)
    finally:
        print(f"elapsed {time.time() - started:.1f}s")
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
