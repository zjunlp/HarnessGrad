"""FlashRSI gepa/_population.py:36-73 and gepa/__init__.py:216-226."""

import random
from flashrsi_helpers import P, member


def test_frontier_keeps_complementary_specialists_and_prunes_redundant_ties():
    rows = [
        member(0, 0.5, {"a": 1, "b": 0}),
        member(1, 0.5, {"a": 0, "b": 1}),
        member(2, 0.5, {"a": 1, "b": 0}),
    ]
    front = P.pareto_front(rows)
    assert front == {"a": {2}, "b": {1}}
    assert {P.gepa_parent(rows, random.Random(i)).round for i in range(20)} == {1, 2}


def test_gepa_admission_needs_paired_scores_and_strict_improvement():
    a = member(0, 0.5, {"a": 1, "b": 0})
    tie = member(1, 0.5, {"a": 0, "b": 1})
    up = member(2, 1, {"a": 1, "b": 1})
    assert not P.admits_gepa(tie, a)
    assert P.admits_gepa(up, a)
    assert not P.admits_gepa(member(3, 1, {"different": 1}), a)
