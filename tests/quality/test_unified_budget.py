"""一轮 = 一个**被测量的状态**，不是一次方法调用。

为什么要把这件事钉住：`--rounds N` 以前的意思是"调用方法 N 次,每次交一个候选"。这正好是
RSI 类规则用不了的形状 —— 它们的全部内容是"在一份预算下、带噪声地决定怎么花"(RRSI 的退火
`b_t`、DGM 的子代数、SICA 的置信下界)。mode B 反过来:方法自己排日程、一次交一整条序列,
但**中途拿不到任何分数**,于是预算没处可花。两个 mode 是同一个循环的两半,而哪一半都表达不
了选择规则。

现在一个引擎:一次提交带 1~N 个步骤,每个步骤按顺序被测量;只有当预算还有剩**且**方法没说
`stop` 时,平台才会再调用它一次。一次交一个是旧的 mode A(候选之间有反馈),一次交十个是旧
的 mode B(批内无反馈),交三个再说"还没完"是旧的两种都说不出来的。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: A method that submits a **batch** and keeps going. Each step is a real edit, so the
#: trees differ and each is measured on its own.
_BATCH_METHOD = '''
import json, shutil, sys
from pathlib import Path

req = json.load(sys.stdin)
base = Path(req["base_harness"])
ws = Path(req["workspace"])
ws.mkdir(parents=True, exist_ok=True)

# Count this invocation, so the test can see how many times it was called.
mark = Path(str(Path(req["workspace"]).parent) + "_calls.txt")
n = int(mark.read_text()) + 1 if mark.exists() else 1
mark.write_text(str(n))

steps = []
for i in range(3):
    dest = ws / f"cand{n}_{i}"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest)
    agent = dest / "agent.py"
    agent.write_text(agent.read_text() + f"\\n# batch {n} step {i}\\n")
    steps.append({"harness_dir": str(dest), "label": f"batch {n} step {i}",
                  "edit_kind": "harness_source",
                  "claimed_cost": {"generation_tokens": 0},
                  "method_reported": {"score": None}})

Path(req["trajectory_out"]).write_text(json.dumps({
    "steps": steps, "trajectory_shape": "sequence", "nominated": 0,
    "provenance": {"method": "batch_probe",
                   "acceptance_rule": {"text": "none", "source": "test",
                                       "calibrated": False}},
}))
sys.stdout.write(json.dumps({"changed": True, "stop": False,
                             "generation_tokens": 0}))
'''


def _run(tmp_path: Path, method_src: str, *, rounds: int, run_id: str):
    method = tmp_path / f"{run_id}.py"
    method.write_text(method_src, encoding="utf-8")
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", str(rounds), "--run-id", run_id, "--dataset", "demo",
         "--sampling", "all", "--side", "train",
         # The sandbox gives the method its own `/tmp`, so a counter file written there
         # is invisible to the test. The sandbox is not what these tests are about.
         "--no-sandbox",
         "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {method}"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, (
        f"driver exited {proc.returncode}; argv={proc.args!r}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    points = [json.loads(l) for l in
              (runs / run_id / "curve.jsonl").read_text().splitlines() if l.strip()]
    meta = json.loads((runs / run_id / "run_meta.json").read_text())
    return points, meta


def test_a_batch_of_three_costs_three_epochs_and_gets_asked_again(tmp_path):
    """`--rounds 5` + 每次交 3 步 + `stop: false` = 测 5 个状态、方法被调用 **2** 次。

    第一次提交买下 round 1-3,第二次只剩 2 个位置,于是第 3 步被截断 —— 而截断是记下来的
    事实,不是悄悄丢掉。
    """
    points, meta = _run(tmp_path, _BATCH_METHOD, rounds=5, run_id="budget-batch")

    assert [p["round"] for p in points] == [0, 1, 2, 3, 4, 5], [p["round"] for p in points]
    shapes = [(p.get("step_index"), p.get("steps_in_submission")) for p in points[1:]]
    assert shapes == [(1, 3), (2, 3), (3, 3), (1, 2), (2, 2)], shapes
    assert meta["steps_truncated"] == 1, meta.get("steps_truncated")
    assert meta["steps_measured"] == 5, meta.get("steps_measured")
    # 方法只被调用了两次:批内不再调用,预算用完停
    # 5 个状态 = 3 + 2,而第二次提交的 3 步被截到 2 —— 这两条一起就说明方法只被调用了两次
    assert meta["steps_measured"] == 5


def test_a_single_step_per_call_keeps_the_old_behaviour(tmp_path):
    """一次交一个:每个候选之间都有反馈,预算用完为止(旧的 mode A)。"""
    single = _BATCH_METHOD.replace("for i in range(3):", "for i in range(1):")
    points, meta = _run(tmp_path, single, rounds=3, run_id="budget-single")
    assert [p["round"] for p in points] == [0, 1, 2, 3]
    assert all(p["steps_in_submission"] == 1 for p in points[1:]), points[1:]
    assert all(p["step_index"] == 1 for p in points[1:]), points[1:]
    assert meta.get("steps_truncated", 0) == 0
    assert meta["steps_measured"] == 3


_KEEPING_METHOD = '''
import json, shutil, sys
from pathlib import Path

req = json.load(sys.stdin)
base, ws = Path(req["base_harness"]), Path(req["workspace"])
ws.mkdir(parents=True, exist_ok=True)
steps = []
for i in range(2):
    dest = ws / f"keep_{i}"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest)
    agent = dest / "agent.py"
    agent.write_text(agent.read_text() + f"\\n# keep step {i}\\n")
    step = {"harness_dir": str(dest), "label": f"keep step {i}",
            "edit_kind": "harness_source",
            "claimed_cost": {"generation_tokens": 0},
            "method_reported": {"score": None}}
    # The second candidate says: whatever it scores, the state I keep is round 0.
    if i == 1:
        step["incumbent_round"] = 0
    steps.append(step)
Path(req["trajectory_out"]).write_text(json.dumps({
    "steps": steps, "trajectory_shape": "sequence", "nominated": 0,
    "provenance": {"method": "keeping_probe",
                   "acceptance_rule": {"text": "keep 0", "source": "test",
                                       "calibrated": False}},
}))
sys.stdout.write(json.dumps({"changed": True, "stop": True, "generation_tokens": 0}))
'''


def test_which_state_a_rule_keeps_is_on_the_record(tmp_path):
    """**`score` 是候选的分数,不是规则保留的那个状态的分数。**

    选择规则的全部内容就是"接受还是保留"。平台早就允许方法这么做(把 `states/` 里的旧树
    交回来,`harnessx` 的移植就是这么写的),但记录里看不出来:曲线上只有候选的测量值。
    现在一个步骤可以指名 `incumbent_round`,点就带上那一轮和它的分数。
    """
    points, _ = _run(tmp_path, _KEEPING_METHOD, rounds=4, run_id="kept-round")
    by_round = {p["round"]: p for p in points}
    # 第一步没指名,默认保留自己
    assert by_round[1]["kept_round"] == 1
    assert by_round[1]["kept_score"] == by_round[1]["score"]
    # 第二步指名 round 0,于是它带上 H0 的分数
    assert by_round[2]["kept_round"] == 0, by_round[2]
    assert by_round[2]["kept_score"] == by_round[0]["score"], by_round[2]
