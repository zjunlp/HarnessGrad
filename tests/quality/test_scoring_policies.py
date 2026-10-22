"""平台记录、但不执行的两条度量政策(来自 Anthropic 的 AAR 工作)。

原话:分数是各 benchmark 的**几何平均**("the lowest one binds and all must be lifted"),
以及**资格门**——"disqualifies a method, whatever its score, if the trained model's 95%
confidence interval on any capability benchmark falls entirely below the base model's"。

**为什么是记录而不是执行**:这个平台没有接受规则(`docs/framework_design.md` §1:平台记录
规则,不拥有规则),而且一个已经交出去的候选没法被"拒绝"。所以两条都算出来写在曲线点上,
方法想用就从点上读 —— 与噪声带同一种分工:数字是平台的,决定是方法的。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad import policies  # noqa: E402


# ------------------------------------------------------------- 几何平均 ---

def test_the_geometric_mean_refuses_to_trade_one_task_for_another():
    """**这就是这条政策存在的理由。** 算术平均允许"用一道题的大涨换另一道题的小跌";
    几何平均不允许 —— 最低的那一项说了算。"""
    assert policies.geomean([1.0, 1.0]) == pytest.approx(1.0)
    assert policies.geomean([1.0, 0.25]) == pytest.approx(0.5)
    # 算术平均会是 0.625;几何平均把它压到 0.5 以下
    assert policies.geomean([1.0, 0.25]) < sum([1.0, 0.25]) / 2
    assert policies.geomean([1.0, 0.5, 0.5]) == pytest.approx(0.63, abs=0.005)


def test_a_single_failed_task_binds_but_leaves_a_usable_number():
    """二值 per-task 分数下,几何平均在有题失败时就是 0 —— 那不是公式的缺陷,而是
    "最低项说了算"的极端形态。所以我们把 0 夹到一个极小的正数:仍然是"被最低项绑住",
    但结果还是个能比较的数(零和零没法比大小)。"""
    got = policies.geomean([1.0, 1.0, 0.0, 1.0])
    assert 0 < got < policies.ZERO_FLOOR ** 0.2, got
    assert got > 0


def test_an_empty_task_set_is_None_not_zero():
    """一道题都没测到不是"得了 0 分" —— 和 `invalid` 与 0 的区分是同一条规则。"""
    assert policies.geomean([]) is None
    assert policies.geomean(["x", None]) is None


def test_a_negative_score_is_refused_loudly():
    """负分的对数在 `math` 里会变成复数并在深处炸掉;在这里说清楚。"""
    with pytest.raises(ValueError, match="negative"):
        policies.geomean([1.0, -0.1])


# --------------------------------------------------------------- 资格门 ---

def test_a_task_below_the_baseline_is_a_violation():
    got = policies.floor_violations({"a": 0.0, "b": 1.0}, {"a": 1.0, "b": 1.0})
    assert [v["task_id"] for v in got["violations"]] == ["a"]
    assert got["violations"][0]["gap"] == -1.0
    assert got["checked"] == 2 and got["count"] == 1
    assert got["violations"][0]["ci_entirely_below"] is None, "一次采样答不了这个问题"


def test_the_interval_criterion_needs_a_spread_and_is_otherwise_unknown():
    """Anthropic 的判据是"区间整段低于基座"。一次采样时诚实的答案是
    `null`(问不出这个问题),不是一个读起来像"及格"的 `false`。"""
    cand = {"a": 0.0}
    base = {"a": 1.0}
    tight = policies.floor_violations(cand, base, candidate_std={"a": 0.01},
                                      baseline_std={"a": 0.01})
    assert tight["violations"][0]["ci_entirely_below"] is True
    assert tight["significant"] == 1 and tight["ci_available"] is True
    wide = policies.floor_violations(cand, base, candidate_std={"a": 0.6},
                                     baseline_std={"a": 0.01})
    assert wide["violations"][0]["ci_entirely_below"] is False
    assert wide["significant"] == 0


def test_the_baseline_wins_ties_and_tolerance_suppresses_small_gaps():
    assert policies.floor_violations({"a": 1.0}, {"a": 1.0})["count"] == 0
    assert policies.floor_violations({"a": 1.0}, {"a": 1.0})["checked"] == 1
    assert policies.floor_violations({"a": 0.95}, {"a": 1.0},
                                     tolerance=0.1)["count"] == 0
    assert policies.floor_violations({"a": 0.95}, {"a": 1.0},
                                     tolerance=0.0)["count"] == 1


def test_a_task_the_baseline_never_had_is_not_checked():
    got = policies.floor_violations({"a": 0.0, "new": 0.0}, {"a": 1.0})
    assert got["checked"] == 1 and [v["task_id"] for v in got["violations"]] == ["a"]


# ---------------------------------------- 诊断侧的选择:平台与方法的副本 ---

TRAINING = {"score_kind": "training", "per_task": {"t": 0.5}, "score": 0.5}
EXAM = {"score_kind": "exam", "per_task": {"t": 0.9}, "train_per_task": {"t": 0.25},
        "split": {"train": ["t"], "eval": ["t"]}}
OLD = {"score": 0.25, "train_score": 0.25}          # 没有 score_kind 的老记录


def test_the_platform_reads_the_studied_side_and_never_the_exam():
    assert policies.studied_per_task(TRAINING) == {"t": 0.5}
    assert policies.studied_per_task(EXAM) == {"t": 0.25}, "读了考试侧"
    assert policies.studied_per_task(OLD) == {}


def test_a_split_with_no_diagnostic_side_returns_nothing_rather_than_the_exam():
    point = {"score_kind": "exam", "per_task": {"t": 0.9},
             "split": {"train": ["t"], "eval": ["t"]}}
    assert policies.studied_per_task(point) == {}


def test_the_platform_copy_agrees_with_the_method_copy():
    """一条规则两份实现是代价(`methods/protocol.py` 是方法侧那份,沙箱里 import 不到平台)。
    代价的缓解就是这条断言:两份必须对同一批形状给同一个答案,否则"方法看到的"和
    "平台记录的"会分叉。"""
    spec = importlib.util.spec_from_file_location("mprotocol", ROOT / "methods" / "protocol.py")
    mprotocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mprotocol)
    for point in (TRAINING, EXAM, OLD,
                  {"score_kind": "training", "per_task": {}},
                  {"score": 0.9, "train_score": 0.1, "split": {"train": ["t"]}}):
        assert policies.studied_per_task(point) == mprotocol.studied_per_task(point), point


# --------------------------------------------------------- 端到端接线 ---

def test_a_candidate_round_carries_both_policies_and_h0_carries_neither(tmp_path):
    """**接线测试。** H0 是基线本身,不可能"低于基线",所以它不该带这两个字段;
    候选轮两个字段都要在。"""
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "policies", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/echo_base/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-600:]}\n{proc.stderr[-600:]}"
    points = [json.loads(l) for l in
              (runs / "policies" / "curve.jsonl").read_text().splitlines() if l.strip()]
    assert len(points) == 2, points
    assert "capability_floor" not in points[0] and "score_geomean" not in points[0]
    assert "improvement" not in points[0], points[0]
    candidate = points[1]
    assert candidate["capability_floor"]["baseline"] == "round-0"
    assert candidate["capability_floor"]["checked"] == len(candidate["per_task"])
    assert isinstance(candidate["score_geomean"], float)
    # `echo_base` hands back the same harness, so no task was discordant and the honest
    # answer is "the question could not be asked" -- not a `false` that reads like a pass,
    # and not an absent field.
    assert candidate["improvement"]["baseline"] == "round-0", candidate["improvement"]
    assert candidate["improvement"]["gained"] == 0
    assert candidate["improvement"]["lost"] == 0
    assert candidate["improvement"]["direction"] == "unchanged"
    assert candidate["improvement"]["significant"] is False


# ------------------------------------------------- 配对改进统计(floor 的镜像) ---

def _pair(n, gained, lost, rate=0.47):
    """n 道题、基线通过率 `rate`:`gained` 道 0->1,`lost` 道 1->0,其余不变。

    默认 `rate=0.47` 是 alfworld 那个池子的形状(134 题、均值 0.470)。两组必须取自不同
    的基线值 —— 第一版把两组都放进 base=0 那半,于是"退步"根本没发生,而且池子均值只有
    0.0075,和"边际区间看不见这个翻转"那条测试的前提完全不符。
    """
    ones = round(n * rate)
    base = {f"t{i}": (1.0 if i < ones else 0.0) for i in range(n)}
    cand = dict(base)
    for i in range(ones, ones + gained):          # 0 -> 1
        cand[f"t{i}"] = 1.0
    for i in range(lost):                         # 1 -> 0
        cand[f"t{i}"] = 0.0
    return cand, base


def test_a_paired_gain_is_detected_where_the_marginal_interval_is_not():
    """**这条就是它存在的理由。** 边际区间是按题算的,所以对同一批题重跑不会缩小它。

    alfworld 的批量是 134 题、均值约 0.47,`score_ci95` 宽度约 0.17 —— 比一次 harness 编辑
    能带来的效应还大。但同一批题在 H0/H1 之间是**配对**的,而 8 比 1 的翻转在符号检验下
    p≈0.039。所以这条测试同时断言两件事:配对检验显著,而边际区间仍然包含基线均值。
    如果有人把这个函数改成比较两个边际均值,第二条会失败。
    """
    from eval.run.metrics import ci95  # noqa: PLC0415

    cand, base = _pair(134, gained=8, lost=1)          # 8 道 0->1,1 道 1->0
    got = policies.improvement(cand, base)
    assert (got["gained"], got["lost"]) == (8, 1)
    assert got["significant"] is True, got
    assert got["direction"] == "better", got

    lo, hi = ci95(list(cand.values()))                 # the marginal reading, same data
    base_mean = sum(base.values()) / len(base)
    assert lo <= base_mean <= hi, (
        f"the marginal interval [{lo:.3f}, {hi:.3f}] no longer contains the baseline mean "
        f"{base_mean:.3f}; this test's premise is that it cannot see an 8-vs-1 flip")


def test_a_small_asymmetry_is_not_called_an_improvement():
    """5 比 1 在符号检验下 p≈0.22 —— 不显著。测试要能拒绝,否则它只是装饰。"""
    cand, base = _pair(134, gained=5, lost=1)
    got = policies.improvement(cand, base)
    assert got["net"] == 4 and got["direction"] == "better"
    assert got["significant"] is False, got


def test_a_significant_regression_is_not_an_improvement():
    """方向与显著性是两个字段,不能合成一个。退步也会"显著"。"""
    cand, base = _pair(134, gained=0, lost=8)
    got = policies.improvement(cand, base)
    assert got["significant"] is True
    assert got["direction"] == "worse", got
    # 读法是合取,不是单独看 significant。
    assert not (got["significant"] and got["direction"] == "better")


def test_no_discordant_pair_says_the_question_could_not_be_asked():
    """没有翻转 = 没问到问题,不是"没有改进"。与 `ci_entirely_below: None` 同一种诚实。"""
    base = {"a": 1.0, "b": 0.0, "c": 0.5}
    got = policies.improvement(dict(base), base)
    assert got["p_value"] == 1.0 and got["significant"] is False
    assert got["direction"] == "unchanged" and got["net"] == 0


def test_only_tasks_present_on_both_sides_are_compared():
    """配对的前提是同一个任务。只在一侧出现的题不能进分母 —— 否则 `checked` 说谎。"""
    got = policies.improvement({"a": 1.0, "b": 1.0}, {"a": 0.0, "c": 1.0})
    assert got["checked"] == 1, got
    assert got["gained_ids"] == ["a"] and got["lost_ids"] == []
    assert got["net_rate"] == 1.0


def test_the_test_ignores_magnitude_but_the_rate_does_not():
    """符号检验数"对",net_rate 数"量"。两者都必须报,读者不该被迫二选一。"""
    base = {"big": 1.0, "small": 0.0}
    cand = {"big": 0.0, "small": 0.1}          # 一个大幅退步,一个微小进步
    got = policies.improvement(cand, base)
    assert got["gained"] == 1 and got["lost"] == 1
    assert got["direction"] == "unchanged"     # the sign test sees one each way
    assert got["net_rate"] < 0, got            # the magnitude sees a loss


# ------------------------------------------------- 冻结参考点:漂移检查 ---

def _point(*, agent_model="m", agent_backend="openai", harness_name="loop",
           framework_version="0.1.0", harness_sha="aaa",
           tasks=("a", "b"), score=0.5):
    """A curve point, with the parameters named the way `REFERENCE_FIXED` names them.

    The first version used short names (`model`, `sha`) and the parametrized drift tests then
    passed `agent_model=...`, which is a `TypeError` rather than a test result -- four tests
    failed for a reason that had nothing to do with the code under test.
    """
    return {"run_id": "r", "round": 0, "score": score,
            "identity": {"agent_model": agent_model, "agent_backend": agent_backend,
                         "harness_name": harness_name,
                         "framework_version": framework_version,
                         "harness_sha": harness_sha},
            "per_task": {t: score for t in tasks}}


def test_editing_the_harness_is_not_drift():
    """**`harness_sha` 必须允许不同** —— 那正是方法改的东西。

    把它放进必须一致的字段里,等于拒绝每一个真实实验。要一致的是"被测对象之外"的那些:
    模型、后端、harness 家族、平台版本。
    """
    assert policies.reference_problems(_point(harness_sha="aaa"), _point(harness_sha="bbb")) == []


@pytest.mark.parametrize("field,value", [
    ("agent_model", "another-model"),
    ("agent_backend", "mock"),
    ("harness_name", "other_harness"),
    ("framework_version", "0.2.0"),
])
def test_a_changed_fixed_field_is_named(field, value):
    ref, cur = _point(), _point(**{field: value})
    problems = policies.reference_problems(ref, cur)
    assert len(problems) == 1 and field in problems[0], problems


def test_contention_is_compared_when_both_sides_record_it():
    """`jobs`/`trials` 决定数字是在什么竞争下产生的,分数里看不出来。

    冻结参考多为这些字段问世之前测的,所以"参考里没有"必须与"两边不同"区分开:前者报
    unknown,后者才是 mismatch。否则每一条比较都会永久报一条问题,读者就会学会跳过这张表。
    """
    ref = _point()
    ref["identity"].update({"jobs": 40, "trials": 1})
    cur = _point()
    cur["identity"].update({"jobs": 6, "trials": 1})
    problems = policies.reference_problems(ref, cur)
    assert len(problems) == 1 and "jobs changed" in problems[0], problems
    # 行数一致、只有 trials 不同时,也要被抓到。
    cur["identity"]["jobs"] = 40
    assert policies.reference_problems(ref, cur) == []
    cur["identity"]["trials"] = 3
    problems = policies.reference_problems(ref, cur)
    assert len(problems) == 1 and "trials changed" in problems[0], problems


def test_a_reference_that_predates_the_field_is_unknown_not_wrong():
    """老参考没有 `jobs` 键: 这**不是**不一致, 是不知道。"""
    cur = _point()
    cur["identity"].update({"jobs": 6, "trials": 1})
    problems = policies.reference_problems(_point(), cur)
    assert len(problems) == 2, problems
    assert all("not recorded on the reference" in p and "unknown" in p.lower()
               or "not known to agree" in p for p in problems), problems


def test_a_changed_task_set_is_reported_with_counts():
    """**这条是最会静默出错的一条。** `improvement` 在交集上配对,所以题目集合不同不会报错,
    它只会让比较悄悄变小,然后给出一个关于幸存者的 p 值。"""
    problems = policies.reference_problems(_point(tasks=("a", "b", "c")),
                                           _point(tasks=("a", "b")))
    assert len(problems) == 1 and "1 dropped" in problems[0] and "2 shared" in problems[0]


def test_a_disjoint_task_set_says_so():
    problems = policies.reference_problems(_point(tasks=("a",)), _point(tasks=("z",)))
    assert any("share no task" in p for p in problems), problems


def test_a_reference_that_still_describes_the_run_has_no_problems():
    assert policies.reference_problems(_point(), _point()) == []


# --------------------------------------------- 冻结参考点:端到端接线 ---

def test_a_frozen_reference_is_honoured_and_recorded(tmp_path):
    """`--baseline` 把基线从"本次 run 的 round 0"换成一份**冻结参考点**,并把它记在点上。

    声明而不是推断:不给 `--baseline` 时基线是本 run 的 round 0(这是**一个**方法的对照,
    对其它每个方法都是**不同的**参考)。冻结参考让所有方法站在一根轴上 —— 代价是没人再测
    它,所以它必须被检查。
    """
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock"}

    def run(run_id, extra):
        proc = subprocess.run(
            [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
             "--rounds", "1", "--run-id", run_id, "--dataset", "demo",
             "--sampling", "all", "--runs-root", str(runs),
             "--work-root", str(tmp_path / ("work-" + run_id)),
             "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/echo_base/run.py'}",
             *extra],
            cwd=ROOT, capture_output=True, text=True, timeout=900, env=env)
        assert proc.returncode == 0, f"{proc.stdout[-600:]}\n{proc.stderr[-600:]}"
        return [json.loads(l) for l in
                (runs / run_id / "curve.jsonl").read_text().splitlines() if l.strip()]

    first = run("ref-run", [])
    # Without `--baseline` the reference is this run's own round 0, and the point says so
    # rather than staying silent: the field is how a reader tells the two kinds apart.
    assert first[1]["baseline"]["kind"] == "same-run", first[1]["baseline"]
    assert first[1]["baseline"]["round"] == 0, first[1]["baseline"]

    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps(first[0]))

    second = run("frozen-run", ["--baseline", str(frozen)])
    got = second[1]["baseline"]
    assert got["kind"] == "frozen", got
    assert got["run_id"] == "ref-run", got
    assert got["problems"] == [], got
    # Both statistics still name their baseline, and the paired test still ran.
    assert second[1]["improvement"]["baseline"].startswith("round-")
    assert second[1]["improvement"]["direction"] in ("better", "worse", "unchanged")
