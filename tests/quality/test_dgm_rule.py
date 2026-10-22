"""DGM 的规则是一次**归档搜索**,不是一步。

它的父代权重是 `sigmoid(10*(分数-0.5)) / (1+子代数)`,按权重抽样 —— 子代数那一项只有在
**同一轮有兄弟**的时候才有意义。以前的移植为了适配 mode A 被写成"一轮一个候选",于是这条
规则每轮只抽一次样,归档永远攒不出兄弟,`1/(1+children)` 那一项从来没用过。

现在一次调用就是算法本身:按同一分布抽样 k 个父代、每个产出一个子代、一批交上去。每个子代
都被测量;预算由平台封顶(`--rounds` = 最多测几个状态)。

上游:**Darwin Godel Machine**,arXiv 2505.22954(`jennyzzt/dgm`)。父代权重与保留门引自
该仓库的归档实现(`archive.py` 的父代抽样与子代数惩罚);本文件钉的是那条公式的算术,
不是“端口能跑”。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))


def _load():
    spec = importlib.util.spec_from_file_location("dgm_probe", ROOT / "methods" / "dgm" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _base(tmp_path: Path, history: list[dict]) -> Path:
    root = tmp_path / "base"
    (root / "_harnessgrad" / "traces").mkdir(parents=True, exist_ok=True)
    (root / "_harnessgrad" / "history").mkdir(parents=True, exist_ok=True)
    (root / "harness.json").write_text(json.dumps(
        {"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    (root / "agent.py").write_text("print('hello')\n")
    (root / "_harnessgrad" / "traces" / "t01.jsonl").write_text('{"reply": "hi"}\n')
    for point in history:
        (root / "_harnessgrad" / "history" / f"round-{point.get('round', 0)}.json"
         ).write_text(json.dumps(point))
        # A staged state per history round, which is what the parent selector names.
        # The **index** is what the method reads (`_state_dir`), not the directory name: a
        # state that could not be materialized is deliberately absent from it.
        state = root / "_harnessgrad" / "states" / f"round-{point.get('round', 0)}"
        state.mkdir(parents=True, exist_ok=True)
        (state / "harness.json").write_text(json.dumps(
            {"name": "probe", "version": "1", "entrypoint": "agent.py"}))
        (state / "agent.py").write_text("print('hello')\n")
        index = json.loads((root / "_harnessgrad" / "states" / "index.json").read_text()) \
            if (root / "_harnessgrad" / "states" / "index.json").exists() else {"kept": []}
        index.update({f"round-{point.get('round', 0)}": {
            "sha": point.get("harness_sha"), "score": point.get("score"),
            "harness_dir": str(state)}})
        index["kept"] = sorted(set(index.get("kept", []) + [f"round-{point.get('round', 0)}"]))
        (root / "_harnessgrad" / "states" / "index.json").write_text(json.dumps(index))
    (root / "_harnessgrad" / "round.json").write_text(
        json.dumps(history[-1] if history else {"round": 0, "score": 0.0}))
    return root


def _request(base: Path, work: Path, traj: Path, *, round_index=1, remaining=None) -> dict:
    req = {"platform_api_version": "0.1.0", "mode": "unified", "base_harness": str(base),
           "pristine_harness": str(base), "workspace": str(work),
           "round_index": round_index, "incumbent_score": 0.0,
           "task_ids": ["t01"], "train_task_ids": ["t01"], "trajectory_out": str(traj),
           "epochs_total": 6, "epochs_measured": round_index - 1}
    if remaining is not None:
        req["epochs_remaining"] = remaining
    return req


def _run(mod, request: dict, monkeypatch, *, children=3, reply=None):
    import editor

    seen: list[str] = []

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        seen.append(prompt)
        n = len(seen)
        return (reply or {"files": [{"path": "agent.py",
                                     "content": f"print('child {n}')\n"}],
                          "hypothesis": f"child {n}"})

    monkeypatch.setattr(editor, "ask", fake_ask)
    monkeypatch.setattr(mod.editor, "ask", fake_ask, raising=False)
    monkeypatch.setenv("HG_DGM_CHILDREN", str(children))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys, "stdout", out)
    mod.main()
    return json.loads(out.getvalue() or "{}"), seen


def _history() -> list[dict]:
    return [{"round": 0, "score": 0.25, "train_score": 0.25, "label": "H0",
             "harness_sha": "a" * 40},
            {"round": 1, "score": 0.5, "train_score": 0.5, "label": "round 1",
             "harness_sha": "b" * 40, "edits_applied": ["agent.py"]}]


def test_method_metadata_records_multiple_children_and_default():
    # 上游 DGM_outer.py:90-100 按概率抽取多个父代，:111-148 逐父代选 target；
    # DGM_outer.py:224 的 --selfimprove_size 默认 2，移植版 HG_DGM_CHILDREN 默认 3。
    metadata = json.loads((ROOT / "methods" / "dgm" / "method.json").read_text())
    text = json.dumps(metadata, ensure_ascii=False).lower()
    assert "no parallel children" not in text
    assert "one candidate per round" not in text
    assert "一轮一个子代" not in metadata["note"]
    deviations = " ".join(
        f"{entry['what']} {entry['why']}" for entry in metadata["deviations"])
    assert "HG_DGM_CHILDREN" in deviations
    assert "default 3" in deviations
    assert "--selfimprove_size" in deviations
    assert "default 2" in deviations


def test_one_call_submits_a_whole_batch(tmp_path, monkeypatch):
    """一次调用 = k 个子代,每个子代一棵独立的树,`stop: true`。"""
    mod = _load()
    base = _base(tmp_path, _history())
    traj = tmp_path / "traj.json"
    reply, calls = _run(mod, _request(base, tmp_path / "ws", traj), monkeypatch, children=3)

    written = json.loads(traj.read_text())
    assert len(written["steps"]) == 3, written
    assert len(calls) == 3, "每个子代一次模型调用"
    dirs = [s["harness_dir"] for s in written["steps"]]
    assert len(set(dirs)) == 3, dirs
    for step in written["steps"]:
        assert Path(step["harness_dir"]).is_dir()
        assert step["method_reported"]["dgm"]["child"] >= 1
    assert reply["changed"] is True and reply["stop"] is True, reply
    assert reply["steps"] == 3, reply


def test_the_batch_never_exceeds_the_budget(tmp_path, monkeypatch):
    """剩余预算只有 2 时,不许交 3 个 —— 被平台截断等于这个方法算错了自己的预算。"""
    mod = _load()
    base = _base(tmp_path, _history())
    traj = tmp_path / "traj.json"
    reply, calls = _run(mod, _request(base, tmp_path / "ws", traj, remaining=2),
                        monkeypatch, children=3)
    written = json.loads(traj.read_text())
    assert len(written["steps"]) == 2, written
    assert len(calls) == 2, calls
    assert reply["steps"] == 2


def test_the_parent_rule_runs_once_per_child(tmp_path, monkeypatch):
    """规则是抽样,不是 argmax:k 个子代各自选父代,所以每个 prompt 都点名了父代轮次。"""
    mod = _load()
    base = _base(tmp_path, _history())
    traj = tmp_path / "traj.json"
    _, calls = _run(mod, _request(base, tmp_path / "ws", traj), monkeypatch, children=4)
    assert len(calls) == 4
    assert all("archive chose round" in prompt for prompt in calls), calls[:1]
    written = json.loads(traj.read_text())
    parents = [s["method_reported"]["dgm_parent"] for s in written["steps"]]
    assert all(p in (0, 1) for p in parents), parents


def test_a_batch_with_nothing_usable_is_reported_not_raised(tmp_path, monkeypatch):
    """每个抽样都失败时:交回 base + `changed: false`,记录里带上 DGM 的表,而不是抛异常。"""
    mod = _load()
    base = _base(tmp_path, _history())
    traj = tmp_path / "traj.json"
    reply, _ = _run(mod, _request(base, tmp_path / "ws", traj), monkeypatch,
                    children=2, reply={"no_change": "not worth editing"})
    written = json.loads(traj.read_text())
    # `no_change` 的每一步都是 changed=False,所以没有任何一步值得测量
    assert reply["changed"] is False, reply
    assert reply["stop"] is False, (
        "一个子代都没产出时不能停 —— 那会把剩下的预算扔掉")
    assert len(written["steps"]) == 1, written
    assert written["steps"][0]["method_reported"]["dgm"], written


def test_a_batch_becomes_one_curve_point_per_child(tmp_path):
    """端到端:一批 k 个子代 → 曲线上 k 个点,每个点都带 `steps_in_submission = k`。

    这是"一个引擎、两种实现"的连接点:B 形状的方法靠**一次交多步**得到它自己的日程,
    而记录里的 `steps_in_submission > 1` 说明这些状态之间有**没有**分数反馈。
    """
    import os
    import subprocess

    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "B",
         "--rounds", "6", "--run-id", "dgm-batch", "--dataset", "demo",
         "--sampling", "all", "--side", "train", "--no-sandbox",
         "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/dgm/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
        # **A dead endpoint, and told to give up fast.** This test is about the *shape* of
        # a batch in the record, not about a model answering; with the default retry budget
        # (3 children x retries x backoff) it spent 900 s and hit the subprocess timeout --
        # a test that hangs is worse than one that fails, because it hides the next failure
        # behind it.
        env={**os.environ, "HG_AGENT_BACKEND": "mock", "HG_METHOD_MODEL": "stub",
             "HG_METHOD_BASE_URL": "http://127.0.0.1:9/v1", "HG_METHOD_API_KEY": "x",
             "HG_METHOD_PARSE_RETRIES": "1", "HG_METHOD_TIMEOUT_S": "2",
             "HG_DGM_CHILDREN": "3"})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        f"driver exited {proc.returncode}; argv={proc.args!r}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    points = [json.loads(l) for l in
              (runs / "dgm-batch" / "curve.jsonl").read_text().splitlines() if l.strip()]
    # 没有可用的方法模型时,dgm 抽到的每个子代都会失败,于是它交回"没有改动"并继续 ——
    # 这条测试要的是**形状**:每个被测量的状态都记着它属于一次几提交。
    assert [p["round"] for p in points] == sorted(p["round"] for p in points), points
    for point in points[1:]:
        assert point.get("steps_in_submission", 1) >= 1, point
        # **The attribution field is wired onto a real curve point**, which is the half unit
        # tests cannot reach (`test_components.py` covers the reading itself). The harness here
        # is `loop_plain`, which declares no `components` map, so every touched path must come
        # back honestly `unattributed` rather than the field being absent or the paths being
        # dropped -- an empty component list is what "nothing changed" looks like, and it is
        # not what happened.
        assert "editable_surface_components" in point, point
        block = point["editable_surface_components"]
        assert set(block) == {"components", "unattributed"}, block
        assert block["components"] == [], block
        assert block["unattributed"] == sorted(point.get("editable_surface_touched") or []), block
    meta = json.loads((runs / "dgm-batch" / "run_meta.json").read_text())
    # `run_meta` 的字段是 `mode`(点上的是 `identity.run_mode`)
    assert meta.get("mode") == "unified", meta.get("mode")
    assert "steps_measured" in meta, sorted(meta)
    # The dead endpoint produced no usable child; H0 is the only measured state.
    # A missing field, null, and a positive count all misdescribe this run.
    assert meta["steps_measured"] == 0, meta
    assert [p["round"] for p in points] == [0], points
