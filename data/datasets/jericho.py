r"""Jericho as a dataset: 56 Z-machine text adventures, scored by the games' own points.

    python3 tools/datasets.py --fetch --dataset jericho
    python3 data/datasets/jericho/control.py --oracle
    python3 data/datasets/jericho/control.py --oracle --platform
    driver.py --dataset jericho ...

Where the tasks come from
-------------------------
Two artifacts, both pinned:

* the **game files** from `BYU-PCCL/z-machine-games`, the archive Jericho's own quick-start
  tells you to download. The suite is 8.5 MB of `.z3`/`.z4`/`.z5`/`.z8` ROMs; the archive is
  110 MB because it also carries the `z-machine-games-master/z-machine-games-master`
  collection (another 100 MB of ROMs this dataset never reads).
* the **task list and reference solutions** from the installed `jericho` package's own
  `jericho/game_info.py`: 57 games, each with `rom`, the default `seed`, a full
  `walkthrough` and `minimal_actions`. That module is the benchmark's own table, so the task
  set is not this adapter's invention.

**56 of the 57 are usable**: `sherbet.z5` is absent from the BYU-PCCL archive (verified by
listing the extracted suite). The 56th is excluded rather than replaced, and `provision_status`
reports it, because a game with no ROM is a task nobody can run.

The metric, and where it comes from
-----------------------------------
Jericho's documented usage is:

    observation, reward, done, info = env.step('open mailbox')
    print('Total Score', info['score'], 'Moves', info['moves'])
    print('Scored', info['score'], 'out of', env.get_max_score())

so a game's result is the **points the game itself awards**, and there are three ways to
reduce it to a number. This adapter uses the fraction:

    reward = info['score'] / env.get_max_score()      (0.0 - 1.0)

and records the raw pair in the evidence. The alternatives, and why not:

* **normalised by the walkthrough's score** -- the usual "normalized score" in the Jericho
  paper. It differs from `get_max_score()` only for games where the reference solution does
  not collect every point, and it needs the walkthrough replayed per task to know the
  denominator; the environment's own maximum is a number the game states about itself, which
  is a better authority than a solution's outcome. Both are recorded, so a reader can pick.
* **absolute points** -- not comparable across games: 350 for Zork 1, 100 for `ludicorp`.
  A mean over absolute points is dominated by whichever games happen to have large maxima.

The fraction makes "0" mean "made no progress" and "1" mean "the game is finished", and it is
the same reduction the environment's own documentation demonstrates.

The oracle
----------
`FrotzEnv.get_walkthrough()`, at the game's **official seed** (`game_info[key]['seed']`, which
is what `get_walkthrough()` is valid for -- the docs say so and the seeds are checked below).
The walkthrough is the benchmark's reference solution, exactly as ALFWorld's `walkthrough` is,
and it lives in the installed package rather than in a file this dataset ships, so the
provisioner cannot corrupt it.

What is deliberately not added
------------------------------
* **No `done` gate.** A game ends when the game says so; `jer done` exists so a harness can
  stop and is recorded, not required. §2's rule, third dataset running.
* **No move budget.** Unlike ALFWorld and ScienceWorld, Jericho's games have no step limit
  and the benchmark does not impose one: Zork 1's walkthrough is 396 moves, and a limit that
  cut it off would score a correct oracle below 1. The budget is therefore **the length of
  the log**; a harness that never stops is bounded by the platform's 300 s timeout, which is
  the honest place for that bound.
"""
from __future__ import annotations

from data.scaffolds.scorer_input import bind as bind_scorer_input

import os
from pathlib import Path

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"


#: The benchmark's own evaluation set, within this adapter's pool: **every task here belongs
#: to it**. The pool is the 56 official games. The benchmark declares no division of its own.
#:
#: `None` is the accurate value only because nothing in the pool is study material -- it does
#: not claim the pool is the whole of the benchmark's evaluation set (see `ds1000`, which is a
#: measured subset). `--split official --side eval` is the comparable run: the whole pool, with
#: no study material in it. `--split declared` keeps this adapter's own division.
OFFICIAL_EVAL = None

#: Where the ROMs are extracted. Outside the platform tree on purpose: dataset bytes are not
#: platform code. `HG_JERICHO_DATA` overrides it.
DATASET_ROOT = Path(os.environ.get("HG_JERICHO_DATA",
                                   "/mnt/20t/xubuqiang/datasets/jericho"))

