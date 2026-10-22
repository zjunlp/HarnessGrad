#!/usr/bin/env python3
r"""The Jericho check: replay the agent's commands in a fresh game and read the game's score.

It reads one thing from the agent: the ordered commands in `jer_actions.jsonl`. It then
rebuilds the game from a trusted copy of `jer_game.json` **and a trusted copy of the ROM**
(both re-materialized by the platform immediately before this runs), replays the commands, and
reports the game's own score over the game's own maximum.

The rule, precisely
-------------------
    reward = info['score'] / env.get_max_score()

`info['score']` is the points the game awards, which is what Jericho's own documentation prints
("Total Score", "Scored X out of Y"), and `get_max_score()` is a number the game states about
itself. The raw pair, the per-step trace, and the score the reference walkthrough would reach
are all in the evidence, so "0.42" can be read back into "147 of 350".

**No `done` gate.** A game ends when the game says so; `jer done` exists so a harness can stop
and is recorded, not required -- the same reading as ALFWorld's and ScienceWorld's. §2.

**A crash is not a score.** A command the interpreter merely does not understand is answered
with text ("I don't know the word ...") and the game goes on, so it is an ordinary step. An
exception from `env.step` is the interpreter or its process dying: the replay stops, records
the exception (type, message, step, action), and this check refuses to measure -- no reward
file, `measured: false`, exit 3 -- rather than grading the last action that happened to work,
which would be indistinguishable from a real partial score.

What makes this check trustworthy
---------------------------------
* The game file, the ROM and this script are re-materialized by the platform before the check
  runs, so editing any of them in the workdir changes nothing.
* The seed is the game's official one, from the same file the agent was given, so the check
  replays the episode the agent actually played rather than a different arrangement.
* A check that cannot measure -- no game file, no ROM, an interpreter that will not start, a
  game that crashes while the log is replayed -- writes **no reward file**, writes evidence
  saying `measured: false`, and exits 3.

Outputs
-------
    reward file    0.0-1.0   (`/verifier/reward.txt` by default)
    evidence file  JSON      why: every step, its score, the maximum, the fraction
    stdout         why, in one line, so the platform's verdict detail carries it too
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

# Resolve helpers beside trusted code, including in the sealed /scorer root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data.scaffolds.scorer_input import add_arguments, run_bound

import jer_env  # noqa: E402

DEFAULT_REWARD_FILE = "/verifier/reward.txt"
DEFAULT_EVIDENCE_FILE = "/verifier/evidence.json"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--game", default=None, help="the trusted game file")
    parser.add_argument("--rom", default=None, help="the ROM name, for evidence")
    parser.add_argument("--log", default=None)
    parser.add_argument("--reward-file", default=DEFAULT_REWARD_FILE)
    parser.add_argument("--evidence-file", default=DEFAULT_EVIDENCE_FILE)
    add_arguments(parser)
    args = parser.parse_args(argv)
    workdir = Path(args.workdir)
    args.game = Path(args.game) if args.game else workdir / jer_env.GAME_FILENAME
    args.log = Path(args.log) if args.log else workdir / jer_env.LOG_FILENAME
    args.reward_file = Path(args.reward_file)
    args.evidence_file = Path(args.evidence_file)
    args.workdir = workdir
    return args


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
    if args.rom is None:
        args.rom = (json.loads(args.game.read_text())["rom"]
                    if args.game.is_file() else "missing-rom")
    return run_bound(args, {str(args.game): args.game, args.rom: args.workdir / args.rom},
                     lambda inputs, receipt: _score(args, inputs, receipt))


def _score(args, inputs, receipt) -> int:
    evidence: dict = {
        "scorer_input": receipt,
        "game_file": str(args.game),
        "rom": args.rom,
        "action_log": str(args.log),
    }

    if not args.game.is_file():
        evidence.update(measured=False, error=f"no game file at {args.game}")
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot measure: {evidence['error']}", file=sys.stderr)
        return 3

    records = jer_env.action_log_records(args.log)
    actions = jer_env.actions_in(records)
    declared_done = any(r.get("kind") == "done" for r in records)
    blank_or_broken = sum(
        1 for line in (args.log.read_text(errors="replace").splitlines()
                       if args.log.is_file() else []) if line.strip()) - len(records)
    evidence.update(actions_in_log=len(actions), log_records=len(records),
                    unparseable_lines=max(0, blank_or_broken),
                    agent_declared_done=declared_done)

    try:
        env = jer_env.load_game(args.workdir, config_path=inputs[str(args.game)],
                                rom_path=inputs[args.rom])
    except Exception as exc:  # noqa: BLE001 - a check that cannot run is not a zero
        evidence.update(measured=False, error=f"could not start the game: {exc}",
                        traceback=traceback.format_exc()[-2000:])
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot measure: {evidence['error']}", file=sys.stderr)
        return 3

    try:
        maximum = int(env.get_max_score())
        spec = json.loads(inputs[str(args.game)].read_text())
        replay = jer_env.replay(env, actions)
    except Exception as exc:  # noqa: BLE001
        evidence.update(measured=False, error=f"the game died mid-episode: {exc}",
                        traceback=traceback.format_exc()[-2000:])
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot measure: {evidence['error']}", file=sys.stderr)
        return 3

    failure = replay["failure"]
    if failure:
        # The game died while the log was being replayed. That is a measurement failure, not a
        # partial score: the steps that did replay were never the episode the agent played, and
        # grading their last score would be indistinguishable from a real partial score. So no
        # reward file, `measured: false`, and the exception itself in the evidence.
        evidence.update(
            measured=False,
            error=(f"the game crashed replaying action {failure['step']} "
                   f"({failure['action']!r}): {failure['type']}: {failure['message']}"),
            failure=failure,
            actions_replayed=len(replay["steps"]),
            steps=replay["steps"],
        )
        write_evidence(args.evidence_file, evidence)
        print(f"check cannot measure: {evidence['error']}", file=sys.stderr)
        return 3

    steps = replay["steps"]
    score = steps[-1]["score"] if steps else 0
    moves = steps[-1]["moves"] if steps else 0
    fraction = jer_env.score_fraction(score, maximum)
    evidence.update(
        measured=True,
        reward=fraction,
        score=score,
        max_score=maximum,
        moves=moves,
        seed=int(spec.get("seed") or 0),
        actions_replayed=len(steps),
        actions_not_replayed=len(actions) - len(steps),
        game_over=bool(steps and steps[-1]["done"]),
        solved=bool(maximum > 0 and score >= maximum),
        steps=steps,
    )
    if score >= maximum and maximum > 0:
        why = f"the game is finished: {score}/{maximum} points in {moves} moves"
    elif not actions:
        why = "the log holds no actions, so no points were scored"
    elif not steps:
        why = (f"none of the {len(actions)} logged action(s) replayed; the game is still at "
               f"{score}/{maximum}")
    else:
        why = (f"{score}/{maximum} points ({fraction:.3f}) after {moves} moves; the game is "
               f"not finished")
    evidence["why"] = why
    write_evidence(args.evidence_file, evidence)

    write_reward(args.reward_file, fraction)
    print(f"reward {fraction:g}: {why}")
    return 0 if fraction >= 1.0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - a crash must not look like a measured zero
        traceback.print_exc()
        raise SystemExit(4)
