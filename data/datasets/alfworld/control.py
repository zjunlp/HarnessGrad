#!/usr/bin/env python3
"""The ALFWorld controls: does the check actually reach 1, and actually reach 0?

A dataset whose oracle also scores 0 has an unreachable check, and every harness score
on it is meaningless. A dataset whose do-nothing control scores 1 has a check that
cannot fail. Both are quiet: they produce a plausible number in the right place. So
both are run and both are asserted here, and the same two runs are what the brief asks
a reader to be able to reproduce.

Both legacy `--oracle`/`--noop` and `--platform` use the normal runner,
managed trusted service and independently sealed native verifier. Dependencies
must be prepared explicitly; platform Python and the actor CLI remain stdlib.
`--no-sandbox` selects the runner's weaker diagnostic mode for either spelling.

The oracle is ALFWorld's own: the `walkthrough` array inside the extracted
`game.tw-pddl`, which is the reference solution the 0.4.2 asset shipped. Verified: all
134 valid_unseen walkthroughs satisfy their game (see docs/dataset-onboarding-skill.md).
"""
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
sys.path.insert(0, str(ROOT))

import data.datasets.alfworld as alfworld  # noqa: E402


def _relay_dir(workdir: Path) -> Path:
    """The host side of a local episode: a **sibling** of the workdir.

    On purpose, twice over. The game must not be inside the directory the agent works in,
    and the published transport pair must not be either -- on a real run the first lives
    on the host and the second is a read-only mount. This control is the one place where
    both halves live on one machine, so it keeps them apart in two subdirectories:
    `host/` (what the relay reads) and `tool/` (what the sandbox would publish).
    """
    return workdir.parent / f"{workdir.name}.relay"


def _published(workdir: Path) -> Path:
    """The directory that stands in for `/run/harnessgrad-tool` in a local episode."""
    return _relay_dir(workdir) / "tool"


def _record(task_id: str | None) -> dict:
    records = alfworld._RECORDS
    if not records:
        raise SystemExit("no ALFWorld tasks; provision the data first "
                         "(python3 tools/datasets.py --list)")
    if task_id is None:
        # A task from the pool a run would see: the eval side of a declared division (a
        # study file was supplied), else every game, since there is no division.
        split = alfworld.SPLIT
        task_id = split["eval"][0] if split else records[0]["task_id"]
    for record in records:
        if record["task_id"] == task_id:
            return record
    raise SystemExit(f"unknown task id {task_id!r}")


def _walkthrough(record: dict) -> list[str]:
    """The reference solution, read from the *unstripped* game file on disk."""
    gamedata = json.loads(Path(record["game_path"]).read_text())
    actions = list(gamedata.get("walkthrough") or [])
    if not actions:
        raise SystemExit(f"{record['task_id']} ships no walkthrough to replay")
    return actions


def materialize(record: dict, workdir: Path) -> None:
    """Stage stdlib client files and a trusted service declaration outside the actor."""
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in alfworld.SETUP[record["task_id"]]["files"]:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"], encoding="utf-8")
        else:
            shutil.copy2(entry["from"], target)
    (workdir / "alf").chmod(0o755)
    published = _published(workdir)
    published.mkdir(parents=True, exist_ok=True)
    _relay_dir(workdir).chmod(0o700)
    shutil.copy2(alfworld.TRANSPORT_SOURCE, published / "tool_transport.py")
    # Trusted declaration, kept outside the actor's workdir.
    declaration = _relay_dir(workdir) / "service.json"
    declaration.write_text(json.dumps(alfworld.SETUP[record["task_id"]]["tool_relay"]))
    declaration.chmod(0o600)


def drive(workdir: Path, actions: list[str], *, budget: int) -> None:
    """Act through the CLI exactly as an agent would, stopping at the budget.

    The environment is served on a host socket for the duration of the episode -- the
    same thing the runner must do -- and the CLI reaches the published pair through
    `HG_ALF_TOOL_DIR`, which this whole file uses only because a host run has no
    `/run/harnessgrad-tool` to mount. `budget` is passed to the CLI as well as to the
    environment, because an episode has one step budget: the CLI and the relay refuse to
    act at two different ones.
    """
    from eval.run import tool_relay
    published = _published(workdir)
    declaration = json.loads((_relay_dir(workdir) / "service.json").read_text())
    declaration["budget"] = budget
    env = dict(os.environ, HG_ALF_TOOL_DIR=str(published))
    with tool_relay.start({"tool_relay": declaration}) as server:
        socket_path = published / "tool.sock"
        socket_path.unlink(missing_ok=True)
        socket_path.symlink_to(server.path)
        try:
            for action in actions[:budget]:
                subprocess.run([sys.executable, "alf", "--budget", str(budget), "do", action],
                               cwd=workdir, env=env, check=True, capture_output=True,
                               text=True, errors="replace")
            subprocess.run([sys.executable, "alf", "--budget", str(budget), "done"],
                           cwd=workdir, env=env, check=True, capture_output=True, text=True)
        finally:
            socket_path.unlink(missing_ok=True)
    if server.fault():
        raise RuntimeError(f"control relay failed: {server.fault()}")


