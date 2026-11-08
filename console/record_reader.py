"""Read and project historical console records without server state.

Paths and live-process observations are supplied by the caller. This module owns
record parsing and the run-row projection; it does not discover roots, authorize
paths, cache lists, or start services. Public server helper names remain aliases.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import time

def read_json(path: pathlib.Path) -> dict:
    """读一个 JSON object。缺失 / 坏掉 / 不是 object 都返回 {}，调用方自己决定怎么显示。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}

def read_curve(run_dir: pathlib.Path) -> tuple[list[dict], str, int]:
    """(points, state, bad_lines)。

    state: ok / missing / empty / unparseable / unreadable
    空文件不能被当成"0 分"或"0 轮"——它是一个没有测点的 run。
    """
    path = run_dir / "curve.jsonl"
    if not path.exists():
        return [], "missing", 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], "unreadable", 0
    if not text.strip():
        return [], "empty", 0
    points: list[dict] = []
    bad = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            points.append(obj)
        else:
            bad += 1
    return points, ("ok" if points else "unparseable"), bad

def as_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def as_num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None

def calls_per_task(calls: int | None, tasks: int | None) -> float | None:
    """每题的 harness 模型调用次数。

    题数未知、题数为 0、调用数未知 -> None（未知），不补 0：分母不知道时
    这个比值没有意义，而 0 是一个确定的结论（这一轮一次都没调模型）。
    """
    if calls is None or tasks is None or tasks <= 0:
        return None
    return calls / tasks

def model_in_loop(calls: int | None) -> bool | None:
    """这一轮的 harness 到底调没调模型：0 -> False（脚本化），未知 -> None。"""
    if calls is None:
        return None
    return calls > 0

def dir_count(path: pathlib.Path, pattern: str) -> int | None:
    """目录不存在 -> None；存在但为空 -> 0。两者在界面上必须能分开。"""
    if not path.is_dir():
        return None
    try:
        return len(list(path.glob(pattern)))
    except OSError:
        return None

_H0 = re.compile(r"^(?P<stamp>h0)-(?P<ts>[0-9TZ]+)-(?P<tail>.*)$")

_RSI = re.compile(r"^(?P<stamp>rsi\d+)-(?P<ts>[0-9TZ]+)-(?P<tail>.*)$")

def split_id(run_id: str) -> tuple[str, str, str]:
    """(stamp, tail, ts) —— 只按平台自己的命名规则拆，不做模糊匹配。"""
    for pattern in (_RSI, _H0):
        m = pattern.match(run_id)
        if m:
            return m.group("stamp"), m.group("tail"), m.group("ts")
    return "", run_id, ""

def pretty(tail: str) -> str:
    return (tail.replace("-ckpt-", " · ckpt ")
                .replace("-ref-", " · ref ")
                .replace("-train", " · train")
                .replace("-", " · ")
                .replace(" · valid_", " · valid_"))

def pool_of(point: dict, tail: str) -> str | None:
    """这个 run 测的是哪个池。eval 运行的 `side` 只说"是评测"，池名在 run_id 尾部。"""
    side = point.get("side") or ""
    if side and side != "eval":
        return side
    for name in ("valid_unseen", "valid_seen", "valid_train", "train"):
        if name in tail:
            return name
    return side or None

