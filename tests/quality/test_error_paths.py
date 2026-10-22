"""跨方法的一致性:同一件事发生时,七个方法必须给出同一种记录。

单个方法的测试看不出这类问题 —— 每个方法在自己的测试里都是对的,而它们**彼此**不一致。
这里跑的就是那种对比:同样一次 provider 报错,七个方法应该都写出 trajectory、都保住自己
的跨轮记忆、都不把这次失败当成"决定收工"。

实测发现的原始问题(被一次全量扫描抓出来):错误路径上五个方法走 `editor.fail`,
丢掉自己的记录键;`tthe` 和 `harnessx` 走 `editor.report`,保住了。同一件事,两种记录。
而那些键正是**跨轮记忆** —— DGM 的 `dgm_parent` 是血缘、AHE 的 `ahe_manifest` 是下一轮
要评分的预注册、RRSI 的 `rrsi_rule` 里有剪枝过的组件。丢了它们,方法会在下一轮失忆,
而记录里看不出发生过什么。
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

#: 每个方法必须在 `method_reported` 里留下的自己的键。
SIGNATURE = {
    "rrsi": "rrsi_rule",
    "dgm": "dgm_parent",
    "hyperagents": "hyper_parent",
    "ahe": "ahe_manifest",
    "tthe": "tthe",
    "harnessx": "harnessx_gate",
    "sica_ci": "sica_rule",
}


def _channel(tmp_path: Path) -> Path:
    """一个能满足所有七个方法的通道:history + states + traces + round.json。"""
    base = tmp_path / "base"
    base.mkdir(parents=True, exist_ok=True)
    (base / "harness.json").write_text(json.dumps({"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    (base / "agent.py").write_text("print('v0')\n")

    hg = base / "_harnessgrad"
    for sub in ("traces", "history", "states"):
        (hg / sub).mkdir(parents=True, exist_ok=True)

    points = []
    for r in range(3):
        point = {"round": r, "score": 0.5, "train_score": 0.5,
                 "train_per_task": {"t1": 1.0, "t2": 0.0},
                 "per_task": {"t1": 1.0, "t2": 0.0},
                 "score_ci95": [0.4, 0.6], "n_scored": 2, "n_passed": 1,
                 "measured_by_platform": True,
                 "split": {"train": ["t1", "t2"], "eval": ["t1", "t2"]},
                 "sampling": {"policy": "all", "task_ids": ["t1", "t2"]},
                 "cost": {"harness_tokens": 10, "evaluation_trials": 2,
                          "method_generation_tokens": 0, "wall_clock_s": 1.0},
                 "cumulative": {"evaluation_trials": 2, "generation_tokens": 0,
                                "wall_clock_s": 1.0},
                 "identity": {"harness_sha": f"sha{r}", "harness_name": "probe",
                              "harness_version": "1", "agent_model": "m",
                              "agent_backend": "mock"},
                 "method_reported": {}}
        points.append(point)
        (hg / "history" / f"round-{r}.json").write_text(json.dumps(point))

    index: dict = {"kept": [], "window": 12, "available": len(points)}
    for r in range(3):
        d = hg / "states" / f"round-{r}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "harness.json").write_text((base / "harness.json").read_text())
        (d / "agent.py").write_text(f"print('v{r}')\n")
        index["kept"].append(f"round-{r}")
        index[f"round-{r}"] = {"sha": f"sha{r}", "files": 2, "score": 0.5,
                               "harness_dir": str(d)}
    (hg / "states" / "index.json").write_text(json.dumps(index))

    (hg / "traces" / "t1.jsonl").write_text(
        json.dumps({"command": "ls", "exit": 1}) + "\n")
    (hg / "round.json").write_text(json.dumps(points[-1]))
    return base


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"err_{name}", ROOT / "methods" / name / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _invoke_with_a_broken_model(mod, base: Path, tmp_path: Path) -> dict:
    """让模型调用抛错,返回方法写到 stdout 的回复。"""
    import editor

    def boom(*a, **k):
        raise RuntimeError("503 no available channel")

    out = io.StringIO()
    req = {"platform_api_version": "0.1.0", "mode": "A",
           "base_harness": str(base), "workspace": str(tmp_path / "ws"),
           "round_index": 3, "incumbent_score": 0.5,
           "task_ids": ["t1", "t2"], "train_task_ids": ["t1", "t2"],
           "trajectory_out": str(tmp_path / "traj.json")}
    (tmp_path / "ws").mkdir(exist_ok=True)

    import types
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps(req))
    sys.stdout = out
    # 只替换 editor.ask,方法和平台其余部分照常
    real_ask = editor.ask
    editor.ask = boom
    try:
        for sub in list(sys.modules):
            if sub.startswith("err_"):
                m = sys.modules[sub]
                if hasattr(m, "editor"):
                    m.editor.ask = boom
        mod.main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
    finally:
        editor.ask = real_ask
        sys.stdin, sys.stdout = old_stdin, old_stdout
    text = out.getvalue()
    return json.loads(text) if text.strip() else {}


@pytest.mark.parametrize("method", sorted(SIGNATURE))
def test_a_provider_error_keeps_the_methods_own_record(method, tmp_path):
    """**这条是全量扫描抓到的那个 bug。** 报错时方法的记录键不能丢。

    丢了它,方法的跨轮记忆就在下一轮消失,而记录里只写着一个 error —— 读的人会以为
    这个方法没有状态。
    """
    base = _channel(tmp_path)
    mod = _load(method)
    reply = _invoke_with_a_broken_model(mod, base, tmp_path)

    traj = tmp_path / "traj.json"
    assert traj.exists(), f"{method}: 报错时没有写出 trajectory"
    step = json.loads(traj.read_text())["steps"][0]["method_reported"]
    assert "error" in step, f"{method}: 失败原因没进记录"
    assert SIGNATURE[method] in step, (
        f"{method}: 报错时丢掉了自己的 {SIGNATURE[method]!r} —— 跨轮记忆会消失;"
        f"记录里只有 {sorted(step)}")


@pytest.mark.parametrize("method", sorted(SIGNATURE))
def test_a_transient_error_does_not_cost_the_method_its_budget(method, tmp_path):
    """一次 500 不是方法做的决定,不该终止整个 run。

    这和 `stop` 字段是同一个道理:RRSI 的筛子、TTHE 的回滚闸门、AHE 的 HARMFUL 都是
    "否决但仍要继续"。基础设施抖动也一样 —— 让七个方法在同一个 500 下拿到不同轮数,
    就等于把它们放在不同的预算上比较。
    """
    base = _channel(tmp_path)
    mod = _load(method)
    reply = _invoke_with_a_broken_model(mod, base, tmp_path)
    assert reply.get("stop") is not True, \
        f"{method}: 一次瞬时错误就让方法收工了(platform 会因此提前结束整条 run)"
    assert reply.get("changed") is False
