"""ALFWorld as a dataset: a text-adventure benchmark whose reward is PDDL goal
satisfaction computed by the environment. INTERFACE.md §2.5.9.

    python3 tools/datasets.py --write        # regenerate DOWNLOADS.md from downloads()
    python3 data/datasets/alfworld/control.py --oracle   # the oracle control (no Docker)
    driver.py --dataset alfworld ...

Where the tasks come from
-------------------------
`alfworld/alfworld`, the `valid_unseen` split of the `json_2.1.1` data. That is the
split ALFWorld's own paper evaluates out-of-distribution on, and it is what a score
here should be compared with. The task list is **not** this adapter's invention: it is
the same filter `alfworld/agents/environment/alfred_tw_env.py:collect_game_files`
applies -- a trial directory must have both `traj_data.json` and `game.tw-pddl`, must
not be a movable-receptacle or sliced-object trajectory, and its `game.tw-pddl` must
carry `solvable: true`. Measured over the extracted data: 134 trials pass, which is
ALFWorld's own `Evaluating with 134 games`.

The three provisioning traps, each verified rather than repeated
---------------------------------------------------------------
1. **The data is in GitHub release assets, not in the repository and not on Hugging
   Face.** The filenames and the release tags come from ALFWorld's own
   `scripts/alfworld-download`; `downloads()` below names them with the tags pinned.
2. **`solvable` lives only in `game.tw-pddl`, which ships in the `0.4.2` asset**
   (`json_2.1.3_tw-pddl.zip`). Verified: `json_2.1.1_json.zip` contains only
   `traj_data.json` (7302 of them) and `json_2.1.1_pddl.zip` only `initial_state.pddl`
   (7080); neither contains a `game.tw-pddl`. Without that key
   `collect_game_files` skips **every** trial and the dataset loads as empty.
   Measured nuance: because the `0.4.2` game file embeds `pddl_problem` and `grammar`,
   the `json_2.1.1_pddl.zip` archive is *not* read by the loading path -- it is read by
   ALFWorld's generation path (`alfworld-generate`). `downloads()` still names all
   three, because `alfworld-download` fetches all three and a regeneration needs it.
3. **The PDDL domain and the TextWorld grammar are in the repository's Python package,
   not in any data pack.** Verified location:
   `alfworld/data/alfred.pddl` and `alfworld/data/alfred.twl2` -- i.e.
   `_src/repo/alfworld/data/` in the checkout this adapter was validated against, and
   `site-packages/alfworld/data/` in an install. ALFWorld's own
   `scripts/alfworld-download` copies them from the *installed package* into
   `$ALFWORLD_DATA/logic/`, which is what `base_config.yaml` and `eval_config.yaml`
   point `logic.domain`/`logic.grammar` at. The symptom of a missing one depends on
   where you enter: pointing TextWorld at a `.pddl` (or extensionless) file gives
   `ValueError: Unsupported game format`, while an empty grammar gives a bare
   `StopIteration` and a missing `--domain` gives `FileNotFoundError` -- none of which
   names the real cause. Our loading path does not need them (the game file is
   self-contained), and the adapter does not depend on the `logic/` copy; it is
   provisioned because ALFWorld's own tooling expects it.

How ALFWorld fits the platform's one-shot contract
--------------------------------------------------
ALFWorld is interactive and multi-turn; the platform grades what a harness *left
behind*. The tau-bench pattern resolves that: the agent acts through a CLI
(`data/datasets/alfworld/cli/alf`), the CLI appends every action to `alf_actions.jsonl`, and
`data/datasets/alfworld/verify/check.py` replays that log in a freshly built environment and
reads TextWorld's own `won` flag. The CLI's observations, its summary and any `won` it
prints are never consulted -- the log holds actions and actions are re-executed.

The environment is `kind: "files"`, not a container. That is forced by a hard constraint
of this exercise (`Do NOT use Docker`) and it is the reason this dataset works on a
host with nothing but `pip install alfworld textworld`: the CLI and the check both run
under the platform's Python. The consequence is stated loudly in
`docs/dataset-onboarding-skill.md`: a `files` environment cannot declare a longer
`agent_timeout_s`, and the platform's `reward_file`/`evidence_file` channels were
container-only before this dataset (`eval/verification/verifier.py::_verify` now honours them for a
host check as well -- the smallest change that let a no-Docker dataset keep the
contract's evidence channel).

What the agent can read, and what that used to cost
--------------------------------------------------
**Closed.** The agent used to receive `game.tw-pddl` in its working directory with only
the reference solution removed (`_stripped_game`), and what was left was the **whole
initial state**: `pddl_domain`, `pddl_problem` and `grammar`. `pddl_problem` is
ALFWorld's own `:init` -- measured on this adapter's first `valid_unseen` game, 291
facts -- and it states which object is inside which receptacle (`inReceptacle AlarmClock_... Desk_...`) and which object or
receptacle is at which location (`objectAtLocation`, `receptacleAtLocation`). The object
and receptacle ids are exactly the ids `alfworld`'s own `Demangler` turns into the names
the agent sees, and that demangler reads nothing but the ids. So an agent that opened the
file could read the object layout instead of exploring for it -- strictly easier than
ALFWorld as published, where finding an unseen object is part of the task.

The file cannot travel with the task any more, and the fix is not a smaller file:
measured, TextWorld raises `KeyError` for a game file missing `pddl_problem`, `grammar`
or `pddl_domain`, so every buildable copy carries the layout. The environment therefore
runs **outside** the agent's namespace. `data/datasets/alfworld/relay.py` builds the same
TextWorld environment from the same bytes with the same `gym_env.build`, and answers two
ops over the platform's transport (`eval/service/tool_transport.py`): `look` (the last
applied step's observation, no step consumed) and `do` (one step). A reply is exactly
`{obs, admissible, won, done, step}` -- five fields projected off the environment's own
record, so there is no key the PDDL, the object ids or the goal literal could travel in.
The agent's CLI (`cli/alf`) is a client of that process and never opens a game file.

`SETUP` no longer ships the game at all. `_stripped_game` still exists and still matters:
it is the bytes the relay is given (and the bytes `VERIFY.inputs` grades from), so the
environment the agent acts in and the environment the check replays are built from one
text -- `tests/quality/test_alfworld_relay.py` pins that byte identity for all 134 games.

Two things about this are **not** in this repository yet, and both are required before
an end-to-end run: (1) the runner must stage those bytes outside the sandbox and start
`relay.py`, then pass the socket to the sandbox as `tool_socket=` so it is published at
`/run/harnessgrad-tool/tool.sock` where the CLI imports and calls it (the mount wiring
lives in `eval/containers/`, a separate task -- and this migration is the first to
require `PLAN_VERSION` 4 -> 5, because from then on what a subject sees has changed);
(2) a relay fault currently makes `alf` exit nonzero, and nothing yet maps that to an
**unmeasured** task rather than a 0 -- `Relay.receipt` is the evidence the runner needs
for that. Neither is faked here. `control.py --oracle` (the local arm) publishes the
same pair into a temp directory itself and is unaffected.

The metric, and what is deliberately not added
----------------------------------------------
`reward = 1` iff the replayed episode reports `won` at some step inside `STEP_BUDGET`
actions. This is ALFWorld's own metric (`evaluate_dagger` takes the maximum of the
per-step `won`). `alf done` exists in the CLI -- the harness needs a way to stop -- but
the check does **not** require it: ALFWorld has no such requirement, and tau-bench's
`done` gate exists only because tau-bench computes a reward only for a finished
episode. Adding one here would be inventing a requirement the benchmark does not have.

The split
---------
The benchmark's own evaluation split is *all* of `valid_unseen` and it declares no
train/eval division. The platform needs one, so it is **imposed here**, derived from the
game id (the task id before `__trial_`) by hash so a game's demonstration trials
stay together and adding or removing a task does not reshuffle the other side
(`_is_eval`, the same rule `data/datasets/terminal_bench.py` uses). A score on the eval side is
a score on about a third of `valid_unseen`, not on the benchmark; `HG_ALFWORLD_SPLIT=none`
runs every task as eval and accepts the driver's warning.

Noise, before anyone compares two ALFWorld runs
-----------------------------------------------
Measured on this machine with `qwen3.5-9b-local`, the same arm, the same 20 tasks, the
same seed and temperature 0, over five batches: **10 / 8 / 11 / 11 / 11 tasks, SD 1.30
tasks = 6.5 percentage points, 4 of 20 tasks changed side at least once.** A move smaller
than roughly 6 points on 20 tasks is the noise. The full note is in
`docs/experiment_noise.md`; quote the task count with every ALFWorld number, because the
granularity is `1/n`.
"""

