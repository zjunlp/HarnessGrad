"""方法读哪个分数:一次运行只关于一侧,所以「诊断侧」不再是一个固定的字段名。

`INTERFACE.md` §2.6 把一次运行收窄到一侧之后:

    训练运行的曲线点   score_kind="training"   score **就是**诊断分
                                              train_score 按设计不存在(单侧运行里
                                              它会是 per_task 的副本)
    切分之前的曲线点   没有 score_kind         train_score 是诊断分,score 是考试

实测到的故障:`loop` + `terminal_bench` + `rrsi`,曲线点里
`train_scores: [null, null, null]`,`S_star: null`,`rule_evaluated: false` ——
规则从未触发,每一轮都报 "no edit"。而这个读数**和一只崩溃的 harness 完全一样**,
于是用户看到的是「方法决定不改」,不是「harness 根本没起来」。

六个方法各自写了一遍这条规则,其中两个有兜底、四个没有 —— 规则的两种写法不一致,
就是这条规则被复制了六份的代价。所以规则现在只在 `protocol` 里写一次。

这里钉住三件事:
  * 训练点上读 `score`,并且**绝不**在没有训练标记时读它(那是考试)
  * 没有 `score_kind` 的旧记录仍然读 `train_score`
  * 六个方法真的走的是同一条规则,而不是各自的副本
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))

import protocol                                                # noqa: E402

#: 一个训练运行的曲线点:这是 `harnessgrad/records.py:_curve_point` 在 `side="train"` 上写出的形态。
TRAINING = {
    "run_id": "loop-terminal_bench-90815", "round": 1,
    "side": "train", "score_kind": "training",
    "score": 0.25, "per_task": {"t01": 0.0, "t02": 1.0},
    # 这两个字段在 train 运行上是空的,而且**存在**(schema 为旧记录保留)
    "train_score": None, "train_per_task": {},
    "split": {"train": ["t01", "t02"], "eval": ["e01", "e02"]},
}

#: 一次考试运行的点:`score` 是考试,方法绝不能读它。
EXAM = {
    "run_id": "loop-terminal_bench-eval", "round": 0,
    "side": "eval", "score_kind": "exam",
    "score": 0.9, "per_task": {"e01": 1.0, "e02": 0.8},
    "train_score": 0.25, "train_per_task": {"t01": 0.0, "t02": 1.0},
    "split": {"train": ["t01", "t02"], "eval": ["e01", "e02"]},
}

#: 切分之前的记录:没有 `score_kind`,两个分数都在。
LEGACY = {
    "run_id": "old", "round": 2,
    "score": 0.9, "per_task": {"e01": 1.0},
    "train_score": 0.25, "train_per_task": {"t01": 0.0, "t02": 1.0},
    "split": {"train": ["t01", "t02"], "eval": ["e01"]},
}


# ------------------------------------------------------------ 规则本身 ---

def test_a_training_point_is_read_through_score():
    """训练运行的诊断分在 `score` 里,不在 `train_score` 里。

    读 `train_score` 会拿到 None,于是规则静默失效 —— 这正是那次运行发生的事。
    """
    assert protocol.studied_score(TRAINING) == 0.25


def test_an_exam_point_never_lends_its_score():
    """`score_kind="exam"` 时 `score` 是考试,方法只能读诊断侧的 `train_score`。

    这是 §2.3 那条泄漏防线:在考试上选候选,等于针对评分用的题目做拟合。
    """
    assert protocol.studied_score(EXAM) == 0.25, \
        "读了考试分 —— 那正是切分要防的泄漏"


def test_a_record_without_score_kind_still_reads_train_score():
    """没有 `score_kind` 的是 §2.6 之前的记录,那时 `train_score` 才是诊断分。

    默认值必须是**安全**的那一侧:把未知当成训练会让旧记录的考试分被读出来。
    """
    assert protocol.studied_score(LEGACY) == 0.25
    assert protocol.studied_score({"score": 0.9, "per_task": {"e": 1.0}}) is None, \
        "没有诊断分时应当返回 None,而不是退回考试分"


def test_per_task_follows_the_same_branch():
    assert protocol.studied_per_task(TRAINING) == {"t01": 0.0, "t02": 1.0}
    assert protocol.studied_per_task(EXAM) == {"t01": 0.0, "t02": 1.0}
    assert protocol.studied_task_ids(TRAINING) == ["t01", "t02"]


def test_a_point_with_a_split_and_no_diagnostic_side_returns_nothing():
    """有 split、却没有诊断侧记录的:宁可什么都不给,也不给考试的那一侧。"""
    partial = {"score_kind": "exam", "score": 0.9, "per_task": {"e01": 1.0},
               "train_per_task": {}, "split": {"train": ["t01"], "eval": ["e01"]}}
    assert protocol.studied_per_task(partial) == {}
    assert protocol.studied_score(partial) is None


def test_a_dataset_with_no_split_has_only_one_measurement():
    """没有 split 时 `per_task` 就是那唯一一次测量,退回它是正确的。"""
    nosplit = {"score": 0.5, "per_task": {"a": 0.0, "b": 1.0}}
    assert protocol.studied_per_task(nosplit) == {"a": 0.0, "b": 1.0}


# -------------------------------------------------- 六个方法走同一条规则 ---

#: 诊断侧的字段名。方法不该自己读它们 —— 该由 `protocol` 从 `score_kind` 判断。
DIAGNOSTIC_FIELDS = {"train_score", "train_per_task"}


def _direct_reads(path: Path) -> list[tuple[int, str]]:
    """真正的**取值**:`point.get("train_score")` 或 `point["train_score"]`。

    走 AST 而不是逐行扫文本,因为这两个名字在注释、文档字符串和返回给平台的
    `score_source` 字段里是**正当出现**的 —— 把它们一起判为违规,测试就只好靠
    「这一行像不像代码」来猜,而那种猜测会在下一次有人换行时失效。
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and arg.value in DIAGNOSTIC_FIELDS:
                hits.append((node.lineno, str(arg.value)))
        elif isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in DIAGNOSTIC_FIELDS:
                hits.append((node.lineno, str(key.value)))
    return sorted(hits)


