"""题集对 harness 质量敏不敏感 —— Evo-Bench 的构造方法,以及我们为什么要它。

**他们的话**(Abstract):*"it leverages auxiliary-task evolution to identify tasks genuinely
sensitive to framework improvements, followed by sensitivity-aware stratified splitting to
ensure robust cross-suite generalization."*

**我们为什么要抄。** 我们的 train/eval 切分只是 `data/datasets/terminal_bench.py` 里声明的一行
(52/37),没有任何证据说明两边都分得出"好 harness"和"坏 harness"。一道所有 harness 都做对
(或都做错)的题,对比较毫无贡献,却贡献噪声 —— 而这个平台的整个主张就是它的数字有意义。

这个文件只测**纯数学**:相关、灵敏度、分层切分。真正跑 harness 的那部分走平台自己的
`evaluate`,已经在别处被测过。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tools.task_sensitivity as ts  # noqa: E402


# ------------------------------------------------------------------ 相关 ---

def test_pearson_is_the_textbook_value():
    assert ts.pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert ts.pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)


def test_a_constant_series_is_undefined_not_zero():
    """**这条是重点。** 返回 0 会把"这道题和 harness 质量无关"和"这题所有 harness 一样分、
    根本问不出问题"混成一句话 —— 而这两种事实的处置完全不同。"""
    assert ts.pearson([1, 1, 1], [1, 2, 3]) is None
    assert ts.pearson([1, 2, 3], [5, 5, 5]) is None


def test_too_few_points_is_undefined():
    """两个点永远能连成一条直线,r 恒为 ±1 —— 那不是证据。"""
    assert ts.pearson([1, 2], [1, 2]) is None
    assert ts.pearson([1, 2, 3], [1, 2, 3], min_n=4) is None


# ---------------------------------------------------------------- 灵敏度 ---

def _scores(rows: dict[str, dict[str, float]]) -> dict[str, dict[str, float | None]]:
    return {t: dict(per) for t, per in rows.items()}


def test_a_task_that_tracks_harness_quality_is_sensitive():
    """好 harness 在别的题上也好、在这道题上也好 → Sens > 0。"""
    scores = _scores({
        # h1..h4 的质量严格递增:去掉 tracks 后每题的平均分是 1/3, 2/3, 1
        "a": {"h1": 0.0, "h2": 1 / 3, "h3": 1 / 3, "h4": 1.0},
        "b": {"h1": 0.0, "h2": 2 / 3, "h3": 2 / 3, "h4": 1.0},
        "c": {"h1": 0.0, "h2": 0.0, "h3": 1.0, "h4": 1.0},
        # 这道题跟着质量走
        "tracks": {"h1": 0.0, "h2": 0.0, "h3": 1.0, "h4": 1.0},
    })
    got = {s["task_id"]: s for s in ts.analyse(scores)}
    assert got["tracks"]["status"] == "sensitive"
    # 手算:score=(0,0,1,1),quality=(0,1/3,2/3,1) → r = 0.6667/sqrt(0.5556)
    assert got["tracks"]["sens"] == pytest.approx(0.8944, abs=1e-3)


def test_a_task_with_no_variation_across_harnesses_is_undefined():
    """所有 harness 在这道题上一样 —— 这道题**问不出** harness 的差别,而这通常是题集里
    最该被删的东西。记成 undefined,不是 0,也不是 0.5 的"平均"。"""
    scores = _scores({
        "a": {"h1": 0.0, "h2": 1.0, "h3": 0.5},
        "b": {"h1": 1.0, "h2": 0.0, "h3": 1.0},
        "c": {"h1": 0.0, "h2": 1.0, "h3": 0.0},
        "flat": {"h1": 1.0, "h2": 1.0, "h3": 1.0},
    })
    got = {s["task_id"]: s for s in ts.analyse(scores)}
    assert got["flat"]["status"] == "undefined"
    assert got["flat"]["sens"] is None
    assert "does not vary" in got["flat"]["reason"]


def test_a_task_that_anti_tracks_quality_is_insensitive():
    """反着来的题(越好的 harness 越做不出来)也是问题,但它和"分不出"是两种问题。"""
    scores = _scores({
        "a": {"h1": 0.0, "h2": 0.0, "h3": 1.0, "h4": 1.0},
        "b": {"h1": 0.0, "h2": 1.0, "h3": 1.0, "h4": 1.0},
        "c": {"h1": 1.0, "h2": 1.0, "h3": 0.0, "h4": 0.0},
        "backwards": {"h1": 1.0, "h2": 1.0, "h3": 0.0, "h4": 0.0},
    })
    got = {s["task_id"]: s for s in ts.analyse(scores)}
    assert got["backwards"]["status"] == "insensitive"
    assert got["backwards"]["sens"] < 0


