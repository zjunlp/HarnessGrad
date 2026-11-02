"""区间解释的输入：读哪些事实，以及在什么情况下**拒绝**回答。

这一条路径的价值全在"拒绝"上：把两轮比较说成"趋势"、把证据不足说成"收敛"，都是读者
无法从文字本身发现的那种错误。所以测试集中在三条拒绝：区间反了、区间里少于两轮、
轮次不在曲线里。

不调用模型：`build_explain_range_inputs` 只读 run 目录。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server                                                            # noqa: E402


def make_run(tmp_path: Path, rounds: list[int], *, with_patch: bool = True) -> Path:
    run = tmp_path / "run-1"
    (run / "diffs").mkdir(parents=True)
    curve = []
    for n in rounds:
        curve.append({"round": n, "score": 0.1 * n, "label": f"r{n}",
                      "identity": {"harness_sha": f"sha{n}"}, "n_scored": 3})
        (run / "diffs" / f"round-{n}.json").write_text(json.dumps(
            {"files": [{"path": "agent.py", "added": n, "removed": 1}]}), encoding="utf-8")
        if with_patch:
            (run / "diffs" / f"round-{n}.patch").write_text(f"@@ -1 +1 @@\n-r{n}\n+r{n + 1}\n",
                                                            encoding="utf-8")
    (run / "curve.jsonl").write_text(
        "\n".join(json.dumps(row) for row in curve) + "\n", encoding="utf-8")
    return run


def test_a_span_carries_every_round_and_the_two_end_patches(tmp_path):
    run = make_run(tmp_path, [0, 1, 2])
    inputs = server.build_explain_range_inputs(run, "run-1", 0, 2)
    assert [row["round"] for row in inputs["rows"]] == [0, 1, 2]
    assert [row["added"] for row in inputs["rows"]] == [0, 1, 2]
    assert set(inputs["patches"]) == {0, 2}, "只看首尾两轮的 patch，中间只给统计"
    system, user = server.explain_range_prompt(inputs)
    assert "走向" in system and "r0 → r2" in user


def test_an_inverted_span_is_refused(tmp_path):
    run = make_run(tmp_path, [0, 1])
    with pytest.raises(ValueError, match="from 必须小于 to"):
        server.build_explain_range_inputs(run, "run-1", 1, 1)


# 说明：`build_explain_range_inputs` 里还有一条"区间内少于两轮"的拒绝。两端都必须在
# 曲线里（否则先被上面的检查拒绝），而 from < to 保证至少两行 —— 所以那条分支对合法输入
# 不可达，只是防御。这里不为它写测试：测一条到不了的路径，只会让人以为它被用到了。


def test_a_round_outside_the_curve_is_named(tmp_path):
    run = make_run(tmp_path, [0, 1, 2])
    with pytest.raises(ValueError, match=r"round \[9\]"):
        server.build_explain_range_inputs(run, "run-1", 0, 9)


def test_a_span_without_patches_still_reports_scores(tmp_path):
    """早期 run 可能只有曲线没有 diff；这时趋势仍可读，只是没有 patch 片段。"""
    run = make_run(tmp_path, [0, 1, 2], with_patch=False)
    inputs = server.build_explain_range_inputs(run, "run-1", 0, 2)
    assert inputs["patches"] == {}
    assert len(inputs["rows"]) == 3
