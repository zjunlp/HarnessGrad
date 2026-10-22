"""What the Jericho adapter promises: the official table, the official reduction, the ROM.

Three of these carry the weight:

* `test_the_official_table_is_what_game_info_says` compares the adapter's task list against
  `jericho.game_info` -- the benchmark's own table -- rather than against a list somebody typed.
* `test_the_reduction_is_the_one_the_documentation_prints` pins `score / get_max_score()`, and
  `test_the_walkthrough_reaches_the_maximum_for_most_games` reports the games where the
  reference solution stops short, because that is a fact about the benchmark and it changes
  what a control failure means.
* `test_the_walkthrough_is_never_written_into_the_workdir` is the leak boundary: the ROM is not
  a secret (the agent cannot play without it), the **walkthrough is**, and it lives in the
  installed package rather than in anything `SETUP` or `VERIFY.inputs` writes.

The suite-is-versioned tests are skipped when the data is absent, because a machine without the
ROMs cannot make a claim about them either way.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def _load_control(dataset: str):
    """The dataset's `control.py`, loaded **by path under a unique module name**.

    Measured, and the full suite caught it: two test files both did `import control`, and
    because the first one leaves `control` in `sys.modules`, the second got the *other
    dataset's* control module -- so `control.materialize` raised `AttributeError` only when the
    whole suite ran, never in isolation. A bare name for a dataset-private helper is a name
    collision waiting for a second dataset to exist.
    """
    import importlib.util

    path = ROOT / "data" / "datasets" / dataset / "control.py"
    spec = importlib.util.spec_from_file_location(f"{dataset}_control_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def jericho():
    import data.datasets.jericho as module
    if not module._RECORDS:
        pytest.skip(f"no Jericho games under {module.games_dir()}; run "
                    f"`python3 tools/datasets.py --fetch --dataset jericho`")
    return module


@pytest.fixture(scope="module")
def live_record(jericho):
    """The shortest game a run would see: these tests replay real walkthroughs.

    The whole pool is what a run sees -- the adapter declares no division (2026-10-24) --
    and the shortest walkthrough keeps the replay affordable.
    """
    return min(jericho._RECORDS, key=lambda r: len(r["walkthrough"]))


@pytest.fixture
def live(jericho, live_record, tmp_path):
    """A **fresh** materialized workdir per test.

    Function-scoped on purpose: measured, a module-scoped one let
    `test_the_check_reports_unmeasured_without_a_game_file` delete `jer_game.json` and break
    the next test, which then failed for a reason that had nothing to do with what it asserts.
    """
    _load_control("jericho").materialize(live_record, tmp_path)
    return tmp_path, live_record


# ---------------------------------------------------------------- the table ---


def test_the_official_table_is_what_game_info_says(jericho):
    """The task list is the benchmark's, not this adapter's.

    `jericho.game_info` is the installed package's own table: one dict per game with `rom`,
    `seed` and `walkthrough`. Every task this dataset returns must be one of those entries, and
    every entry whose ROM is present must be a task.
    """
    import jericho.game_info as info

    official = {}
    for key in sorted(dir(info)):
        if key.startswith("_"):
            continue
        entry = getattr(info, key)
        if isinstance(entry, dict) and "rom" in entry:
            official[str(entry["rom"])] = entry
    present = {rom: e for rom, e in official.items()
               if (jericho.games_dir() / rom).is_file()}
    assert len(present) == 56, (
        f"{len(present)} of {len(official)} official games have a ROM; measured 56 "
        f"(sherbet.z5 is absent from the BYU-PCCL archive)")
    assert {r["rom"] for r in jericho._RECORDS} == set(present)
    for record in jericho._RECORDS:
        entry = official[record["rom"]]
        assert record["seed"] == int(entry["seed"])
        assert record["walkthrough"] == [a for a in str(entry["walkthrough"]).split("/") if a]


def test_the_missing_official_game_is_reported(jericho):
    """A game the archive does not carry is named, not silently dropped."""
    assert jericho.missing_games() == ["sherbet.z5"]


def test_every_task_has_a_goal_and_the_interface_it_needs(jericho):
    tasks, scorable = jericho.load()
    assert set(scorable) == {t["task_id"] for t in tasks}
    for task in tasks:
        assert task["goal"].strip()
        assert "jer do" in task["goal"]
        assert "jer look" in task["goal"], "the fixed observation command must stay advertised"
        assert "jer valid" not in task["goal"], "a command that cannot work is not advertised"
        assert "jer_game.json" not in task["goal"], "the internal filename is not the interface"


def test_a_task_dict_carries_no_grade(jericho):
    forbidden = {"verify", "verifier", "setup", "expected", "scorable", "env"}
    for task in jericho.load()[0]:
        assert not (set(task) & forbidden), task["task_id"]


def test_no_division_is_declared_over_the_suite_s_games(jericho):
    """The suite's games are the benchmark's evaluation material; it publishes no study side."""
    assert jericho.SPLIT is None and jericho.SPLIT_SOURCE == "none"
    tasks, _ = jericho.load()
    assert {t["task_id"] for t in tasks} == {r["task_id"] for r in jericho._RECORDS}


# ------------------------------------------------------------- the reduction ---


def test_the_reduction_is_the_one_the_documentation_prints(jericho):
    """`info['score']` over `get_max_score()`, from Jericho's own quick-start.

    The docs print `Total Score info['score']` and `Scored X out of env.get_max_score()`, so
    the fraction is the documented reading. The alternatives are pinned here too: absolute
    points are not comparable across games (350 for Zork 1, 60 for `detective`), and normalising
    by a walkthrough's score would make the denominator depend on a solution.
    """
    assert jericho_lib().score_fraction(0, 100) == 0.0
    assert jericho_lib().score_fraction(50, 100) == 0.5
    assert jericho_lib().score_fraction(100, 100) == 1.0
    # A game that somehow reports more than its maximum is clamped, not allowed above 1.
    assert jericho_lib().score_fraction(150, 100) == 1.0
    assert jericho_lib().score_fraction(10, 0) == 0.0, "no division by zero"


def jericho_lib():
    import importlib.util
    path = ROOT / "data" / "datasets" / "jericho" / "lib" / "jer_env.py"
    spec = importlib.util.spec_from_file_location("jer_env_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_walkthrough_reaches_the_maximum_for_most_games(jericho):
    """How many reference solutions actually finish their game.

    This is the number that decides what an oracle failure means. If every walkthrough reached
    the maximum then an oracle scoring below 1 would be a check bug; measured, some do not, so
    the control reports which game it ran and a reader can tell the two apart. Recorded for all
    56 games here, once, rather than rediscovered per control run.
    """
    from jericho import FrotzEnv

    unfinished = []
    checked = 0
    for record in jericho._RECORDS:
        env = FrotzEnv(str(record["path"]), seed=record["seed"])
        env.reset()
        score = 0
        try:
            for action in record["walkthrough"]:
                _obs, _reward, done, info = env.step(action)
                score = int(info.get("score", 0))
                if done:
                    break
            maximum = int(env.get_max_score())
        finally:
            env.close()
        if score < maximum:
            unfinished.append((record["task_id"], score, maximum))
        checked += 1
    assert checked == 56
    # The assertion is on the *shape* of the finding, not on a number that a game update could
    # move: whatever the count is, it has to be a small minority or the reduction is wrong.
    assert len(unfinished) <= 8, (
        f"{len(unfinished)} of {checked} walkthroughs stop short of get_max_score(); if that "
        f"many, the fraction is probably not the right reduction: {unfinished}")


# ---------------------------------------------------------- the leak boundary ---


def test_the_walkthrough_is_never_written_into_the_workdir(jericho, live):
    """The ROM is public; the reference solution is not.

    `SETUP` ships the game -- the agent cannot play without it -- and must not ship the command
    list. `game_info.py` holds it, in the installed package, which is not on the workdir's
    disk at all.
    """
    workdir, record = live
    blob = json.dumps(jericho.SETUP[record["task_id"]])
    assert record["rom"] in blob, "the ROM must reach the agent"
    for command in record["walkthrough"][:5]:
        assert command not in blob, command
    written = {p.name for p in workdir.iterdir()}
    assert record["rom"] in written
    assert not any("walkthrough" in name.lower() for name in written)


def test_the_verifier_declares_the_contract_paths(jericho):
    for task_id, verifier in jericho.VERIFY.items():
        assert verifier["kind"] == "command"
        assert verifier["reward_file"] == "/verifier/reward.txt"
        assert verifier["evidence_file"] == "/verifier/evidence.json"
        for entry in verifier["inputs"]:
            if "from" in entry:
                assert Path(entry["from"]).is_file(), (task_id, entry)
            elif entry["path"].endswith("__init__.py"):
                # The scorer-input shim ships two deliberately **empty** package markers
                # (`data/__init__.py`, `data/scaffolds/__init__.py`, added by 63606f42). An empty file
                # is the whole content of a package marker, so this is the one legitimate exception to
                # "every content entry carries a payload" - and it is stated rather than skipped.
                assert entry["content"] == "", (task_id, entry)
            else:
                assert entry["content"].strip(), (task_id, entry)


def test_every_task_can_tell_the_agent_where_the_rom_is(jericho):
    """Both `SETUP` and `VERIFY.inputs` carry the ROM.

    Measured: the first version shipped it only to the check, and `--oracle --platform` scored 0
    -- "the log holds no actions" -- because the harness's CLI could not start the game. The
    harness and the check share one workdir, so everything both sides need goes in both places.
    """
    by_id = {r["task_id"]: r for r in jericho._RECORDS}
    for task_id, setup in jericho.SETUP.items():
        rom_name = by_id[task_id]["rom"]
        assert any(e["path"] == rom_name for e in setup["files"]), task_id
        assert any(e.get("path") == rom_name for e in jericho.VERIFY[task_id]["inputs"]), \
            f"{task_id}: the check cannot see the ROM"


# ------------------------------------------------------------ the episode ---


def _run_check(jericho, record: dict, workdir: Path, *,
               re_materialize: bool = True,
               skip: set[str] | None = None) -> tuple[float, dict]:
    """Run the check as `eval/verification/verifier.py` does: re-materialize the inputs, then run.

    `skip` exists for the one test that needs an input to be **absent**: the platform writes
    every entry, so "the check cannot measure" has to be provoked by materializing everything
    else and leaving that one out -- the first version deleted `jer_game.json` and then ran the
    check, which failed on a missing `check.py` instead and reported nothing.
    """
    verifier = jericho.VERIFY[record["task_id"]]
    if re_materialize:
        for entry in verifier.get("inputs") or []:
            if skip and entry["path"] in skip:
                (workdir / entry["path"]).unlink(missing_ok=True)
                continue
            target = workdir / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if "content" in entry:
                target.write_text(entry["content"])
            else:
                shutil.copy2(entry["from"], target)
    proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=workdir,
                          capture_output=True, text=True)
    reward_path = workdir / jericho.REWARD_REL
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = workdir / jericho.EVIDENCE_REL
    evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
    return reward, {"stdout": proc.stdout, "stderr": proc.stderr, **evidence}


def test_the_check_grades_the_walkthrough_as_one(jericho, live):
    workdir, record = live
    from jericho import FrotzEnv
    env = FrotzEnv(str(record["path"]), seed=record["seed"])
    env.reset()
    maximum = env.get_max_score()
    env.close()
    if not maximum:
        pytest.skip("this game reports no maximum score")
    for action in record["walkthrough"]:
        subprocess.run([sys.executable, "jer", "do", action], cwd=workdir, check=True,
                       capture_output=True, text=True)
    reward, evidence = _run_check(jericho, record, workdir)
    # The walkthrough of the shortest eval-side game is expected to finish it; if this ever
    # fails, `test_the_walkthrough_reaches_the_maximum_for_most_games` says whether it is the
    # game or the check.
    assert reward == 1.0, evidence
    assert evidence["score"] == evidence["max_score"]


def test_the_check_grades_no_commands_as_zero(jericho, live):
    workdir, record = live
    (workdir / jericho.LOG_FILE).unlink(missing_ok=True)
    reward, evidence = _run_check(jericho, record, workdir)
    assert reward == 0.0
    assert evidence["measured"] is True
    assert evidence["score"] == 0


def test_the_check_re_materializes_the_game_and_the_rom(jericho, live):
    """Editing the workdir's game file or ROM does not move the grade."""
    workdir, record = live
    (workdir / jericho.LOG_FILE).unlink(missing_ok=True)
    # Plant a different game's identity. The check must read the re-materialized one.
    planted = next(r for r in jericho._RECORDS if r["rom"] != record["rom"])
    (workdir / jericho.GAME_FILE).write_text(json.dumps(
        {"task_id": planted["task_id"], "rom": planted["rom"], "seed": planted["seed"]}))
    reward, evidence = _run_check(jericho, record, workdir)
    assert reward == 0.0
    assert evidence["actions_in_log"] == 0
    assert evidence["game_file"].endswith(jericho.GAME_FILE)