@pytest.mark.parametrize("name", sorted(
    p.parent.name for p in (ROOT / "methods").glob("*/run.py")))
def test_no_method_reads_the_diagnostic_field_directly(name):
    """方法不得自己去读 `train_score` / `train_per_task`。

    这条规则被复制过六份,而它变过一次 —— 结果两个方法跟着变了、四个没有。
    源码级的检查是对这个具体故障唯一有效的守门方式:每个方法都是一个独立进程,
    没有一条运行时路径能把所有方法都覆盖到。
    """
    hits = _direct_reads(ROOT / "methods" / name / "run.py")
    assert not hits, (
        f"methods/{name}/run.py 直接读了诊断侧的字段名,应当走 "
        f"protocol.studied_score / studied_per_task: {hits}")


def test_the_shared_editor_does_not_read_them_either():
    """共用的编辑器也一样 —— 它被每个方法用来排「先看哪些题」。"""
    hits = _direct_reads(ROOT / "methods" / "editor.py")
    assert not hits, (
        f"methods/editor.py 直接读了诊断侧的字段名: {hits};它有一份规则副本,"
        "而且那份副本会在规则下一次变化时再次跑偏")


def test_editor_uses_the_shared_rule():
    """共用的编辑器也一样 —— 它被每个方法用来排「先看哪些题」。"""
    src = (ROOT / "methods" / "editor.py").read_text(encoding="utf-8")
    assert "protocol.studied_per_task(point)" in src


def _load(name: str):
    """把一个方法模块真加载进来,调它自己的函数。

    比「在子进程里重跑一遍 protocol」强的地方:这里测的是**这个方法的**规则,
    包括它有没有真的接上共用那份。两个模块都是 import 安全的(`main()` 在
    `if __name__` 之下)。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / "methods" / name / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rrsi_reads_the_training_score_on_a_training_point():
    """那次运行的直接回归。修复前 `_train_score` 恒为 None → S_star 恒为 null →
    每一轮 "no edit",而那只 harness 其实一步都没跑起来。"""
    rrsi = _load("rrsi")
    assert rrsi._train_score(TRAINING) == 0.25
    assert rrsi._train_score(EXAM) == 0.25, "读了考试分"
    assert rrsi._train_score({"score_kind": "exam", "score": 0.9}) is None


def test_ahe_attributes_on_a_training_point():
    """修复前 `observed_diff` 在训练点上永远得到 `shared == []`,于是归因静默地
    报告「什么都没有变」—— 一个看起来像结论的空结果。"""
    ahe = _load("ahe")
    before = dict(TRAINING, score=0.0, per_task={"t01": 0.0, "t02": 0.0})
    after = dict(TRAINING, score=0.5, per_task={"t01": 1.0, "t02": 0.0})
    diff = ahe.observed_diff(before, after)
    assert diff["compared"] == ["t01", "t02"], diff
    assert diff["flipped"] == ["t01"], diff
    assert diff["stable_fail"] == ["t02"], diff


def test_ahe_does_not_attribute_from_the_exam_side():
    """考试点上的 `per_task` 是考试题,不能拿来归因。"""
    ahe = _load("ahe")
    before = {"score_kind": "exam", "score": 0.9, "per_task": {"e01": 0.0},
              "train_per_task": {"t01": 0.0}, "train_score": 0.0,
              "split": {"train": ["t01"], "eval": ["e01"]}}
    after = dict(before, score=1.0, per_task={"e01": 1.0},
                 train_per_task={"t01": 0.0}, train_score=0.0)
    diff = ahe.observed_diff(before, after)
    assert diff["compared"] == ["t01"], diff
    assert diff["flipped"] == [], "从考试题上做出了归因"


if __name__ == "__main__":                                       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
