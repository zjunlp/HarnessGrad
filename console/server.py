#!/usr/bin/env python3
"""实验台 · 观察 API、对话与受守卫保护的实验启动器。

三个刻意的限制，都是为了**不干扰正在跑的实验**：

1. **观察面只读，启动面受守卫保护**。实验、轨迹、版本都只读；`/chat.html`
   只有在服务端预检查通过且用户二次确认后才允许启动一次 run。
2. **只绑 127.0.0.1**。它是这台机器上的工具，不是服务。
3. **不缓存到磁盘**。列表只在内存里缓存 2 秒，避免边跑边看时反复读 299 个目录。
   唯一的例外是 `/api/explain` 的模型回复：它按
   `(run, round, harness_sha, model, prompt_sha)` 落在 `.cache/explain/*.json`，
   因为一次模型调用很贵，而同样的输入应该得到同样的答案（GET 仍然幂等、可缓存）。

主界面有三屏（实验 / 轨迹 / 版本），另有一个独立对话/启动页。下面标了
**not surfaced** 的接口是保留但没有任何界面在用的只读端点。

读取接口（GET）：
    /api/health                     平台 sha、在跑的 driver、磁盘余量、run 计数
    /api/runs?kind=study|eval|unknown|""  列表（派生字段：轮数、最好、状态、进度）
    /api/runs/<id>                  曲线 + identity + 每轮摘要
    /api/runs/<id>/rounds/<n>       patch + 文件列表 + 轨迹/判定数
    /api/runs/<id>/rounds/<n>/file?path=agent.py
    /api/methods                    [not surfaced] 方法语料 + 校验状态 + 额外文件
    /api/methods/<name>             [not surfaced] 一个方法的元数据 + 文件清单
    /api/methods/<name>/file?path=  [not surfaced] 方法文件正文（带上限；只读）
    /api/protocol                   [not surfaced] methods/protocol.py 的 docstring
    /api/base                       base_harness/loop 的文件清单（起点）
    /api/base/file?path=agent.py    base_harness/loop 的文件正文（只读）
    /api/base-harnesses             [not surfaced] base_harness/*/ 的清单
    /api/base-harness/file?name=&path=  [not surfaced] 某个基线的文件正文（只读，超限截断）
    /api/adapters                   [not surfaced] data/datasets/*.py 的适配器清单（只读 KIND）
    /api/compare?method=<name>&dataset=  同一轮：train 分 vs 三个 eval 池的分（按 method+round 拼）
    /api/coverage                   round × pool 覆盖矩阵（8 个方法 × 每个已测轮次 × 3 个池）
    /api/explain?run=<id>&round=<n>[&force=1]  [not surfaced] LLM 解释这一轮改动（只读；缓存）

显式写接口（POST）：
    /api/chat                       调用配置好的模型；不启动实验
    /api/runs                       经启动守卫检查后启动一次实验

数据形状里最重要的约定：**读不到就是 null，不是 0**。JSON 里的 null 到了前端
会显示成 "—"；`traces: 0` 只在目录存在且确实为空时出现。

    python3 hg-console/server.py [--port 4460]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT = pathlib.Path(__file__).resolve().parent
STATIC = ROOT / "static"
RUNS = pathlib.Path(os.environ.get("HG_CONSOLE_RUNS", "/mnt/20t/xubuqiang/hg-work/rsi10/runs"))
PLATFORM = pathlib.Path(os.environ.get("HG_CONSOLE_PLATFORM", "/mnt/20t/xubuqiang/Study/HarnessGrad"))
SKIP_DIRS = {"__pycache__", ".git", ".state"}
MAX_FILE_BYTES = 400_000
#: 方法文件读进浏览器时的上限。方法 run.py 通常 20–30 KB；超过这个数的文件
#: 在中间栏只显示前 N 行，并明确写出被截断，而不是让浏览器卡住。
METHOD_FILE_BYTES = 120_000
METHOD_FILE_LINES = 2_000
#: 文件面板（对话里技能 A 的第 2 步）读一个文件的上限。短于 run 里的状态文件，
#: 因为面板要在卡片里同时放树和代码：超过就明写“未完整展开”，不给半份行号。
PANEL_FILE_BYTES = 200_000
PANEL_FILE_LINES = 1_200
BASE_HARNESS = PLATFORM / "base_harness" / "loop"
BASE_HARNESSES = PLATFORM / "base_harness"
DATASETS_DIR = PLATFORM / "data" / "datasets"
LIST_TTL_S = 2.0

_list_cache: dict = {"at": 0.0, "rows": []}
_list_lock = threading.Lock()


# ─────────────────────────────────────────────────────── 读工具
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


def boot_time() -> float | None:
    """开机时刻（epoch 秒）。/proc/stat 的 btime，读不到就退回 /proc/uptime。

    `stat` 第 22 个字段是"自开机以来的 clock tick"，不减去开机时刻就会把
    进程开始时间当成已运行时长（一个跑了 7 分钟的 driver 会显示成 80 天）。
    """
    try:
        for line in pathlib.Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        uptime = float(pathlib.Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return time.time() - uptime


def live_drivers() -> dict[str, dict]:
    """pid -> {run_id, elapsed_s}，从 /proc 读，不 sudo、不 pkill。"""
    found: dict[str, dict] = {}
    try:
        entries = list(pathlib.Path("/proc").iterdir())
    except OSError:
        return found
    btime = boot_time()
    hz = os.sysconf("SC_CLK_TCK")
    now = time.time()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [a.decode("utf-8", "replace") for a in argv if a]
        if not any(a.endswith("driver.py") for a in args):
            continue
        run_id = ""
        if "--run-id" in args:
            i = args.index("--run-id")
            run_id = args[i + 1] if i + 1 < len(args) else ""
        start_ticks = None
        try:
            start_ticks = int((entry / "stat").read_text().split()[21])
        except (OSError, ValueError, IndexError):
            start_ticks = None
        if start_ticks is None or btime is None:
            etime = None
        else:
            etime = max(0, round(now - (btime + start_ticks / hz)))
        if run_id:
            found[run_id] = {"pid": int(entry.name), "elapsed_s": etime}
    return found


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


def all_rows(force: bool = False) -> list[dict]:
    now = time.time()
    with _list_lock:
        if not force and now - _list_cache["at"] < LIST_TTL_S:
            return _list_cache["rows"]
        live = live_drivers()
        rows: list[dict] = []
        if RUNS.is_dir():
            try:
                dirs = sorted(RUNS.iterdir())
            except OSError:
                dirs = []
            for d in dirs:
                try:
                    row = run_row(d, live)
                except OSError:
                    continue
                if row:
                    rows.append(row)
        rows.sort(key=lambda r: r["mtime_s"] or 0, reverse=True)
        _list_cache.update({"at": now, "rows": rows})
        return rows


def find_run(run_id: str) -> pathlib.Path | None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id or "") or run_id in (".", ".."):
        return None
    path = RUNS / run_id
    try:
        if path.parent.resolve() != RUNS.resolve() or not path.is_dir():
            return None
    except OSError:
        return None
    return path


def within(base: pathlib.Path, target: pathlib.Path) -> bool:
    """target 是否真的在 base 下面（resolve 过，符号链接逃逸也会被挡住）。"""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def json_safe(value):
    """JSON 里不允许 NaN/Infinity；它们是"没有数"，转成 null，别让整个响应 500。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def kind_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"study": 0, "eval": 0, "unknown": 0}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return counts


# ─────────────────────────────────────────────────────── 各接口
# ─────────────────────────────────────────────────────── 对话 / 跑实验
#: 这是控制台**唯一的写接口**。三条纪律：① 有 driver 活着就拒绝；② run_id 由服务端生成；
#: ③ 每次启动追加一行到 `.launch-log.jsonl`（时间、run_id、完整 argv），出事能倒查。
LAUNCH_DIR = ROOT / ".launch"
LAUNCH_LOG = ROOT / ".launch-log.jsonl"
BUDGET_DIR = pathlib.Path("/tmp/hg-rsi10-lb")
DRIVER = PLATFORM / "driver.py"


def python_supported() -> bool:
    return sys.version_info >= (3, 10)


def adapters() -> list[dict]:
    """可选数据集 = 平台自己发布的清单，不自己扫目录。

    这里原来按 `data/datasets/*.py` 前 4 KB 里找 `KIND = "dataset"`，结果漏了 9 个
    （`alfworld`、`agentdojo`、`gaia`、`terminal_bench`… —— 它们的 KIND 写在文件更后面）。
    `data/scaffolds/registry.py::published()` 被文档指定为"哪些数据集存在"的唯一答案，
    所以直接问它；只有它导不进来时才退回扫目录。
    """
    try:
        sys.path.insert(0, str(PLATFORM))
        from data.scaffolds.registry import published  # noqa: PLC0415
        return [{"name": name, "path": f"data/datasets/{name}.py"} for name in published()]
    except Exception:  # noqa: BLE001 - 平台不可导入时仍然给一个能用的列表
        out = []
        for f in sorted((PLATFORM / "data" / "datasets").glob("*.py")):
            if f.name.startswith("_"):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r'^KIND\s*=\s*"dataset"', text, re.M):
                out.append({"name": f.stem, "path": f"data/datasets/{f.name}"})
        return out


def base_harnesses() -> list[str]:
    root = PLATFORM / "base_harness"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / "harness.json").is_file())


def improvers() -> list[dict]:
    data = read_json(PLATFORM / "improvers" / "improvers.json")
    wanted = data.get("improvers") if isinstance(data.get("improvers"), dict) else {}
    out = []
    for name, spec in sorted(wanted.items()):
        if not isinstance(spec, dict):
            continue
        out.append({"name": name, "model": spec.get("model") or "", "kind": spec.get("kind") or ""})
    return out


