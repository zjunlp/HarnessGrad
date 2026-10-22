#!/usr/bin/env python3
r"""The ScienceWorld check: replay the agent's actions in a **fresh** world and read the JVM's score.

This is the dataset's whole grade. It does not read a summary the agent wrote, what the
agent's CLI printed, or any score the CLI recorded -- all of those are the agent's own
bytes. It reads one thing from the agent: the ordered list of actions in
`scw_actions.jsonl`. It then rebuilds the world from a trusted copy of the task file (the
platform re-materializes `VERIFY.inputs` immediately before this runs), applies the
actions one at a time, and takes **the last score the JVM reported**.

The rule, precisely
-------------------
ScienceWorld's own evaluation loop (`examples/random_agent.py`) is:

    observation, reward, isCompleted, info = env.step(userInputStr)
    score = info['score']
    ...
    finalScores.append(score)
    avg = sum([x for x in finalScores if x >= 0]) / len(finalScores)

So the number is the **final** `info['score']`, an integer 0-100, and success is that score
reaching 100. The check reports `reward = score / 100.0` -- a fraction, because the
platform's reward file parses as a float and a 40/100 episode is genuinely a partial
result; the evidence carries the integer beside it. The success predicate is
`score == 100`, which is what the environment stops at when the goal is met.

**A `done` gate is not added.** The environment reports `done` when the goal is met *or*
when the step limit is spent, so `done` alone does not mean solved; and the benchmark does
not require the agent to declare anything. `scw done` exists because a harness needs a way
to stop, it is recorded in the evidence, and it is not a gate -- the same reasoning as
ALFWorld's (`docs/dataset-onboarding-skill.md` §2).

What makes this check trustworthy
---------------------------------
* The task file is re-materialized by the platform before this runs, so editing the
  workdir's copy changes nothing.
* The JRE is re-materialized too (`VERIFY.inputs`), so the check and the agent's CLI use
  the same pinned runtime.
* A check that cannot measure -- no task file, no JVM, a world that will not build --
  writes **no reward file**, writes evidence saying `measured: false`, and exits 3. The
  platform turns that into an invalid task rather than a zero the agent earned.

Outputs
-------
    reward file    0.0-1.0   (`/verifier/reward.txt` by default)
    evidence file  JSON      why: every step, its score, how many moves were spent
    stdout         why, in one line, so the platform's verdict detail carries it too
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

# Resolve helpers beside trusted code, also in the sealed /scorer root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data.scaffolds.scorer_input import add_arguments, run_bound

import scw_env  # noqa: E402

DEFAULT_REWARD_FILE = "/verifier/reward.txt"
DEFAULT_EVIDENCE_FILE = "/verifier/evidence.json"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--task", default=None, help="the trusted task file")
    parser.add_argument("--log", default=None, help="the agent's action log")
    parser.add_argument("--reward-file", default=DEFAULT_REWARD_FILE)
    parser.add_argument("--evidence-file", default=DEFAULT_EVIDENCE_FILE)
    parser.add_argument("--budget", type=int, default=scw_env.STEP_BUDGET)
    add_arguments(parser)
    args = parser.parse_args(argv)
    workdir = Path(args.workdir)
    args.task = Path(args.task) if args.task else workdir / scw_env.TASK_FILENAME
    args.log = Path(args.log) if args.log else workdir / scw_env.LOG_FILENAME
    args.reward_file = Path(args.reward_file)
    args.evidence_file = Path(args.evidence_file)
    return args


def _point_at_the_jvm(workdir: Path) -> Path | None:
    """Set `JAVA_HOME`/`PATH` **before anything imports py4j**.

    Measured, and it cost a control run: `py4j.java_gateway.launch_gateway` resolves the
    interpreter as `os.path.join(os.environ["JAVA_HOME"], "bin", "java")` and falls back to
    the bare name `java`. The platform hands a check a **minimal environment**
    (`eval/verification/verifier.py::_verifier_env`: PATH, HOME, LANG only), so without this the check
    dies with `FileNotFoundError: [Errno 2] No such file or directory: 'java'` -- and the
    CLI, which ran with the login shell's PATH, had worked. A JVM only one of the two sides
    can find is exactly the failure `docs/dataset-onboarding-skill.md` §12 warns about.

    Returns the home it found, or None when there is none -- the caller reports that as
    `measured: false` rather than crashing.
    """
    # The JRE arrives as a **prepared tree** in `VERIFY.inputs` (the platform copies a
    # directory with `copytree`), so there is nothing to unpack here. A tarball was the first
    # design and it failed on the read-only task tree during verification -- measured,
    # `[Errno 30] Read-only file system` -- which is the same lesson as ALFWorld's
    # materialize-everything rule, one layer down.
    try:
        home = scw_env.java_home(workdir)
    except RuntimeError:
        return None
    os.environ["JAVA_HOME"] = str(home)
    os.environ["PATH"] = str(home / "bin") + os.pathsep + os.environ.get("PATH", "")
    return home


def write_reward(path: Path, score: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{score:g}\n")


def write_evidence(path: Path, evidence: dict) -> None:
    """Evidence is for a reader, never for the score: failing to write it is not fatal."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=1, sort_keys=True,
                                   ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"could not write evidence: {exc}", file=sys.stderr)


