"""控制台必须能从 `snapshots/round-N.json` 打开一轮 —— 即使没有状态目录、也没有补丁。

背景：`state_after_roundN/` 与 `diffs/round-N.patch` 只在部分 run 里存在，于是编辑页对
其他 run 只能回 `round-not-stored`，哪怕这一轮的 harness 被 `harnessgrad/snapshot.py` 写成
了一个自校验的单文件快照。这一轮修的就是这条：候选的来源按

    state_after_roundN/  →  snapshots/round-N.json  →  diffs/round-N.patch

的顺序取第一个可用的，并在响应里写清楚用了哪个（`source`）。这里用 `tmp_path` 里的合成
run 逐条钉住顺序、字节内容与错误信息，不碰真实 run 目录。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "console" / "server.py"
RUN = "snapshot-only-fixture-20261119T0000Z"
MANIFEST = {
    "name": "loop",
    "version": "0.1",
    "path": ".",
    "entrypoint": "agent.py",
    "backend": "files",
    "env_kinds": ["files"],
}


def snapshot_module():
    """平台自己的 `harnessgrad/snapshot.py`：测试写快照、对照签出都用同一份实现。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from harnessgrad import snapshot

    return snapshot


def load_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """按临时目录加载一份 server 模块：run 与候选都在 tmp 里，不碰真实数据。"""
    runs = tmp_path / "runs"
    candidates = tmp_path / "candidates"
    (runs / RUN).mkdir(parents=True)
    monkeypatch.setenv("HG_CONSOLE_RUNS", str(runs))
    monkeypatch.setenv("HG_CONSOLE_CANDIDATES", str(candidates))
    # 快照模块从这棵工作树里加载，和被测的控制台代码是同一个提交。
    monkeypatch.setenv("HG_CONSOLE_PLATFORM", str(ROOT))
    spec = importlib.util.spec_from_file_location("hg_console_server_snapshot_rounds", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, runs, candidates


def write_tree(work: Path, agent_text: str) -> None:
    """一个最小的 harness 树，带一层子目录：签出要能还原子目录。"""
    (work / "pkg").mkdir(parents=True)
    (work / "harness.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (work / "agent.py").write_text(agent_text, encoding="utf-8")
    (work / "requirements.txt").write_text("", encoding="utf-8")
    (work / "pkg" / "tools.py").write_text("VALUE = 1\n", encoding="utf-8")


def write_snapshot(run: Path, work: Path, round_no: int) -> Path:
    snapshot = snapshot_module()
    return snapshot.write(run, round_no, work, run_id=RUN, harness_sha="a" * 40)


def test_snapshot_only_round_opens_from_the_snapshot(tmp_path, monkeypatch):
    """只有 `snapshots/round-1.json` 的 run：不靠补丁也能打开，且内容就是快照的内容。"""
    server, runs, candidates = load_server(tmp_path, monkeypatch)
    run = runs / RUN
    work = tmp_path / "work"
    write_tree(work, "print('from snapshot')\n")
    snap_file = write_snapshot(run, work, 1)
    # 前提真的是"只有快照"：没有状态目录，也没有 diffs/。
    assert not (run / "state_after_round1").exists()
    assert not (run / "diffs").exists()

    code, payload = server.harness_candidate_from_round(RUN, 1)

    assert code == 201
    assert payload["source"] == "snapshot"
    assert payload["reused"] is False
    assert payload["from"] == str(snap_file)
    assert not payload["reconstructed"] and payload["applied"] == []

    # 候选里每个文件（除被改名的那份 harness.json）与 check_out 出来的那一份逐字节相同。
    snapshot = snapshot_module()
    reference = tmp_path / "checked-out"
    snapshot.check_out(snap_file, reference)
    candidate = candidates / payload["candidate"]
    stored = snapshot.load(snap_file)["files"]
    for rel in stored:
        assert (candidate / rel).is_file(), f"候选缺了快照里的 {rel}"
        if rel == "harness.json":
            continue
        assert (candidate / rel).read_bytes() == (reference / rel).read_bytes(), rel
    # harness.json 只差候选名（控制台对每一路来源都这样做，编辑器和试跑按目录名认人）。
    checked_out = json.loads((reference / "harness.json").read_text(encoding="utf-8"))
    renamed = json.loads((candidate / "harness.json").read_text(encoding="utf-8"))
    assert renamed["name"] == payload["candidate"]
    assert {k: v for k, v in renamed.items() if k != "name"} == \
        {k: v for k, v in checked_out.items() if k != "name"}

    # 第二次打开创建新候选，来源仍报 snapshot。
    code2, again = server.harness_candidate_from_round(RUN, 1)
    assert (code2, again["reused"]) == (201, False)
    assert again["candidate"] != payload["candidate"]
    assert again["source"] == "snapshot"
    rows = {row["dir"]: row for row in server.harness_candidates()["rows"]}
    assert rows[payload["candidate"]]["source"] == "snapshot"
    assert rows[payload["candidate"]]["from"] == str(snap_file)


def test_none_of_the_three_sources_names_all_three(tmp_path, monkeypatch):
    """三处都没有时仍然是 `round-not-stored`，但理由必须点名三处来源。"""
    server, _runs, _ = load_server(tmp_path, monkeypatch)
    # 这一轮的 run 目录存在、却什么也没存下来。
    code, payload = server.harness_candidate_from_round(RUN, 2)

    assert code == 409
    assert payload["error"] == "round-not-stored"
    detail = payload["detail"]
    assert "state_after_round2" in detail
    assert "snapshots/round-2.json" in detail
    assert "diffs/round-" in detail and ".patch" in detail


def test_a_state_directory_wins_over_the_snapshot(tmp_path, monkeypatch):
    """两处都有时用状态目录（最快的一路），而不是快照，也不是重放。"""
    server, runs, candidates = load_server(tmp_path, monkeypatch)
    run = runs / RUN
    snapshot_work = tmp_path / "snapshot-work"
    write_tree(snapshot_work, "print('from snapshot')\n")
    write_snapshot(run, snapshot_work, 1)
    # 状态目录里放**不同**的内容，这样"用了哪一路"能从字节上看出来。
    state = run / "state_after_round1"
    write_tree(state, "print('from state')\n")

    code, payload = server.harness_candidate_from_round(RUN, 1)

    assert (code, payload["source"]) == (201, "state")
    assert payload["from"] == str(state)
    assert (candidates / payload["candidate"] / "agent.py").read_text(encoding="utf-8") == \
        "print('from state')\n"


def test_a_corrupt_snapshot_is_skipped_and_named_in_the_error(tmp_path, monkeypatch):
    """快照存在但坏了：不拿它当来源；没有补丁可重放时，理由里说明快照坏在哪里。"""
    server, runs, _ = load_server(tmp_path, monkeypatch)
    run = runs / RUN
    work = tmp_path / "work"
    write_tree(work, "print('kept from snapshot')\n")
    snap_file = write_snapshot(run, work, 1)
    payload = json.loads(snap_file.read_text(encoding="utf-8"))
    payload["files"]["agent.py"]["content_b64"] = "cHJpbnQoJ3RhbXBlcmVkJykK"
    snap_file.write_text(json.dumps(payload), encoding="utf-8")   # id 不再对得上

    code, answer = server.harness_candidate_from_round(RUN, 1)

    # 没有补丁可重放，因此走不到任何一路；理由里说明快照存在但签不出来。
    assert (code, answer["error"]) == (409, "unsafe-recovery")
    assert "snapshots/round-1.json invalid" in answer["detail"]
