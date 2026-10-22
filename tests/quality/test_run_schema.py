"""一个 run 目录必须能被程序判定是否可用。

## 为什么

实测(2026-10-05):82 个 run 目录,**没有一个**记录"是哪份代码产出了这些数字",也没有任何
程序能回答"这个 run 能不能引用"。要回答只能人工读。而那一天里有两个 run **根本不是测量**
——一个是我在 driver 跑着的时候改了 `eval/` 文件,另一个的"提升"来自一个在行为上不可能有
差别的候选 —— 它们在磁盘上和有效的 run 长得一模一样。

另一条同源:曲线点有 **38 个可能出现过的字段,其中只有 3 个出现在每个点上**。所以"字段缺失
因为代码路径没设"、"字段缺失因为这个点早于该字段"、"字段在但为 null"这三种不同的**事实**
有同一个写法:什么都没有。这正是那个假 `+0.100` 能被读成提升的原因。

这两条是同一类缺陷:**记录报告了结论,却没有任何东西说明这个结论能不能用。**

## 这一层解决的问题

* `check_point` / `check_run_meta`:声明字段。缺一个**新增于某日**的字段是**提示**,不是错误
  —— 一个把所有旧 run 都判失败的校验器没人会用。
* `certify`:读 `curve.jsonl` + `run_meta.json`,回答"这个 run 是不是一次测量"。
  两级:`valid`(能读)和 `certified`(并且记录指明了产出它的平台)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad.store import certify, check_point, check_run_meta  # noqa: E402


def _minimal_point(**over):
    point = {
        "run_id": "t", "round": 0,
        "identity": {"harness_sha": "abc"}, "score": 0.5, "score_kind": "training",
        "side": "train", "per_task": {"a": 0.5}, "n_scored": 1,
        "env": {}, "sampling": {}, "cost": {}, "timestamp_utc": "2026-10-05T00:00:00Z",
        "measured_by_platform": True,
    }
    point.update(over)
    return point


def test_a_complete_point_has_no_problems():
    problems, _ = check_point(_minimal_point())
    assert problems == [], problems


def test_a_point_the_method_scored_itself_is_not_a_measurement():
    """方法自己报的分不是测量 —— 平台的整个存在理由就是判分不可委托。"""
    problems, _ = check_point(_minimal_point(measured_by_platform=False))
    assert any("measured_by_platform" in p for p in problems), problems
    problems, _ = check_point(_minimal_point(measured_by_platform=None))
    assert any("measured_by_platform" in p for p in problems), problems


def test_a_point_that_cannot_say_which_harness_it_scored_is_a_problem():
    problems, _ = check_point(_minimal_point(identity={}))
    assert any("harness_sha" in p for p in problems), problems
    problems, _ = check_point(_minimal_point(identity=None))
    assert any("identity" in p for p in problems), problems


def test_a_point_without_the_split_cannot_be_read():
    """没有 `side` 就不知道这个分是 train 的还是 exam 的 —— 而这两种数不能混用。"""
    p = _minimal_point()
    del p["side"]
    problems, _ = check_point(p)
    assert any("side" in x for x in problems), problems


def test_a_field_added_later_is_a_note_not_a_problem():
    """**旧 run 不是坏 run。** 一个把所有历史记录判失败的校验器没人会用。

    这正是模式要挡住的第三种事实:"缺失因为早于该字段"必须读得出来。
    """
    old = _minimal_point()          # 没有任何 stage 0/1 之后新增的字段
    problems, notes = check_point(old)
    assert problems == [], problems
    assert any("test_rate" in n for n in notes), notes
    assert any("added 2026-10-05" in n for n in notes), notes


def test_a_wrong_type_is_a_problem():
    problems, _ = check_point(_minimal_point(score="0.5"))
    assert any("expected int/float" in p for p in problems), problems
    problems, _ = check_point(_minimal_point(round="0"))
    assert any("`round` is str" in p for p in problems), problems


# ------------------------------------------------------------- certify ---

def _write_run(root: Path, *, points, meta, name="r"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "curve.jsonl").write_text(
        "\n".join(json.dumps(p) for p in points) + "\n")
    (d / "run_meta.json").write_text(json.dumps(meta))
    return d


def _meta(**over):
    meta = {"run_id": "r", "agent_model": "m", "agent_backend": "mock", "harness": "loop",
            "dataset": "demo", "workspace": "/tmp/w",
            "platform": {"content_sha": "deadbeef"}}
    meta.update(over)
    return meta


def test_a_run_that_names_its_platform_is_certified(tmp_path):
    d = _write_run(tmp_path, points=[_minimal_point()], meta=_meta())
    got = certify(d)
    assert got["valid"] and got["certified"], got
    assert got["platform_content_sha"] == "deadbeef"


def test_a_run_that_does_not_name_its_platform_is_valid_but_not_certified(tmp_path):
    """**这就是那 82 个 run 的处境。** 能读,但没法和别的代码产出的数比较。

    不是"错误",是"不能用它做跨 run 的结论"。校验器必须把这两件事分开说。
    """
    d = _write_run(tmp_path, points=[_minimal_point()], meta=_meta(platform=None))
    got = certify(d)
    assert got["valid"], got
    assert not got["certified"]
    assert any("does not name the platform" in n for n in got["notes"]), got["notes"]


def test_a_run_with_no_curve_is_not_a_measurement(tmp_path):
    d = tmp_path / "empty"
    (d / "run_meta.json").parent.mkdir(parents=True, exist_ok=True)
    (d / "run_meta.json").write_text(json.dumps(_meta()))
    got = certify(d)
    assert not got["valid"]
    assert any("nothing was measured" in p for p in got["problems"]), got["problems"]


def test_a_tampered_run_is_reported_and_never_certified(tmp_path):
    """实测:`eval/` 在 driver 跑着的时候被改过,那一轮 run 作废。

    在磁盘上它和有效的 run 没有区别 —— 除非校验器去看 `tampered`。
    """
    point = _minimal_point(tampered={"modified": ["eval/run/runner.py"]})
    d = _write_run(tmp_path, points=[point], meta=_meta())
    got = certify(d)
    assert got["tampered"], got
    assert not got["certified"]
    assert any("eval/run/runner.py" in t for t in got["tampered"])


def test_a_startup_failure_is_not_silently_a_low_score(tmp_path):
    """harness 从没启动过,和 harness 弱,必须是两件事(`v5` 的教训)。"""
    point = _minimal_point(startup_failure={"reason": "harness left no trace",
                                            "not_run": ["a", "b"]})
    d = _write_run(tmp_path, points=[point], meta=_meta())
    got = certify(d)
    assert not got["valid"], got
    assert any("startup_failure" in p for p in got["problems"]), got["problems"]


def test_the_validator_runs_as_a_command_on_a_real_run(tmp_path):
    """命令行必须能用 —— 不能只在 import 时有意义。"""
    d = _write_run(tmp_path, points=[_minimal_point()], meta=_meta())
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_run.py"), str(d)],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "certified" in proc.stdout

    # 一个不可用的 run 必须返回非零,否则它在 CI 里没有意义
    bad = _write_run(tmp_path, points=[_minimal_point(measured_by_platform=False)],
                     meta=_meta(), name="bad")
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_run.py"), str(bad)],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)


def test_a_real_run_records_the_platform_that_produced_it():
    """`smoke-stage1` 是**项目历史上第一个 certified 的 run**(对照:82 个里 0 个)。

    这条测试盯的是机制:平台内容哈希由**写记录的那一层**盖上去,不由调用方传 ——
    两个调用点都能忘,而忘了的记录和"另一份代码产出的记录"长得一样。
    """
    run = ROOT / "runs" / "smoke-stage1"
    if not (run / "run_meta.json").is_file():
        import pytest
        pytest.skip("smoke-stage1 不在这台机器上")
    meta = json.loads((run / "run_meta.json").read_text())
    platform = meta.get("platform") or {}
    assert len(platform.get("content_sha") or "") == 64, platform
    assert platform.get("git_sha"), platform
    assert isinstance(platform.get("dirty"), bool), platform


def test_the_declared_acceptance_rule_reaches_the_run_record(tmp_path):
    """**写在 `provenance` 里,读在顶层 —— 于是它哪儿都没到。**

    `methods/editor.py:report` 把方法声明的规则写在
    `trajectory["provenance"]["acceptance_rule"] = {"text", "source", "calibrated"}`,
    而 `harnessgrad/loops/mode_a.py` 读的是 `trajectory["acceptance_rule"]`。所以那个键
    从来没被找到过,`run_meta.acceptance_rule` **没有任何东西在写** —— 而且轨迹住在轮末
    就被删掉的 scratch 目录里,所以方法声明的规则**没有到达任何持久记录**。

    实测(2026-10-06):`grep -l acceptance_rule runs/*/run_meta.json` 在 84 个 run 上是空的,
    包括那些方法确实声明了的。这条测试钉住两处都读。

    用真的 driver 跑一个 stub 方法,因为要验的正是"生产者写的形状能被消费者读到" ——
    直接调用 mode_a 的内部循环会把被测的那段代码排除在外。
    """
    method = tmp_path / "rule_stub.py"
    method.write_text('''
import json, os, shutil, sys
from pathlib import Path
req = json.loads(sys.stdin.read())
base = Path(req["base_harness"]); ws = Path(req["workspace"])
dest = ws / "cand"
if dest.exists(): shutil.rmtree(dest)
shutil.copytree(base, dest)
(dest / "agent.py").write_text((dest / "agent.py").read_text() + "\\n# stub\\n")
Path(req["trajectory_out"]).write_text(json.dumps({
    "steps": [{"harness_dir": str(dest), "label": "stub", "edit_kind": "harness_source",
               "claimed_cost": {"generation_tokens": 0},
               "method_reported": {"score": None}}],
    "trajectory_shape": "sequence", "nominated": 0,
    # **这个形状就是 producer 的形状。** 规则嵌在 provenance 里,带着它的来源和
    # 是否经过校准 —— 不是顶层的一个裸字符串。
    "provenance": {"method": "rule_stub",
                   "acceptance_rule": {"text": "accept if the score does not fall",
                                       "source": "methods/rule_stub.py",
                                       "calibrated": False}},
}))
sys.stdout.write(json.dumps({"changed": True, "stop": True,
                             "generation_tokens": 0}))
''', encoding="utf-8")
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "rule-record", "--dataset", "demo",
         "--sampling", "all", "--side", "train", "--no-sandbox",
         "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {method}"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    meta = json.loads((runs / "rule-record" / "run_meta.json").read_text())
    assert meta.get("acceptance_rule"), (
        "方法声明的验收规则没有到达 run 记录 —— 这正是它此前 84 个 run 里的处境")
    rule = meta["acceptance_rule"]
    text = rule.get("text") if isinstance(rule, dict) else rule
    assert "does not fall" in str(text), rule