def live_drivers_by_run() -> dict[str, dict]:
    """在跑的 driver：读 /proc，不 grep ps（`ps` 会截断 cmdline）。"""
    found: dict[str, dict] = {}
    try:
        entries = list(pathlib.Path("/proc").iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv = [a.decode("utf-8", "replace") for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if not any(a.endswith("driver.py") or a == "driver.py" for a in argv):
            continue
        run_id = ""
        if "--run-id" in argv:
            i = argv.index("--run-id")
            run_id = argv[i + 1] if i + 1 < len(argv) else ""
        found[run_id or f"pid-{entry.name}"] = {"pid": int(entry.name), "argv": argv[:6]}
    return found


# ─────────────────────────────────────────────────────── 对话（模型，服务端代理）
#: key 只在服务端：浏览器拿到的是回复，不是凭证。缺这个文件时接口返回 503，
#: 界面显示"未配置"，而不是假装能聊。
LLM_CONFIG = ROOT / ".local" / "llm.json"

CHAT_SYSTEM = """你是 HarnessGrad 实验台的助手。这个平台在一个共享机器上跑"改 harness"的实验:
给定数据集与基线 harness,一个改进器(improver)多轮地改这个 harness,每轮用平台自己的
评测在给定题集上打分。你只做两件事:

1. 回答关于这些数据的问题(数据集有哪些、某个 run 是什么、曲线怎么读、H0 是什么)。
2. 当用户想**跑一次实验**时,给出配置。不要描述步骤,直接输出一个 fenced 代码块:

```skill
{"dataset": "<名字>", "harness": "<名字>", "improver": "<名字>", "improver_model": "<模型>",
 "rounds": <1-20>, "jobs": <1-64>, "trials": <1-3>, "reason": "<一句话说明为什么这样配>"}
```

可用的值:
  datasets: <<DATASETS>>
  base harnesses: <<HARNESSES>>
  improvers: <<IMPROVERS>>

规则:
- 用中文回答,简短。不要编造数字;不知道就说不知道。
- 基线 harness 默认用 `loop`(平台的研究基线);只有用户明确要别的才换。
- 改进器默认 `dsh`;轮数默认 10、并发默认 12、trials 默认 1,除非用户说了别的。
- **你不需要执行权限**:起跑由平台上的人点两次确认完成。你的输出就是那份配置。
- 只要用户表达了"想跑 / 训练 / 在某个数据集上试",就必须在回复里带上那个 ```skill 代码块
  (可以先写一句解释),不要用自然语言描述步骤、也不要说"我无法直接跑"。
- 起跑由人点两次确认,你只提供配置;不要声称已经启动。
- 如果用户没说要跑,就不要输出 skill 块。
"""


def llm_config() -> dict | None:
    data = read_json(LLM_CONFIG)
    if not data.get("api_key") or not data.get("base_url"):
        return None
    return data


def api_chat(messages: list[dict]) -> tuple[int, dict]:
    cfg = llm_config()
    if not cfg:
        return 503, {"error": "chat-not-configured",
                     "detail": f"缺少 {LLM_CONFIG.relative_to(ROOT)}（base_url / model / api_key）"}
    form = launch_form()
    system = (CHAT_SYSTEM
              .replace("<<DATASETS>>", ", ".join(a["name"] for a in form["datasets"]))
              .replace("<<HARNESSES>>", ", ".join(form["harnesses"]))
              .replace("<<IMPROVERS>>",
                       ", ".join(f"{x['name']}({x['model'] or '模型自选'})" for x in form["improvers"])))
    payload = {"model": cfg.get("model") or "gpt-6.1-sol",
               "messages": [{"role": "system", "content": system}] + [
                   {"role": str(m.get("role") or "user")[:12],
                    "content": str(m.get("content") or "")[:8000]} for m in messages[-12:]],
               "max_tokens": 700, "temperature": 0.2}
    def call() -> dict:
        request = urllib.request.Request(
            cfg["base_url"].rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {cfg['api_key']}",
                     "Content-Type": "application/json",
                     # 这个网关按客户端特征筛请求；显式声明 UA 比让它猜稳。
                     "User-Agent": "codex_cli_rs/0.1 (harnessgrad-console)"})
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    body = None
    last_error = ""
    for _attempt in range(3):
        if _attempt:
            # 网关在连发时会用 403 挡一下（不是鉴权问题：同样的请求隔开几秒就 200）。
            time.sleep(1.2 * _attempt)
        try:
            body = call()
            break
        except urllib.error.HTTPError as exc:
            last_error = f"{exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}"
            if exc.code not in (403, 429, 502, 503):
                break
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = str(exc)[:300]
            break
    if body is None:
        return 502, {"error": "model-unreachable", "detail": last_error}
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return 502, {"error": "model-shape", "detail": json.dumps(body)[:300]}
    usage = body.get("usage") or {}
    return 200, {"text": text or "", "model": body.get("model"),
                 "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens")}}


def launch_form() -> dict:
    """选择器的取值全部来自平台自己，不写死。"""
    names = adapters()
    return {"datasets": names, "harnesses": base_harnesses(), "improvers": improvers(),
            "pools": list(POOLS), "defaults": {"dataset": "alfworld", "harness": "loop",
                                             "rounds": 10, "jobs": 12, "trials": 1,
                                             "sampling": "all"}}


def launch_argv(form: dict, run_id: str) -> list[str]:
    """预览与真正起跑**共用这一个函数** —— 否则"预览的命令"和"实际跑的命令"迟早分叉。"""
    jobs = int(form["jobs"])
    return [sys.executable, str(DRIVER),
            "--harness", str(form["harness"]),
            "--dataset", str(form["dataset"]),
            "--split", "declared",
            "--side", "train",
            "--sampling", str(form.get("sampling") or "all"),
            "--rounds", str(int(form["rounds"])),
            "--trials", str(int(form["trials"])),
            "--jobs", str(jobs),
            "--improver", str(form["improver"]),
            "--improver-model", str(form["improver_model"]),
            "--run-id", run_id,
            "--runs-root", str(RUNS),
            "--gpu-budget", str(jobs),
            "--gpu-budget-dir", str(BUDGET_DIR)]


def new_run_id(form: dict) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = re.sub(r"[^A-Za-z0-9]+", "_", str(form["dataset"])) or "run"
    return f"rsi10-{stamp}-{tag}-{form['improver']}-train"


def guard_launch(form: dict) -> tuple[int, dict]:
    """返回 (状态码, 错误体)；200 表示可以起跑。判断顺序是有意的：先安全，再合法性。"""
    live = live_drivers_by_run()
    if live:
        return 409, {"error": "driver-running", "detail": "已经有一个 driver 在跑，先等它结束",
                     "live": [{"run_id": k, **v} for k, v in live.items()]}
    if not python_supported():
        return 400, {"error": "unsupported-python",
                     "detail": (f"当前 Python 是 {sys.version_info.major}.{sys.version_info.minor}，"
                                "HarnessGrad 需要 Python 3.10+")}
    if not DRIVER.is_file():
        return 400, {"error": "no-driver", "detail": f"找不到 driver.py：{DRIVER}"}
    if not RUNS.is_dir() or not os.access(RUNS, os.W_OK):
        return 400, {"error": "runs-root-not-writable",
                     "detail": f"runs 根目录不存在或不可写：{RUNS}"}
    if not BUDGET_DIR.is_dir():
        return 400, {"error": "no-budget-dir",
                     "detail": f"GPU 预算目录不存在：{BUDGET_DIR}（要先排窗口）"}
    known = {a["name"] for a in adapters()}
    if form.get("dataset") not in known:
        return 400, {"error": "unknown-dataset",
                     "detail": f"{form.get('dataset')!r} 不是 KIND=dataset 的适配器"}
    if form.get("harness") not in set(base_harnesses()):
        return 400, {"error": "unknown-harness", "detail": "基线 harness 不存在"}
    if not form.get("improver"):
        return 400, {"error": "no-improver", "detail": "train 侧必须指定改进器"}
    for key, lo, hi in (("rounds", 1, 20), ("jobs", 1, 64), ("trials", 1, 3)):
        try:
            value = int(form[key])
        except (KeyError, TypeError, ValueError):
            return 400, {"error": "bad-value", "detail": f"{key} 必须是整数"}
        if not lo <= value <= hi:
            return 400, {"error": "bad-value", "detail": f"{key} 必须在 {lo}..{hi}"}
    return 200, {}


def api_launch(form: dict) -> tuple[int, dict]:
    code, err = guard_launch(form)
    if code != 200:
        return code, err
    run_id = new_run_id(form)
    argv = launch_argv(form, run_id)
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_DIR / f"{run_id}.log"
    with log_path.open("ab") as handle:
        subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT,
                         start_new_session=True, cwd=str(PLATFORM))
    with LAUNCH_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "run_id": run_id, "argv": argv, "by": "console chat"},
                                ensure_ascii=False) + "\n")
    return 202, {"run_id": run_id, "argv": argv, "log": str(log_path.relative_to(ROOT))}


def launch_preview(form: dict) -> dict:
    """返回命令预览和与真正起跑相同的守卫结果，不产生任何副作用。"""
    code, err = guard_launch(form)
    try:
        argv = launch_argv(form, "rsi10-<UTC>-<dataset>-<improver>-train")
    except (KeyError, TypeError, ValueError):
        argv = []
        if code == 200:
            code = 400
            err = {"error": "bad-value", "detail": "运行参数不完整或格式不正确"}
    return {
        "argv": argv,
        "form": form,
        "preflight": {"ok": code == 200, "status": code, **err},
    }


def api_health() -> dict:
    live = live_drivers()
    rows = all_rows()
    _base, _model, _key, explain_missing = explain_config()
    sha = ""
    for row in rows[:16]:
        meta = read_json(RUNS / row["id"] / "run_meta.json")
        platform = meta.get("platform")
        sha = (platform.get("content_sha") if isinstance(platform, dict) else "") or ""
        if sha:
            break
    try:
        usage = shutil.disk_usage("/")
        disk_free = round(usage.free / 2**30, 1)
    except OSError:
        disk_free = None
    # 只报告落在本 console 的 runs 根目录下的 driver；别的 work-root 不属于这一屏。
    running = [{"run_id": k, **v} for k, v in sorted(live.items()) if find_run(k)]
    return {
        "platform_content_sha": sha or None,
        "running": running,
        "disk_free_gb": disk_free,
        "runs_root": str(RUNS),
        "runs_count": len(rows),
        "kinds": kind_counts(rows),
        # 只报告“是否配置”，不报告 base_url / model / key。前端据此在未配置时
        # 直接显示 setup note，而不是先发一个注定 503 的请求（浏览器会把它记成
        # console error，而只读台的控制台必须是干净的）。
        "explain_configured": not explain_missing,
        "explain_missing_env": explain_missing,
    }


def api_run(run_id: str) -> dict | None:
    path = find_run(run_id)
    if not path:
        return None
    points, curve_state, curve_errors = read_curve(path)
    row = run_row(path, live_drivers(), points, curve_state, curve_errors)
    if not row:
        return None
    rounds = []
    bad_points = 0
    for p in points:
        n = as_int(p.get("round"))
        if n is None or n < 0:
            bad_points += 1
            continue
        cost = p.get("cost") if isinstance(p.get("cost"), dict) else {}
        calls = as_int(cost.get("harness_model_calls"))
        patch = path / "diffs" / f"round-{n}.patch"
        state = path / f"state_after_round{n}"
        traces = path / f"round{n}_traces"
        ident = p.get("identity") if isinstance(p.get("identity"), dict) else {}
        rounds.append({
            "round": n,
            "score": as_num(p.get("score")),
            "n_scored": as_int(p.get("n_scored")),
            "n_invalid": as_int(p.get("n_invalid")),
            "n_harness_failed": as_int(p.get("n_harness_failed")),
            "harness_sha": (str(ident.get("harness_sha") or "")[:8] or None),
            "tokens": as_int(cost.get("harness_tokens")),
            "calls": calls,
            "calls_per_task": calls_per_task(calls, as_int(p.get("n_scored"))),
            "model_in_loop": model_in_loop(calls),
            "wall_s": as_num(cost.get("wall_clock_s")),
            "has_diff": patch.is_file(),
            "has_state": state.is_dir(),
            "has_traces": traces.is_dir(),
            "traces": dir_count(traces, "*.jsonl"),
            "patch_size": patch.stat().st_size if patch.is_file() else None,
        })
    meta = read_json(path / "run_meta.json")
    return {
        "row": row,
        "rounds": rounds,
        "bad_points": bad_points,
        "curve_state": curve_state,
        "curve_errors": curve_errors,
        #: 这个方法名只用于“回实验室”和 eval 池：先看 run_meta 里记录的入口，
        #: 再看 run id 的形状；都没有就是 null（界面显示 —，不猜）。
        "method": method_of_run(run_id, meta),
        "meta": {k: meta.get(k) for k in ("dataset", "harness", "jobs", "trials",
                                          "sampling_policy", "platform", "config")},
    }


#: `rsi10-<stamp>-<method>-train` / `...-ckpt-r<N>-<pool>`。和 api_compare 的
#: _STUDY / _CKPT 是同一段 id 形状，但这里只需要 method 那一段。
_RUN_METHOD = re.compile(
    r"^[^-]+-[0-9]{8}T[0-9]{4,6}Z-(?P<method>[A-Za-z0-9_]+)"
    r"-(?:train|ckpt-r\d+-valid_(?:unseen|seen|train))$")


