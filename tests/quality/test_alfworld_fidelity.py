"""Synthetic ALFWorld protocol controls; no dataset, TextWorld or model is run."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "data/datasets/alfworld"


def _module(name, path):
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


@pytest.fixture
def protocol(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    gym = _module("alfworld_fidelity_gym", ASSETS / "lib/gym_env.py")
    monkeypatch.setitem(sys.modules, "gym_env", gym)
    check = _module("alfworld_fidelity_check", ASSETS / "verify/check.py")
    cli = _module("alfworld_fidelity_cli", ASSETS / "cli/alf")
    return gym, check, cli


def test_upstream_budget_defaults_agree(protocol):
    gym, check, cli = protocol
    assert gym.STEP_BUDGET == 50
    assert gym.build.__defaults__ == gym.replay.__defaults__ == (50,)
    assert check.parse_args([]).budget == cli._parse_global([])[0].budget == 50
    tree = ast.parse((ROOT / "data/datasets/alfworld.py").read_text())
    declaration = next(n for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "STEP_BUDGET"
                               for t in n.targets))
    assert ast.unparse(declaration.value) == "_GYM_ENV.STEP_BUDGET"


@pytest.mark.parametrize("winning_step", [31, 50, 51])
def test_replay_can_win_after_thirty_but_stops_at_fifty(protocol, monkeypatch,
                                                       winning_step):
    gym, _, _ = protocol
    calls, closed = [], []
    env = SimpleNamespace(close=lambda: closed.append(True))

    def build(game, budget):
        assert game == "synthetic-game" and budget == 50
        return env

    def step(actual_env, action):
        assert actual_env is env
        calls.append(action)
        return {"won": len(calls) == winning_step,
                "done": len(calls) >= min(winning_step, 50),
                "observation": "synthetic", "admissible": []}

    monkeypatch.setattr(gym, "build", build)
    monkeypatch.setattr(gym, "reset", lambda _: {
        "won": False, "observation": "synthetic", "admissible": []})
    monkeypatch.setattr(gym, "step", step)
    state = gym.replay("synthetic-game", ["look"] * 60)
    assert len(state["records"]) == min(winning_step, 50)
    assert state["won"] is (winning_step <= 50)
    assert closed == [True]


@pytest.mark.parametrize("logged,replayed,won,done", [
    (0, 0, False, False),
    (49, 49, False, False),
    (50, 50, False, True),  # TextWorld can signal done when the cap is reached.
    (51, 50, False, True),
    (50, 50, False, False),
    (50, 50, True, True),   # Winning on the last step is not exhaustion.
    (51, 50, True, True),
    (51, 3, False, True),   # Early non-winning termination is not budget exhaustion.
    (50, 3, True, True),
])
def test_budget_evidence_distinguishes_cap_from_win(protocol, monkeypatch, tmp_path,
                                                   logged, replayed, won, done):
    gym, check, _ = protocol
    game, log = tmp_path / "game", tmp_path / "actions.jsonl"
    game.write_text("synthetic game; never loaded")
    log.write_text("".join(json.dumps({"kind": "action", "action": "look"}) + "\n"
                           for _ in range(logged)))
    records = [{"action": "look", "observation": "synthetic",
                "won": won and i == replayed - 1,
                "done": done and i == replayed - 1}
               for i in range(replayed)]

    def replay(actual_game, actions, budget):
        assert actual_game == str(game) and len(actions) == logged and budget == 50
        return {"records": records, "won": won}

    monkeypatch.setattr(gym, "replay", replay)
    args = check.parse_args(["--game", str(game), "--log", str(log), "--budget", "50",
                             "--reward-file", str(tmp_path / "reward"),
                             "--evidence-file", str(tmp_path / "evidence")])
    assert check._score(args, {str(game): game}, {"synthetic": True}) == (0 if won else 1)
    evidence = json.loads(args.evidence_file.read_text())
    reached = replayed == 50
    assert evidence["budget_exhausted"] is (reached and not won)
    assert evidence["budget_reached"] is reached
    assert evidence["episode_done"] is done
    assert evidence["actions_not_replayed"] == logged - replayed
    assert evidence["reward"] == evidence["official_game_points"] == int(won)
    assert args.reward_file.read_text() == f"{int(won)}\n"
    if reached and not won:
        assert "50-step budget" in evidence["why"]


def test_cli_allows_fiftieth_action_and_refuses_fifty_first(protocol, monkeypatch,
                                                          tmp_path):
    """The budget boundary, through the relay the CLI now acts against.

    Two counts have to agree for an episode to be honest: the log's (which the CLI
    enforces) and the environment's (which the relay holds). Every call carries the step
    it is acting from and the relay refuses a mismatch, so this also pins that the
    fiftieth action is applied *and* logged and the fifty-first is refused before the
    environment is asked anything. The environment is synthetic -- no TextWorld here.
    """
    gym, _, cli = protocol
    relay = _module("alfworld_fidelity_relay", ASSETS / "relay.py")
    applied = []
    env = SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(gym, "build", lambda game, budget: env)
    monkeypatch.setattr(gym, "reset", lambda _: {
        "won": False, "observation": "synthetic", "admissible": []})

    def step(actual_env, action):
        assert actual_env is env
        applied.append(action)
        return {"won": False, "done": len(applied) >= 50,
                "observation": f"synthetic {len(applied)}", "admissible": []}

    monkeypatch.setattr(gym, "step", step)

    # The pair the sandbox publishes at /run/harnessgrad-tool, built here because a host
    # run has nothing to mount. `HG_ALF_TOOL_DIR` is the host-side stand-in.
    published = tmp_path / "tool"
    published.mkdir()
    shutil.copy2(ROOT / "eval" / "service" / "tool_transport.py",
                 published / "tool_transport.py")
    socket_path = published / "tool.sock"
    log = tmp_path / gym.LOG_FILENAME
    with relay.EnvServer("synthetic-game", socket_path, budget=50) as server:
        monkeypatch.setenv("HG_ALF_TOOL_DIR", str(published))
        for index in range(50):
            assert cli.main(["--workdir", str(tmp_path), "do", "look"]) == 0
            assert len(gym.action_log_records(log)) == index + 1
        before = log.read_text()
        assert cli.main(["--workdir", str(tmp_path), "do", "look"]) == 2
        assert log.read_text() == before, "a refused action reached the log"
    assert len(applied) == 50
    assert server.receipt["errors"] == server.receipt["refused"] == 0, server.receipt


def test_the_default_pool_declares_no_division_and_the_hash_rule_is_a_run_choice():
    """ALFWorld's hash split was an adapter claim; it is a run-time choice now (2026-10-24).

    The adapter used to cut `valid_unseen` into a hash third by game id and call one side
    "study". A benchmark whose default pool is its held-out set has no division to declare,
    so `SPLIT` is `None` and the whole pool is scored; `--split default` still gives a run
    the platform's task-id hash, which is documentably **not** group-aware -- trials of one
    game can land on both sides -- so a leakage-free division is a study file
    (`HG_ALFWORLD_STUDY_IDS`) or an explicit `--split <file>`.
    """
    from data.scaffolds import registry

    module = importlib.import_module("data.datasets.alfworld")
    if module._LOAD_ERROR is not None:
        pytest.skip(f"alfworld is not provisioned: {module._LOAD_ERROR}")
    assert module.SPLIT is None and module.SPLIT_SOURCE == "none"
    tasks, _ = module.load()
    ids = [t["task_id"] for t in tasks]
    assert ids, "the default pool is empty"
    default = registry.default_split(tasks)
    assert set(default["train"]) | set(default["eval"]) == set(ids)
    assert not (set(default["train"]) & set(default["eval"]))
