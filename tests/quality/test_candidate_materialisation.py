"""Recovery preserves existing candidates and creates a fresh copy each time."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2] / "console" / "server.py"
RUN = "appworld-study-fixture-20261019T1816Z"


def load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """按临时目录加载一份 server 模块：候选与 run 都在 tmp 里，不碰真实数据。"""
    runs = tmp_path / "runs"
    candidates = tmp_path / "candidates"
    run = runs / RUN
    snap = run / "state_after_round1"
    snap.mkdir(parents=True)
    (snap / "harness.json").write_text(
        json.dumps({"name": "loop", "version": "0.1", "entrypoint": "agent.py",
                    "backend": "files", "env_kinds": ["files"], "role": "baseline"},
                   ensure_ascii=False), encoding="utf-8")
    (snap / "agent.py").write_text("print('baseline')\n", encoding="utf-8")
    (snap / "requirements.txt").write_text("", encoding="utf-8")
    monkeypatch.setenv("HG_CONSOLE_RUNS", str(runs))
    monkeypatch.setenv("HG_CONSOLE_CANDIDATES", str(candidates))
    spec = importlib.util.spec_from_file_location("hg_console_server_candidates", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, candidates


def test_same_round_twice_creates_fresh_copies(tmp_path, monkeypatch):
    server, candidates = load(tmp_path, monkeypatch)
    code, first = server.harness_candidate_from_round(RUN, 1)
    code2, second = server.harness_candidate_from_round(RUN, 1)

    assert (code, first["reused"]) == (201, False)
    assert (code2, second["reused"]) == (201, False)
    assert second["candidate"] != first["candidate"]
    assert {p.name for p in candidates.iterdir() if p.is_dir() and not p.name.startswith(".")} == {first["candidate"], second["candidate"]}
    # 账本写在候选目录之外，不混进被哈希的 harness 树。
    assert (candidates / ".sources.json").is_file()
    assert not (candidates / first["candidate"] / ".sources.json").exists()


def test_a_hand_edited_copy_is_never_overwritten(tmp_path, monkeypatch):
    server, candidates = load(tmp_path, monkeypatch)
    _, first = server.harness_candidate_from_round(RUN, 1)
    edited = candidates / first["candidate"] / "agent.py"
    edited.write_text("print('my change')\n", encoding="utf-8")

    code, second = server.harness_candidate_from_round(RUN, 1)

    assert (code, second["reused"]) == (201, False)
    assert second["candidate"] != first["candidate"], "改过的副本不能被就地重写"
    assert edited.read_text(encoding="utf-8") == "print('my change')\n"
    # 新副本仍然是这一轮的原样内容。
    fresh = candidates / second["candidate"] / "agent.py"
    assert fresh.read_text(encoding="utf-8") == "print('baseline')\n"


def test_legacy_copy_is_preserved(tmp_path, monkeypatch):
    """A pre-existing directory is not adopted or overwritten."""
    server, candidates = load(tmp_path, monkeypatch)
    legacy = candidates / f"{RUN}-r1"
    legacy.mkdir(parents=True)
    (legacy / "harness.json").write_text(
        json.dumps({"name": legacy.name, "version": "0.1", "entrypoint": "agent.py",
                    "backend": "files"}, ensure_ascii=False), encoding="utf-8")
    (legacy / "agent.py").write_text("print('baseline')\n", encoding="utf-8")

    code, payload = server.harness_candidate_from_round(RUN, 1)

    assert (code, payload["reused"]) == (201, False)
    assert payload["candidate"] != legacy.name
    assert (legacy / "agent.py").read_text(encoding="utf-8") == "print('baseline')\n"
    assert legacy.name not in json.loads((candidates / ".sources.json").read_text(encoding="utf-8"))