def method_of_run(run_id: str, meta: dict | None = None) -> str | None:
    """run 是哪个方法跑出来的：先读 `run_meta.json` 的入口，再看 run id。"""
    entry = (((meta or {}).get("config") or {}).get("run") or {}).get("method_entrypoint")
    if isinstance(entry, str):
        match = re.search(r"/methods/([^/]+)/run\.py$", entry)
        if match:
            return match.group(1)
    match = _RUN_METHOD.match(run_id or "")
    return match.group("method") if match else None


def api_round(run_id: str, n: int) -> dict | None:
    path = find_run(run_id)
    if not path:
        return None
    patch_path = path / "diffs" / f"round-{n}.patch"
    stat = read_json(path / "diffs" / f"round-{n}.json")
    state = path / f"state_after_round{n}"
    files: list[dict] = []
    if state.is_dir():
        try:
            for f in sorted(state.rglob("*")):
                rel = f.relative_to(state)
                if f.is_dir() or any(part in SKIP_DIRS for part in rel.parts):
                    continue
                try:
                    size = f.stat().st_size
                except OSError:
                    size = None
                files.append({"path": str(rel), "size": size})
        except OSError:
            files = []
    traces = path / f"round{n}_traces"
    verdicts = path / "verdicts" / f"round-{n}"
    has_patch = patch_path.is_file()
    return {
        "round": n,
        "patch": patch_path.read_text(encoding="utf-8", errors="replace") if has_patch else "",
        "has_patch": has_patch,
        "patch_size": patch_path.stat().st_size if has_patch else None,
        "stat": stat,
        "files": files,
        "has_state": state.is_dir(),
        "traces": dir_count(traces, "*.jsonl"),
        "has_traces": traces.is_dir(),
        "verdicts": dir_count(verdicts, "*.json"),
        "has_verdicts": verdicts.is_dir(),
    }


def api_file(run_id: str, n: int, rel: str) -> dict | None:
    path = find_run(run_id)
    if not path:
        return None
    base = path / f"state_after_round{n}"
    if not base.is_dir() or not within(base, base / rel):
        return None
    target = (base / rel).resolve()
    if not target.is_file():
        return None
    try:
        size = target.stat().st_size
    except OSError:
        return None
    if size > MAX_FILE_BYTES:
        return {
            "path": rel,
            "size": size,
            "text": "",
            "truncated": True,
            "note": f"文件 {human_bytes(size)}，超过 400 KB（400,000 字节）读取上限，未在浏览器展开。",
        }
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {"path": rel, "size": size, "text": text, "truncated": False, "note": ""}


def human_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 2**20:
        return f"{n / 2**20:.1f} MB"
    if n >= 2**10:
        return f"{n / 2**10:.1f} KB"
    return f"{n} B"


def _verdict(path: pathlib.Path, task_id: str, n: int) -> dict:
    return read_json(path / "verdicts" / f"round-{n}" / f"{task_id}.json")


def _trace_files(path: pathlib.Path, n: int) -> list[pathlib.Path]:
    d = path / f"round{n}_traces"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def _task_of(stem: str) -> tuple[str, int]:
    """`<task-id>` 或 `<task-id>.trial<k>` → (task_id, trial)。"""
    if ".trial" in stem:
        head, _, tail = stem.rpartition(".trial")
        try:
            return head, int(tail)
        except ValueError:
            return stem, 1
    return stem, 1


def round_exists(path: pathlib.Path, n: int) -> bool:
    """这一轮在这个 run 的曲线里存在吗。

    三种情况要分开，因为界面对它们的反应不同：
      · 轮次不存在       → 400：你问了一个不存在的轮次
      · 存在但没留轨迹   → 200 + 空列表：这一轮确实没保留轨迹
      · 存在且有轨迹     → 200 + 列表
    否则 `rounds/999` 会回一个空壳，界面把它读成"这一轮没有编辑/没有轨迹"。
    """
    return any(point.get("round") == n for point in read_curve(path)[0])


def api_traces(run_id: str, n: int) -> dict | None:
    """这一轮的轨迹清单。分数来自该轮的判定文件；没有判定就显示 None，不是 0。"""
    path = find_run(run_id)
    if not path:
        return None
    per_task: dict = {}
    for point in read_curve(path)[0]:
        if point.get("round") == n:
            raw = point.get("per_task")
            per_task = raw if isinstance(raw, dict) else {}
            break
    tasks = []
    for f in _trace_files(path, n):
        task_id, trial = _task_of(f.stem)
        verdict = _verdict(path, task_id, n)
        score = verdict.get("score")
        if score is None:
            score = per_task.get(task_id)
        steps = 0
        solved = None
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            step = as_int(ev.get("step"))
            if step is not None:
                steps = max(steps, step + 1)
            if ev.get("environment_solved") is not None:
                solved = bool(ev.get("environment_solved"))
        tasks.append({"task_id": task_id, "trial": trial, "name": f.name,
                      "steps": steps, "bytes": f.stat().st_size,
                      "score": score, "solved": solved,
                      "detail": (verdict.get("detail") or "")[:300]})
    tasks.sort(key=lambda t: (t["score"] is None, t["score"] if t["score"] is not None else 0,
                              t["task_id"]))
    return {"run": run_id, "round": n, "tasks": tasks, "count": len(tasks)}


def api_trace(run_id: str, n: int, name: str) -> dict | None:
    """一条轨迹，按步渲染。长输出裁剪并标注，不静默截断。"""
    path = find_run(run_id)
    base = path / f"round{n}_traces" if path else None
    if not path or not base.is_dir() or "/" in name or "\\" in name:
        return None
    if not within(base, base / name):
        return None
    target = (base / name).resolve()
    if not target.is_file():
        return None
    task_id, trial = _task_of(target.stem)
    verdict = _verdict(path, task_id, n)
    events: list[dict] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if "harness_identity" in ev:
            events.append({"kind": "identity", "config": ev["harness_identity"]})
        elif "reply" in ev:
            events.append({"kind": "reply", "step": ev.get("step"), "model_ms": ev.get("model_ms"),
                           "text": str(ev.get("reply") or "")[:8000]})
        elif "command" in ev:
            out = str(ev.get("output") or "")
            events.append({"kind": "command", "step": ev.get("step"), "command": ev.get("command"),
                           "exit": ev.get("exit"), "command_ms": ev.get("command_ms"),
                           "output": out[:6000], "output_chars": len(out),
                           "truncated": len(out) > 6000})
        elif "env_step" in ev:
            events.append({"kind": "env", "step": ev.get("step"),
                           "env_step": ev.get("env_step"),
                           "solved": ev.get("environment_solved")})
        elif "wall_clock_s" in ev:
            events.append({"kind": "meta", "label": "wall clock", "value": f"{ev['wall_clock_s']} s"})
        elif "usage" in ev:
            events.append({"kind": "meta", "label": "tokens", "value": json.dumps(ev["usage"])})
        elif "context_trims" in ev:
            events.append({"kind": "meta", "label": "context trims",
                           "value": f"{ev.get('context_trims')} 次（保留 {ev.get('history_kept')} 条）"})
        if len(events) >= 1200:
            events.append({"kind": "meta", "label": "截断", "value": "事件超过 1200 条，只显示前 1200 条"})
            break
    return {"run": run_id, "round": n, "name": name, "task_id": task_id, "trial": trial,
            "events": events, "score": verdict.get("score"),
            "detail": verdict.get("detail") or "", "verdict_kind": verdict.get("kind")}


def _method_dir(name: str) -> pathlib.Path | None:
    """`methods/<name>/`，名字只允许安全字符，且解析后必须仍在 methods/ 下面。"""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name or "") or name in (".", ".."):
        return None
    root = PLATFORM / "methods"
    d = root / name
    try:
        if not d.is_dir() or not within(root, d):
            return None
    except OSError:
        return None
    return d


def _deviation_items(spec: dict) -> list[dict] | None:
    """`method.json.deviations` -> 稳定的条目列表；没有 method.json 时是 null，不是 []。

    两种写法都见过：dict 条目（what/why/direction/id）和纯字符串。缺字段的条目
    不编造，保留已有键；`what` 是界面上“可以借什么”的正文。
    """
    if not spec:
        return None
    raw = spec.get("deviations")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            row = {}
            for key in ("id", "what", "why", "direction"):
                value = item.get(key)
                if value is None or isinstance(value, (str, int, float, bool)):
                    row[key] = value
                else:
                    row[key] = json.dumps(value, ensure_ascii=False)[:2000]
            if not any(row.get(k) for k in ("id", "what", "why")):
                row["what"] = json.dumps(item, ensure_ascii=False)[:2000]
            out.append(row)
        elif isinstance(item, str):
            out.append({"what": item})
        else:
            out.append({"what": str(item)})
    return out


def _method_files(d: pathlib.Path) -> list[dict]:
    """方法目录里可读的文件。run.py / method.json 先列，其余按路径排。"""
    out: list[dict] = []
    run_path = d / "run.py"
    spec_path = d / "method.json"
    if run_path.is_file():
        out.append({"path": "run.py", "size": _size_of(run_path), "role": "runner"})
    if spec_path.is_file():
        out.append({"path": "method.json", "size": _size_of(spec_path), "role": "spec"})
    try:
        entries = sorted(d.rglob("*"))
    except OSError:
        return out
    for f in entries:
        try:
            rel = f.relative_to(d)
        except ValueError:
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if not f.is_file() or str(rel) in ("run.py", "method.json"):
            continue
        out.append({"path": str(rel), "size": _size_of(f), "role": "extra"})
    return out