def run_row(run_dir: pathlib.Path, live: dict[str, dict],
            points: list[dict] | None = None,
            curve_state: str | None = None,
            curve_errors: int | None = None) -> dict | None:
    if not run_dir.is_dir():
        return None
    if points is None:
        points, curve_state, curve_errors = read_curve(run_dir)
    assert curve_state is not None and curve_errors is not None

    meta = read_json(run_dir / "run_meta.json")
    last = points[-1] if points else {}
    ident = last.get("identity") if isinstance(last.get("identity"), dict) else {}

    scores = []
    for p in points:
        score = as_num(p.get("score"))
        if score is not None:
            scores.append((p.get("round"), score))
    best_round, best = (max(scores, key=lambda x: x[1]) if scores else (None, None))

    # 只有每个测点都写了 n_invalid，才敢给总数；否则是 null（未知），不是 0。
    invalid_values = [as_int(p.get("n_invalid")) for p in points]
    invalid = (sum(invalid_values)
               if invalid_values and all(v is not None for v in invalid_values)
               else None)
    n_tasks = as_int(last.get("n_scored"))
    last_score = as_num(last.get("score"))

    # 模型调用：最新一轮的调用数决定"模型还在不在回路里"；总数只在每个测点都有
    # 调用数时才给。一个测点没写 cost，总和就只是"已知部分的合计"，那是编的。
    call_values = []
    for p in points:
        cost = p.get("cost") if isinstance(p.get("cost"), dict) else {}
        call_values.append(as_int(cost.get("harness_model_calls")))
    total_calls = (sum(call_values)
                   if call_values and all(v is not None for v in call_values)
                   else None)
    last_cost = last.get("cost") if isinstance(last.get("cost"), dict) else {}
    last_calls = as_int(last_cost.get("harness_model_calls"))

    # 进度：progress/round-N/ 里已写了几条轨迹（正在跑的 run 唯一能显示的"进展"）
    progress = None
    prog_root = run_dir / "progress"
    if prog_root.is_dir():
        rounds = sorted((p for p in prog_root.iterdir() if p.is_dir()),
                        key=lambda p: int(re.sub(r"\D", "", p.name) or 0))
        if rounds:
            traces = rounds[-1] / "traces"
            if traces.is_dir():
                progress = {"round": re.sub(r"\D", "", rounds[-1].name) or "0",
                            "done": len(list(traces.glob("*.jsonl")))}

    stamp, tail, ts = split_id(run_dir.name)
    try:
        mtime_s = run_dir.stat().st_mtime
        mtime = time.strftime("%m-%d %H:%M", time.localtime(mtime_s))
    except OSError:
        mtime_s, mtime = None, None

    if not points:
        kind = "unknown"
        status = "running" if run_dir.name in live else "empty"
    else:
        kind = "study" if (len(points) > 1 or last.get("side") == "train") else "eval"
        status = "running" if run_dir.name in live else "done"

    harness_config = ident.get("harness_config")
    if not isinstance(harness_config, dict):
        harness_config = {}

    #: meta-agent 是 harness **和** model，不是只有 model。曲线 identity 优先；
    #: 没有测点（unknown run）时退回 run_meta.improver。读不到就是 null，不是 ""。
    improver = ident.get("improver")
    if not isinstance(improver, dict):
        improver = meta.get("improver")
    if not isinstance(improver, dict):
        improver = {}

    return {
        "id": run_dir.name,
        "stamp": stamp,
        "tail": tail,
        "pretty": pretty(tail),
        "dataset": meta.get("dataset") or None,
        "pool": pool_of(last, tail),
        #: 一个 run 是"研究"(多轮训练)还是"一次测量"(某个 ckpt 在某池上的单点)。
        #: 界面上默认只看研究：一页 240 行里 220 行是单点测量，混在一起没人看得下去。
        "kind": kind,
        "rounds": len(points),
        "has_curve": bool(points),
        "curve_state": curve_state,
        "curve_errors": curve_errors,
        "best": best,
        "best_round": best_round,
        "last": last_score,
        "n_tasks": n_tasks,
        #: harness 这一轮一共调了几次模型（全部轮次），以及最新一轮的每题调用数。
        #: 0 是结论（脚本化 harness），null 是未知；两者在界面上必须能分开。
        "calls": total_calls,
        "calls_per_task": calls_per_task(last_calls, n_tasks),
        "model_in_loop": model_in_loop(last_calls),
        "jobs": as_int(meta.get("jobs")),
        "trials": as_int(meta.get("trials")),
        "invalid": invalid,
        "status": status,
        "agent_model": ident.get("agent_model") or meta.get("agent_model") or None,
        "harness": ident.get("harness_name") or meta.get("harness") or None,
        "harness_sha": (str(ident.get("harness_sha") or "")[:8] or None),
        "steps": as_int(harness_config.get("max_steps")),
        "improver": improver.get("model") or None,
        "improver_harness": improver.get("name") or None,
        "improver_version": improver.get("version") or None,
        "mtime": mtime,
        "mtime_s": mtime_s,
        "progress": progress,
        "live": live.get(run_dir.name),
    }
