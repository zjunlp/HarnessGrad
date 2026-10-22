#!/usr/bin/env python3
"""The ALFWorld check: replay the agent's actions in a **fresh** environment and read
the environment's own `won` flag.

This is the dataset's whole grade. It does not read a summary the agent wrote, it does
not read the agent's observation, and it does not read any `won` the agent's CLI
recorded -- all of those are the agent's own bytes and the agent can make them say
anything. It reads one thing from the agent: the ordered list of actions in
`alf_actions.jsonl`. It then rebuilds the game from a trusted copy of the game file,
applies those actions one at a time to TextWorld, and asks TextWorld whether the PDDL
goal is satisfied. Success is `infos["won"]`, exactly as
`alfworld/agents/eval/evaluate_dagger.py` reads it.

The rule, precisely
-------------------
    reward = 1  iff  the replayed episode reports `won` at some step
    reward = 0  otherwise

"At some step" is upstream's rule, not a convenience: ALFWorld's evaluator takes the
maximum of the per-step `won` scores over the episode. An episode that satisfies the
goal and then keeps acting is still a success, and the replay stops at the first `done`
(TextWorld's own end-of-episode signal) exactly as the evaluator's loop does.

**`alf done` is recorded but is not a gate.** tau-bench's check *must* require `tau
done`, because tau-bench itself only computes a reward for a finished episode. ALFWorld
has no such rule: an episode ends when TextWorld says it ends (the goal is met or the
step budget is spent), and nothing in the benchmark asks the agent to declare anything.
Adding a `done` gate here would be inventing a requirement the benchmark does not have
-- which is a different failure from dropping one, and just as misleading. The record
still carries whether `done` was declared, because it is useful evidence.

What makes this check trustworthy
---------------------------------
* The game file is re-materialized by the platform at verification time
  (`VERIFY.inputs`), so editing `<workdir>/game.tw-pddl` does not change the grade.
* The same is true of this file and of `gym_env.py`: `SETUP` ships them for the agent's
  own CLI, and verification overwrites them. The check imports `gym_env` from the
  workdir *after* that overwrite.
* An episode the check cannot measure -- a game that will not load, a game file that is
  missing -- writes **no reward file** and exits 3. The platform then reports the task
  as unmeasured rather than as a zero the agent earned. A bare 0 is indistinguishable
  from a wrong answer, and that distinction is the whole reason this file writes
  evidence.

Outputs
-------
    reward file    1 or 0   (`/verifier/reward.txt` by default)
    evidence file  JSON     why, per action: what happened, which step won, what the
                            official per-step reduction would have produced
    stdout         why, in one line, so the platform's verdict detail carries it too
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

# Resolve helpers beside trusted code, including in the sealed /scorer root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data.scaffolds.scorer_input import add_arguments, run_bound

import gym_env  # noqa: E402

#: The container contract's locations. On a host (`files`) environment these are
#: overridden by the flags below, because `/verifier` does not exist there.
DEFAULT_REWARD_FILE = "/verifier/reward.txt"
DEFAULT_EVIDENCE_FILE = "/verifier/evidence.json"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", default=os.environ.get("ALFWORLD_WORKDIR", "."))
    parser.add_argument("--game", default=None, help="the trusted game file")
    parser.add_argument("--log", default=None, help="the agent's action log")
    parser.add_argument("--reward-file",
                        default=os.environ.get("ALFWORLD_REWARD_FILE",
                                               DEFAULT_REWARD_FILE))
    parser.add_argument("--evidence-file",
                        default=os.environ.get("ALFWORLD_EVIDENCE_FILE",
                                               DEFAULT_EVIDENCE_FILE))
    parser.add_argument("--budget", type=int, default=gym_env.STEP_BUDGET)
    add_arguments(parser)
    args = parser.parse_args(argv)
    workdir = Path(args.workdir)
    args.game = Path(args.game) if args.game else workdir / gym_env.GAME_FILENAME
    args.log = Path(args.log) if args.log else workdir / gym_env.LOG_FILENAME
    args.reward_file = Path(args.reward_file)
    args.evidence_file = Path(args.evidence_file)
    return args


def write_reward(path: Path, score: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{score}\n")


def write_evidence(path: Path, evidence: dict) -> None:
    """Evidence is for a reader, never for the score: failing to write it is not fatal."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=1, sort_keys=True,
                                   ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"could not write evidence: {exc}", file=sys.stderr)