def _size_of(path: pathlib.Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _method_row(d: pathlib.Path) -> dict | None:
    spec_path = d / "method.json"
    run_path = d / "run.py"
    if not spec_path.is_file() and not run_path.is_file():
        return None
    spec = read_json(spec_path)
    has_spec = bool(spec)
    has_run_py = run_path.is_file()
    deviations = _deviation_items(spec)
    note = (spec.get("note") or spec.get("what") or spec.get("summary") or "")
    if not isinstance(note, str):
        note = str(note)
    rule_test = PLATFORM / "tests" / "quality" / f"test_{d.name}_rule.py"
    has_test = rule_test.is_file()
    title = spec.get("title") or ""
    kind = spec.get("kind") or None
    fields = {key: bool(spec.get(key)) for key in ("kind", "title", "note")}
    blockers: list[str] = []
    if not has_run_py:
        blockers.append("没有 run.py：方法没有入口")
    if not has_test:
        blockers.append(f"没有规则测试：tests/quality/test_{d.name}_rule.py 不存在")
    if not has_spec:
        blockers.append("没有可解析的 method.json：kind / title / note 都读不到")
    else:
        missing = [key for key, ok in fields.items() if not ok]
        if missing:
            blockers.append(f"method.json 缺字段：{'、'.join(missing)}")
    return {
        "name": d.name,
        "dir": f"methods/{d.name}",
        "entrypoint": f"python3 {run_path}" if has_run_py else None,
        "title": title,
        "kind": kind,
        "has_spec": has_spec,
        "has_run_py": has_run_py,
        "deviations": deviations,
        "deviation_count": None if deviations is None else len(deviations),
        "extra_files": [f for f in _method_files(d)
                        if f["role"] == "extra"],
        "has_test": has_test,
        "test_path": f"tests/quality/test_{d.name}_rule.py" if has_test else None,
        #: “能否作为起点”= 有入口、有规则测试、有可解析的 spec。只检查存在，
        #: 本台不执行测试 —— 执行已经是起跑，M4 之前不做。
        "can_start": has_run_py and has_test and has_spec and all(fields.values()),
        "start_blockers": blockers,
        "validation": {
            "has_run_py": has_run_py,
            "has_test": has_test,
            "has_spec": has_spec,
            "fields": fields,
            "ok": has_run_py and has_test and has_spec and all(fields.values()),
        },
        "summary": note[:300],
        "note": note,
    }


def api_methods() -> list[dict]:
    out: list[dict] = []
    root = PLATFORM / "methods"
    if not root.is_dir():
        return out
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for d in dirs:
        if d.name.startswith(".") or d.name in SKIP_DIRS:
            continue
        row = _method_row(d)
        if row:
            out.append(row)
    return out


def api_method(name: str) -> dict | None:
    """一个方法的元数据 + 文件清单（不含文件正文）。"""
    d = _method_dir(name)
    if not d:
        return None
    row = _method_row(d)
    if not row:
        return None
    row["path"] = f"methods/{d.name}"
    row["files"] = _method_files(d)
    return row


def _read_text_capped(target: pathlib.Path, rel: str,
                      max_bytes: int = METHOD_FILE_BYTES,
                      max_lines: int = METHOD_FILE_LINES) -> dict | None:
    """读一个只读文件，带上限和一句人话解释。超过上限时不读正文。"""
    try:
        size = target.stat().st_size
    except OSError:
        return None
    if size > max_bytes:
        return {
            "path": rel, "size": size, "text": "", "truncated": True,
            "lines": None, "shown_lines": 0,
            "note": f"文件 {human_bytes(size)}，超过 {human_bytes(max_bytes)} 读取上限，未在浏览器展开。",
        }
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    all_lines = text.splitlines()
    total = len(all_lines)
    if total > max_lines:
        return {
            "path": rel, "size": size,
            "text": "\n".join(all_lines[:max_lines]), "truncated": True,
            "lines": total, "shown_lines": max_lines,
            "note": f"只显示前 {max_lines} 行（共 {total} 行）。",
        }
    return {
        "path": rel, "size": size, "text": text, "truncated": False,
        "lines": total, "shown_lines": total, "note": "",
    }


def api_method_file(name: str, rel: str) -> dict | None:
    d = _method_dir(name)
    if not d or not rel:
        return None
    target = (d / rel).resolve()
    if not within(d, target) or not target.is_file():
        return None
    return _read_text_capped(target, rel)


def api_protocol() -> dict:
    """`methods/protocol.py` 的模块 docstring —— 服务端读，前端只引用，不转述。"""
    path = PLATFORM / "methods" / "protocol.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"path": "methods/protocol.py", "doc": None, "sha": None,
                "lines": None, "error": "读不到 methods/protocol.py"}
    doc = None
    error = ""
    try:
        doc = ast.get_docstring(ast.parse(text), clean=False)
    except (SyntaxError, ValueError) as exc:
        error = f"protocol.py 解析失败：{exc}"
    if not doc:
        error = error or "protocol.py 没有模块 docstring"
    return {
        "path": "methods/protocol.py",
        "doc": doc,
        "sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "lines": len(text.splitlines()),
        "error": error or None,
    }


def api_base() -> dict:
    """基线起点 `base_harness/loop` 的清单。

    `harness_sha` 是 null：run 里记的 sha 是 staging 之后（会补 `.gitignore` /
    `.state/`）的 git tree hash，本台不重算，也不拿别的 sha 冒充它。
    """
    base = BASE_HARNESS
    if not base.is_dir():
        return {"path": "base_harness/loop", "exists": False, "manifest": {},
                "files": [], "harness_sha": None,
                "sha_note": "base_harness/loop 不存在"}
    files: list[dict] = []
    try:
        entries = sorted(base.rglob("*"))
    except OSError:
        entries = []
    for f in entries:
        try:
            rel = f.relative_to(base)
        except ValueError:
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if not f.is_file():
            continue
        files.append({"path": str(rel), "size": _size_of(f)})
    manifest = read_json(base / "harness.json")
    return {
        "path": "base_harness/loop",
        "exists": True,
        "manifest": {key: manifest.get(key) for key in
                     ("name", "version", "entrypoint", "backend", "install")},
        "files": files,
        "harness_sha": None,
        "sha_note": "基线没有 run 记录；run 里的 harness_sha 是 staging 后的 git tree hash，本台不重算。",
    }


def api_base_file(rel: str) -> dict | None:
    base = BASE_HARNESS
    if not base.is_dir() or not rel:
        return None
    target = (base / rel).resolve()
    if not within(base, target) or not target.is_file():
        return None
    return _read_text_capped(target, rel)


#: `harness.json` 里跟"这是什么起点"有关的字段。其余的（install/backend 的细节）
#: 不是这一步要回答的问题，不搬上屏。
MANIFEST_KEYS = ("name", "version", "entrypoint", "backend", "install")


def _base_harnesses_root() -> pathlib.Path:
    return BASE_HARNESSES


def _base_harness_dirs() -> list[pathlib.Path]:
    root = _base_harnesses_root()
    if not root.is_dir():
        return []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    return [p for p in entries
            if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".")]


def _safe_rel_files(root: pathlib.Path) -> list[dict]:
    """root 下的可读文件（跳过 __pycache__/.state/隐藏目录），按路径排序。"""
    out: list[dict] = []
    try:
        entries = sorted(root.rglob("*"))
    except OSError:
        return out
    for f in entries:
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if not f.is_file():
            continue
        out.append({"path": str(rel), "size": _size_of(f)})
    return out


def api_base_harnesses() -> dict:
    """`base_harness/*/` 的清单 —— 技能 A 第 1 步的候选起点。

    只读目录和 `harness.json`；不 import、不执行、不复制。sha 是 null：
    基线没有 run 记录，run 里的 harness_sha 是 staging 之后的 git tree hash。
    """
    rows: list[dict] = []
    for d in _base_harness_dirs():
        manifest = read_json(d / "harness.json")
        files = _safe_rel_files(d)
        rows.append({
            "name": d.name,
            "path": f"base_harness/{d.name}",
            "manifest": {key: manifest.get(key) for key in MANIFEST_KEYS},
            "files": files,
            "file_count": len(files),
            "bytes": sum(f["size"] for f in files if isinstance(f["size"], int)),
            "harness_sha": None,
        })
    return {
        "root": "base_harness",
        "rows": rows,
        "count": len(rows),
        "sha_note": ("基线没有 run 记录；run 里的 harness_sha 是 staging（补 .gitignore/.state）"
                     "之后的 git tree hash，本台不重算，也不拿别的 sha 冒充它。"),
        "note": ("`base_harness/*/` 是已存在的起点；这一屏只读它们，不复制、不执行、不修改。"
                 "没有列出来的目录（__pycache__ 等）不是起点。"),
    }


def _base_harness_dir(name: str) -> pathlib.Path | None:
    if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name or "")
            or name in (".", "..") or name in SKIP_DIRS or name.startswith(".")):
        return None
    root = _base_harnesses_root()
    d = root / name
    try:
        if not d.is_dir() or not within(root, d):
            return None
    except OSError:
        return None
    return d


def api_base_harness_file(name: str, rel: str) -> dict | None:
    """`base_harness/<name>/<rel>` 的正文（只读，超限截断并说明）。"""
    d = _base_harness_dir(name)
    if not d or not rel:
        return None
    try:
        target = (d / rel).resolve()
    except OSError:
        return None
    if not within(d, target) or not target.is_file():
        return None
    return _read_text_capped(target, rel, max_bytes=PANEL_FILE_BYTES,
                             max_lines=PANEL_FILE_LINES)


_KIND_RE = re.compile(r"^KIND\s*=\s*[\"']([A-Za-z_]+)[\"']\s*$", re.M)
_SPLIT_RE = re.compile(r"^SPLIT\s*=", re.M)


