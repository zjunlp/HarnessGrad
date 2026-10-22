"""The ALFWorld relay: what the agent's environment is given, and that nothing changed.

The defect this file is about
-----------------------------
`SETUP` used to hand the agent `game.tw-pddl` -- the game minus the reference solution,
which is the whole PDDL initial state, the object -> receptacle -> room map included --
because the agent's CLI built the TextWorld environment itself. It does not any more:
the environment runs in a host process (`data/datasets/alfworld/relay.py`) that the
agent's sandbox cannot read, and `cli/alf` is a client of it over the platform's one
transport. So there are two things to prove, and they are the two halves of this file:

* **nothing leaked** -- the relay was given the same bytes the workdir used to get, and
  no reply can carry the PDDL; and
* **nothing changed** -- a real multi-step episode produces the same stdout, stderr,
  exit code and action log, byte for byte, as the build-and-replay path it replaced.

The second one is the test that matters. It does not compare against a re-implementation
of the old code: it runs the **pre-change CLI** out of git (`ccdd5e9e:.../cli/alf`,
pinned by sha256 so it cannot silently become some other revision) against the
pre-change workdir, next to the new client acting through a live relay. Behaviour
identity outranks elegance here: if a command shape cannot be reproduced, this file is
where it fails rather than in a pilot.

The published path is the contract, not a setting: inside a task the CLI imports the
transport from `/run/harnessgrad-tool` and calls `/run/harnessgrad-tool/tool.sock`
(`eval/containers/tool_route.py`). There is no way to mount that on this host, so the
host-side tests build a directory laid out the same way -- `tool_transport.py` and
`tool.sock`, nothing else -- and point the CLI at it with `HG_ALF_TOOL_DIR`, which exists
for tests and the local control only.

What is deliberately not tested here: the sandbox mount itself, the runner that will
start the relay and pass `tool_socket=` (`eval/containers/`, a separate task), and the
runner's mapping of a relay fault to an unmeasured task. Neither is wired, and faking
either would be worse than saying so.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

pytest.importorskip("textworld", reason="ALFWorld needs textworld")
pytest.importorskip("alfworld", reason="ALFWorld needs the alfworld package")

#: The commit this worktree is based on. The pre-change CLI is read from it, and its
#: sha256 is pinned: a different revision would be a different program, and the identity
#: proof would stop meaning what it says.
BASE_COMMIT = "ccdd5e9e"
LEGACY_CLI = f"{BASE_COMMIT}:data/datasets/alfworld/cli/alf"
LEGACY_CLI_SHA256 = "b8ccdcc88acb2322beccb01edaf69bbd07d23961788ddca3fca22578f6561749"

#: What a reply may never contain: the PDDL problem's predicates, its key, the key of the
#: domain, and the goal expression. `obs` is TextWorld's own text and legitimately opens
#: with "Your task is to: ..." -- that is the benchmark showing the player the task, and
#: the prompt states it too. `:init` facts, object ids and the goal *expression* are not.
FORBIDDEN = ("inReceptacle", "objectAtLocation", "pddl_problem", ":objects",
             "pddl_domain", "(:goal")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def alfworld():
    import data.datasets.alfworld as module
    if not module._RECORDS:
        pytest.skip(f"no extracted ALFWorld data under {module.DATASET_ROOT}")
    return module


@pytest.fixture(scope="module")
def relay():
    return _load("alfworld_relay_under_test",
                 ROOT / "data/datasets/alfworld" / "relay.py")


def _walkthrough(record: dict) -> list[str]:
    """ALFWorld's own reference solution, read from the *unstripped* game on disk."""
    return list(json.loads(Path(record["game_path"]).read_text())["walkthrough"])


def _record_for(alfworld, task_type: str) -> dict:
    """The longest walkthrough of a task type. Deterministic, and it exercises the most
    steps: a longer episode is where a divergence would hide."""
    wanted = [r for r in alfworld._RECORDS if r["task_type"] == task_type]
    assert wanted, f"no task of type {task_type}"
    return max(wanted, key=lambda r: (len(_walkthrough(r)), r["task_id"]))