def official_reduction(won_per_step: list[bool]) -> float:
    """The per-episode score ALFWorld's own evaluator computes from a `won` sequence.

    Copied from `alfworld/agents/eval/evaluate_dagger.py`, which builds

        step_rewards = [curr - prev for curr, prev in zip(scores, prev_rewards)]
        ...
        game_points = np.max(np.array(sequence_game_points), 0).tolist()

    with `scores = [float(item) for item in infos["won"]]`. Because `won` is monotone
    (TextWorld does not un-win a game) and the sequence starts at 0.0, the maximum of
    the increments equals "did any step win", which is what this file returns. The
    reduction is computed here anyway so the evidence carries both numbers, and
    `tests/quality/test_alfworld_official_scorer.py` pins the equality against a
    verbatim copy of upstream's code -- the copy, not an import of their package.
    """
    prev = 0.0
    increments: list[float] = []
    for won in won_per_step:
        curr = 1.0 if won else 0.0
        increments.append(curr - prev)
        prev = curr
    return max(increments) if increments else 0.0


def main(argv) -> int:
    args = parse_args(argv)
    return run_bound(args, {str(args.game): args.game},
                     lambda inputs, receipt: _score(args, inputs, receipt))


def _score(args, inputs, receipt) -> int:
    evidence: dict = {
        "scorer_input": receipt,
        "game_file": str(args.game),
        "action_log": str(args.log),
        "reward_file": str(args.reward_file),
    }

    if not args.game.is_file():
        # The check's own input is missing: not a measurement.
        evidence["error"] = f"no game file at {args.game}"
        evidence["measured"] = False
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot run: {evidence['error']}", file=sys.stderr)
        return 3

    records = gym_env.action_log_records(args.log)
    actions = [str(r["action"]) for r in records
               if r.get("kind") == "action" and isinstance(r.get("action"), str)]
    declared_done = any(r.get("kind") == "done" for r in records)
    unparseable = len([line for line in
                       (args.log.read_text(errors="replace").splitlines()
                        if args.log.is_file() else []) if line.strip()]) - len(records)
    evidence.update(actions_in_log=len(actions), log_records=len(records),
                    unparseable_lines=unparseable, agent_declared_done=declared_done,
                    budget=args.budget)

    try:
        state = gym_env.replay(str(inputs[str(args.game)]), actions, args.budget)
    except Exception as exc:  # noqa: BLE001 - a check that cannot run is not a zero
        evidence["error"] = f"could not build or replay the environment: {exc}"
        evidence["traceback"] = traceback.format_exc()[-2000:]
        evidence["measured"] = False
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot run: {evidence['error']}", file=sys.stderr)
        return 3

    won_per_step = [bool(r["won"]) for r in state["records"]]
    won_step = next((i for i, w in enumerate(won_per_step) if w), None)
    replayed = state["records"]
    # TextWorld's `done` also signals the step cap, so it cannot distinguish a win
    # from truncation. Reaching the cap is observable even with no extra logged
    # action; exhaustion additionally means the goal was never satisfied.
    budget_reached = len(replayed) >= args.budget
    budget_exhausted = budget_reached and not state["won"]
    # A command TextWorld did not understand comes back as "Nothing happens." It still
    # cost a step, and recording it is how a reader tells "the agent tried and was
    # refused" apart from "the agent did nothing".
    no_ops = [r["action"] for r in replayed
              if "Nothing happens" in str(r.get("observation", ""))]
    reward = 1 if state["won"] else 0
    official = official_reduction(won_per_step)

    evidence.update(
        measured=True,
        reward=reward,
        won=bool(state["won"]),
        won_at_step=won_step,
        actions_replayed=len(replayed),
        actions_not_replayed=len(actions) - len(replayed),
        budget_reached=bool(budget_reached),
        budget_exhausted=bool(budget_exhausted),
        episode_done=bool(replayed and replayed[-1]["done"]),
        no_op_actions=no_ops,
        won_per_step=won_per_step,
        official_game_points=official,
        # The agent's own narrative is not trusted, so it is not included. What is
        # included is the *action* at each step and TextWorld's answer, which is the
        # material a reader needs to see why the reward is what it is.
        steps=[{"step": i, "action": r["action"], "won": bool(r["won"]),
                "done": bool(r["done"]),
                "observation": str(r.get("observation", ""))[:200]}
               for i, r in enumerate(replayed)],
    )

    if won_step is None:
        if budget_exhausted:
            why = (f"the goal was not satisfied in the {args.budget}-step budget; the "
                   f"log holds {len(actions)} actions and only {len(replayed)} replayed")
        elif not actions:
            why = "the log holds no actions, so the goal cannot have been satisfied"
        else:
            why = (f"the goal was not satisfied after {len(replayed)} action(s); the "
                   f"episode ended without a win")
    else:
        why = f"the goal was satisfied at step {won_step + 1} of {len(replayed)}"
    evidence["why"] = why
    write_evidence(args.evidence_file, evidence)

    if reward == 1:
        write_reward(args.reward_file, 1)
        print(f"reward 1: {why} (official game points {official:g}; "
              f"agent declared done={declared_done})")
        return 0

    write_reward(args.reward_file, 0)
    print(f"reward 0: {why} (official game points {official:g}; "
          f"no-op actions {len(no_ops)})")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - a crash must not look like a measured zero
        traceback.print_exc()
        raise SystemExit(4)
