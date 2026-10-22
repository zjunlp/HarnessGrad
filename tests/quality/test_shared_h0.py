"""共用的 round 0：九个方法必须看到**同一个**第一分。

**为什么需要它。** 方法的第一条输入是 round 0 —— 平台在原始 harness 上的测量。每个 run 各测
一次时，批次组成不同会让浮点累加顺序不同，偶尔翻一个 token，于是方法 A 看到 0.53、方法 B
看到 0.51，而记录上两者都写"round 0"。方法读到的 trace 也可能不是同一批。比较的前提是
它们被提供了同一份证据，而不是"通常一样"。

`--h0-from RUN` 让一个 run 复用另一个 run 的 round-0 测量：分数、逐题结果、身份（它是**怎么**
被测出来的）、以及 trace 一起搬过来，点上的 `shared_from` 说明出处。两个 run 的 base 树不同
时**拒绝**，因为那已经不是同一个起点。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(tmp_path: Path, run_id: str, *extra: str) -> subprocess.CompletedProcess:
    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--rounds", "0", "--dataset", "demo", "--sampling", "all", "--jobs", "1",
            "--run-id", run_id, "--runs-root", str(tmp_path / "runs"),
            "--work-root", str(tmp_path / f"work-{run_id}"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}",
            *extra]
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})


def _first_point(tmp_path: Path, run_id: str) -> dict:
    path = tmp_path / "runs" / run_id / "curve.jsonl"
    return json.loads(path.read_text().splitlines()[0])


def test_a_run_reuses_another_runs_round_zero(tmp_path):
    """复用之后：分数与逐题结果**一模一样**，并且点自己说出它来自谁。"""
    donor = _run(tmp_path, "donor")
    assert donor.returncode == 0, donor.stderr[-800:]
    assert (tmp_path / "runs" / "donor" / "h0.json").is_file(), "donor 必须留下可复用的测量"

    consumer = _run(tmp_path, "consumer", "--h0-from", "donor")
    assert consumer.returncode == 0, consumer.stderr[-800:]

    a, b = _first_point(tmp_path, "donor"), _first_point(tmp_path, "consumer")
    assert b["per_task"] == a["per_task"], "逐题结果必须完全相同"
    assert b["score"] == a["score"]
    assert b["shared_from"]["run_id"] == "donor"
    assert b["run_id"] == "consumer", "点仍属于本 run，出处写在 shared_from 里"
    # 身份描述的是"这个数是怎么产生的"，所以它保持 donor 的，不是本 run 的
    assert b["identity"] == a["identity"]


def test_reusing_a_measurement_from_a_different_base_tree_is_refused(tmp_path):
    """base 树不同就不是同一个起点，必须拒绝而不是悄悄换一个分数。"""
    donor = _run(tmp_path, "donor")
    assert donor.returncode == 0, donor.stderr[-600:]
    payload = tmp_path / "runs" / "donor" / "h0.json"
    data = json.loads(payload.read_text())
    data["h0_sha"] = "0" * 40
    payload.write_text(json.dumps(data))

    consumer = _run(tmp_path, "consumer", "--h0-from", "donor")
    assert consumer.returncode != 0
    assert "base" in consumer.stderr and "refusing" in consumer.stderr


def test_a_missing_donor_is_refused(tmp_path):
    consumer = _run(tmp_path, "consumer", "--h0-from", "no-such-run")
    assert consumer.returncode != 0
    assert "refusing" in consumer.stderr


def test_a_donor_measured_before_the_payload_existed_is_still_sharable(tmp_path):
    """**实测的用途**：我们那份 0.53 的 H0 是在 `h0.json` 之前测的，不能因此再测一遍。

    曲线点保留了 round-0 路径需要的全部东西（逐题分数、invalid、harness_failed、运行时
    记录、trial 离散度），trace 在 `round0_traces/` 里。所以从点重建测量即可，数和原来一样。
    """
    donor = _run(tmp_path, "donor")
    assert donor.returncode == 0, donor.stderr[-600:]
    (tmp_path / "runs" / "donor" / "h0.json").unlink()      # 退回"旧 run"的形态

    consumer = _run(tmp_path, "consumer", "--h0-from", "donor")
    assert consumer.returncode == 0, consumer.stderr[-800:]
    a, b = _first_point(tmp_path, "donor"), _first_point(tmp_path, "consumer")
    assert b["per_task"] == a["per_task"]
    assert b["shared_from"]["run_id"] == "donor"


def test_a_shared_round_zero_stages_the_donors_traces(tmp_path):
    """**回归**：共用 H0 时 `base` 里没有 `traces`，方法一被调用就 `KeyError: 'traces'`。

    方法的 trace 通道是从内存里的 dict 布出去的，不是从 run 目录读的 —— 所以共用必须把
    donor 的 trace body 一起读回来，否则九方法拿到的是空通道。
    """
    donor = _run(tmp_path, "donor")
    assert donor.returncode == 0, donor.stderr[-600:]
    (tmp_path / "runs" / "donor" / "h0.json").unlink()
    traces_dir = tmp_path / "runs" / "donor" / "round0_traces"
    assert traces_dir.is_dir() and any(traces_dir.glob("*.jsonl")), "donor 应当留下了 trace"

    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--rounds", "1", "--dataset", "demo", "--sampling", "all", "--jobs", "1",
            "--run-id", "consumer", "--runs-root", str(tmp_path / "runs"),
            "--work-root", str(tmp_path / "work-consumer"), "--h0-from", "donor",
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, proc.stderr[-1200:]
    staged = tmp_path / "runs" / "consumer" / "round0_traces"
    assert staged.is_dir() and any(staged.glob("*.jsonl")), "trace 必须跟着测量一起搬过来"
    assert "KeyError" not in proc.stderr


def test_a_shared_point_carries_this_runs_improver_not_the_donors(tmp_path):
    """**2026-10-17 整批失败就是这条。** donor 用 `--improver none` 测（我们那份真实的 H0 就是），
    共用时把它的 `improver` 一起抄过来 —— 那里根本没有这个键 —— 于是九个方法都以为
    "我没有 CLI improver"，退回聊天端点，用为 CLI 准备的 prompt 去撞 16k 窗口，round 1 全灭。

    测量事实（树、agent 模型、jobs）属于 donor；`improver` 属于**本次 run**。
    """
    donor = _run(tmp_path, "donor", "--improver", "none")
    assert donor.returncode == 0, donor.stderr[-600:]
    assert "improver" not in (_first_point(tmp_path, "donor")["identity"]), (
        "--improver none 的点不该记录 improver，这个前提不成立就测不到那条 bug")

    consumer = _run(tmp_path, "consumer", "--h0-from", "donor", "--improver", "dsh")
    assert consumer.returncode == 0, consumer.stderr[-800:]
    b = _first_point(tmp_path, "consumer")
    improver = (b.get("identity") or {}).get("improver")
    assert improver and improver.get("name") == "dsh", (
        f"共用点必须记录本次 run 解析到的 improver，实际是 {improver!r}")
    # donor 的测量条件仍然可查
    assert b["shared_from"]["measured_with"]["jobs"] == 1