#: Where the BYU-PCCL archive extracts the suite, relative to the dataset root.
GAMES_SUBDIR = "z-machine-games-master/jericho-game-suite"

#: The archive, and **what is pinned about it**. It is the `master` archive of
#: `BYU-PCCL/z-machine-games` -- the URL Jericho's own quick-start gives -- and the pin is the
#: **sha256 of the download** rather than a commit, because that is the honest one: the
#: repository is archived and its commit could not be resolved from this host
#: (`github.com/.../archive/<sha>` needs the sha, and the API that would give it is
#: unreachable here). `--fetch` verifies the digest and deletes a mismatch, and
#: `tools/datasets.py --check` re-verifies it, so the bytes are as reproducible as a commit
#: would make them. Recorded this way rather than inventing a commit sha.
ARCHIVE_VERSION = ARCHIVE_SHA256 = "7c3b38d946009c591c3f8e88d87b6a0141f0f701f447b97477162dfc17ff9b2c"
ARCHIVE_URL = ("https://codeload.github.com/BYU-PCCL/z-machine-games/zip/refs/heads/master")
ARCHIVE_NAME = "z-machine-games.zip"

REWARD_FILE = "/verifier/reward.txt"
EVIDENCE_FILE = "/verifier/evidence.json"
REWARD_REL = "verifier/reward.txt"
EVIDENCE_REL = "verifier/evidence.json"

#: The game, the seed and the action log live in the workdir so both the CLI and the check
#: read the same trusted bytes: `SETUP` writes them and `VERIFY.inputs` re-materializes them.
GAME_FILE = "jer_game.json"
LOG_FILE = "jer_actions.jsonl"


def games_dir() -> Path:
    return DATASET_ROOT / GAMES_SUBDIR


def _official_games() -> list[dict]:
    """The benchmark's own game table, filtered to the ROMs that are actually present.

    `jericho.game_info` is imported **lazily**: `data/scaffolds/registry.py::published` imports every
    adapter to decide what the chooser offers, and an import that pulls in `jericho` (a
    ctypes-wrapped C library) at that point is a second thing that can fail on a machine that
    has not installed it.
    """
    import jericho.game_info as info

    suite = games_dir()
    records = []
    for key in sorted(dir(info)):
        if key.startswith("_"):
            continue
        entry = getattr(info, key)
        if not isinstance(entry, dict) or "rom" not in entry:
            continue
        rom = str(entry["rom"])
        path = suite / rom
        if not path.is_file():
            continue
        walkthrough = [a for a in str(entry.get("walkthrough") or "").split("/") if a]
        records.append({
            "task_id": Path(rom).stem,
            "key": key,
            "rom": rom,
            "path": path,
            "seed": int(entry.get("seed") or 0),
            "walkthrough": walkthrough,
        })
    if not records:
        raise FileNotFoundError(
            f"no game ROMs under {suite}; provision them with "
            f"`python3 tools/datasets.py --fetch --dataset jericho`")
    return records


def missing_games() -> list[str]:
    """Official games whose ROM the archive does not carry. Reported, not silently dropped."""
    import jericho.game_info as info

    suite = games_dir()
    out = []
    for key in sorted(dir(info)):
        if key.startswith("_"):
            continue
        entry = getattr(info, key)
        if isinstance(entry, dict) and "rom" in entry and not (suite / str(entry["rom"])).is_file():
            out.append(str(entry["rom"]))
    return out


def _goal(record: dict) -> str:
    """The instruction: what the interface is, and the budget that actually exists.

    Nothing here hints at the solution. The game's own opening text is deliberately **not**
    included: printing it in the goal would be free information the agent should get by
    looking, and it would go stale the moment the seed changes the starting room.
    """
    return (
        f"# Task\n"
        f"\n"
        f"You are playing a classic interactive-fiction game, `{record['task_id']}`, "
        f"described one room at a time. You act by typing commands.\n"
        f"\n"
        f"## How to act\n"
        f"\n"
        f"- See where you are and what you can see:\n"
        f"      python3 jer look\n"
        f"- Take one action (ordinary parser commands; the game decides whether it\n"
        f"  understands, and an action it does not understand still costs a move):\n"
        f"      python3 jer do \"open mailbox\"\n"
        f"- See your score and how many moves you have spent:\n"
        f"      python3 jer score\n"
        f"- See what you are carrying:\n"
        f"      python3 jer inventory\n"
        f"- When you are finished, say so:\n"
        f"      python3 jer done\n"
        f"- Every action is appended to `{LOG_FILE}`.\n"
        f"\n"
        f"## How it is scored\n"
        f"\n"
        f"- Your actions are replayed from the beginning in a fresh copy of the game at its\n"
        f"  official seed, and the **game's own score** is read at the end, as a fraction of\n"
        f"  the maximum the game can award. Finishing the game means reaching 1.0.\n"
        f"- There is no move limit: play until the game ends or you stop.\n"
        f"- Nothing you print is read. Only the actions you take are.\n"
    )