from __future__ import annotations

from data.scaffolds.scorer_input import bind as bind_scorer_input

#: See `data/scaffolds/registry.py:KIND_DATASET`. A fixture is platform test material and is
#: deliberately not offered as a choice in the Launch form.
KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"


#: The benchmark's own evaluation set, within this adapter's pool: **every task here belongs
#: to it**. The pool is `valid_unseen`, which is exactly ALFWorld's own evaluation split (134 tasks). The benchmark's 3,553-task `train` set is not loaded, so this pool holds no study material.
#:
#: `None` is the accurate value only because nothing in the pool is study material -- it does
#: not claim the pool is the whole of the benchmark's evaluation set (see `ds1000`, which is a
#: measured subset). `--split official --side eval` is the comparable run: the whole pool, with
#: no study material in it. `--split declared` keeps this adapter's own division.
OFFICIAL_EVAL = None

import json
import os
import re
from pathlib import Path

#: Where the data lives. Outside the platform tree on purpose: dataset content is not
#: platform code, is never committed, and is what `tools/datasets.py` provisions.
#: `HG_ALFWORLD_DATA` overrides it so a reader can point at another copy.
DATASET_ROOT = Path(os.environ.get("HG_ALFWORLD_DATA",
                                   "/mnt/20t/xubuqiang/datasets/alfworld"))