def _shipped(alfworld, task_id: str, dest: Path) -> None:
    """Materialise exactly what `SETUP` ships, the way the platform does."""
    dest.mkdir(parents=True, exist_ok=True)
    for entry in alfworld.SETUP[task_id]["files"]:
        target = dest / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"], encoding="utf-8")
        else:
            shutil.copy2(entry["from"], target)
    (dest / "alf").chmod(0o755)


def _serve(relay, alfworld, record: dict, root: Path, budget: int):
    """A live relay over the *trusted* bytes, and the pair a sandbox would publish.

    Two directories, because on a real run they are two places: `host/` is where the
    runner stages the game (outside the sandbox) and `tool/` is what
    `eval/containers/tool_route.py` publishes read-only at `/run/harnessgrad-tool` --
    exactly `tool_transport.py` and the socket. `HG_ALF_TOOL_DIR` is the host-side
    stand-in for that mount.
    """
    host = root / "host"
    host.mkdir(parents=True, exist_ok=True)
    game = relay.stage(relay.game_content(alfworld.VERIFY[record["task_id"]]),
                       host / alfworld.GAME_IN_WORKDIR)
    published = root / "tool"
    published.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "eval/service/tool_transport.py",
                 published / "tool_transport.py")
    socket_path = published / "tool.sock"
    return relay.serve(game, socket_path, budget=budget), published, socket_path


# -- the bytes ---------------------------------------------------------------
def test_the_relay_is_given_the_bytes_the_workdir_used_to_get(alfworld, relay, tmp_path):
    """All 134 games: sha256(relay's game) == sha256(what `SETUP` wrote into the workdir).

    The old `SETUP` entry was `{"path": "game.tw-pddl", "content": record["stripped_game"]}`,
    and the platform writes `content` verbatim, so "the file the workdir got" is exactly
    that string. It is rebuilt here independently -- the game on disk minus `walkthrough`
    -- so the comparison is not the adapter's `_stripped_game` checked against itself.
    """
    checked = 0
    for record in alfworld._RECORDS:
        game = json.loads(Path(record["game_path"]).read_text())
        workdir_copy = json.dumps({k: v for k, v in game.items() if k != "walkthrough"})
        assert record["stripped_game"] == workdir_copy, record["task_id"]

        old = tmp_path / "old" / f"{record['task_id']}.tw-pddl"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(workdir_copy, encoding="utf-8")
        staged = relay.stage(relay.game_content(alfworld.VERIFY[record["task_id"]]),
                             tmp_path / "host" / alfworld.GAME_IN_WORKDIR)
        assert (hashlib.sha256(staged.read_bytes()).hexdigest()
                == hashlib.sha256(old.read_bytes()).hexdigest()), record["task_id"]
        checked += 1
    assert checked == len(alfworld._RECORDS) == 134


def test_setup_ships_no_game_and_verify_still_ships_one(alfworld):
    """The hole is in `SETUP`; the grade still builds from its own copy of the game.

    `VERIFY` is untouched on purpose: the check replays from `VERIFY.inputs`, so the file
    the relay is given and the file the check grades are the same bytes and neither is
    the agent's.
    """
    for record in alfworld._RECORDS[:5]:
        assert record["task_id"] in alfworld.SETUP
        paths = [entry["path"] for entry in alfworld.SETUP[record["task_id"]]["files"]]
        assert alfworld.GAME_IN_WORKDIR not in paths, paths
        assert set(paths) == {"gym_env.py", "alf"}, paths
        # The transport is published by the sandbox, read-only, not shipped here: a copy
        # in the workdir would be a copy the agent could edit.
        assert alfworld.TOOL_SOCKET == Path("/run/harnessgrad-tool/tool.sock")
        verifier = alfworld.VERIFY[record["task_id"]]
        game_entries = [entry for entry in verifier["inputs"]
                        if entry["path"] == alfworld.GAME_IN_WORKDIR]
        assert game_entries and game_entries[0]["content"] == record["stripped_game"]
        assert alfworld.GAME_IN_WORKDIR in verifier["argv"]