def run_check(workdir: Path, record: dict, *, budget: int) -> tuple[int, str]:
    """Run the check the way the platform's verifier does, and read its verdict.

    The check's inputs are written here **again**, exactly as `VERIFY.inputs` are
    re-materialized after the harness stops. That is what makes the grade independent of
    anything the agent did to `<workdir>/check.py`, `<workdir>/gym_env.py` or
    `<workdir>/game.tw-pddl`, and it is why this function does not simply trust the
    copies `materialize()` wrote.
    """
    from copy import deepcopy
    from eval.verification.verifier import _verify
    verifier = deepcopy(alfworld.VERIFY[record["task_id"]])
    verifier["argv"][verifier["argv"].index("--budget") + 1] = str(budget)
    verdict = _verify(workdir, verifier, "", task_id=record["task_id"],
                      sandbox=True, env=alfworld.ENV[record["task_id"]])
    score = verdict.get("score")
    reward = int(score) if score is not None and not verdict.get("unmeasured") else -1
    return reward, json.dumps(verdict, indent=1, sort_keys=True)


def run_local(record: dict, *, oracle: bool, workdir: Path, budget: int) -> int:
    return run_platform(record, oracle=oracle, workdir=workdir, budget=budget)


def run_platform(record: dict, *, oracle: bool, workdir: Path,
                 sandbox: bool = True, budget: int = alfworld.STEP_BUDGET) -> int:
    """The control goes through the normal managed service and native scorer route."""
    from eval.run.runner import run_one

    tid = record["task_id"]
    task = next(t for t in alfworld.load()[0] if t["task_id"] == tid)
    from copy import deepcopy
    setup = deepcopy(alfworld.SETUP[tid])
    setup["tool_relay"]["budget"] = budget
    setup["files"].append({"path": ".oracle/budget.json", "content": json.dumps(budget)})
    verifier = deepcopy(alfworld.VERIFY[tid])
    verifier["argv"][verifier["argv"].index("--budget") + 1] = str(budget)
    if oracle:
        setup["files"].append({"path": ".oracle/actions.json",
                               "content": json.dumps(_walkthrough(record)[:budget])})
    work_root = workdir.resolve() / "work"
    harness = work_root / "oracle_harness"
    shutil.copytree(ROOT / "data" / "datasets" / "alfworld" / "oracle_harness", harness,
                    dirs_exist_ok=True)
    # The platform reads this to decide what the sandbox may reveal; a real run sets it
    # to a directory outside the platform root.
    previous_work_root = os.environ.get("HARNESSGRAD_WORK_ROOT")
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work_root)
    try:
        result = run_one(
            repo=harness,
            task=task,
            setup=setup,
            verifier=verifier,
            env=alfworld.ENV[tid],
            sandbox=sandbox,
            run_id="alfworld-control",
            run_seed="0",
        )
    finally:
        if previous_work_root is None:
            os.environ.pop("HARNESSGRAD_WORK_ROOT", None)
        else:
            os.environ["HARNESSGRAD_WORK_ROOT"] = previous_work_root
    verdict = result.get("verdict") or {}
    print(f"task     {tid}")
    print(f"mode     {'oracle' if oracle else 'do-nothing'} (platform run_one, "
          f"sandbox={sandbox})")
    print(f"exit     {result.get('exit_code')}")
    print(f"score    {verdict.get('score')}  passed {verdict.get('passed')}")
    print(f"detail   {verdict.get('detail')}")
    if verdict.get("evidence"):
        print(f"evidence {json.dumps(verdict['evidence'].get('why'), ensure_ascii=False)}")
    expected = 1.0 if oracle else 0.0
    score = verdict.get("score")
    if score is None or verdict.get("unmeasured") or result.get("exit_code") != 0:
        # No score is not a zero. The platform refused the task, the scorer failed, or
        # the run produced nothing -- each is unmeasured, and an unmeasured do-nothing
        # arm must never be counted as a passing control. Treating `None` as 0.0 here
        # did exactly that: it turned a refusal into "the noop arm behaved".
        print(f"CONTROL UNMEASURED: no score for {tid}", file=sys.stderr)
        return 2
    if float(score) != expected:
        print(f"CONTROL FAILED: expected score {expected}, got {score}",
              file=sys.stderr)
        return 1
    return 0


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true",
                      help="replay ALFWorld's own walkthrough, expect reward 1")
    mode.add_argument("--noop", action="store_true",
                      help="do nothing, expect reward 0")
    parser.add_argument("--platform", action="store_true",
                        help="legacy spelling; controls always use the normal managed runner")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="run without the mount namespace (the "
                             "weaker mode; the default is the namespace on)")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--workdir", default=None,
                        help="where to work; default is a fresh temporary directory")
    parser.add_argument("--keep", action="store_true",
                        help="with --workdir, do not delete it first")
    parser.add_argument("--budget", type=int, default=alfworld.STEP_BUDGET)
    args = parser.parse_args(argv)

    if args.budget < 1:
        parser.error("--budget must be positive")
    record = _record(args.task_id)
    if args.workdir:
        workdir = Path(args.workdir)
        if workdir.exists() and not args.keep:
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        workdir = Path(tempfile.mkdtemp(prefix="alfworld-control-"))
        cleanup = True
    try:
        if args.platform:
            return run_platform(record, oracle=args.oracle, workdir=workdir,
                                sandbox=not args.no_sandbox, budget=args.budget)
        return run_platform(record, oracle=args.oracle, workdir=workdir,
                            sandbox=not args.no_sandbox, budget=args.budget)
    finally:
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)
            # `_relay_dir` is a sibling of the workdir, so it is not inside the tree the
            # line above removes: the staged game and the socket go with it.
            shutil.rmtree(_relay_dir(workdir), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
