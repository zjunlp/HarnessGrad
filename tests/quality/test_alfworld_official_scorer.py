"""ALFWorld's own scorer, copied in, and a differential test against our check.

The rule this file exists to enforce
------------------------------------
Our reward must be **the paper's metric**, and it must be possible to see that without
trusting us. So the reduction ALFWorld's evaluator performs is transcribed here, and our
check's reward is compared with it on real games. The transcription is not an import of
`alfworld`: importing their package and calling our own function "the same" would prove
only that we can call their code, not that our reward is their metric. The copied lines
are quoted with their upstream line numbers, and a drift test below asserts they are
still verbatim in the upstream file when that checkout is present.

Why "any step won" and not "the last step won"
----------------------------------------------
Upstream builds a per-step reward as `score[t] - score[t-1]` and takes
`np.max(sequence_game_points, axis=0)` -- the maximum increment, not the final value.
An episode that satisfies the goal and then keeps acting is still a success. Our check
returns `1 iff won at some step`, which is the same predicate; the differential test
below pins the equality rather than asserting it in prose.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data" / "datasets" / "alfworld" / "lib"))

DATA_ROOT = Path(os.environ.get("HG_ALFWORLD_DATA",
                                "/mnt/20t/xubuqiang/datasets/alfworld"))
UPSTREAM = (DATA_ROOT / "_src" / "repo" / "alfworld" / "agents" / "eval"
            / "evaluate_dagger.py")
LIB = ROOT / "data" / "datasets" / "alfworld" / "lib"
CHECK = ROOT / "data" / "datasets" / "alfworld" / "verify" / "check.py"

pytest.importorskip("textworld", reason="ALFWorld needs textworld")
pytest.importorskip("alfworld", reason="ALFWorld needs the alfworld package")


def official_game_points(won_per_step) -> float:
    """**Upstream's reduction, transcribed for a batch of one.**

    From `alfworld/agents/eval/evaluate_dagger.py`; the line numbers are upstream's:

        98   scores = [float(item) for item in infos["won"]]
       122   step_rewards = [float(curr) - float(prev) for curr, prev in zip(scores, prev_rewards)]
       123   prev_rewards = scores
       124   sequence_game_points.append(step_rewards)
       134   game_points = np.max(np.array(sequence_game_points), 0).tolist()

    Upstream keeps `prev_rewards` as the batch's previous scores and zips; with one game
    that is a one-element list, which is the only change made here. Nothing else is
    adapted: the arithmetic, the `np.max(..., 0)` axis and the `.tolist()` are upstream's.
    `test_the_copied_lines_are_still_verbatim_upstream` fails if they ever stop matching.
    """
    prev_rewards = [0.0]
    sequence_game_points = []
    scores = [float(item) for item in won_per_step]
    for curr in scores:
        step_rewards = [float(curr) - float(prev)
                        for curr, prev in zip([curr], prev_rewards)]
        prev_rewards = [curr]
        sequence_game_points.append(step_rewards)
    game_points = np.max(np.array(sequence_game_points), 0).tolist()
    return float(game_points[0])


def _check_module():
    spec = importlib.util.spec_from_file_location("alfworld_check", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data_available() -> bool:
    try:
        import data.datasets.alfworld as alfworld  # noqa: F401
        return bool(alfworld._RECORDS)
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def alfworld():
    if not _data_available():
        pytest.skip(f"no extracted ALFWorld data under {DATA_ROOT}")
    import data.datasets.alfworld as module
    return module


@pytest.fixture(scope="module")
def control():
    path = ROOT / "data" / "datasets" / "alfworld" / "control.py"
    spec = importlib.util.spec_from_file_location("alfworld_control", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(task_type: str):
    import data.datasets.alfworld as alfworld
    wanted = [r for r in alfworld._RECORDS if r["task_type"] == task_type]
    assert wanted, f"no task of type {task_type}"
    # Shortest walkthrough of the type, so the differential test stays cheap while still
    # exercising a real multi-step episode.
    return min(wanted, key=lambda r: len(json.loads(
        Path(r["game_path"]).read_text()).get("walkthrough") or []))


def _walkthrough(record) -> list[str]:
    return list(json.loads(Path(record["game_path"]).read_text())["walkthrough"])


def _won_sequence(game_path: Path, actions: list[str], budget: int = 30) -> list[bool]:
    """An **independent** replay, so the comparison is not our code against itself.

    This builds the TextWorld environment directly rather than through `gym_env.py`.
    If it used `gym_env`, the differential test would only prove that our reduction
    calls our reduction.
    """
    import textworld
    import textworld.gym
    from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos

    request_infos = textworld.EnvInfos(won=True, admissible_commands=True,
                                       extras=["gamefile"])
    env_id = textworld.gym.register_games(
        [str(game_path)], request_infos, batch_size=1, asynchronous=False,
        max_episode_steps=budget,
        wrappers=[AlfredDemangler(shuffle=False), AlfredInfos])
    env = textworld.gym.make(env_id)
    try:
        _obs, infos = env.reset()
        sequence = bool(infos.get("won", [False])[0])
        out = []
        for action in actions[:budget]:
            _obs, _scores, dones, infos = env.step([action])
            won = bool(infos.get("won", [False])[0])
            out.append(won)
            sequence = sequence or won
            if dones[0]:
                break
        assert not sequence or any(out), "won must come from a step, not a reset"
        return out
    finally:
        env.close()


@pytest.mark.parametrize("task_type", ["look_at_obj_in_light",
                                       "pick_clean_then_place_in_recep"])
def test_our_reward_equals_the_official_reduction_on_a_real_win(task_type, alfworld,
                                                                 control, tmp_path):
    record = _record(task_type)
    actions = _walkthrough(record)
    official = official_game_points(_won_sequence(record["game_path"], actions))
    assert official == 1.0, f"the walkthrough did not win: {record['task_id']}"

    workdir = tmp_path / task_type
    control.materialize(record, workdir)
    control.drive(workdir, actions, budget=alfworld.STEP_BUDGET)
    reward, detail = control.run_check(workdir, record, budget=alfworld.STEP_BUDGET)
    assert reward == official, detail
    evidence = json.loads((workdir / alfworld.EVIDENCE_REL).read_text())
    assert evidence["measured"] is True
    assert evidence["scorer_input"]["verified"] is True
    assert evidence["scorer_input"]["task_id"] == record["task_id"]


def test_an_empty_episode_is_zero_for_our_check(alfworld, control, tmp_path):
    """The official reduction is undefined for an episode of zero steps.

    Upstream's evaluator loop always runs at least one step, so `np.max` never sees an
    empty `sequence_game_points`; our check does, because an agent that acts not at all
    is a real and common case. There is therefore nothing to compare the empty case
    against, and the sensible reading -- "no step won, so the goal was not satisfied" --
    is pinned here instead. The non-empty reductions are the differential test above.
    """
    record = _record("look_at_obj_in_light")
    assert official_game_points([False] * 5) == 0.0
    with pytest.raises(ValueError):
        official_game_points([])
    workdir = tmp_path / "noop"
    control.materialize(record, workdir)
    reward, detail = control.run_check(workdir, record, budget=alfworld.STEP_BUDGET)
    assert reward == 0, detail
    assert "no actions" in detail


def test_the_reduction_takes_the_maximum_not_the_last_value():
    """A won-then-still-acting episode is a win upstream, and must be here too.

    `won` is monotone in TextWorld, so a `False` after a `True` cannot happen through a
    real episode; the reduction is nonetheless `max` of the increments, and this pins
    that it is not accidentally implemented as "the final score".
    """
    assert official_game_points([True, False]) == 1.0
    assert official_game_points([False, True, False]) == 1.0
    check = _check_module()
    for sequence in ([True, False], [False, True, False], [False, False], [True]):
        assert check.official_reduction(sequence) == official_game_points(sequence)


def test_the_copied_lines_are_still_verbatim_upstream():
    """Drift guard: the transcription above is only evidence while it still matches.

    Skipped when the pinned checkout is absent -- this is a provenance check, and a
    machine without the checkout cannot make a claim about it either way.
    """
    if not UPSTREAM.is_file():
        pytest.skip(f"no upstream checkout at {UPSTREAM}")
    source = UPSTREAM.read_text()
    for line in (
        'scores = [float(item) for item in infos["won"]]',
        "step_rewards = [float(curr) - float(prev) "
        "for curr, prev in zip(scores, prev_rewards)]",
        "prev_rewards = scores",
        "sequence_game_points.append(step_rewards)",
        "game_points = np.max(np.array(sequence_game_points), 0).tolist()",
    ):
        assert line in source, f"upstream no longer contains {line!r}"