def _setup(record: dict) -> dict:
    """What the agent receives: the CLI, its library, the game's identity, and the ROM.

    The ROM has to be here and not only in `VERIFY.inputs`, which cost a platform control run
    to learn: measured, `--oracle --platform` scored 0 with "the log holds no actions", because
    the harness's `jer do ...` could not start the game -- the harness and the check share one
    workdir, and the ROM had only been written for the check. The platform's own dataset
    pattern (ALFWorld, ScienceWorld) puts everything both sides need in `SETUP` **and** in
    `VERIFY.inputs`, and that is now what this does.

    The ROM is not a secret -- it is the game, and the agent cannot play without it. The
    *answer* is the walkthrough. The agent sees a stripped package projection;
    the installed reference-bearing package is reserved for trusted verification.
    """
    import json

    return {"files": [
        {"path": LOG_FILE, "content": ""},
        {"path": GAME_FILE,
         "content": json.dumps({"task_id": record["task_id"], "rom": record["rom"],
                                "seed": record["seed"]})},
        {"path": "jer_env.py", "from": str(LIB_DIR / "jer_env.py")},
        {"path": "jer", "from": str(CLI_DIR / "jer")},
        {"path": record["rom"], "from": str(record["path"])},
    ]}


def _verifier(record: dict) -> dict:
    """The grade: the check, the ROM, and where the verdict goes.

    The ROM is re-materialized as an input so the check replays a copy the agent cannot have
    edited -- the same argument as ALFWorld's game file, and the reason a `files` environment
    needs `reward_file`/`evidence_file` to be honoured on the host path (§1).
    """
    import json

    spec = {
        "kind": "command",
        "argv": [
            "python3", "check.py",
            "--workdir", ".",
            "--game", GAME_FILE,
            "--rom", record["rom"],
            "--log", LOG_FILE,
            "--reward-file", REWARD_REL,
            "--evidence-file", EVIDENCE_REL,
        ],
        "inputs": [
            {"path": "check.py", "from": str(VERIFY_DIR / "check.py")},
            {"path": "jer_env.py", "from": str(LIB_DIR / "jer_env.py")},
            {"path": GAME_FILE,
             "content": json.dumps({"task_id": record["task_id"], "rom": record["rom"],
                                    "seed": record["seed"]})},
            {"path": record["rom"], "from": str(record["path"])},
        ],
        "reward_file": REWARD_FILE,
        "evidence_file": EVIDENCE_FILE,
        "timeout_s": VERIFY_TIMEOUT_S,
    }
    from data.runtimes import NATIVE_PINS
    spec["native_runtime"] = NATIVE_PINS["jericho"].copy()
    return bind_scorer_input(spec, record["task_id"], [GAME_FILE, record["rom"]])


def downloads() -> list[dict]:
    """Everything this dataset needs, with the traps written down beside it."""
    return [
        {
            "kind": "http",
            "name": "BYU-PCCL z-machine-games archive (jericho-game-suite)",
            "version": ARCHIVE_VERSION,
            "url": ARCHIVE_URL,
            "size": "110 MB archive; the 8.5 MB `jericho-game-suite` is what is read",
            "sha256": ARCHIVE_SHA256,
            "dst": "_src/" + ARCHIVE_NAME,
            "extract_to": ".",
            "note": "Trap 1: the `jericho` wheel ships `libfrotz.so` and the **game table** "
                    "(`jericho/game_info.py`: rom, seed, walkthrough) but **no ROMs**, so the "
                    "games are a provisioning step; the docs' own instruction points here. "
                    "Trap 2: the archive is 110 MB for 8.5 MB of usable games -- it also "
                    "carries a second full ROM collection under "
                    "`z-machine-games-master/z-machine-games-master/` that this dataset never "
                    "reads. Trap 3: `sherbet.z5` is in `game_info` and **not** in the archive "
                    "(verified by listing it), so 56 of the 57 official games are usable.",
        },
        {
            "kind": "pip",
            "name": "jericho",
            "package": "jericho",
            "version": "3.3.1",
            "size": "~3 MB (includes `libfrotz.so`)",
            "note": "The Frotz environment: the Z-machine interpreter (`libfrotz.so`), the "
                    "object tree, `get_valid_actions()`, and **`game_info.py`** -- the "
                    "benchmark's own game table with each game's seed and walkthrough, which "
                    "is where this dataset's task list and oracle come from. Installing it "
                    "from source needs a C compiler; the wheel needs none.",
        },
    ]


