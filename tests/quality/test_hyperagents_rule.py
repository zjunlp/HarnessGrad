"""HyperAgents' parent-selection rule, and the finding this port exists to make checkable.

From the method's own code (`utils/gl_utils.py:570-584`):

    mid = mean(top-3 scores)
    p(c) ∝ sigmoid(10*(score_c - mid)) * exp(-(children_c / 8)^3)

HyperAgents ships **two** selectors that disagree, and the disagreement is not the formula --
it is whether the formula is used at all. `select_next_parent.py:49-57` computes the same
child counts and scores and then discards them for `random.choice` ("keeping the search space
open"). So the uniform branch is uniform because its weights are **dead code**, *not* because
a normalised sigmoid cancels out. An earlier version of the port claimed the stronger,
wrong thing and the refutation is recorded in the file rather than dropped.

These tests pin both halves: the arithmetic, and the fact that the arithmetic is not uniform.
They exist because this was the one published port in `methods/` with no rule test, so the
only thing standing behind it was the prose above.
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "hyperagents", ROOT / "methods" / "hyperagents" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HA = _load()


def _point(round_no: int, score: float | None, *, measured: bool = True,
           rejected: bool = False) -> dict:
    """A curve point shaped the way this method reads one."""
    return {"round": round_no, "score_kind": "training", "score": score,
            "per_task": {"a": score} if score is not None else {},
            "measured_by_platform": measured,
            "method_reported": {"rejected": True} if rejected else {}}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# ------------------------------------------------------------------ 算术 ---


def test_the_sigmoid_is_centred_on_the_mean_of_the_top_three_not_on_a_half():
    """The difference from DGM, and the reason `mid` is computed at all.

    Scores 0.9 / 0.8 / 0.7 give `mid = 0.8`, so the weights must be symmetric *around
    0.8*: sigmoid(1), sigmoid(0), sigmoid(-1). Centring on a fixed 0.5 would give
    sigmoid(4), sigmoid(3), sigmoid(2) -- a different vector, and a different search.
    """
    candidates = [_point(0, 0.9), _point(1, 0.8), _point(2, 0.7)]
    scores = {0: 0.9, 1: 0.8, 2: 0.7}
    weights = HA.weights_for(candidates, scores, {}, mid=0.8, kind="score_child_prop")
    assert weights[0] == pytest.approx(_sigmoid(1.0), abs=1e-9)
    assert weights[1] == pytest.approx(0.5, abs=1e-9)
    assert weights[2] == pytest.approx(_sigmoid(-1.0), abs=1e-9)
    # ... and not what a fixed 0.5 would have produced.
    assert weights[0] != pytest.approx(_sigmoid(4.0), abs=1e-6)


def test_the_child_penalty_is_a_cube_that_barely_bites_before_eight():
    """`exp(-(c/8)^3)`: one child costs 0.2%, four cost 12%, eight cost 63%."""
    candidates = [_point(0, 0.5)]
    scores = {0: 0.5}
    base = HA.weights_for(candidates, scores, {0: 0}, mid=0.5, kind="score_child_prop")[0]
    assert base == pytest.approx(0.5, abs=1e-9)
    for children, factor in ((1, math.exp(-((1 / 8) ** 3))),
                             (4, math.exp(-0.125)),
                             (8, math.exp(-1.0)),
                             (16, math.exp(-8.0))):
        got = HA.weights_for(candidates, scores, {0: children}, mid=0.5,
                             kind="score_child_prop")[0]
        assert got == pytest.approx(base * factor, abs=1e-12), children
    # "Only starts to bite past about eight" -- the smallest two are near 1.0, and the
    # penalty is monotone.
    assert math.exp(-((1 / 8) ** 3)) > 0.998
    assert math.exp(-0.125) > 0.88
    assert math.exp(-1.0) < 0.37


def test_the_weight_is_the_sigmoid_times_the_child_penalty():
    """The product, computed by hand for one candidate."""
    candidates = [_point(0, 0.8)]
    scores = {0: 0.8}
    got = HA.weights_for(candidates, scores, {0: 4}, mid=0.3,
                         kind="score_child_prop")[0]
    want = _sigmoid(10.0 * (0.8 - 0.3)) * math.exp(-0.125)
    assert got == pytest.approx(want, abs=1e-12)


def test_the_child_penalty_can_outweigh_a_much_higher_score():
    """Why the penalty exists: a heavily-used parent stops attracting work.

    Candidate 0 scores 0.9 with 16 children; candidate 1 scores 0.5 with none. With
    `mid = 0.7` the lower scorer carries ~400x the weight, so the search leaves the
    exhausted branch without a rule that says "leave it".
    """
    candidates = [_point(0, 0.9), _point(1, 0.5)]
    scores = {0: 0.9, 1: 0.5}
    w = HA.weights_for(candidates, scores, {0: 16}, mid=0.7, kind="score_child_prop")
    assert w[1] / w[0] > 300
    assert w[1] == pytest.approx(_sigmoid(-2.0), abs=1e-9)


def test_best_is_one_hot_and_uniform_is_uniform():
    candidates = [_point(0, 0.2), _point(1, 0.9), _point(2, 0.5)]
    scores = {0: 0.2, 1: 0.9, 2: 0.5}
    assert HA.weights_for(candidates, scores, {}, 0.5, "best") == {0: 0.0, 1: 1.0, 2: 0.0}
    assert HA.weights_for(candidates, scores, {}, 0.5, "uniform") == {0: 1.0, 1: 1.0, 2: 1.0}


def test_a_normalised_sigmoid_is_not_uniform():
    """The claim the port got wrong once, pinned so it cannot come back.

    `score_prop` is the formula *without* the child penalty. Normalising a sigmoid over
    candidates keeps their ordering, so it is not uniform -- only the branch that throws
    its weights away is.
    """
    candidates = [_point(0, 0.2), _point(1, 0.9), _point(2, 0.5)]
    scores = {0: 0.2, 1: 0.9, 2: 0.5}
    w = HA.weights_for(candidates, scores, {}, mid=0.5, kind="score_prop")
    assert len(set(w.values())) == 3, w
    assert w[1] > w[2] > w[0]
    assert not all(abs(v - 1 / 3) < 1e-9 for v in w.values())


# ------------------------------------------------------------ 有效性闸门 ---


def test_the_validity_gate_never_reads_the_score():
    """`generate_loop.py:706-714`: a node leaves the candidate set for *not running*.

    HyperAgents never drops an archive entry for scoring badly, so a measured round that
    scored zero and was not rejected is a legitimate parent.
    """
    assert HA._valid(_point(0, 0.0)) is True
    assert HA._valid(_point(0, None)) is False, "scoreless runs are not scored parents"
    assert HA._valid(_point(0, 0.9, measured=False)) is False
    assert HA._valid(_point(0, 0.9, rejected=True)) is False


def test_only_the_validity_gate_decides_who_may_be_a_parent():
    """A rejected round is absent from the candidate set even when it has the top score."""
    history = [_point(0, 0.9, rejected=True), _point(1, 0.4)]
    chosen, report = HA.select_parent(history, round_index=0)
    assert report["candidates"] == [1]
    assert chosen["round"] == 1


# --------------------------------------------------------------- 选择本身 ---


def test_the_report_names_both_upstream_selectors_and_measures_uniformity(
        monkeypatch):
    """The finding is reported, not asserted: the deviation is computed per point."""
    history = [_point(0, 0.2), _point(1, 0.9)]
    _, report = HA.select_parent(history, round_index=1)
    assert "select_next_parent.py" in report["note"]
    assert "gl_utils.py" in report["note"]
    assert report["rule"].startswith("HyperAgents score_child_prop")
    assert report["seed"] == 1 * 104729

    monkeypatch.setattr(HA, "SETTING", "uniform")
    _, uniform = HA.select_parent(history, round_index=1)
    assert uniform["is_uniform"] is True
    assert uniform["deviation_from_uniform"] == 0.0

    monkeypatch.setattr(HA, "SETTING", "score_child_prop")
    _, scored = HA.select_parent(history, round_index=1)
    assert scored["is_uniform"] is False
    assert scored["deviation_from_uniform"] > 0.0


def test_the_same_round_always_picks_the_same_parent():
    history = [_point(i, s) for i, s in enumerate((0.2, 0.9, 0.4, 0.6))]
    first = HA.select_parent(history, round_index=3)[0]["round"]
    second = HA.select_parent(history, round_index=3)[0]["round"]
    assert first == second


def test_a_degenerate_weight_vector_falls_back_to_uniform(monkeypatch):
    """`gl_utils.py:565-568`: when the weights do not normalise, the source samples
    uniformly rather than dividing by zero."""
    monkeypatch.setattr(HA, "weights_for",
                        lambda candidates, scores, children, mid, kind:
                        {p["round"]: 0.0 for p in candidates})
    chosen, report = HA.select_parent([_point(0, 0.2), _point(1, 0.9)], round_index=0)
    assert chosen["round"] in (0, 1)
    assert set(report["weights"].values()) == {1.0}


def test_metadata_describes_multiple_children_instead_of_a_single_candidate():
    """Upstream utils/gl_utils.py:570-584 supplies the sampler used by main's batch."""
    metadata = json.loads((ROOT / "methods/hyperagents/method.json").read_text())
    text = json.dumps(metadata).lower()
    assert not re.search(r"one[\s-]+candidate[\s-]+per[\s-]+round", text)
    assert "no real archive" not in text
    assert "multiple children per call" in text


def test_metadata_archive_window_matches_the_configuration_default():
    """Upstream generate_loop.py:924-925 grows the archive; the channel may cap it.

    Read the platform's declared default, rather than repeating the old window size
    in this test. HG_METHOD_HISTORY_ROUNDS=0 means all states, not an empty archive.
    """
    from harnessgrad.config.areas import MethodConfig

    metadata = json.loads((ROOT / "methods/hyperagents/method.json").read_text())
    archive = metadata["deviations"][0]
    text = archive["what"] + " " + archive["why"]
    documented = re.search(r"HG_METHOD_HISTORY_ROUNDS=(\d+)", text)
    assert documented is not None, "archive documentation must name its configured default"
    assert int(documented.group(1)) == MethodConfig().history_rounds
    assert "all states by default" in text
    assert "newest N states" in text
    assert "skipped" in text