def main(argv) -> int:
    args = parse_args(argv)
    return run_bound(args, {str(args.task): args.task, "scienceworld.jar": Path(args.workdir) / "scienceworld.jar"},
                     lambda inputs, receipt: _score(args, inputs, receipt))


def _score(args, inputs, receipt) -> int:
    workdir = Path(args.workdir)
    evidence: dict = {
        "scorer_input": receipt,
        "task_file": str(args.task),
        "action_log": str(args.log),
        "budget": args.budget,
    }

    if not args.task.is_file():
        evidence.update(measured=False, error=f"no task file at {args.task}")
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot measure: {evidence['error']}", file=sys.stderr)
        return 3

    records = scw_env.action_log_records(args.log)
    actions = scw_env.actions_in(records)
    declared_done = any(r.get("kind") == "done" for r in records)
    evidence.update(actions_in_log=len(actions), log_records=len(records),
                    agent_declared_done=declared_done,
                    unparseable_lines=None)

    java_home = _point_at_the_jvm(workdir)
    evidence["java_home"] = str(java_home) if java_home else None
    if java_home is None:
        evidence.update(measured=False,
                        error="no Java runtime in the workdir or under the dataset root")
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot measure: {evidence['error']}", file=sys.stderr)
        return 3

    try:
        env = scw_env.load_task(workdir, step_budget=args.budget,
                                config_path=inputs[str(args.task)],
                                server_path=(inputs["scienceworld.jar"] if receipt["verified"] else None))
    except Exception as exc:  # noqa: BLE001 - a check that cannot run is not a zero
        evidence.update(measured=False,
                        error=f"could not build the world: {exc}",
                        traceback=traceback.format_exc()[-2000:])
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot measure: {evidence['error']}", file=sys.stderr)
        return 3

    steps = []
    score = 0
    try:
        for index, action in enumerate(actions[:args.budget]):
            observation, _reward, done, info = env.step(action)
            score = int(info.get("score", 0))
            steps.append({"step": index, "action": action, "score": score,
                          "done": bool(done),
                          "observation": str(observation)[:200]})
            if done:
                break
    except Exception as exc:  # noqa: BLE001
        evidence.update(measured=False, error=f"the world died mid-episode: {exc}",
                        traceback=traceback.format_exc()[-2000:], steps=steps)
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot measure: {evidence['error']}", file=sys.stderr)
        return 3

    # Read the goal report **before** closing: `get_goal_progress()` is a JVM call, and
    # after `close()` the gateway is gone -- measured, it raised `Gateway is not
    # connected.` and turned a graded episode into a crash.
    try:
        goal_progress = str(env.get_goal_progress())[:2000]
    except Exception as exc:  # noqa: BLE001 - a missing report is not a missing grade
        goal_progress = f"<could not read the goal report: {exc}>"
    env.close()
    moves = len(steps)
    solved = score >= 100
    reward = max(0, min(score, 100)) / 100.0
    evidence.update(
        measured=True,
        reward=reward,
        score=score,
        solved=solved,
        moves=moves,
        budget_exhausted=bool(moves >= args.budget and not solved),
        steps=steps,
        # The environment's own goal report, kept because it is the material a reader
        # needs to see *why* a partial score is partial.
        goal_progress=goal_progress,
    )
    if solved:
        why = f"the goal was met: score {score}/100 after {moves} move(s)"
    elif moves == 0:
        why = "the log holds no actions, so the goal cannot have been met"
    elif moves >= args.budget:
        why = (f"the {args.budget}-move budget was spent with score {score}/100; the "
               f"goal was not met")
    else:
        why = (f"the episode ended after {moves} move(s) with score {score}/100; the "
               f"goal was not met")
    evidence["why"] = why
    write_evidence(args.evidence_file, evidence)

    if solved:
        write_reward(args.reward_file, 1.0)
        print(f"reward 1: {why} (agent declared done={declared_done})")
        return 0

    write_reward(args.reward_file, reward)
    print(f"reward {reward:g}: {why}")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - a crash must not look like a measured zero
        traceback.print_exc()
        raise SystemExit(4)
