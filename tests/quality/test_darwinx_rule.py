"""FlashRSI darwinx/__init__.py:163-232: preserving extension invariant."""

import random
from flashrsi_helpers import P, member


def test_gain_cannot_pay_for_lost_solved_task():
    root = member(0, 0.5, {"a": 1, "b": 0})
    child = member(1, 0.75, {"a": 0.5, "b": 1}, {"parents": [0]})
    verdict = P.preserving_extension(child, [root], {"a"}, tolerance=1)
    assert not verdict["accepted"] and verdict["lost_invariant"] == ["a"]


def test_archive_drops_rejected_lineage_and_extends_invariant():
    root = member(0, 0.5, {"a": 1, "b": 0})
    up = member(1, 1, {"a": 1, "b": 1}, {"parents": [0]})
    bad = member(2, 0.5, {"a": 0, "b": 1}, {"parents": [1]})
    archive, invariant, gains, verdicts = P.darwin_archive([root, up, bad])
    assert [m.round for m in archive] == [0, 1, 2] and invariant == {"a", "b"}
    assert gains[1] == 1 and not verdicts["2"]["accepted"]


def test_rejected_complementary_branch_can_be_a_merge_donor_but_not_mutation_parent():
    root = member(0, 0.5, {"a": 1, "b": 0})
    rejected = member(1, 0.5, {"a": 0, "b": 1}, {"parents": [0]})
    archive, invariant, gains, _ = P.darwin_archive([root, rejected])
    assert not rejected.info["_accepted"]
    assert (
        len(P.darwin_parents(archive, gains, random.Random(1), 2, merge_every=3)) == 2
    )
    assert P.darwin_parents(
        archive, gains, random.Random(1), 1, merge_every=3, exploration=1
    ) == [root]


def test_independent_branches_inherit_own_parent_invariant_not_other_branches():
    root = member(0, 0, {"a": 0, "b": 0})
    branch_a = member(1, 0.5, {"a": 1, "b": 0}, {"parents": [0]})
    branch_b = member(2, 0.5, {"a": 0, "b": 1}, {"parents": [0]})
    archive, invariant, gains, verdicts = P.darwin_archive([root, branch_a, branch_b])
    assert verdicts["1"]["accepted"] and verdicts["2"]["accepted"]
    assert branch_a.info["_invariant"] == ["a"] and branch_b.info["_invariant"] == ["b"]