# -- byte identity with the path this replaced -------------------------------
def _legacy_program(tmp_path: Path) -> str:
    proc = subprocess.run(["git", "-C", str(ROOT), "show", LEGACY_CLI],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip(f"{LEGACY_CLI} is not readable from git: {proc.stderr.strip()}")
    text = proc.stdout
    assert hashlib.sha256(text.encode()).hexdigest() == LEGACY_CLI_SHA256, (
        f"the CLI at {BASE_COMMIT} is not the program this file was written against; "
        f"the identity proof would compare against something else")
    return text


def _pre_change_workdir(alfworld, record: dict, legacy: str, dest: Path) -> None:
    """The workdir `SETUP` used to build: the stripped game, `gym_env.py`, the old CLI."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / alfworld.GAME_IN_WORKDIR).write_text(record["stripped_game"], encoding="utf-8")
    shutil.copy2(alfworld.LIB_DIR / "gym_env.py", dest / "gym_env.py")
    (dest / "alf").write_text(legacy, encoding="utf-8")
    (dest / "alf").chmod(0o755)


def _episode(record: dict, budget: int) -> list[tuple]:
    """A scripted episode with every command shape a divergence could hide in.

    A no-op command, ALFWorld's own reference solution (so the episode wins), a command
    *after* the win (which the environment refuses and the CLI must refuse without
    logging), a `look` (which must print the last applied step's observation, not a fresh
    look at the room), and `done`.
    """
    return ([("do", "inventory")]
            + [("do", action) for action in _walkthrough(record)[:budget]]
            + [("do", "look around"), ("look",), ("done",)])


@pytest.mark.parametrize("task_type,budget", [
    ("look_at_obj_in_light", 50),
    ("pick_two_obj_and_place", 50),
    ("pick_clean_then_place_in_recep", 6),
], ids=["look_at", "pick_two", "capped"])
def test_the_relay_backed_cli_is_byte_identical_to_build_and_replay(
        alfworld, relay, tmp_path, task_type, budget):
    """The old program and the new client, command by command, byte for byte.

    Exit code, stdout and stderr are all compared -- stdout is what the requirement names,
    and stderr is where a refusal says why. The action log is compared at the end because
    it, not the transcript, is what the check replays.

    The `capped` case runs a seven-command walkthrough through a six-step budget, which is
    where the two paths could most easily disagree: TextWorld ends the episode at the cap,
    the CLI prints "episode over" and then refuses the next action. The relay is built for
    *the same* budget and refuses a call that claims a different one, so this is also the
    test that the two budgets are one number.
    """
    record = _record_for(alfworld, task_type)
    legacy = _legacy_program(tmp_path)
    old = tmp_path / "old"
    new = tmp_path / "new"
    _pre_change_workdir(alfworld, record, legacy, old)
    _shipped(alfworld, record["task_id"], new)
    assert (old / alfworld.GAME_IN_WORKDIR).is_file()
    assert not (new / alfworld.GAME_IN_WORKDIR).exists(), \
        "the defect is not closed: the agent's workdir still gets the game file"

    server, published, socket_path = _serve(relay, alfworld, record, tmp_path, budget)
    env = dict(os.environ, HG_ALF_TOOL_DIR=str(published))
    transcript = []
    with server as running:
        for command in _episode(record, budget):
            argv = [sys.executable, "alf", "--budget", str(budget), *command]
            before = subprocess.run(argv, cwd=old, capture_output=True, text=True,
                                    errors="replace")
            after = subprocess.run(argv, cwd=new, env=env, capture_output=True, text=True,
                                   errors="replace")
            try:
                assert before.returncode == after.returncode
                assert before.stdout == after.stdout
                assert before.stderr == after.stderr
            except AssertionError:
                raise AssertionError(
                    f"{record['task_id']} budget {budget}: `alf {' '.join(command)}` "
                    f"diverged\n"
                    f"exit {before.returncode} -> {after.returncode}\n"
                    f"--- old stdout ---\n{before.stdout}\n"
                    f"--- new stdout ---\n{after.stdout}\n"
                    f"--- old stderr ---\n{before.stderr}\n"
                    f"--- new stderr ---\n{after.stderr}\n") from None
            transcript.append((command, after.returncode, after.stdout))
    assert running.receipt["errors"] == 0, running.receipt
    assert running.receipt["refused"] == 0, running.receipt

    # The log is the state the check grades, so it has to be the same log.
    old_log = old / alfworld.LOG_IN_WORKDIR
    new_log = new / alfworld.LOG_IN_WORKDIR
    assert old_log.is_file() and new_log.is_file()
    assert new_log.read_bytes() == old_log.read_bytes()
    # And it is a real episode, not two empty transcripts agreeing.
    actions = [json.loads(line) for line in new_log.read_text().splitlines()]
    applied = min(1 + min(len(_walkthrough(record)), budget), budget)
    assert len([r for r in actions if r["kind"] == "action"]) == applied, actions
    assert transcript[-2][0] == ("look",)
    assert "[alf] step" in transcript[-2][2]


# -- nothing in a reply can be the PDDL --------------------------------------
def test_no_reply_carries_the_initial_state(alfworld, relay, tmp_path):
    """Every reply of an episode, checked against the PDDL it was built from.

    The proof is structural as well as empirical. Structurally, a reply is
    `Episode.current()`: five keys, filled from `gym_env.step`'s observation, its
    admissible list, its `won`/`done` flags and a counter -- the game text goes into
    `gym_env.build` and is never read again, so there is no key for a predicate to sit
    in. Empirically, every reply this episode produced is scanned for the four names the
    requirement names (plus the domain key and the goal expression), and the game it was
    built from is checked to actually contain them -- otherwise the scan proves nothing.
    """
    record = _record_for(alfworld, "pick_two_obj_and_place")
    game = json.loads(record["stripped_game"])
    # The game the relay built from really does carry every token the scan looks for --
    # otherwise the scan below proves nothing. Measured: `:objects` is in the *problem*
    # (the object list), not in the domain.
    for key, token in (("pddl_problem", "inReceptacle"),
                       ("pddl_problem", "objectAtLocation"),
                       ("pddl_problem", ":objects"),
                       ("pddl_domain", "receptacleAtLocation"),
                       ("grammar", "inReceptacle")):
        assert token in game[key], f"{token} is not in {key}; the scan would be vacuous"

    assert tuple(relay.REPLY_KEYS) == ("obs", "admissible", "won", "done", "step")
    server, _published, socket_path = _serve(relay, alfworld, record, tmp_path, 50)
    replies = []
    with server:
        from eval.service.tool_transport import call

        def look(step):
            return call(socket_path, "look", {"step": step, "budget": 50})

        def step(index, action):
            return call(socket_path, "do", {"step": index, "budget": 50,
                                            "action": action})

        replies.append(look(0))
        # An unknown command is a *successful* step: TextWorld's own refusal text, a
        # counted step, and no traceback. Asked first, while the episode is still live.
        first = step(0, "wibble the wibble")
        assert first["step"] == 1 and first["done"] is False
        assert relay.REFUSAL_TEXT in first["obs"], first["obs"][:200]
        replies.append(first)
        applied = 1
        for action in _walkthrough(record):
            replies.append(step(applied, action))
            applied += 1
            # `look` must not consume a step: the same five fields, the same step.
            replies.append(look(applied))
    assert applied == len(_walkthrough(record)) + 1
    for reply in replies:
        assert set(reply) == set(relay.REPLY_KEYS)
        assert isinstance(reply["obs"], str) and isinstance(reply["step"], int)
        assert isinstance(reply["admissible"], list)
        assert isinstance(reply["won"], bool) and isinstance(reply["done"], bool)
        blob = json.dumps(reply)
        for token in FORBIDDEN:
            assert token not in blob, f"a reply carried {token!r}: {blob[:400]}"


def test_the_environment_refuses_what_it_cannot_check_and_never_invents_a_reply(
        alfworld, relay, tmp_path):
    """The guards: a wrong step, a wrong budget, an unknown op, an unknown command.

    Each of the first three is a **failed call**, not a plausible empty answer -- that is
    the transport's rule, and a wrong step is the one that matters: the live episode and
    the log the check replays have parted company, and answering anyway would show the
    agent one episode while grading another. The fourth is the opposite case: a command
    TextWorld does not understand is a legal, budgeted step and must not be a fault.
    """
    from eval.service.tool_transport import ToolTransportError, call

    record = _record_for(alfworld, "look_at_obj_in_light")
    server, _published, socket_path = _serve(relay, alfworld, record, tmp_path, 50)
    with server:
        first = call(socket_path, "look", {"step": 0, "budget": 50})
        assert first["step"] == 0
        assert call(socket_path, "look", {"step": 0, "budget": 50})["obs"] == first["obs"]
        assert call(socket_path, "look", {"step": 0, "budget": 50})["step"] == 0

        for op, args, why in (
            ("do", {"step": 3, "budget": 50, "action": "look"}, "step"),
            ("do", {"step": 0, "budget": 7, "action": "look"}, "budget"),
            ("look", {"step": 0, "budget": 7}, "budget"),
            ("teleport", {"step": 0, "budget": 50}, "op"),
        ):
            with pytest.raises(ToolTransportError) as caught:
                call(socket_path, op, args)
            assert why in str(caught.value), (op, args, str(caught.value))

        second = call(socket_path, "do", {"step": 0, "budget": 50,
                                          "action": "wibble the wibble"})
        assert second["step"] == 1
        assert second["done"] is False and second["won"] is False
        assert relay.REFUSAL_TEXT in second["obs"], second["obs"][:200]
        assert second["obs"].strip() == "Nothing happens."


# -- the client without an environment ---------------------------------------
def test_alf_without_a_relay_exits_nonzero_and_writes_no_action_log(alfworld, tmp_path):
    """No socket is a platform fault, not an episode: say so and log nothing.

    Two shapes, both of them real: the sandbox was never given a relay (nothing is
    published at `/run/harnessgrad-tool`, so a real task lands here), and a published
    directory that exists without the socket in it (the relay died). Both, for all three
    commands including `done`: a `done` marker written over a missing environment is a
    log that replays as a measured zero, which is the one outcome this dataset must never
    produce from a platform failure.
    """
    record = alfworld._RECORDS[0]
    work = tmp_path / "task"
    _shipped(alfworld, record["task_id"], work)
    absent = {k: v for k, v in os.environ.items() if k != "HG_ALF_TOOL_DIR"}
    empty = tmp_path / "empty-tool"; empty.mkdir()

    cases = [("unpublished", absent, str(alfworld.TOOL_SOCKET)),
             ("no-socket", dict(absent, HG_ALF_TOOL_DIR=str(empty)),
              str(empty / "tool.sock"))]
    for label, env, expected in cases:
        for command in (("look",), ("do", "go to desk 1"), ("done",)):
            proc = subprocess.run([sys.executable, "alf", *command], cwd=work, env=env,
                                  capture_output=True, text=True, errors="replace")
            assert proc.returncode != 0, (label, command, proc.stdout)
            assert proc.stdout == "", (label, command, proc.stdout)
            assert expected in proc.stderr, (label, command, proc.stderr)
            assert not (work / alfworld.LOG_IN_WORKDIR).exists(), (label, command)


def test_the_relay_program_takes_the_documented_flags(relay):
    """The runnable server, without starting one: the interface the runner will use."""
    args = relay.parse_args(["--game", "/games/g.tw-pddl", "--socket", "/run/alf.sock"])
    assert args.budget == 50 and args.deadline_s is None
    assert args.max_calls >= 64 and args.receipt is None
    args = relay.parse_args(["--game", "g", "--socket", "s", "--deadline-s", "90",
                             "--receipt", "/r/receipt.json"])
    assert args.deadline_s == 90.0 and args.receipt == "/r/receipt.json"
    with pytest.raises(SystemExit):
        relay.parse_args(["--game", "g"])