def test_the_check_reports_unmeasured_without_a_game_file(jericho, live):
    """No game file means no measurement -- not the agent's zero.

    `re_materialize=False` is the point of the test: the platform *would* write the file, so
    this simulates the case where its trusted input is absent (a by-hand run, or a
    materialization failure) and asserts the check distinguishes that from a 0 it measured.
    """
    workdir, record = live
    reward, evidence = _run_check(jericho, record, workdir,
                                  skip={jericho.GAME_FILE})
    assert reward == -1.0, "no reward file when the check cannot measure"
    assert evidence["measured"] is False
    # Since 63606f42 the check binds the inputs it consumes *before* it scores, so a workdir missing
    # the game file is refused by the input binding and the message names the file. The property this
    # test exists for is that the outcome is unmeasured rather than a zero, and the message must still
    # name what is missing, so both phrasings are accepted and neither is dropped.
    assert jericho.GAME_FILE in evidence["error"] or "game file" in evidence["error"], evidence


def test_the_cli_can_report_the_score(jericho, live):
    workdir, _record = live
    out = subprocess.run([sys.executable, "jer", "score"], cwd=workdir,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "score" in out.stdout and "/" in out.stdout


def test_the_cli_s_valid_actions_are_best_effort(jericho, live):
    """`jer valid` is gone, and this test says why it was not merely fixed.

    The command used to be advertised and returned an empty list: the agent runtime is a
    code-only projection whose `defines.py` has `BINDINGS_DICT = {}` (that table hides the
    walkthrough, and it is also what `FrotzEnv` builds its template action generator from), so
    `get_valid_actions()` short-circuits to `[]`. Restoring the table is not enough either --
    the generator needs spaCy's `en_core_web_sm`, whose fallback is a **network download**, and
    this dataset's agent runtime has `network: none` (measured: this environment has spaCy but
    not the model, and `get_valid_actions()` then raises from `spacy.cli.download`). The
    interface therefore no longer offers it. See
    `test_jericho_interface_regressions.py::test_the_cli_no_longer_ships_the_action_lister`.
    """
    workdir, _record = live
    valid = subprocess.run([sys.executable, "jer", "valid"], cwd=workdir,
                           capture_output=True, text=True)
    assert valid.returncode == 2, valid.stdout
    assert "unknown command" in valid.stderr and "valid" in valid.stderr
    assert "jer valid" not in subprocess.run([sys.executable, "jer", "help"], cwd=workdir,
                                             capture_output=True, text=True).stdout