#: This module's own directory: the adapter is `data/datasets/jericho.py`, its content `data/datasets/jericho/`.
ASSETS = Path(__file__).resolve().parent / "jericho"
CLI_DIR = ASSETS / "cli"
LIB_DIR = ASSETS / "lib"
VERIFY_DIR = ASSETS / "verify"

AGENT_TIMEOUT_S = 300
VERIFY_TIMEOUT_S = 300
DEFAULT_ENV: dict = {"kind": "files", "network": "none",
                     "agent_timeout_s": AGENT_TIMEOUT_S,
                     "verify_timeout_s": VERIFY_TIMEOUT_S}
_LOAD_ERROR: str | None = None


def _env() -> dict:
    import importlib.util

    spec = importlib.util.find_spec("jericho")
    if spec is None or not spec.origin:
        raise ValueError("jericho runtime is not installed")
    runtime_spec = importlib.util.spec_from_file_location("_jericho_public_runtime", ASSETS / "runtime.py")
    runtime = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime)
    return {**DEFAULT_ENV, "agent_runtime": runtime.agent_runtime(str(Path(spec.origin).parent))}


def provision_status() -> dict:
    """What is present locally, for `tools/datasets.py --check` and `--list`."""
    suite = games_dir()
    roms = sorted(p.name for p in suite.glob("*.z*")) if suite.is_dir() else []
    archive = DATASET_ROOT / "_src" / ARCHIVE_NAME
    return {
        "root": str(DATASET_ROOT),
        "present": bool(roms) and archive.is_file(),
        "split_dir": str(suite),
        "roms": len(roms),
        "archive": str(archive) if archive.is_file() else None,
        "missing_official_roms": missing_games() if roms else [],
        "tasks": len(__getattr__("_RECORDS")),
    }


def load() -> tuple[list[dict], dict[str, str]]:
    """`(tasks, scorable)`. Every task is graded by its `VERIFY` entry."""
    records = __getattr__("_RECORDS")
    if _LOAD_ERROR is not None:
        raise ValueError(
            f"jericho: cannot read the game suite ({_LOAD_ERROR}). Provision it with "
            f"`python3 tools/datasets.py --fetch --dataset jericho` and install "
            f"`jericho==3.3.1`.")
    if not records:
        raise ValueError(f"jericho: no games under {games_dir()}")
    tasks = [{"task_id": r["task_id"], "goal": _goal(r), "inputs": []} for r in records]
    return tasks, {r["task_id"]: "" for r in records}


def _map(builder) -> dict:
    return {r["task_id"]: builder(r) for r in __getattr__("_RECORDS")}


def __getattr__(name: str):
    """`ENV`/`SETUP`/`VERIFY`/`SPLIT`, built on first use.

    Not at import: `data/scaffolds/registry.py::published` imports this module to decide what the
    chooser offers, and the maps carry one entry per game with a ROM path and a JSON blob.
    """
    if name == "_RECORDS":
        if "_RECORDS" not in globals():
            try:
                globals()["_RECORDS"] = _official_games()
            except Exception as exc:  # reported by load(), as with eager provisioning
                globals()["_RECORDS"] = []
                globals()["_LOAD_ERROR"] = f"{type(exc).__name__}: {exc}"
        return globals()["_RECORDS"]
    if name == "ENV":
        env = _env()
        return {r["task_id"]: dict(env) for r in __getattr__("_RECORDS")}
    if name == "SETUP":
        return _map(_setup)
    if name == "VERIFY":
        return _map(_verifier)
    if name == "SPLIT":
        # No division: the suite's games are the benchmark's evaluation material and it
        # publishes no study side (2026-10-24). `HG_JERICHO_SPLIT=none` selected this and
        # is gone; `--split default` asks for the platform's hash rule.
        return None
    if name == "SPLIT_SOURCE":
        return "none"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["KIND", "DATASET_ROOT", "load", "downloads", "provision_status",
           "missing_games", "games_dir", "DEFAULT_ENV"]