#: The split this dataset is. `valid_seen` and `train` are on disk too; only the
#: out-of-distribution evaluation split is used, which is the one ALFWorld reports.
DATA_SUBDIR = ("json_2.1.1", "valid_unseen")

#: **Which directory this run reads.** The default is the benchmark's own OOD evaluation
#: split. `valid_seen` and `valid_train` are its other evaluation splits, and `train` is the
#: training split -- read here only as *study* material (see `_STUDY_FILE`).
_POOL = (os.environ.get("HG_ALFWORLD_POOL") or DATA_SUBDIR[-1]).strip()

#: **A study set drawn from `train`, named by a file of task ids.** When it is set the pool
#: spans two directories: those ids are the train side and the whole `_POOL` directory is the
#: eval side.
#:
#: Why this exists: the port's earlier design drew its study tasks from `valid_unseen` itself,
#: so search and report came from the same evaluation pool. That is defensible (the games are
#: disjoint) but it invites the objection that the method was tuned on the benchmark's test
#: split. Studying on the official training split removes the objection without changing the
#: cost of a round, and makes the number directly comparable with the benchmark's protocol.
#:
#: The ids are read from a file rather than sampled here: a draw made at import is a draw
#: nobody can reproduce, and `train100.csv` is a file a reader can diff.
_STUDY_FILE = (os.environ.get("HG_ALFWORLD_STUDY_IDS") or "").strip()

#: This module's own directory: the adapter is `data/datasets/alfworld.py`, its content is
#: `data/datasets/alfworld/`.
ASSETS = Path(__file__).resolve().parent / "alfworld"
CLI_DIR = ASSETS / "cli"
LIB_DIR = ASSETS / "lib"
VERIFY_DIR = ASSETS / "verify"

#: The platform's one tool transport, and the fixed place the sandbox publishes it:
#: `eval/containers/tool_route.py` mounts `/run/harnessgrad-tool/tool_transport.py` and
#: `tool.sock` read-only, and the agent's CLI imports the module from there and calls the
#: socket. It is deliberately **not** shipped by `SETUP`: a copy in the agent's workdir
#: would be a copy the agent could edit, and the published pair is the platform's, not
#: this dataset's. `TRANSPORT_SOURCE` is the file that gets published, named here so a
#: reader can see the CLI and `relay.py` share one implementation of the wire format.
TOOL_SOCKET = Path("/run/harnessgrad-tool/tool.sock")
TRANSPORT_SOURCE = ASSETS.parents[2] / "eval" / "service" / "tool_transport.py"

