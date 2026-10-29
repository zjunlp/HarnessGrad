"""A method may end a round by declaring the candidate *is* the incumbent.

The case this exists for: a wrapper knows it did not change anything -- the base method
returned an unmodified tree, or the incumbent already passes every scored task -- and the
platform re-runs the whole task set to learn a number the record already holds. Measured
in this project's own runs: 22 of 76 rounds in one dataset and 15 of 199 in another were of
that kind, and every one of them paid for a full measurement.

Two halves can be wrong silently, and they are what these tests pin:

* the claim is wrong (the named round is missing, the tree is not identical, the earlier
  measurement used a different trials count) -- reusing there would put a number on the
  curve for a tree nobody measured;
* the claim is right but the *record* does not say the method made it, which is the
  difference between a platform cache hit and a stop rule the method owns.
"""

from __future__ import annotations

from harnessgrad.loops.mode_a import _declared_no_change


def _point(round_no: int, sha: str, *, trials: int = 1, score: float = 0.5) -> dict:
    return {
        "round": round_no,
        "score": score,
        "identity": {"harness_sha": sha},
        "sampling": {"trials": trials},
        "per_task": {},
    }


SHA_A = "a" * 40
SHA_B = "b" * 40


def test_a_matching_declaration_reuses_that_round():
    curve = [_point(0, SHA_A), _point(1, SHA_B)]
    point, note = _declared_no_change(
        {"decision": "no_change", "incumbent_round": 1}, curve, SHA_B, 1, has_manifest=True)
    assert point is curve[1]
    assert note is None


def test_a_candidate_that_is_not_identical_is_measured_instead():
    curve = [_point(0, SHA_A), _point(1, SHA_B)]
    point, note = _declared_no_change(
        {"decision": "no_change", "incumbent_round": 0}, curve, SHA_B, 1, has_manifest=True)
    assert point is None
    assert note["decision"] == "no_change"
    assert "not byte-identical" in note["ignored"]


def test_an_unknown_round_is_refused():
    curve = [_point(0, SHA_A)]
    point, note = _declared_no_change(
        {"decision": "no_change", "incumbent_round": 7}, curve, SHA_A, 1, has_manifest=True)
    assert point is None
    assert "not in this curve" in note["ignored"]


def test_a_non_numeric_round_is_refused():
    curve = [_point(0, SHA_A)]
    point, note = _declared_no_change(
        {"decision": "no_change", "incumbent_round": "the last one"}, curve, SHA_A, 1,
        has_manifest=True)
    assert point is None
    assert "not a round number" in note["ignored"]


def test_a_different_trials_count_is_refused():
    curve = [_point(0, SHA_A, trials=3)]
    point, note = _declared_no_change(
        {"decision": "no_change", "incumbent_round": 0}, curve, SHA_A, 1, has_manifest=True)
    assert point is None
    assert "different trials" in note["ignored"]


def test_a_candidate_that_is_not_a_harness_is_refused():
    curve = [_point(0, SHA_A)]
    point, note = _declared_no_change(
        {"decision": "no_change", "incumbent_round": 0}, curve, SHA_A, 1, has_manifest=False)
    assert point is None
    assert "not a harness" in note["ignored"]


def test_no_declaration_is_not_a_note():
    curve = [_point(0, SHA_A)]
    point, note = _declared_no_change({}, curve, SHA_A, 1, has_manifest=True)
    assert point is None
    assert note is None


def test_the_oldest_matching_round_is_found_by_its_number_not_its_position():
    curve = [_point(0, SHA_A), _point(2, SHA_B), _point(3, SHA_A)]
    point, note = _declared_no_change(
        {"decision": "no_change", "incumbent_round": 3}, curve, SHA_A, 1, has_manifest=True)
    assert point is curve[2]
    assert note is None


def test_a_reused_measurement_is_not_a_trial_of_this_round():
    """The tasks ran at the round the number came from, and `reused_from` says which.

    Charging them here makes a free round look paid for, which is the one thing a cost
    table has to get right for "the wrapper saved this many trials" to mean anything.
    """
    from harnessgrad.loops.mode_a import _trials_of

    assert _trials_of({"reused_from": 0}, 100) == 0
    assert _trials_of({"per_task": {"t": 1.0}}, 100) == 100
    assert _trials_of(None, 100) == 0, "an unmeasured round spent nothing"
