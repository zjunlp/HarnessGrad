"""`changed: false` 曾经同时表示两件事,而 mode A 对两件事都按第一件处理。

    "我没有别的要说了"        方法已经结束
    "我否决这个候选"          方法的贡献**就是**一道过滤器

第二件不该终止整个 run。RRSI 筛掉一个候选后会做有限次修复再重试,TTHE 的回滚闸门
是"保住现任、等下一批",AHE 的 HARMFUL 判定是"回滚换一层再来" —— 在旧规则下这三个
都会因为**一次**否决而丢掉剩下的全部预算。

这不是假设:用假模型端到端跑七个移植时,`methods/rrsi` 和 `methods/tthe` 在 0.0 的
基线上只拿到 1 个方法轮,而每个"来者不拒"的方法拿到 3 个。一个以"公平对比"为承诺的
平台,不能让谨慎的方法比激进的方法少跑几轮。

`stop` 默认等于 `not changed`,所以**没有这个字段的方法行为一字不变**。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 一个最小的外部方法:原样交回 base harness,并按参数声明 changed/stop。
#: 写成文件而不是内联,是因为它必须是一个真正的进程调用 —— 平台从不 import 方法。
STUB = '''\
import json, sys
from pathlib import Path
req = json.loads(sys.stdin.read())
base = Path(req["base_harness"])
reply = json.loads(sys.argv[1])
Path(req["trajectory_out"]).write_text(json.dumps({
    "steps": [{"harness_dir": str(base), "label": "stub", "edit_kind": "none",
               "claimed_cost": {}, "method_reported": {}}],
    "trajectory_shape": "sequence", "nominated": 0,
    "provenance": {"method": "stub"}}))
reply.setdefault("changed", False)
sys.stdout.write(json.dumps(reply))
'''


def _run(tmp_path: Path, reply: dict, rounds: int = 3) -> list[dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    stub = tmp_path / "stub_method.py"
    stub.write_text(STUB)
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", str(rounds), "--run-id", "stop-check", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {stub} '{json.dumps(reply)}'"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-600:]}\n{proc.stderr[-600:]}"
    return [json.loads(l) for l in
            (runs / "stop-check" / "curve.jsonl").read_text().splitlines() if l.strip()]


def test_the_default_is_unchanged_a_method_without_stop_still_ends_the_run(tmp_path):
    """向后兼容:没写 `stop` 的方法拿到的行为和以前一模一样。"""
    points = _run(tmp_path, {"changed": False})
    assert len(points) == 2, f"changed=false 且无 stop 时应当只跑一轮:{len(points)}"


def test_an_explicit_stop_true_ends_the_run(tmp_path):
    points = _run(tmp_path, {"changed": True, "stop": True})
    assert len(points) == 2, points


def test_rejecting_a_candidate_does_not_end_the_run(tmp_path):
    """这一条就是修的东西:否决一个候选 ≠ 方法已经结束。"""
    points = _run(tmp_path, {"changed": False, "stop": False})
    assert len(points) == 4, \
        f"stop=false 应当让方法拿到全部三轮,实际只有 {len(points)} 个点"
    # 每个被否决的候选仍然要被平台测一次 —— 方法不能免费地不动现任
    assert all(p["measured_by_platform"] for p in points[1:])


def test_a_cautious_method_and_an_eager_one_get_the_same_number_of_rounds(tmp_path):
    """公平性本身:谨慎不该被折算成更少的轮数。"""
    cautious = _run(tmp_path / "a", {"changed": False, "stop": False})
    eager = _run(tmp_path / "b", {"changed": True})
    assert len(cautious) == len(eager) == 4, (len(cautious), len(eager))
