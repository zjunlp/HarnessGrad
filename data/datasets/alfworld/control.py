#!/usr/bin/env python3
"""The ALFWorld controls: does the check actually reach 1, and actually reach 0?

A dataset whose oracle also scores 0 has an unreachable check, and every harness score
on it is meaningless. A dataset whose do-nothing control scores 1 has a check that
cannot fail. Both are quiet: they produce a plausible number in the right place. So
both are run and both are asserted here, and the same two runs are what the brief asks
a reader to be able to reproduce.

Two ways to run each control
----------------------------
`--oracle`/`--noop` drive the CLI by hand and run the check directly. This needs
nothing but `pip install alfworld textworld` plus the extracted data, and it is the
"no Docker" path. The local arm starts the **relay** itself
(`data/datasets/alfworld/relay.py`): the environment has to live outside the agent's
working directory, so "drive the CLI" now means "serve the episode on a host socket and
let the CLI act against it".

`--platform` runs the same episode through the platform's own `eval.run.runner.run_one`
with an oracle control harness (`data/datasets/alfworld/oracle_harness/`). That exercises
`SETUP` -> harness -> `VERIFY` end to end on a `files` environment. It is **refused**
here: the relay belongs to the task's environment, so the *runner* must stage the game
and start it -- `eval/containers/` does not wire that yet (a separate task). Refusing is
the honest arm: a `--platform` run without a relay fails every action and would read as
a measured zero.

The oracle is ALFWorld's own: the `walkthrough` array inside the extracted
`game.tw-pddl`, which is the reference solution the 0.4.2 asset shipped. Verified: all
134 valid_unseen walkthroughs satisfy their game (see docs/dataset-onboarding-skill.md).
"""
from __future__ import annotations

import argparse
import importlib.util
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


