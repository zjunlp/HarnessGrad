r"""The shared pieces for a Jericho episode: the environment, the log, and the reduction.

Both the agent's CLI and the check import this module, so "the game the agent played" and
"the game being scored" are built by **exactly one** code path -- the same argument ALFWorld
records in §6 of the skill, and the reason the two cannot quietly disagree about what an
action does.

The game's identity lives in the workdir
----------------------------------------
`jer_game.json` (written by `SETUP`, re-materialized by `VERIFY.inputs`) names the ROM and its
official seed. Neither side takes the game from the other's argv, and the check reads it from
a copy the platform rewrites immediately before grading.

Why the seed matters
--------------------
`game_info.py` gives each game a default seed, and `FrotzEnv.get_walkthrough()` is only valid
at that seed -- the walkthroughs are generated runs, not abstract solutions. Resetting with a
different seed moves the objects and the reference solution stops working, which would look
exactly like an unreachable check.
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path

#: The filename `SETUP` writes with the game's identity, and the log the CLI appends to.
GAME_FILENAME = "jer_game.json"
LOG_FILENAME = "jer_actions.jsonl"


def make_env(rom: Path, *, seed: int):
    """A `FrotzEnv` for `rom`, reset at `seed`.

    Imported inside the function so `data/datasets/jericho.py` stays importable on a machine without
    the package (`data/scaffolds/registry.py::published` imports every adapter to decide what the
    chooser offers).
    """
    from jericho import FrotzEnv

    # The seed is a **constructor** argument, not a `reset` one (measured:
    # `FrotzEnv(sf, seed=None)` and `reset(self)`). A seed that failed to apply would move the
    # objects and break the walkthrough, and the resulting 0 would look like an unreachable
    # check -- so the env is constructed with the seed and then reset, in that order.
    env = FrotzEnv(str(rom), seed=seed)
    env.reset()
    return env


def load_game(workdir: Path, *, rom_dir: Path | None = None,
              config_path: Path | None = None, rom_path: Path | None = None):
    """Build and reset the game named by `jer_game.json`. Returns the live env.

    `rom_dir` defaults to the workdir, which is where `VERIFY.inputs` puts the ROM for the
    check; the CLI's workdir has it too, because `SETUP` ships it there as well.
    """
    spec = json.loads((config_path or workdir / GAME_FILENAME).read_text())
    rom = rom_path or (rom_dir or workdir) / spec["rom"]
    if not rom.is_file():
        raise FileNotFoundError(f"no ROM at {rom}")
    return make_env(rom, seed=int(spec.get("seed") or 0))


def action_log_records(path: Path) -> list[dict]:
    """Every parseable record in the log; blank and broken lines are skipped.

    A truncated final line can only come from a crash mid-append, and a crash means the
    episode is over. The check counts what it skipped so the evidence says so rather than
    silently grading a shorter episode.
    """
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def actions_in(records: list[dict]) -> list[str]:
    return [str(r["action"]) for r in records
            if r.get("kind") == "action" and isinstance(r.get("action"), str)]


def append_action(path: Path, action: str, step: int) -> None:
    """Append one action. Raises on failure: a dropped append changes the episode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"kind": "action", "step": step, "action": action},
                                ensure_ascii=False) + "\n")


def append_marker(path: Path, kind: str, step: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"kind": kind, "step": step}) + "\n")


def replay(env, actions: list[str]) -> dict:
    """Apply `actions` in order and report the game's answer, including a crash.

    Returns `{"steps": [...], "failure": None | {...}}`: one step record per action the game
    accepted, and -- when the game died -- the exception that stopped the replay.

    The replay stops for exactly two reasons, and they are **not** the same event:

    * `done` -- the game ended. Zork can end, and continuing to act in a finished game is not
      what the reference solution does. `failure` is `None`; this is a measured episode.
    * an exception from `env.step` -- the interpreter or its process died. That is a
      **measurement failure**, and it is recorded as such (`type`, `message`, the step index,
      the action, and a traceback) instead of being swallowed. `check.py` then reports
      `measured: false`, writes no reward and exits 3, because the alternative -- grading the
      score of the last action that happened to work -- is indistinguishable from a real
      partial score.

    A command the parser simply does not understand is **not** an exception: measured, an empty
    line, an unknown word, a 300-character command and raw control bytes all come back as
    ordinary text ("I don't know the word ...", "I beg your pardon?"), with the game still
    running. So a raise cannot be read as "the game declined this action", and it is not
    replayed past.
    """
    steps = []
    for index, action in enumerate(actions):
        try:
            observation, reward, done, info = env.step(action)
        except Exception as exc:  # noqa: BLE001 - recorded, and the caller refuses to score it
            return {"steps": steps,
                    "failure": {"step": index, "action": action,
                                "type": type(exc).__name__, "message": str(exc),
                                "traceback": traceback.format_exc()[-2000:]}}
        steps.append({
            "step": index,
            "action": action,
            "score": int(info.get("score", 0)),
            "moves": int(info.get("moves", 0)),
            "reward": int(reward),
            "done": bool(done),
            "observation": str(observation)[:400],
        })
        if done:
            break
    return {"steps": steps, "failure": None}


def score_fraction(score: int, maximum: int) -> float:
    """The dataset's reduction: the game's own points over the game's own maximum.

    `get_max_score()` is a number the game states about itself, so the denominator does not
    depend on a solution. A game whose maximum is 0 (none in this suite, but a guard) scores
    0.0 rather than dividing by zero, and the evidence carries the pair either way.
    """
    if maximum <= 0:
        return 0.0
    return max(0, min(int(score), int(maximum))) / float(maximum)