def api_adapters() -> dict:
    """`data/datasets/*.py` 的适配器清单（技能 A 第 1 步的数据集）。

    不 import 适配器：导入会执行模块、可能下载数据，而这一屏只负责"列出能选的"。
    这里读的是每个模块自己声明的 `KIND` —— `data/scaffolds/registry.py` 也用同一
    条规矩（`KIND == "dataset"` 才是可选的基准，其余是平台自己的 fixture）。
    导入失败 / 缺依赖只会在真起跑时暴露，本台不替它下结论。
    """
    rows: list[dict] = []
    if DATASETS_DIR.is_dir():
        try:
            entries = sorted(DATASETS_DIR.glob("*.py"))
        except OSError:
            entries = []
        for path in entries:
            if path.stem in ("__init__", "registry"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _KIND_RE.search(text)
            kind = m.group(1) if m else None
            rows.append({
                "name": path.stem,
                "kind": kind,
                "published": kind == "dataset",
                "module": f"data.datasets.{path.stem}",
                "path": f"data/datasets/{path.name}",
                "has_split": bool(_SPLIT_RE.search(text)),
            })
    return {
        "root": "data/datasets",
        "rows": rows,
        "count": len(rows),
        "published": [r["name"] for r in rows if r["published"]],
        "note": ("KIND == \"dataset\" 的才是可选数据集；其余是平台自己的 fixture。"
                 "本台不 import 适配器，也不下载数据。"),
    }


# ─────────────────────────────────────────────────────── 对比：train 分 vs eval 池
#: 这一屏要回答的问题只有一个：train 列是 improver **见过**的 100 个任务上的分，
#: eval 三列是别的池上的分。两个数字在数据集里本来就是分开的 run，所以这里按
#: method + round 把它们拼起来 —— 但不能拼错。
POOLS = ("valid_unseen", "valid_seen", "valid_train")

#: 评测侧的单点 run：`rsi10-<stamp>-<method>-ckpt-r<N>-<pool>`，一个 run 一个分。
#: 只认这一段：`-repair-…` / `-fix` 之类的尾巴不是同一批测量，宁可不进这一屏。
_CKPT = re.compile(
    r"^rsi10-(?P<stamp>[0-9]{8}T[0-9]{4,6}Z)-(?P<method>[A-Za-z0-9_]+)"
    r"-ckpt-r(?P<round>\d{1,3})-(?P<pool>valid_unseen|valid_seen|valid_train)$")

#: 训练侧的多轮 run：`rsi10-<stamp>-<method>-train`。
_STUDY = re.compile(r"^(?P<method>[A-Za-z0-9_]+)-train$")
_STAMP = re.compile(r"^[a-z0-9]+-(?P<stamp>[0-9]{8}T[0-9]{4,6}Z)-")


def stamp_of(run_id: str) -> str:
    """`rsi10-20261018T062351Z-…` 里的 `20261018T062351Z`。

    注意 `run_row()["stamp"]` 是 `rsi10`（脚本名），不是这一批实验的时间戳；
    两批训练（`20261017T123952Z` / `20261018T062351Z`）的 ckpt 可以同名，
    所以拼的时候必须看这个时间戳。
    """
    m = _STAMP.match(run_id or "")
    return m.group("stamp") if m else ""


def eval_index(rows: list[dict], dataset: str) -> dict:
    """method -> round -> pool -> [测量]，一次遍历。

    只有真的读出分数的 run 才算一次测量；`curve.jsonl` 为空的 run
    （跑过但没测出分）不是 0 分，这里直接不收录，界面显示 "—"。
    """
    index: dict = {}
    for row in rows:
        if dataset and (row.get("dataset") or "") != dataset:
            continue
        score = as_num(row.get("last"))
        if score is None:
            continue
        m = _CKPT.match(row["id"])
        exam = _EVAL_EXAM.match(row["id"])
        if not m and not exam:
            continue
        n_scored = n_invalid = n_failed = None
        if m:
            method, pool, round_no = m.group("method"), m.group("pool"), int(m.group("round"))
            stamp = m.group("stamp")
        else:
            # exam 名字里没有轮次：轮次只写在曲线的 `evaluated_from.round` 里。
            # 少读这一行，这个状态就会被当成"另一批的 round N"，而这正是
            # dream_rsi r6 曾经在对比里显示 0.000 的原因。
            points = read_curve(RUNS / row["id"])[0]
            point = points[-1] if points else {}
            round_no = (point.get("evaluated_from") or {}).get("round")
            if round_no is None:
                continue
            n_scored = as_int(point.get("n_scored"))
            n_invalid = as_int(point.get("n_invalid"))
            n_failed = as_int(point.get("n_harness_failed"))
            method, pool = exam.group("method"), exam.group("pool")
            stamp = exam.group("stamp")
        record = {
            "run_id": row["id"],
            "stamp": stamp,
            "form": "ckpt" if m else "exam",
            "score": score,
            "n_scored": n_scored,
            "n_invalid": n_invalid,
            "n_harness_failed": n_failed,
            "harness_sha": row.get("harness_sha"),
            "mtime_s": row.get("mtime_s") or 0.0,
        }
        (index.setdefault(method, {})
                   .setdefault(int(round_no), {})
                   .setdefault(pool, [])
                   .append(record))
    return index


def study_candidates(rows: list[dict], dataset: str) -> dict:
    """method -> [训练 run]。一个方法可能被训练过好几次，选哪一次见 pick_study。"""
    out: dict = {}
    for row in rows:
        if row.get("kind") != "study" or not row.get("has_curve"):
            continue
        m = _STUDY.match(row.get("tail") or "")
        if not m:
            continue
        if dataset and (row.get("dataset") or "") != dataset:
            continue
        out.setdefault(m.group("method"), []).append(row)
    return out


def _stamp_coverage(stamp: str, by_round: dict) -> int:
    """这个 stamp 下有几个 (round, pool) 真的测出了分。"""
    if not stamp:
        return 0
    return sum(1 for pools in by_round.values() for records in pools.values()
               if any(rec["stamp"] == stamp for rec in records))


def pick_study(candidates: list[dict], by_round: dict) -> dict | None:
    """同一个方法可能有好几批训练。选**和自己那批 ckpt 评测对得上**的那一批。

    dream_rsi / meta_harness 各有两批：`20261017T123952Z` 和 `20261018T062351Z`。
    两批的 `round N` 不是同一个 state，混着拼会得到一个不存在的分数。
    所以排序是：① 本 stamp 的 ckpt 覆盖数（越多说明这一批才是被认真测过的那次）
    ② 轮数 ③ 时间戳。只有 r0 的 run（没开始训练）没有 ckpt 可对，直接排除。
    """
    ranked = []
    for row in candidates:
        if as_int(row.get("rounds")) is None or int(row["rounds"]) < 2:
            continue
        stamp = stamp_of(row["id"])
        ranked.append((_stamp_coverage(stamp, by_round), int(row["rounds"]), stamp, row))
    if not ranked:
        return None
    # 只按 tuple[:3] 比，不把 dict 塞进比较里。
    return max(ranked, key=lambda item: item[:3])[3]


def _cell_valid(rec: dict, pool: str) -> bool:
    """这一格是不是"把整个池测满且没有无效题"。ckpt 形式没有逐题计数时为 None（未知）。"""
    size = COVERAGE_POOL_SIZES.get(pool)
    if rec.get("n_scored") is None:
        return True          # ckpt 形式：没有计数可查，沿用原行为
    return (rec["n_scored"] == size and not (rec.get("n_invalid") or 0)
            and not (rec.get("n_harness_failed") or 0))


def _measure(records: list[dict], study_stamp: str, pool: str = "") -> dict | None:
    """同一个 (round, pool) 在多批里都有时：先要 study run 自己那批，再退回最新的。

    退回去的那次一定带着 `stamp` 一起返回，界面上会挂一个 stamp chip —— 拼的是
    两次测量，不能让它看起来像一次。
    """
    if not records:
        return None
    # 有效性优先于批次：宁可显示 `—`，也不拿一次缺题/带无效题的测量去填一个格。
    valid = [rec for rec in records if _cell_valid(rec, pool)]
    picked = valid or records
    same = [rec for rec in picked if rec["stamp"] == study_stamp]
    chosen = same or picked
    return max(chosen, key=lambda rec: (rec["stamp"] == study_stamp, rec["stamp"],
                                        rec["mtime_s"]))


def api_compare(method: str, dataset: str = "") -> dict:
    rows = all_rows()
    evals = eval_index(rows, dataset)
    studies = study_candidates(rows, dataset)

    #: 只有两侧都在的方法才进选择器；只训练没评测（或反过来）的方法不在这里出现。
    picked: dict[str, dict] = {}
    for name in sorted(set(evals) | set(studies)):
        by_round = evals.get(name) or {}
        row = pick_study(studies.get(name) or [], by_round)
        if row is None or not by_round:
            continue
        picked[name] = row

    out: dict = {
        "dataset": dataset or None,
        "pools": list(POOLS),
        "methods": [],
        "method_rows": [],
        "study": None,
        "series": [],
        "gap": [],
        "note": "",
    }

    def summary(name: str, row: dict) -> dict:
        by_round = evals.get(name) or {}
        measured = sum(1 for pools in by_round.values()
                       if any(rec["stamp"] == stamp_of(row["id"])
                              for records in pools.values() for rec in records))
        return {
            "name": name,
            "study_run_id": row["id"],
            "stamp": stamp_of(row["id"]) or None,
            "dataset": row.get("dataset"),
            "rounds": as_int(row.get("rounds")),
            "measured_rounds": measured,
            "best": row.get("best"),
            "last": row.get("last"),
        }

    #: 测量最全的排前面：默认打开的一屏应该是有东西看的那一屏。
    order = sorted(picked, key=lambda n: (-(summary(n, picked[n])["measured_rounds"]),
                                          -(as_int(picked[n].get("rounds")) or 0), n))
    out["methods"] = order
    out["method_rows"] = [summary(n, picked[n]) for n in order]

    #: 不传 method 时打开第一项（测量最全的那个），这样直接打 /api/compare 也是一屏数据，
    #: 而不是要前端先拿列表再问第二次。选了哪个方法一定写在 `method` 里。
    if not method and order:
        method = order[0]
    out["method"] = method or None

    row = picked.get(method)
    if row is None:
        if dataset and not picked:
            out["note"] = f"dataset={dataset} 下没有两侧都齐的方法（训练 run 与 ckpt 评测缺一侧）。"
        elif not method:
            out["note"] = "没有可以对比的方法：两侧都齐的方法一个也没有。"
        elif method in studies and not evals.get(method):
            out["note"] = "这个方法有训练 run，但没有可按轮配对的 ckpt 评测。"
        elif method in evals and method not in picked:
            out["note"] = "这个方法有 ckpt 评测，但没有多轮训练 run。"
        else:
            out["note"] = "没有这个方法，或者它两侧的数据都不全。"
        return json_safe(out)

    study_stamp = stamp_of(row["id"])
    points, _, _ = read_curve(RUNS / row["id"])
    study_rounds: list[dict] = []
    #: round -> 这一轮 train 测量的调用信息。series/gap 是"这一轮的 harness"，
    #: 所以两者用同一份数字，不在 eval 池上另算一个比值。
    calls_of_round: dict[int, dict] = {}
    bad = 0
    for point in points:
        n = as_int(point.get("round"))
        if n is None or n < 0:
            bad += 1
            continue
        ident = point.get("identity") if isinstance(point.get("identity"), dict) else {}
        cost = point.get("cost") if isinstance(point.get("cost"), dict) else {}
        calls = as_int(cost.get("harness_model_calls"))
        per_task = calls_per_task(calls, as_int(point.get("n_scored")))
        in_loop = model_in_loop(calls)
        calls_of_round[n] = {"calls_per_task": per_task, "model_in_loop": in_loop}
        study_rounds.append({
            "round": n,
            "train": as_num(point.get("score")),
            "n_scored": as_int(point.get("n_scored")),
            "n_invalid": as_int(point.get("n_invalid")),
            "harness_sha": (str(ident.get("harness_sha") or "")[:8] or None),
            "calls": calls,
            "calls_per_task": per_task,
            "model_in_loop": in_loop,
        })
    study_rounds.sort(key=lambda item: item["round"])

    scored = [item for item in study_rounds if item["train"] is not None]
    best_point = max(scored, key=lambda item: item["train"], default=None)
    last_point = study_rounds[-1] if study_rounds else None
    out["study"] = {
        "run_id": row["id"],
        "stamp": study_stamp or None,
        "dataset": row.get("dataset"),
        "rounds": study_rounds,
        "bad_points": bad,
        "best": best_point["train"] if best_point else None,
        "best_round": best_point["round"] if best_point else None,
        "last": last_point["train"] if last_point else None,
        "last_round": last_point["round"] if last_point else None,
    }

    by_round = evals.get(method) or {}
    train_of = {item["round"]: item["train"] for item in study_rounds}
    #: 只按训练 run 自己的轮次走。评测侧多出来的轮次（同一个方法、另一批训练的
    #: ckpt）不是同一个 state，放进来就等于把两批训练缝成一条曲线。
    for item in study_rounds:
        n = item["round"]
        pools = by_round.get(n) or {}
        calls_here = calls_of_round.get(n) or {}
        series: dict = {
            "round": n,
            "calls_per_task": calls_here.get("calls_per_task"),
            "model_in_loop": calls_here.get("model_in_loop"),
            "stamps": {}, "sources": {}, "shas": {},
        }
        for pool in POOLS:
            rec = _measure(pools.get(pool) or [], study_stamp, pool)
            series[pool] = rec["score"] if rec else None
            series["stamps"][pool] = rec["stamp"] if rec else None
            series["sources"][pool] = rec["run_id"] if rec else None
            series["shas"][pool] = rec["harness_sha"] if rec else None
        out["series"].append(series)

        measured = [(pool, series[pool]) for pool in POOLS if series[pool] is not None]
        best_pool, best_eval = max(measured, key=lambda pair: pair[1]) if measured else (None, None)
        train = train_of.get(n)
        out["gap"].append({
            "round": n,
            "train": train,
            "best_eval": best_eval,
            "best_pool": best_pool,
            "delta": (best_eval - train) if (best_eval is not None and train is not None) else None,
            "calls_per_task": calls_here.get("calls_per_task"),
            "model_in_loop": calls_here.get("model_in_loop"),
        })
    return json_safe(out)


# ─────────────────────────────────────────────────────── 覆盖矩阵（round × pool）
#: 语料里最强的一句结论是"round × pool 网格是完整的"。这一屏把它变成一个能数
#: 出来的东西：8 个方法 × 每个方法**测过的每一个训练轮次** × 3 个评测池。
#:
#: 方法 -> stamp 的对应关系是**实验批次**的事实，写死而不是从目录名里猜：
#: `dream_rsi` / `meta_harness` 在 20261017T123952Z 下也有一批 ckpt 目录，但那是
#: 另一次训练，round N 不是同一个 state（同 pick_study 的理由）。按 stamp 收，
#: 那些目录就不会冒充成格子。
COVERAGE_METHODS: tuple[tuple[str, str], ...] = (
    ("sica_ci", "20261017T123952Z"),
    ("ahe", "20261017T123952Z"),
    ("dgm", "20261017T123952Z"),
    ("harnessx", "20261017T123952Z"),
    ("hyperagents", "20261017T123952Z"),
    ("tthe", "20261017T123952Z"),
    ("dream_rsi", "20261018T062351Z"),
    ("meta_harness", "20261018T062351Z"),
)

#: 不进矩阵的方法，但必须在界面上说出来 —— 否则读的人会以为这一屏漏了它。
COVERAGE_EXCLUDED: tuple[tuple[str, str, str], ...] = (
    ("rrsi", "20261017T123952Z", "已废弃（deprecated），评测不进矩阵"),
)

#: 池的题数。一个格子只有把整个池测满才算有效：少一题就不是同一个测量，
#: 也就不能拿来支撑"171 格全齐"这句话。
COVERAGE_POOL_SIZES = {"valid_unseen": 134, "valid_seen": 140, "valid_train": 200}

#: exam 形式的评测：`<stamp>-<method>-exam-<pool>`。名字里没有轮次，轮次只写在
#: 曲线测点的 `evaluated_from.round` 里 —— 漏了这条就会把 10 个真格子报成空缺。
_EVAL_EXAM = re.compile(
    r"^rsi10-(?P<stamp>[0-9]{8}T[0-9]{4,6}Z)-(?P<method>[A-Za-z0-9_]+)"
    r"-exam-(?P<pool>valid_unseen|valid_seen|valid_train)$")


def _coverage_eval_index() -> dict:
    """(stamp, method, pool) -> {"ckpt": {round: dir}, "exam": dir}。

    只收**完整命名**的 run。`-repair-…` / `-control-…` / `-fix` 这些尾巴是另一次
    测量，不是这一格 —— 和 eval_index 用同一条规矩。
    """
    index: dict = {}
    try:
        entries = sorted(RUNS.iterdir())
    except OSError:
        return index
    for entry in entries:
        if not entry.is_dir():
            continue
        m = _CKPT.match(entry.name)
        if m:
            slot = index.setdefault(
                (m.group("stamp"), m.group("method"), m.group("pool")),
                {"ckpt": {}, "exam": None})
            slot["ckpt"][int(m.group("round"))] = entry
            continue
        m = _EVAL_EXAM.match(entry.name)
        if m:
            slot = index.setdefault(
                (m.group("stamp"), m.group("method"), m.group("pool")),
                {"ckpt": {}, "exam": None})
            slot["exam"] = entry
    return index


def _coverage_rounds(stamp: str, method: str) -> tuple[list[int], str | None, str]:
    """这个方法在**自己那批**训练里测过的轮次（不含 r0），以及曲线的读取状态。

    r0 是共享基线（`h0-*`），不是这个方法的一次训练，所以不进矩阵；
    r8 只有 harness 快照、从没打分的那种轮次也不会出现在这里 —— 曲线里没有它。
    """
    run_id = f"rsi10-{stamp}-{method}-train"
    run_dir = RUNS / run_id
    if not run_dir.is_dir():
        return [], None, "missing"
    points, state, _ = read_curve(run_dir)
    rounds: set[int] = set()
    for point in points:
        n = as_int(point.get("round"))
        if n is not None and n >= 1:
            rounds.add(n)
    return sorted(rounds), run_id, state


def _coverage_verdict(last: dict, size: int | None) -> tuple[list[str], dict]:
    """一个测点算不算有效格：返回 (不满足的判据, 三个计数 + 分数)。

    未知（读不到 / 没写）一律 None 并写进 why；不补 0。
    """
    n = as_int(last.get("n_scored")) if last else None
    invalid = as_int(last.get("n_invalid")) if last else None
    failed = as_int(last.get("n_harness_failed")) if last else None
    score = as_num(last.get("score")) if last else None
    why: list[str] = []
    if not last:
        why.append("曲线没有测点")
    else:
        if n is None:
            why.append("n_scored 未知")
        elif size is not None and n != size:
            why.append(f"n_scored {n} ≠ 池大小 {size}")
        if invalid is None:
            why.append("n_invalid 未知")
        elif invalid != 0:
            why.append(f"n_invalid {invalid} ≠ 0")
        if failed is None:
            why.append("n_harness_failed 未知")
        elif failed != 0:
            why.append(f"n_harness_failed {failed} ≠ 0")
        if score is None:
            why.append("没有分数")
    return why, {"score": score, "n_scored": n, "n_invalid": invalid,
                 "n_harness_failed": failed}


def _coverage_cell(slot: dict | None, round_n: int, pool: str,
                   size: int | None) -> dict:
    """一格的解析：先 ckpt（轮次在目录名里），再 exam（轮次在曲线里）。

    返回的 `run_id` 为 None 表示**这一格根本没有 run**（空格）；有 run 但没过
    判据是"无效"，两者在界面上必须分得开。
    """
    base = {"round": round_n, "pool": pool, "ok": False, "score": None,
            "run_id": None, "n_scored": None, "n_invalid": None,
            "n_harness_failed": None, "why_if_not": None, "form": None}
    candidates: list[tuple[str, pathlib.Path, dict]] = []
    exam_round = None
    if slot:
        ckpt_dir = (slot.get("ckpt") or {}).get(round_n)
        if ckpt_dir is not None:
            points, _, _ = read_curve(ckpt_dir)
            candidates.append(("ckpt", ckpt_dir, points[-1] if points else {}))
        exam_dir = slot.get("exam")
        if exam_dir is not None:
            points, _, _ = read_curve(exam_dir)
            last = points[-1] if points else {}
            source = last.get("evaluated_from")
            if isinstance(source, dict):
                exam_round = as_int(source.get("round"))
            if exam_round == round_n:
                candidates.append(("exam", exam_dir, last))

    if not candidates:
        if slot is None:
            why = f"没有这一格的 run（ckpt-r{round_n} 与 exam 两种命名都没有）"
        elif exam_round is None:
            why = "exam run 没有可用测点，轮次未知，无法归到某一轮"
        else:
            why = f"exam run 覆盖的是第 {exam_round} 轮，不是第 {round_n} 轮"
        return {**base, "why_if_not": why}

    chosen = None
    for kind, path, last in candidates:
        why, info = _coverage_verdict(last, size)
        if not why:
            chosen = (kind, path, why, info)
            break
    if chosen is None:
        kind, path, last = candidates[0]
        why, info = _coverage_verdict(last, size)
        chosen = (kind, path, why, info)

    kind, path, why, info = chosen
    return {
        **base,
        "ok": not why,
        "score": info["score"],
        "run_id": path.name,
        "n_scored": info["n_scored"],
        "n_invalid": info["n_invalid"],
        "n_harness_failed": info["n_harness_failed"],
        "why_if_not": "；".join(why) if why else None,
        "form": kind,
    }


def api_coverage() -> dict:
    """`GET /api/coverage`：8 个方法 × 轮次 × 3 池的格子，一格一次测量。

    171 这个数不是写在这里的常量：它是 `rounds × pools` 数出来的；某一格没有
    run、或者 run 没测满，`valid` 就掉下来，`statement` 跟着改口。
    """
    index = _coverage_eval_index()
    pools = list(POOLS)
    out_methods: list[dict] = []
    expected = valid = absent = invalid = rounds_total = 0

    for method, stamp in COVERAGE_METHODS:
        rounds, train_run, curve_state = _coverage_rounds(stamp, method)
        rounds_total += len(rounds)
        cells: list[dict] = []
        for round_n in rounds:
            for pool in pools:
                cell = _coverage_cell(
                    index.get((stamp, method, pool)), round_n, pool,
                    COVERAGE_POOL_SIZES.get(pool))
                cells.append(cell)
                expected += 1
                if cell["ok"]:
                    valid += 1
                elif cell["run_id"] is None:
                    absent += 1
                else:
                    invalid += 1
        method_valid = sum(1 for cell in cells if cell["ok"])
        note = None
        if train_run is None:
            note = f"找不到训练 run `rsi10-{stamp}-{method}-train`：轮次未知，格子无法计数。"
        elif not rounds:
            note = (f"训练 run `{train_run}` 的曲线状态是 `{curve_state}`："
                    "没有 r1 以后的测点，这一批还没有可数的轮次。")
        out_methods.append({
            "name": method,
            "stamp": stamp,
            "train_run_id": train_run,
            "rounds": rounds,
            "pools": pools,
            "cells": cells,
            "valid": method_valid,
            "expected": len(cells),
            "note": note,
        })

    missing = expected - valid
    if missing:
        statement = (
            f"{len(COVERAGE_METHODS)} 个方法 · {rounds_total} 个训练轮次 · "
            f"{len(pools)} 个评测池 = {expected} 格，{valid} 格有效，{missing} 格缺。")
    else:
        statement = (
            f"{len(COVERAGE_METHODS)} 个方法 · {rounds_total} 个训练轮次 · "
            f"{len(pools)} 个评测池 = {expected} 格，{valid} 格有效：round × pool 网格完整。")

    return json_safe({
        "pools": pools,
        "pool_sizes": dict(COVERAGE_POOL_SIZES),
        "methods": out_methods,
        "totals": {
            "expected": expected,
            "valid": valid,
            "missing": missing,
            "absent": absent,
            "invalid": invalid,
            "rounds": rounds_total,
            "methods": len(COVERAGE_METHODS),
            "pools": len(pools),
        },
        "excluded": [{"name": name, "stamp": stamp, "reason": why}
                     for name, stamp, why in COVERAGE_EXCLUDED],
        "statement": statement,
    })


# ─────────────────────────────────────────────────────── M3 解释（LLM，只读）
#: 一次解释的输入上限。patch 只取前 40 KB；0 分任务只看最后几个事件。
#: 这些数字是**给模型看的**上限，不是磁盘读取上限。
EXPLAIN_PATCH_BYTES = 40 * 1024
EXPLAIN_ZERO_TASKS = 3
EXPLAIN_LAST_EVENTS = 3
EXPLAIN_EVENT_TYPES = {"harness_identity", "usage", "wall_clock_s", "context_trims"}
EXPLAIN_TIMEOUT_S = 60.0
EXPLAIN_CACHE_DIR = ROOT / ".cache" / "explain"
EXPLAIN_ENV = ("HG_CONSOLE_EXPLAIN_BASE_URL", "HG_CONSOLE_EXPLAIN_MODEL",
               "HG_CONSOLE_EXPLAIN_API_KEY")

EXPLAIN_SYSTEM = """你是 HarnessGrad 实验台的只读解释器。用户会给你某一轮 harness 改动的 patch、\
这一轮的分数与文件行数统计，以及最多三个得 0 分的任务轨迹的最后几个事件。

你的任务：解释这次改动在**机械层面**做了什么，以及它可能带来什么风险。
规则：
1. 只使用用户提供的内容。不要猜测没有给出的文件、分数、日志或外部事实。
2. 只输出一个 JSON 对象。不要 markdown 代码块，不要前后说明，不要多余文字。
3. JSON 必须且只能有这四个键：
   - "claim": 一句中文，说明这次改动机械上改变了什么（不是“提升了性能”这类空话）。
   - "evidence": 2 到 4 条中文短句；每条都必须引用 patch 里的具体改动或失败任务里的具体事件。
   - "risk": 一句中文，说明这次改动可能破坏什么。
   - "next": 一句中文，建议**一次**值得跑的对照（说明对照什么、看哪个指标）。
4. 所有字符串用中文。不要编造数字；没有数字就说没有。
"""


class ExplainNoPatch(Exception):
    """这一轮没有 patch：没有可解释的改动。"""


class ExplainUpstream(Exception):
    """模型端点返回了错误 / 非 JSON。"""


_explain_mem: dict[str, dict] = {}
_explain_mem_order: list[str] = []
_explain_lock = threading.Lock()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _clip(value, n: int) -> str:
    text = str(value if value is not None else "")
    return text if len(text) <= n else text[:n] + "…"


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def explain_config() -> tuple[str, str, str, list[str]]:
    """(base_url, model, api_key, missing_names)。只读 env，绝不把值写进日志或响应。"""
    values = [os.environ.get(name, "").strip() for name in EXPLAIN_ENV]
    missing = [name for name, value in zip(EXPLAIN_ENV, values) if not value]
    return values[0], values[1], values[2], missing


def _read_jsonl_tail(path: pathlib.Path, max_bytes: int = 128 * 1024) -> list[dict]:
    """读一个 jsonl 的尾部（不超过 max_bytes）。坏行跳过，不从半行开始解析。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            start = max(0, size - max_bytes)
            fh.seek(start)
            raw = fh.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    events: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def _last_events(path: pathlib.Path, n: int) -> list[dict]:
    """最后 n 个**有内容**的事件。

    轨迹末尾常常是 usage / wall_clock 这种记账事件；它们不是失败模式。跳过它们，
    否则模型看到的“最后 3 个事件”全是 tokens 数。仍然最多只发 n 个。
    """
    events = _read_jsonl_tail(path)
    picked: list[dict] = []
    for ev in reversed(events):
        if any(key in ev for key in EXPLAIN_EVENT_TYPES):
            continue
        picked.append(ev)
        if len(picked) >= n:
            break
    picked.reverse()
    return picked


def _compact_event(ev: dict) -> dict:
    """把一条轨迹事件压成模型需要的字段；长输出裁剪并标注。"""
    out: dict = {}
    for key in ("step", "command", "exit", "command_ms", "env_step",
                "environment_solved", "submitted", "auto_submit", "missing"):
        if key in ev:
            out[key] = ev[key]
    if "reply" in ev:
        out["reply"] = _clip(ev.get("reply"), 700)
    if "output" in ev:
        text = str(ev.get("output") or "")
        out["output"] = _clip(text, 1200)
        if len(text) > 1200:
            out["output_chars"] = len(text)
            out["output_truncated"] = True
    if not out:
        out["keys"] = sorted(str(k) for k in ev.keys())[:12]
    return out


def _zero_tasks(path: pathlib.Path, n: int, point: dict) -> list[dict]:
    """这一轮得 0 分的任务里，最多取 3 个有轨迹的；每个只带最后 3 个事件。"""
    ids: list[str] = []
    per_task = point.get("per_task")
    if isinstance(per_task, dict):
        ids = sorted(str(task) for task, score in per_task.items() if as_num(score) == 0)
    if not ids:
        # 没有 per_task 时退回该轮 verdicts；仍然是“读文件”，不是推断。
        vdir = path / "verdicts" / f"round-{n}"
        if vdir.is_dir():
            for f in sorted(vdir.glob("*.json")):
                if as_num(read_json(f).get("score")) == 0:
                    ids.append(_task_of(f.stem)[0])
    ids = sorted(set(ids))

    traces: dict[str, pathlib.Path] = {}
    tdir = path / f"round{n}_traces"
    if tdir.is_dir():
        for f in sorted(tdir.glob("*.jsonl")):
            task_id, _trial = _task_of(f.stem)
            traces.setdefault(task_id, f)

    out: list[dict] = []
    for task_id in ids:
        trace = traces.get(task_id)
        if trace is None:
            continue
        verdict = _verdict(path, task_id, n)
        events = [_compact_event(ev) for ev in _last_events(trace, EXPLAIN_LAST_EVENTS)]
        out.append({
            "task_id": task_id,
            "score": 0.0,
            "kind": str(verdict.get("kind") or ""),
            "detail": _clip(verdict.get("detail") or "", 500),
            "note": "最后几个事件（已裁剪）；不是完整轨迹" if events else "没有可读事件",
            "events": events,
        })
        if len(out) >= EXPLAIN_ZERO_TASKS:
            break
    return out


def build_explain_inputs(path: pathlib.Path, run_id: str, n: int) -> dict:
    """把一次解释需要的全部输入读齐。只读 run 目录，不读平台、不读浏览器。"""
    points, _state, _bad = read_curve(path)
    point = next((p for p in points if as_int(p.get("round")) == n), None)
    if point is None:
        raise ValueError(f"round {n} is not in this run")
    patch_path = path / "diffs" / f"round-{n}.patch"
    if not patch_path.is_file():
        raise ExplainNoPatch(f"diffs/round-{n}.patch 不存在")
    try:
        patch_size = patch_path.stat().st_size
        if patch_size == 0:
            raise ExplainNoPatch(f"diffs/round-{n}.patch 是 0 字节：这一轮没有改动")
        with patch_path.open("rb") as fh:
            raw = fh.read(EXPLAIN_PATCH_BYTES + 1)
    except OSError as exc:
        raise ExplainNoPatch(f"读不了 diffs/round-{n}.patch：{exc}") from None
    truncated = len(raw) > EXPLAIN_PATCH_BYTES
    patch_text = raw[:EXPLAIN_PATCH_BYTES].decode("utf-8", errors="replace")
    if truncated:
        cut = patch_text.rfind("\n")
        if cut > EXPLAIN_PATCH_BYTES // 2:
            patch_text = patch_text[:cut]
        patch_text += f"\n…（patch 已截断：只发送前 40 KB，共 {patch_size} 字节）"

    ident = point.get("identity") if isinstance(point.get("identity"), dict) else {}
    raw_stat = read_json(path / "diffs" / f"round-{n}.json")
    stat_files = []
    if isinstance(raw_stat.get("files"), list):
        for item in raw_stat["files"]:
            if isinstance(item, dict):
                stat_files.append({
                    "path": str(item.get("path") or ""),
                    "added": as_int(item.get("added")),
                    "removed": as_int(item.get("removed")),
                })
    return {
        "run_id": run_id,
        "round": n,
        "label": _clip(point.get("label") or "", 300),
        "harness_sha": str(ident.get("harness_sha") or ""),
        "score": as_num(point.get("score")),
        "n_scored": as_int(point.get("n_scored")),
        "n_invalid": as_int(point.get("n_invalid")),
        "patch": patch_text,
        "patch_truncated": truncated,
        "patch_bytes": patch_size,
        "stat": {
            "files": stat_files,
            "added": as_int(raw_stat.get("added")),
            "removed": as_int(raw_stat.get("removed")),
            "from": _clip(raw_stat.get("from") or "", 40),
            "to": _clip(raw_stat.get("to") or "", 40),
        },
        "zero_tasks": _zero_tasks(path, n, point),
    }


def explain_prompt(inputs: dict) -> tuple[str, str]:
    """(system, user)。user 里只有输入，没有指令；system 里只有规则。"""
    stat = inputs["stat"]
    file_lines = []
    for f in stat["files"]:
        file_lines.append(
            f"- {f['path']}: +{f['added'] if f['added'] is not None else '?'}"
            f" / -{f['removed'] if f['removed'] is not None else '?'}")
    files_text = "\n".join(file_lines) if file_lines else "（stat 里没有文件列表）"
    parts = [
        "## 这一轮的事实（来自 run 目录里的文件）",
        f"run: {inputs['run_id']}",
        f"round: {inputs['round']}",
        f"label: {inputs['label'] or '—'}",
        f"harness_sha: {inputs['harness_sha'] or '—'}",
        f"score: {inputs['score'] if inputs['score'] is not None else '—'}",
        f"n_scored: {inputs['n_scored'] if inputs['n_scored'] is not None else '—'}",
        f"n_invalid: {inputs['n_invalid'] if inputs['n_invalid'] is not None else '—'}",
        "",
        "## 改动文件与行数（diffs/round-%d.json）" % inputs["round"],
        files_text,
        f"合计: +{stat['added'] if stat['added'] is not None else '?'}"
        f" / -{stat['removed'] if stat['removed'] is not None else '?'}",
        "",
        "## patch（diff，服务端已截断到 40 KB）",
        "```diff",
        inputs["patch"],
        "```",
    ]
    zeros = inputs["zero_tasks"]
    if zeros:
        parts.append("")
        parts.append("## 这一轮得 0 分的任务（最多 3 个；每个只给最后几个事件）")
        for task in zeros:
            parts.append("")
            parts.append(f"### {task['task_id']}（score 0）")
            if task["kind"] or task["detail"]:
                parts.append(f"判定: {task['kind'] or '未记录'} · {task['detail'] or '—'}")
            parts.append(task["note"])
            if task["events"]:
                parts.append("```json")
                parts.append(json.dumps(task["events"], ensure_ascii=False, indent=1))
                parts.append("```")
    else:
        parts.extend(["", "## 这一轮得 0 分的任务",
                      "没有可用的 0 分任务轨迹（可能这一轮没有 0 分，或没有保留轨迹）。"])
    return EXPLAIN_SYSTEM, "\n".join(parts)


def _chat_completion(base_url: str, model: str, api_key: str,
                     system: str, user: str) -> str:
    """一次 OpenAI 兼容的 /chat/completions 调用。key 只在 header 里，不写日志。"""
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=EXPLAIN_TIMEOUT_S) as resp:
            raw = resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(4000).decode("utf-8", errors="replace")
        except OSError:
            pass
        raise ExplainUpstream(
            f"模型端点 HTTP {exc.code}：{_redact(_clip(detail, 600), api_key)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ExplainUpstream(
            f"模型端点请求失败：{_redact(_clip(exc, 300), api_key)}") from None
    try:
        reply = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        raise ExplainUpstream("模型端点返回的不是 JSON") from None
    choices = reply.get("choices") if isinstance(reply, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ExplainUpstream("模型端点没有返回 choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ExplainUpstream("模型端点返回了空 content")
    return content


def _parse_explain_reply(text: str) -> tuple[dict | None, str]:
    """严格解析四个键。允许正好包一层 ``` 代码块，但那之后仍必须是合法 JSON。"""
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        return None, f"不是合法 JSON：{exc}"
    if not isinstance(obj, dict):
        return None, "顶层不是 JSON object"
    required = ("claim", "evidence", "risk", "next")
    missing = [key for key in required if key not in obj]
    extra = [key for key in obj if key not in required]
    if missing:
        return None, f"缺少键：{', '.join(missing)}"
    if extra:
        return None, f"多出键：{', '.join(sorted(extra))}"
    for key in ("claim", "risk", "next"):
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            return None, f"{key} 不是非空字符串"
    evidence = obj.get("evidence")
    if not isinstance(evidence, list) or not 2 <= len(evidence) <= 4:
        return None, "evidence 必须是 2–4 条的数组"
    if not all(isinstance(item, str) and item.strip() for item in evidence):
        return None, "evidence 里有非字符串或空项"
    return {
        "claim": obj["claim"].strip(),
        "evidence": [item.strip() for item in evidence],
        "risk": obj["risk"].strip(),
        "next": obj["next"].strip(),
    }, ""


def _explain_cache_key(run_id: str, n: int, harness_sha: str, model: str,
                       prompt_sha: str) -> str:
    return json.dumps([run_id, n, harness_sha, model, prompt_sha],
                      ensure_ascii=False, separators=(",", ":"))


def _explain_cache_path(key: str) -> pathlib.Path:
    return EXPLAIN_CACHE_DIR / (_sha(key)[:40] + ".json")


def _explain_remember(key: str, payload: dict) -> None:
    with _explain_lock:
        _explain_mem[key] = payload
        try:
            _explain_mem_order.remove(key)
        except ValueError:
            pass
        _explain_mem_order.append(key)
        while len(_explain_mem_order) > 128:
            old = _explain_mem_order.pop(0)
            _explain_mem.pop(old, None)


def _explain_cache_get(key: str) -> dict | None:
    with _explain_lock:
        hit = _explain_mem.get(key)
        if hit is not None:
            return json.loads(json.dumps(hit, ensure_ascii=False))
    path = _explain_cache_path(key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("key") != key:
        return None
    payload = data.get("response")
    if not isinstance(payload, dict):
        return None
    _explain_remember(key, payload)
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _explain_cache_put(key: str, payload: dict) -> None:
    _explain_remember(key, payload)
    try:
        EXPLAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _explain_cache_path(key)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"key": key, "response": payload},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # 磁盘缓存失败不该让解释失败；内存缓存还在。
        pass


def api_explain(run_id: str, n: int, force: bool = False) -> tuple[int, dict]:
    """(HTTP status, payload)。GET 是幂等的；缓存命中时 elapsed_ms 必须远小于一次调用。"""
    started = time.perf_counter()
    run_path = find_run(run_id)
    if not run_path:
        return 404, {"error": "run not found", "detail": f"没有这个 run：{run_id}"}
    points, _state, _bad = read_curve(run_path)
    if not any(as_int(p.get("round")) == n for p in points):
        return 400, {"error": "round not in run",
                     "detail": f"round {n} 不在这个 run 的曲线里"}

    base_url, model, api_key, missing = explain_config()
    if missing:
        return 503, {
            "error": "explain-not-configured",
            "detail": ("解释功能没有配置：缺少环境变量 "
                       + "、".join(missing)
                       + "。设置一个 OpenAI 兼容端点和 key 后重启 console。"),
        }

    try:
        inputs = build_explain_inputs(run_path, run_id, n)
    except ExplainNoPatch as exc:
        return 400, {"error": "explain-no-patch", "detail": str(exc)}
    except (OSError, ValueError) as exc:
        return 500, {"error": "explain-inputs", "detail": _clip(exc, 400)}

    system, user = explain_prompt(inputs)
    prompt_sha = _sha(system + "\n\n" + user)
    input_sha = _sha(json.dumps(inputs, ensure_ascii=False, sort_keys=True))
    key = _explain_cache_key(run_id, n, inputs["harness_sha"], model, prompt_sha)

    if not force:
        cached = _explain_cache_get(key)
        if cached is not None:
            cached["cache_hit"] = True
            cached["elapsed_ms"] = int(round((time.perf_counter() - started) * 1000))
            return 200, cached

    try:
        raw = _chat_completion(base_url, model, api_key, system, user)
    except ExplainUpstream as exc:
        return 502, {"error": "explain-upstream", "detail": _clip(exc, 800)}

    parsed, why = _parse_explain_reply(raw)
    elapsed = int(round((time.perf_counter() - started) * 1000))
    if parsed is None:
        # 不猜、不把半截 JSON 塞进四个块；原样交给前端，前端明确标成 degraded。
        return 200, {
            "claim": "", "evidence": [], "risk": "", "next": "",
            "model": model, "prompt_sha": prompt_sha, "input_sha": input_sha,
            "cache_hit": False, "elapsed_ms": elapsed,
            "degraded": True, "parse_error": why, "raw": _clip(raw, 8000),
        }

    payload = {
        **parsed,
        "model": model, "prompt_sha": prompt_sha, "input_sha": input_sha,
        "cache_hit": False, "elapsed_ms": elapsed,
    }
    _explain_cache_put(key, {k: v for k, v in payload.items()
                             if k not in ("cache_hit", "elapsed_ms")})
    return 200, payload


# ─────────────────────────────────────────────────────── HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "hg-console/0.2"

    def log_message(self, fmt: str, *args) -> None:  # 安静一点，别刷屏
        pass

    def _send(self, payload, status: int = 200,
              ctype: str = "application/json; charset=utf-8",
              cache_control: str | None = None) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(
            json_safe(payload), ensure_ascii=False, allow_nan=False,
            default=str).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control or "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, message: str, status: int = 400) -> None:
        self._send({"error": message}, status)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        query = parse_qs(url.query)

        if not parts or parts[0] != "api":
            return self._static(url.path)

        try:
            if parts[1:] == ["health"]:
                return self._send(api_health())
            if parts[1:] == ["runs"]:
                kind = (query.get("kind") or [""])[0]
                if kind not in ("", "study", "eval", "unknown"):
                    return self._error("kind must be study, eval, unknown or empty", 400)
                rows = all_rows()
                if kind:
                    rows = [r for r in rows if r["kind"] == kind]
                return self._send({"rows": rows, "count": len(rows)})
            if len(parts) == 3 and parts[1] == "runs":
                data = api_run(parts[2])
                return self._send(data) if data else self._error("run not found", 404)
            if len(parts) == 5 and parts[1] == "runs" and parts[3] == "rounds":
                if not re.fullmatch(r"\d{1,9}", parts[4]):
                    return self._error("round must be a non-negative integer", 400)
                run_path = find_run(parts[2])
                if run_path and not round_exists(run_path, int(parts[4])):
                    return self._error(f"round {parts[4]} is not in this run", 400)
                data = api_round(parts[2], int(parts[4]))
                return self._send(data) if data else self._error("run not found", 404)
            if (len(parts) == 6 and parts[1] == "runs" and parts[3] == "rounds"
                    and parts[5] == "file"):
                if not re.fullmatch(r"\d{1,9}", parts[4]):
                    return self._error("round must be a non-negative integer", 400)
                rel = (query.get("path") or [""])[0]
                if not rel:
                    return self._error("path query parameter is required", 400)
                data = api_file(parts[2], int(parts[4]), rel)
                return self._send(data) if data else self._error("file not found", 404)
            if len(parts) == 6 and parts[1] == "runs" and parts[3] == "rounds" and parts[5] == "traces":
                if not re.fullmatch(r"\d{1,9}", parts[4]):
                    return self._error("round must be a non-negative integer", 400)
                run_path = find_run(parts[2])
                if run_path and not round_exists(run_path, int(parts[4])):
                    return self._error(f"round {parts[4]} is not in this run", 400)
                data = api_traces(parts[2], int(parts[4]))
                return self._send(data) if data else self._send({"error": "not found"}, 404)
            if len(parts) == 6 and parts[1] == "runs" and parts[3] == "rounds" and parts[5] == "trace":
                if not re.fullmatch(r"\d{1,9}", parts[4]):
                    return self._error("round must be a non-negative integer", 400)
                name = (query.get("name") or [""])[0]
                if not name:
                    return self._error("name query parameter is required", 400)
                data = api_trace(parts[2], int(parts[4]), name)
                return self._send(data) if data else self._send({"error": "not found"}, 404)
            if parts[1:] == ["chat", "status"]:
                cfg = llm_config()
                return self._send({"configured": bool(cfg),
                                   "model": (cfg or {}).get("model"),
                                   "base_url": (cfg or {}).get("base_url")})
            if parts[1:] == ["launch", "form"]:
                return self._send(launch_form())
            if parts[1:] == ["launch", "preview"]:
                # 预览和起跑共用 launch_argv() 与 guard_launch()，只是 run_id 用占位符。
                base = launch_form()["defaults"]
                form = {k: (query.get(k) or [base[k]])[0] for k in
                        ("dataset", "harness", "rounds", "jobs", "trials", "sampling")}
                form["improver"] = (query.get("improver") or ["dsh"])[0]
                form["improver_model"] = (query.get("improver_model") or ["deepseek-flash"])[0]
                return self._send(launch_preview(form))
            if parts[1:] == ["methods"]:
                return self._send({"rows": api_methods()})
            if len(parts) == 3 and parts[1] == "methods":
                data = api_method(parts[2])
                return self._send(data) if data else self._error("method not found", 404)
            if len(parts) == 4 and parts[1] == "methods" and parts[3] == "file":
                rel = (query.get("path") or [""])[0]
                if not rel:
                    return self._error("path query parameter is required", 400)
                data = api_method_file(parts[2], rel)
                return self._send(data) if data else self._error("file not found", 404)
            if parts[1:] == ["protocol"]:
                return self._send(api_protocol())
            if parts[1:] == ["base"]:
                return self._send(api_base())
            if parts[1:] == ["base", "file"]:
                rel = (query.get("path") or [""])[0]
                if not rel:
                    return self._error("path query parameter is required", 400)
                data = api_base_file(rel)
                return self._send(data) if data else self._error("file not found", 404)
            if parts[1:] == ["base-harnesses"]:
                return self._send(api_base_harnesses())
            if parts[1:] == ["base-harness", "file"]:
                name = (query.get("name") or [""])[0].strip()
                rel = (query.get("path") or [""])[0]
                if not name:
                    return self._error("name query parameter is required", 400)
                if not rel:
                    return self._error("path query parameter is required", 400)
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
                    return self._error("name must match [A-Za-z0-9_.-]{1,64}", 400)
                data = api_base_harness_file(name, rel)
                return self._send(data) if data else self._error("file not found", 404)
            if parts[1:] == ["adapters"]:
                return self._send(api_adapters())
            if parts[1:] == ["compare"]:
                method = (query.get("method") or [""])[0].strip()
                dataset = (query.get("dataset") or [""])[0].strip()
                if method and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", method):
                    return self._error("method must match [A-Za-z0-9_.-]{1,64}", 400)
                if dataset and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", dataset):
                    return self._error("dataset must match [A-Za-z0-9_.-]{1,64}", 400)
                return self._send(api_compare(method, dataset))
            if parts[1:] == ["coverage"]:
                return self._send(api_coverage())
            if parts[1:] == ["explain"]:
                run_id = (query.get("run") or [""])[0].strip()
                raw_round = (query.get("round") or [""])[0].strip()
                if not run_id:
                    return self._error("run query parameter is required", 400)
                if not re.fullmatch(r"\d{1,9}", raw_round):
                    return self._error("round must be a non-negative integer", 400)
                force = (query.get("force") or ["0"])[0].strip().lower()
                status, payload = api_explain(run_id, int(raw_round),
                                              force not in ("", "0", "false", "no"))
                # 只有配置好的解释才可缓存；503 的配置状态要立刻能被看见。
                return self._send(payload, status,
                                  cache_control="private, max-age=300" if status == 200 else None)
        except (ValueError, OSError) as exc:
            return self._send({"error": str(exc)}, 500)
        return self._error("unknown endpoint", 404)

    def do_POST(self) -> None:  # noqa: N802
        """控制台唯一的写接口：起一次实验。别的一律 404。"""
        url = urlparse(self.path)
        route = url.path.strip("/")
        if route == "api/chat":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8") or "{}")
                messages = body.get("messages")
                if not isinstance(messages, list) or not messages:
                    raise ValueError("messages must be a non-empty list")
            except (ValueError, UnicodeDecodeError) as exc:
                return self._send({"error": "bad-json", "detail": str(exc)}, 400)
            code, payload = api_chat(messages)
            return self._send(payload, code)
        if route != "api/runs":
            return self._send({"error": "unknown endpoint"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            form = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(form, dict):
                raise ValueError("body must be an object")
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send({"error": "bad-json", "detail": str(exc)}, 400)
        try:
            code, payload = api_launch(form)
        except OSError as exc:
            return self._send({"error": "launch-failed", "detail": str(exc)}, 500)
        return self._send(payload, code)

    def _static(self, path: str) -> None:
        rel = unquote(path.lstrip("/")) or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        target = (STATIC / rel).resolve()
        if not within(STATIC, target) or not target.is_file():
            return self._error("not found", 404)
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
        }.get(target.suffix, "application/octet-stream")
        try:
            body = target.read_bytes()
        except OSError:
            return self._error("not found", 404)
        return self._send(body, ctype=ctype)


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4460)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if not RUNS.is_dir():
        raise SystemExit(f"runs root not found: {RUNS}")
    httpd = ConsoleServer((args.host, args.port), Handler)
    print(f"hg-console on http://{args.host}:{args.port}  (runs={RUNS})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