def test_an_unmeasured_cell_is_dropped_not_counted_as_zero():
    """`None` 是"平台没测到"(invalid / 崩溃 / 检查没跑),把它当 0 等于把一次网关故障
    读成"这个 harness 不行" —— 这正是 invalid 与 0 分开的整个理由。"""
    scores = _scores({
        "a": {"h1": 0.0, "h2": 1.0, "h3": None, "h4": 1.0},
        "b": {"h1": 0.0, "h2": 1.0, "h3": None, "h4": 1.0},
        "c": {"h1": 1.0, "h2": 0.0, "h3": None, "h4": 1.0},
        "d": {"h1": 0.0, "h2": 1.0, "h3": None, "h4": 0.0},
    })
    got = {s["task_id"]: s for s in ts.analyse(scores)}
    assert got["a"]["harnesses_measured"] == 3, "没测到的那一格不该进入相关"
    assert got["a"]["cells"]["h3"] is None, "报告里保留 None —— 读者要能看见缺口"


def test_too_few_harnesses_is_undefined_with_the_reason():
    """两个 harness 的"相关"恒为 ±1。说清楚是"不够算",不是"算出来是 0"。"""
    scores = _scores({"a": {"h1": 0.0, "h2": 1.0}, "b": {"h1": 0.0, "h2": 1.0},
                      "c": {"h1": 1.0, "h2": 0.0}})
    got = {s["task_id"]: s for s in ts.analyse(scores)}
    assert got["a"]["status"] == "undefined"
    assert "fewer than" in got["a"]["reason"]


# ------------------------------------------------------------ 分层切分 ---

def _stat(tid: str, perf: float, sens: float | None, status: str = "sensitive") -> dict:
    return {"task_id": tid, "perf": perf, "sens": sens, "status": status,
            "reason": "", "harnesses_measured": 4, "cells": {}}


def test_only_sensitive_tasks_are_eligible():
    stats = [_stat("easy", 0.9, 0.9), _stat("flat", 0.5, None, "undefined"),
             _stat("bad", 0.5, -0.4, "insensitive"), _stat("hard", 0.1, 0.8)]
    split = ts.stratified_split(stats, strata=2, per_stratum=2)
    kept = set(split["train"]) | set(split["eval"])
    assert kept == {"easy", "hard"}
    assert split["eligible"] == 2


def test_both_sides_draw_from_every_difficulty_band():
    """**这是分层的全部意义。** 一边全是简单题、一边全是难题的切分,会让"一边涨一边跌"
    变成两个题集的性质而不是 harness 的性质 —— Evo-Bench 的原话是两边必须 follow the same
    difficulty distribution。"""
    stats = [_stat(f"t{i}", perf=i / 10.0, sens=0.9 - i * 0.01) for i in range(9)]
    split = ts.stratified_split(stats, strata=3, per_stratum=3)
    assert len(split["strata"]) == 3
    for band in split["strata"]:
        # **保证**,不是概率:每档至少两题时,前两题一边一个。掷硬币有三成概率让某一档
        # 只喂一边,而那正是分层要防的事。
        assert band["train"] and band["eval"], f"band {band['band']} 只喂了一边"
    assert len(split["train"]) + len(split["eval"]) == 9


def test_the_split_is_recomputable_from_the_recorded_seed():
    """Evo-Bench 写的是"随机切分",而随机切分从论文里复现不出来。同一份输入 + 同一个
    seed 必须给出同一个切分,否则这个切分没有人能核对。"""
    stats = [_stat(f"t{i}", perf=i / 8.0, sens=0.5 + i * 0.05) for i in range(8)]
    first = ts.stratified_split(stats, strata=2, per_stratum=3, seed=7)
    second = ts.stratified_split(stats, strata=2, per_stratum=3, seed=7)
    other = ts.stratified_split(stats, strata=2, per_stratum=3, seed=8)
    assert first == second
    assert first["seed"] == 7
    assert (first["train"], first["eval"]) != (other["train"], other["eval"])


def test_hardest_tasks_are_cut_into_the_first_band():
    stats = [_stat("hard", 0.0, 0.9), _stat("mid", 0.5, 0.9), _stat("easy", 1.0, 0.9)]
    split = ts.stratified_split(stats, strata=1, per_stratum=3)
    assert split["strata"][0]["band"] == 0
    assert split["strata"][0]["difficulty"] == [1.0, 0.5, 0.0]


def test_an_empty_task_set_does_not_crash():
    """没有敏感题时要给出一份空切分,而不是抛异常 —— "这套题没有一道分得出 harness 好坏"
    本身就是最该被报出来的结果。"""
    split = ts.stratified_split([], strata=3, per_stratum=2)
    assert split["train"] == [] and split["eval"] == []
    assert split["eligible"] == 0 and split["tasks_total"] == 0


def test_the_table_shows_every_task_and_the_undefined_ones():
    stats = [_stat("tracks", 0.5, 0.87), _stat("flat", 0.5, None, "undefined")]
    text = ts.render_table(stats, ["base", "flash"])
    assert "tracks" in text and "flat" in text
    assert "undefined" in text and "n/a" in text
    assert "base, flash" in text
