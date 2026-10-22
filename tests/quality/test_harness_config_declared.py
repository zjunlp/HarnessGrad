"""外壳的运行时预算必须落在曲线点上。

**这是实测出来的缺口。** `HG_AGENT_MAX_STEPS=16` 和 `=32` 跑的是同一个文件:一条轨迹
在第 16 步停,另一条能跑到 32 步。实测(2026-10-04,本地 9B 跑 `build-pmars`)本地模型每步
约 35 秒,32 步约 1100 秒,超过题目自带的 900 秒预算,于是被超时杀掉;16 步跑得完。

这两次测量的曲线点上,原来**没有任何一个字段不同**:`harness_sha` 相同(文件没改),
`env` 相同(同题),`agent_model` 相同。于是"同一个外壳测了两次"和"两个外壳各测一次"在
记录里同形 —— 而 0.333 和 0.000 的差别,可能就是从这来的。

修法与身份那条一致(INTERFACE.md §3):外壳自己声明,平台记下。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_loop():
    path = ROOT / "base_harness" / "loop" / "agent.py"
    spec = importlib.util.spec_from_file_location("hg_loop_config", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_loop_harness_declares_the_budgets_it_actually_used(tmp_path, monkeypatch):
    """声明的是**生效值**,不是默认值 —— 否则这条记录只是把默认值抄了一遍。"""
    agent = _load_loop()
    monkeypatch.setattr(agent, "MAX_STEPS", 7)
    monkeypatch.setattr(agent, "OUTPUT_LIMIT", 111)
    monkeypatch.setattr(agent, "CONTEXT_CHAR_BUDGET", 222)
    monkeypatch.setattr(agent, "_complete", lambda messages: '{"answer": "done"}')

    agent.run({"goal": "say done"}, tmp_path)

    first = json.loads((tmp_path / "trace.jsonl").read_text().splitlines()[0])
    declared = first["harness_identity"]["config"]
    assert declared["max_steps"] == 7, declared
    assert declared["output_limit"] == 111, declared
    assert declared["context_chars"] == 222, declared
    # 其余旋钮也在,否则读者只看到一半预算
    for key in ("submit_retries", "parse_retries", "finish_nudge_steps",
                "repeat_nudge_limit"):
        assert isinstance(declared[key], int), (key, declared)


def test_the_declaration_lands_on_the_curve_point(tmp_path):
    """平台把它抬成 `identity.harness_config`,读者不必知道声明的形状。"""
    from harnessgrad.identity import _identity

    man = {"name": "loop", "version": "1"}
    declared = {"harness": "loop", "config": {"max_steps": 16, "output_limit": 4000}}
    identity = _identity(man, "a" * 40, "a", declared=declared)
    assert identity["harness_config"] == {"max_steps": 16, "output_limit": 4000}
    # 原始声明仍然在,取证要看它
    assert identity["harness_identity"] == declared


def test_a_harness_that_declares_nothing_gets_no_config_key(tmp_path):
    """缺席是"这个外壳没说",不是"预算是零"。"""
    from harnessgrad.identity import _identity

    man = {"name": "loop", "version": "1"}
    assert "harness_config" not in _identity(man, "a" * 40, "a", declared=None)
    assert "harness_config" not in _identity(
        man, "a" * 40, "a", declared={"harness": "quiet"})


def test_the_trace_says_whether_the_model_or_the_command_ate_the_budget(tmp_path,
                                                                       monkeypatch):
    """实测:本地 9B 跑 `build-pmars`,26 步撞上题目自带的 900 秒预算被杀。

    记录里只有步数,没有时间 —— 于是"模型慢"和"命令慢"同形。处置相反:模型慢要缩步数
    上限或换模型;命令慢是这道题自己的成本(装依赖),换模型没用。所以每步记两个毫秒数,
    末尾再给一行总和。
    """
    import time as _time

    agent = _load_loop()

    def slow_model(messages):
        _time.sleep(0.05)
        return '{"tool": "bash", "command": "sleep 0.05"}'

    monkeypatch.setattr(agent, "_complete", slow_model)
    monkeypatch.setattr(agent, "MAX_STEPS", 2)
    agent.run({"goal": "burn time"}, tmp_path)

    events = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text().splitlines()]
    replies = [e for e in events if "model_ms" in e]
    commands = [e for e in events if "command_ms" in e]
    assert len(replies) == 2 and len(commands) == 2, events
    assert all(e["model_ms"] >= 40 for e in replies), replies
    assert all(e["command_ms"] >= 40 for e in commands), commands
    wall = [e for e in events if "wall_clock_s" in e]
    assert wall, events
    assert wall[0]["wall_clock_s"]["model"] >= 0.08, wall
    assert wall[0]["wall_clock_s"]["commands"] >= 0.08, wall
    assert wall[0]["wall_clock_s"]["total"] >= wall[0]["wall_clock_s"]["model"], wall