#: The step budget and the two filenames are **declared once**, in
#: `data/datasets/alfworld/lib/gym_env.py`, which the agent's CLI and the check both import.
#: Repeating the number here would be a second declaration that can drift from the one the
#: environment is built with -- and a drift there means an action is legal for the agent
#: and truncated for the check. Loaded by file path rather than by `import gym_env`,
#: because `lib/` is not on `sys.path` and putting it there from inside a dataset module
#: would leak a generic module name into the driver's interpreter.
def _load_gym_env():
    import importlib.util
    spec = importlib.util.spec_from_file_location("alfworld_gym_env",
                                                  LIB_DIR / "gym_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GYM_ENV = _load_gym_env()

#: Upstream ALFWorld's 50-step episode limit; see `gym_env.py` for the source.
STEP_BUDGET = _GYM_ENV.STEP_BUDGET
GAME_IN_WORKDIR = _GYM_ENV.GAME_FILENAME
LOG_IN_WORKDIR = _GYM_ENV.LOG_FILENAME

#: Where the check is told to put its verdict. The absolute paths are the container
#: contract; on a `files` environment the platform resolves them against the workdir
#: (`eval/verification/verifier.py::_verify`), and the check's `argv` passes the same location in the
#: relative spelling its own working directory understands.
REWARD_FILE = "/verifier/reward.txt"
EVIDENCE_FILE = "/verifier/evidence.json"
REWARD_REL = "verifier/reward.txt"
EVIDENCE_REL = "verifier/evidence.json"

#: ALFWorld's task-type vocabulary. A `traj_data.json` whose `task_type` is not in this
#: set is not a task ALFWorld evaluates, so it is not one this dataset returns.
TASK_TYPES = {
    1: "pick_and_place_simple",
    2: "look_at_obj_in_light",
    3: "pick_clean_then_place_in_recep",
    4: "pick_heat_then_place_in_recep",
    5: "pick_cool_then_place_in_recep",
    6: "pick_two_obj_and_place",
}

#: The templated goal lives in the game's TextWorld grammar, as the `rhs` of the `task`
#: rule. The grammar is TWL2 (JSON with `#` comments), so it cannot be `json.loads`-ed;
#: the rule is pulled out with a regex and unescaped as a JSON string. Verified on all
#: 134 valid_unseen games.
_GOAL_RE = re.compile(r'"task"\s*:\s*\[\s*\{\s*"rhs"\s*:\s*"((?:[^"\\]|\\.)*)"')

#: The one field of a game file that must never reach the agent. `walkthrough` is
#: ALFWorld's own reference solution for the game -- a list of the exact commands that
#: solve it -- and handing it over would hand over the answer. The initial state itself
#: (`pddl_problem`, `pddl_domain`, `grammar`) cannot be stripped either: TextWorld
#: refuses a game file missing any of the three, so there is no smaller buildable copy.
#: That is why the copy exists at all -- it is what the **relay** builds from, outside
#: the sandbox, not what the agent receives (see the module docstring, "What the agent
#: can read, and what that used to cost"). Every verifier input therefore carries
#: `_stripped_game()` rather than the file on disk, and the oracle control reads the
#: original -- `walkthrough` included -- from the data root.
_LEAKED_FIELD = "walkthrough"


def _split_dir(pool: str | None = None) -> Path:
    """The directory for `pool` (default: this run's `HG_ALFWORLD_POOL`)."""
    return DATASET_ROOT / DATA_SUBDIR[0] / (pool or _POOL)


def _study_ids() -> list[str]:
    """The train-side ids named by `HG_ALFWORLD_STUDY_IDS`, in file order."""
    if not _STUDY_FILE:
        return []
    text = Path(_STUDY_FILE).read_text(encoding="utf-8").strip()
    return [chunk.strip() for chunk in text.split(",") if chunk.strip()]


def _collect(roots: list[tuple[Path, set[str] | None]] | None = None) -> list[dict]:
    """Every runnable game in the split, filtered exactly as ALFWorld filters them.

    The filter is `AlfredTWEnv.collect_game_files`, reproduced rather than called:
    calling it would drag in `textworld` and crawl the whole data tree at import time,
    and the adapter must be importable on a machine that has not provisioned the data
    (see `published()` in `data/scaffolds/registry.py`: a module that will not import is hidden
    from the chooser). The rules, in the same order upstream applies them:

      * a trial directory must hold `traj_data.json` and `game.tw-pddl`;
      * its path must not contain `movable` or `Sliced` (unsupported trajectories);
      * `traj_data.json`'s `task_type` must be one ALFWorld evaluates;
      * `game.tw-pddl` must carry `solvable` and it must be true.

    Each returned record is everything the adapter needs and nothing it does not.
    """
    records: list[dict] = []
    game_paths: list[Path] = []
    for root, wanted in (roots or [(_split_dir(), None)]):
        if wanted is None:
            game_paths += sorted(Path(root).glob("*/*/game.tw-pddl"))
            continue
        # Named ids are opened directly instead of scanning 2,435 directories: reading
        # every game file under `train` costs seconds at import, and a study set is a
        # hundred of them.
        for task_id in sorted(wanted):
            task_dir, _, trial_dir = task_id.partition("__")
            game_paths.append(Path(root) / task_dir / trial_dir / "game.tw-pddl")
    for game_path in game_paths:
        trial_dir = game_path.parent
        task_dir = trial_dir.parent
        label = f"{task_dir.name}/{trial_dir.name}"
        if "movable" in str(trial_dir) or "Sliced" in str(trial_dir):
            continue
        traj_path = trial_dir / "traj_data.json"
        if not traj_path.is_file():
            continue
        try:
            traj_data = json.loads(traj_path.read_text())
            gamedata = json.loads(game_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"alfworld: {label} is unreadable: {exc}") from exc
        if traj_data.get("task_type") not in TASK_TYPES.values():
            continue
        if "solvable" not in gamedata:
            # The 0.4.2 asset is the only one with this key; an extraction that skipped
            # it lands here for every trial. Say that rather than "0 tasks".
            raise ValueError(
                f"alfworld: {label} has no `solvable` key -- the game files are not the "
                f"0.4.2 `json_2.1.3_tw-pddl.zip` ones. See downloads() and "
                f"tools/datasets.py --check")
        if not gamedata["solvable"]:
            continue
        match = _GOAL_RE.search(str(gamedata.get("grammar") or ""))
        if not match:
            raise ValueError(
                f"alfworld: {label}: the game grammar has no `task` rule, so the "
                f"goal text cannot be read")
        goal = json.loads('"' + match.group(1) + '"')
        records.append({
            # The official per-game label is the last two path components
            # (`evaluate_dagger` prints `"/".join(game_names[i].split("/")[-3:-1])`).
            # A slash is not safe in a platform task id (it becomes a container name
            # and a path component), so it is spelled `__` here.
            "task_id": f"{task_dir.name}__{trial_dir.name}",
            "label": label,
            "task_type": traj_data["task_type"],
            "goal": goal,
            "game_path": game_path,
            "stripped_game": _stripped_game(gamedata),
        })
    # **Deduplicated, because the same directory can be named twice.** The pool is a list of
    # (root, wanted) pairs and nothing stops two of them from resolving to one directory --
    # that is what a `train` study set over a `train` pool does, and it produced 200 train
    # ids for a 100-task file, i.e. every study task measured twice. A duplicate is not a
    # harmless repeat here: it is a second curve entry and a second charge for one task.
    seen: set[str] = set()
    unique: list[dict] = []
    for record in records:
        if record["task_id"] in seen:
            continue
        seen.add(record["task_id"])
        unique.append(record)
    return unique


def _stripped_game(gamedata: dict) -> str:
    """The game file without the reference solution, as JSON text.

    Only `_LEAKED_FIELD` is removed; everything else -- including the initial state --
    stays, because TextWorld cannot build the environment without it (see the module
    docstring, "What the agent can read, and what that used to cost").

    Kept as a string because that is the shape `VERIFY.inputs` takes for inline content,
    and because it means the stripped copy is built once at import instead of being
    written somewhere on disk that could drift from its source. **The agent never
    receives it.** Its two readers are the relay, which builds the environment from it
    outside the sandbox (`relay.game_content`, `relay.stage`), and the check, which
    replays the action log from it.
    """
    clean = {k: v for k, v in gamedata.items() if k != _LEAKED_FIELD}
    return json.dumps(clean)


def _goal(record: dict) -> str:
    """The instruction, with a preamble that names only the interface.

    Nothing here hints at the solution. It states the goal the environment will check,
    how to act, and the budget -- the three things the agent cannot discover from the
    interface alone. It names no file: the environment is not in the working directory.
    """
    return (
        f"# Task\n"
        f"\n"
        f"You are in a text-based house, described one room at a time, and you act by\n"
        f"typing commands. The environment is served by a process outside your working\n"
        f"directory, and these commands are the only way to reach it.\n"
        f"\n"
        f"**{record['goal']}**\n"
        f"\n"
        f"## How to act\n"
        f"\n"
        f"- Look at where you are and what you may do:\n"
        f"      python3 alf look\n"
        f"- Take one action (it must be one of the admissible actions `alf look`\n"
        f"  prints; quote it):\n"
        f"      python3 alf do \"go to desk 2\"\n"
        f"- When the task is finished, say so:\n"
        f"      python3 alf done\n"
        f"- Every action is appended to `{LOG_IN_WORKDIR}` and costs one of your\n"
        f"  **{STEP_BUDGET}** steps. A step that TextWorld does not understand still\n"
        f"  costs a step.\n"
        f"\n"
        f"## How it is scored\n"
        f"\n"
        f"- Your actions are replayed from the beginning in a fresh environment, and the\n"
        f"  task counts as solved only if the environment's own goal check reports the\n"
        f"  goal satisfied within the {STEP_BUDGET}-step budget. There is no judge and\n"
        f"  no partial credit.\n"
        f"- Nothing you print is read. Only the actions you take are.\n"
    )


def _setup(record: dict) -> dict:
    """What the agent receives before it starts.

    The CLI and its shared environment library, and **no game file**. That is the whole
    change: the game is not in the agent's working directory any more, because the
    environment that reads it lives in a host process (`relay.py`) reached over a socket.
    `gym_env.py` still travels because the CLI imports it (for the log, the budget and
    the action parser); the transport does **not** travel, because the platform publishes
    it read-only at `TOOL_SOCKET`'s directory and a copy in the workdir would be a copy
    the agent could edit.

    `VERIFY.inputs` keeps the game, and that copy is the one the runner stages for the
    relay: the same bytes, read by the check and by the relay, never by the agent.
    """
    return {
        "files": [
            {"path": "gym_env.py", "from": str(LIB_DIR / "gym_env.py")},
            {"path": "alf", "from": str(CLI_DIR / "alf")},
        ],
        # The environment itself. Deliberately not under "files": `eval/run/tool_relay.py`
        # stages these bytes in a directory the platform owns, starts `relay.py` on them, and
        # mounts only the resulting socket into the sandbox. `record["stripped_game"]` is the
        # same string `VERIFY.inputs` carries, so the relay and the check read identical bytes.
        "tool_relay": {
            "program": str(ASSETS / "relay.py"),
            "filename": GAME_IN_WORKDIR,
            "content": record["stripped_game"],
            "budget": STEP_BUDGET,
        },
    }


def _verifier(record: dict) -> dict:
    """The grade: the check, its trusted game, and where the verdict goes.

    `inputs` use `path` (relative to the workdir), not `dst`: on a `files` environment
    the platform materialises these into the workdir, and `dst` is a container path.
    The check, the game and the environment library are re-materialised at verification
    time, so nothing the agent wrote to any of them is what runs.

    `reward_file`/`evidence_file` are absolute in-environment paths -- the container
    contract. On a `files` environment `eval/verification/verifier.py` resolves them against the
    workdir, and the `argv` flags below write to that same resolved location. Note the
    **top-level-in-the-container** rule from `data/datasets/tau_bench.py:_verifier` does not
    apply here (there is no `docker cp`), but the paths are kept absolute anyway so the
    adapter is a valid container dataset if one is ever built.
    """
    spec = {
        "kind": "command",
        "argv": [
            "python3", "check.py",
            "--workdir", ".",
            "--game", GAME_IN_WORKDIR,
            "--log", LOG_IN_WORKDIR,
            "--reward-file", REWARD_REL,
            "--evidence-file", EVIDENCE_REL,
            "--budget", str(STEP_BUDGET),
        ],
        "inputs": [
            {"path": "check.py", "from": str(VERIFY_DIR / "check.py")},
            {"path": "gym_env.py", "from": str(LIB_DIR / "gym_env.py")},
            {"path": GAME_IN_WORKDIR, "content": record["stripped_game"]},
        ],
        "reward_file": REWARD_FILE,
        "evidence_file": EVIDENCE_FILE,
        "timeout_s": 300,
    }
    from data.runtimes import NATIVE_PINS
    spec["native_runtime"] = NATIVE_PINS["alfworld"].copy()
    return bind_scorer_input(spec, record["task_id"], [GAME_IN_WORKDIR])


def downloads() -> list[dict]:
    """Everything this dataset needs, with the traps written down beside it.

    Read by `tools/datasets.py`; the generated `DOWNLOADS.md` is rendered from this
    function, never from a hand-written table, so the two cannot disagree. Version
    fields are pinned: a release tag, a commit sha or an exact package version, never a
    branch name.

    The three `http` assets are the ones ALFWorld's own `scripts/alfworld-download`
    fetches; the release tags and URLs below are copied from that script, not guessed.
    The `sha256` of each is the copy this adapter was validated against, so
    `tools/datasets.py --check` can tell a re-download that changed bytes.
    """
    return [
        {
            "kind": "http",
            "name": "json_2.1.1_json.zip",
            "version": "0.2.2",
            "url": "https://github.com/alfworld/alfworld/releases/download/"
                   "0.2.2/json_2.1.1_json.zip",
            "size": "~72 MB",
            "sha256": "25171f16e20ad7b048c47275c45b0babf3aa1cbab29cec97387922350a9844bc",
            "dst": "_src/json_2.1.1_json.zip",
            "extract_to": ".",
            "note": "Trap 1: this is a GitHub *release asset*, not in the repository and "
                    "not on Hugging Face. It holds only `traj_data.json` (7302 files); "
                    "on its own it cannot make a task.",
        },
        {
            "kind": "http",
            "name": "json_2.1.1_pddl.zip",
            "version": "0.2.2",
            "url": "https://github.com/alfworld/alfworld/releases/download/"
                   "0.2.2/json_2.1.1_pddl.zip",
            "size": "~35 MB",
            "sha256": "913942ebed06659ea0da2f8122512d98bc6add30d84961ca803132d8fbcad585",
            "dst": "_src/json_2.1.1_pddl.zip",
            "extract_to": ".",
            "note": "Holds only `initial_state.pddl` (7080 files). Measured: the loading "
                    "path does not read it (the 0.4.2 game file embeds `pddl_problem`), "
                    "but ALFWorld's generation path does, and `alfworld-download` "
                    "fetches it, so it is provisioned.",
        },
        {
            "kind": "http",
            "name": "json_2.1.3_tw-pddl.zip",
            "version": "0.4.2",
            "url": "https://github.com/alfworld/alfworld/releases/download/"
                   "0.4.2/json_2.1.3_tw-pddl.zip",
            "size": "~35 MB",
            "sha256": "5df77ea759f2211a4106082839ddbbb790f1ba4e7d097ed732cf453f72aa36cf",
            "dst": "_src/json_2.1.3_tw-pddl.zip",
            "extract_to": ".",
            "note": "Trap 2: the ONLY archive with `game.tw-pddl`, and therefore the "
                    "only one with the `solvable` key (and the `walkthrough` we strip). "
                    "A version other than 0.4.2 has no `solvable` and every task is "
                    "filtered out.",
        },
        {
            "kind": "http",
            "name": "alfworld repo tarball",
            "version": "aaba6870f86c5be6a08a491f32a50b906227bc3e",
            "url": "https://github.com/alfworld/alfworld/archive/"
                   "aaba6870f86c5be6a08a491f32a50b906227bc3e.tar.gz",
            "size": "~3.8 MB",
            "sha256": "5592fbb36124b08d24167c5f7612a55a2cc610e0c39170f638a69b628835ee3b",
            "dst": "_src/repo.tar.gz",
            "extract_to": "_src",
            "note": "Trap 3: the PDDL domain and the TextWorld grammar live here, at "
                    "`alfworld/data/alfred.pddl` and `alfworld/data/alfred.twl2` -- not "
                    "in any data pack. `alfworld-download` copies them into "
                    "`$ALFWORLD_DATA/logic/`. Pinned by commit sha, not by `main`.",
        },
        {
            "kind": "pip",
            "name": "alfworld",
            "package": "alfworld",
            "version": "0.4.2",
            "size": "~2 MB",
            "note": "The loader (`AlfredTWEnv`), the CLI's environment wrappers and the "
                    "packaged copy of the logic files. `pip install alfworld==0.4.2`.",
        },
        {
            "kind": "pip",
            "name": "textworld",
            "package": "textworld",
            "version": "1.7.0",
            "size": "~40 MB",
            "note": "The TextWorld engine that owns the `won` flag this dataset's reward "
                    "is. `pip install textworld==1.7.0`. 1.7.0 removed Glulx support; "
                    "ALFWorld's `.tw-pddl` games do not use it.",
        },
    ]


def provision_status() -> dict:
    """What is present locally, for `tools/datasets.py --check`.

    Deliberately does not import `textworld` or build an environment: this is the
    answer to "did the provisioning run", which has to work on a machine where it did
    not.
    """
    split = _split_dir()
    games = sorted(split.glob("*/*/game.tw-pddl")) if split.is_dir() else []
    solvable = 0
    for game in games:
        try:
            if "solvable" in json.loads(game.read_text()):
                solvable += 1
        except (OSError, ValueError):
            continue
    logic = DATASET_ROOT / "logic"
    return {
        "root": str(DATASET_ROOT),
        "present": bool(games),
        "split_dir": str(split),
        "game_files": len(games),
        "with_solvable": solvable,
        "logic_files": sorted(p.name for p in logic.glob("*")) if logic.is_dir() else [],
        "tasks": len(_RECORDS),
    }


#: Read once at import. `load()` is called more than once per run and the content is
#: immutable. A missing data root is **not** an import failure: `published()` catches
#: exceptions and hides a module that will not import, and a dataset that cannot be
#: seen in the chooser before its data is provisioned is a dataset a reader cannot
#: find the instructions for. `_LOAD_ERROR` carries the reason into `load()`.
#: The train-side ids this run studies on (empty unless `HG_ALFWORLD_STUDY_IDS` is set).
_STUDY: list[str] = []
try:
    _STUDY = _study_ids()
    if _STUDY:
        if _POOL == "train":
            raise ValueError(
                "HG_ALFWORLD_POOL=train would measure the split the study set is drawn "
                "from; the eval pool is one of valid_seen / valid_train / valid_unseen")
        _RECORDS: list[dict] = _collect([(_split_dir("train"), set(_STUDY)),
                                         (_split_dir(), None)])
        _found = {r["task_id"] for r in _RECORDS}
        _missing = [t for t in _STUDY if t not in _found]
        if _missing:
            # Loudly, because a study set that silently lost its tasks changes what the
            # method was shown without changing anything a reader can see.
            raise ValueError(
                f"{len(_missing)} of {len(_STUDY)} study ids in {_STUDY_FILE} are not "
                f"runnable games under {_split_dir('train')} "
                f"(first: {_missing[:3]})")
    else:
        _RECORDS = _collect()
    _LOAD_ERROR: str | None = None
except Exception as _exc:  # noqa: BLE001 - reported by load(), which is where it bites
    _RECORDS = []
    _LOAD_ERROR = f"{type(_exc).__name__}: {_exc}"


def load() -> tuple[list[dict], dict[str, str]]:
    """`(tasks, scorable)`. Every task is graded by its `VERIFY` entry, so the
    `scorable` map is present only because the interface asks for it."""
    if _LOAD_ERROR is not None:
        raise ValueError(f"alfworld: cannot read {_split_dir()} ({_LOAD_ERROR}). "
                         f"Provision it with `python3 tools/datasets.py --list`.")
    if not _RECORDS:
        raise ValueError(
            f"alfworld: no runnable game under {_split_dir()}. The usual cause is the "
            f"0.4.2 `json_2.1.3_tw-pddl.zip` not being extracted, which leaves every "
            f"trial without `game.tw-pddl`. See downloads() and "
            f"`python3 tools/datasets.py --check`.")
    tasks = [{"task_id": r["task_id"], "goal": _goal(r), "inputs": []}
             for r in _RECORDS]
    return tasks, {r["task_id"]: "" for r in _RECORDS}


#: One environment for every task: the host, because the hard constraint of this
#: exercise is that nothing here needs Docker. `files` cannot carry resource or timeout
#: fields (`data/scaffolds/registry.py::_check_env` refuses them), so the platform's defaults
#: apply: 2 cpus, 4 GB, 300 s for the harness and 300 s for the check. Runtime at the
#: upstream 50-step cap must be assessed separately from these resource defaults.
DEFAULT_ENV: dict = {"kind": "files"}

ENV: dict[str, dict] = {r["task_id"]: {"kind": "files"} for r in _RECORDS}
SETUP: dict[str, dict] = {r["task_id"]: _setup(r) for r in _RECORDS}
VERIFY: dict[str, dict] = {r["task_id"]: _verifier(r) for r in _RECORDS}

if _STUDY:
    # **The declared sides, without a hash.** The train side is exactly the study file and
    # the eval side is the rest of the pool, so the two cannot drift: they are the same
    # partition seen twice. `_is_eval` is not consulted -- it exists to cut *one* pool in
    # two, and this pool is already two directories.
    _ids = [r["task_id"] for r in _RECORDS]
    _study_set = set(_STUDY)
    SPLIT = {"train": [t for t in _ids if t in _study_set],
             "eval": [t for t in _ids if t not in _study_set]}
    SPLIT_SOURCE = "declared"
else:
    # **No division.** The default pool is the official `valid_unseen` games, which is the
    # benchmark's evaluation material: with no study file there is nothing to declare
    # (2026-10-24). `HG_ALFWORLD_SPLIT=none` selected exactly this and is gone; a study file
    # (`HG_ALFWORLD_STUDY_IDS`) states the two sides, and `--split default` gives a run the
    # platform's hash rule.
    SPLIT = None
    SPLIT_SOURCE = "none"