def _relay():
    """`relay.py` loaded by path.

    Not `import data.datasets.alfworld.relay`: `data/datasets/alfworld.py` is a *module*
    with the same name as this directory, so the directory is not a package and a
    submodule import cannot resolve. (The same reason the adapter loads `lib/gym_env.py`
    by path.)
    """
    path = Path(__file__).resolve().parent / "relay.py"
    spec = importlib.util.spec_from_file_location("alfworld_relay_control", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _relay_dir(workdir: Path) -> Path:
    """The host side of a local episode: a **sibling** of the workdir.

    On purpose, twice over. The game must not be inside the directory the agent works in,
    and the published transport pair must not be either -- on a real run the first lives
    on the host and the second is a read-only mount. This control is the one place where
    both halves live on one machine, so it keeps them apart in two subdirectories:
    `host/` (what the relay reads) and `tool/` (what the sandbox would publish).
    """
    return workdir.parent / f"{workdir.name}.relay"


def _host_game(workdir: Path) -> Path:
    """Where the relay's own copy of the game is staged (never in the workdir)."""
    return _relay_dir(workdir) / "host" / alfworld.GAME_IN_WORKDIR


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
    """Put the task where `SETUP` would: the CLI and its environment library.

    Exactly `SETUP`'s file list, so a change there cannot drift from what this control
    exercises. Nothing else goes in the workdir: the **game** is staged into
    `_host_game(workdir)` from the trusted `VERIFY.inputs` copy, and the transport is
    *published* into `_published(workdir)` the way `eval/containers/tool_route.py`
    publishes it into a sandbox -- one fixed pair, `tool_transport.py` and the socket,
    which is what the CLI imports and calls.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in alfworld.SETUP[record["task_id"]]["files"]:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"], encoding="utf-8")
        else:
            shutil.copy2(entry["from"], target)
    (workdir / "alf").chmod(0o755)
    relay = _relay()
    published = _published(workdir)
    published.mkdir(parents=True, exist_ok=True)
    shutil.copy2(alfworld.TRANSPORT_SOURCE, published / "tool_transport.py")
    relay.stage(relay.game_content(alfworld.VERIFY[record["task_id"]]),
                _host_game(workdir))


def drive(workdir: Path, actions: list[str], *, budget: int) -> None:
    """Act through the CLI exactly as an agent would, stopping at the budget.

    The environment is served on a host socket for the duration of the episode -- the
    same thing the runner must do -- and the CLI reaches the published pair through
    `HG_ALF_TOOL_DIR`, which this whole file uses only because a host run has no
    `/run/harnessgrad-tool` to mount. `budget` is passed to the CLI as well as to the
    environment, because an episode has one step budget: the CLI and the relay refuse to
    act at two different ones.
    """
    published = _published(workdir)
    socket_path = published / "tool.sock"
    env = dict(os.environ, HG_ALF_TOOL_DIR=str(published))
    with _relay().serve(_host_game(workdir), socket_path,
                        budget=budget) as server:
        for action in actions[:budget]:
            subprocess.run([sys.executable, "alf", "--budget", str(budget), "do", action],
                           cwd=workdir, env=env, check=True, capture_output=True,
                           text=True, errors="replace")
        subprocess.run([sys.executable, "alf", "--budget", str(budget), "done"],
                       cwd=workdir, env=env, check=True, capture_output=True, text=True,
                       errors="replace")
        # The episode is over; nothing should have asked for more than its budget.
        if server.receipt["errors"] or server.receipt["refused"]:
            raise SystemExit(f"relay reported faults during the episode: "
                             f"{json.dumps(server.receipt, sort_keys=True)}")


def run_check(workdir: Path, record: dict, *, budget: int) -> tuple[int, str]:
    """Run the check the way the platform's verifier does, and read its verdict.

    The check's inputs are written here **again**, exactly as `VERIFY.inputs` are
    re-materialized after the harness stops. That is what makes the grade independent of
    anything the agent did to `<workdir>/check.py`, `<workdir>/gym_env.py` or
    `<workdir>/game.tw-pddl`, and it is why this function does not simply trust the
    copies `materialize()` wrote.
    """
    verifier = alfworld.VERIFY[record["task_id"]]
    for entry in verifier["inputs"]:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"], encoding="utf-8")
        else:
            source = Path(entry["from"])
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
    argv = [sys.executable, *verifier["argv"][1:]]
    argv[argv.index("--budget") + 1] = str(budget)
    proc = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, errors="replace")
    reward_path = workdir / alfworld.REWARD_REL
    reward = int(reward_path.read_text().strip()) if reward_path.is_file() else -1
    evidence_path = workdir / alfworld.EVIDENCE_REL
    evidence = (json.loads(evidence_path.read_text())
                if evidence_path.is_file() else {})
    verdict = {
        "returncode": proc.returncode,
        "reward": reward,
        "why": evidence.get("why"),
        "won_at_step": evidence.get("won_at_step"),
        "actions_replayed": evidence.get("actions_replayed"),
        "official_game_points": evidence.get("official_game_points"),
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    return reward, json.dumps(verdict, indent=1, sort_keys=True)


def run_local(record: dict, *, oracle: bool, workdir: Path, budget: int) -> int:
    materialize(record, workdir)
    if oracle:
        drive(workdir, _walkthrough(record), budget=budget)
    reward, detail = run_check(workdir, record, budget=budget)
    print(f"task    {record['task_id']}")
    print(f"mode    {'oracle' if oracle else 'do-nothing'}")
    print(f"reward  {reward}")
    print(detail)
    expected = 1 if oracle else 0
    if reward != expected:
        print(f"CONTROL FAILED: expected reward {expected}, got {reward}", file=sys.stderr)
        return 1
    return 0


def run_platform(record: dict, *, oracle: bool, workdir: Path,
                 sandbox: bool = True) -> int:
    """The same episode through `eval.run.runner.run_one`, with no Docker and no model.

    **Refused, and the refusal is the measurement.** The agent's CLI is now a client of a
    host-side environment (`relay.py`), and that environment is the *task's*: the runner
    stages the stripped game outside the sandbox, starts the relay, and passes its socket
    to the sandbox as `tool_socket=` so the CLI finds it published at
    `/run/harnessgrad-tool/tool.sock`. `eval/containers/` does not do that yet (a
    separate task owns the mount wiring), so a `--platform` run today would fail every
    `alf do`, leave a log with no actions, and come back as a measured zero -- exactly
    the number that must never be reported for an unwired platform. The local arm has no
    such dependency: it publishes the pair itself and starts the relay itself.

    Everything below is kept, unreachable, because it is the arm that has to work once
    the wiring lands and deleting it would lose the review.
    """
    print("CONTROL UNMEASURED: `--platform` needs the runner to stage the game, start "
          "data/datasets/alfworld/relay.py for the task and pass its socket as "
          "`tool_socket=`; eval/containers/ does not wire the relay yet (this migration "
          "also needs PLAN_VERSION 4 -> 5). The local arm "
          "(`control.py --oracle`, no `--platform`) runs the same episode with the relay "
          "started here and is the runnable control.", file=sys.stderr)
    return 2


def _run_platform_when_wired(record: dict, *, oracle: bool, workdir: Path,
                             sandbox: bool = True) -> int:
    """The `--platform` body, waiting for the runner to own the relay.

    The control harness (`data/datasets/alfworld/oracle_harness/`) replays `.oracle/actions.json`
    through the CLI and otherwise does nothing, which is exactly what a harness control
    should be: a fixed program that runs what the dataset hands it.

    The harness is **copied into the work root** before the run, because a real run
    copies its harness there too, and because the sandbox refuses to run when the
    subject's tree is inside the platform it must hide. With the sandbox on, this
    control also proves that the adapter's `VERIFY` works with the namespace in place --
    including the writable verdict directory the reward-file contract needs.
    """
    from eval.run.runner import run_one

    tid = record["task_id"]
    task = next(t for t in alfworld.load()[0] if t["task_id"] == tid)
    setup = {"files": list(alfworld.SETUP[tid]["files"])}
    if oracle:
        setup["files"].append({"path": ".oracle/actions.json",
                               "content": json.dumps(_walkthrough(record))})
    work_root = workdir / "work"
    harness = work_root / "oracle_harness"
    shutil.copytree(ROOT / "data" / "datasets" / "alfworld" / "oracle_harness", harness,
                    dirs_exist_ok=True)
    # The platform reads this to decide what the sandbox may reveal; a real run sets it
    # to a directory outside the platform root.
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work_root)
    result = run_one(
        repo=harness,
        task=task,
        setup=setup,
        verifier=alfworld.VERIFY[tid],
        env=alfworld.ENV[tid],
        sandbox=sandbox,
        run_id="alfworld-control",
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
                        help="run through eval.runner.run_one instead of by hand")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="with --platform, run without the mount namespace (the "
                             "weaker mode; the default is the namespace on)")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--workdir", default=None,
                        help="where to work; default is a fresh temporary directory")
    parser.add_argument("--keep", action="store_true",
                        help="with --workdir, do not delete it first")
    parser.add_argument("--budget", type=int, default=alfworld.STEP_BUDGET)
    args = parser.parse_args(argv)

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
                                sandbox=not args.no_sandbox)
        return run_local(record, oracle=args.oracle, workdir=workdir,
                         budget=args.budget)
    finally:
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)
            # `_relay_dir` is a sibling of the workdir, so it is not inside the tree the
            # line above removes: the staged game and the socket go with it.
            shutil.rmtree(_relay_dir(workdir), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
