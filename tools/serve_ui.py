#!/usr/bin/env python3
"""HarnessGrad 的运维层:准备环境、起停 run、看进度、比较候选、管理模型服务。

    python3 tools/serve_ui.py --port 8770

**名字是旧的,它已经不是 UI 了。** 这里原本是"HarnessGrad 控制台"——一个用标准库
手写的单页应用(先是 2447 行的 `PAGE` 字符串,后来拆成 `tools/ui/` 三个文件)。
2026-10-06 那套东西连同 `tests/quality/test_ui.py` 一起删掉了:stage 5 的 SPA
(`web/`,Vite+React+TS,107 个 vitest 用例,自带只读 API `web/server/readapi.py`)
已经取代它,而 `tools/ui/` 那 140 KB 自 `web/` 落地后就没有任何东西加载过。

留下的是**运维层**,读 API 一样都没取代:

* `prepare_report` / `start_prepare` —— 跑之前把 docker 镜像导进来。平台在 run 进行中
  拒绝拉镜像,所以这是跑新数据集的前提;
* `start_job` / `job_status` / `stop_job` / `driver_pid` —— 从界面起停一个 run;
* `model_service` —— 起停本地 vLLM;
* `promote` / `version_graph` —— 把某个候选状态提升为当前 harness,以及版本图;
* `step_diff` / `explain_diff`、`provider_catalog` / `read_settings` / `write_settings` /
  `test_connection`、`model_presets` / `method_catalogue` / `improver_options` /
  `scan_options` / `task_options`。

SPA 的 Launch 页自己写着 "the write API does not exist yet",说的就是这些还没被搬过去。
所以这个文件不能删;改名是另一件事,而删掉它等于删掉唯一能准备环境的入口。

页面没了,`GET /` 与 `/ui/*` 返回 410 并指路到 SPA。

下面三条原设计约束对留下来的运维代码仍然成立:

* **选项从磁盘扫出来。** harness、数据集、方法都是列目录得到的,不是写死的列表。
  加一个新 harness 不需要改这个文件 —— 加一个新数据集同理。
* **进度是日志,不是猜测。** 子进程的 stdout 原样读到调用方。与其解析出一个可能
  不准的百分比,不如让人看见它真实在做什么。
* **只用标准库**(`data.*`/`eval.*` 除外)。一个需要 `npm install` 才能工作的入口,
  会在最需要它的时候失败 —— 页面交给 SPA,运维留在标准库里。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import urllib.request
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# After the stdlib imports, because this needs `Path` -- and the first version put it
# above them, which is the third name-not-defined bug of this session (a missing
# `import os` in `tools/import_env.py`, and a `registry` that was never imported and
# whose failure a bare `except Exception` swallowed). The order is load-bearing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data.scaffolds.registry as registry   # noqa: E402
import eval.containers.container as container   # noqa: E402
import eval.containers.harness_runtime as harness_runtime   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
WORK_ROOTS = [ROOT.parent / "harnessgrad_work", ROOT / "runs"]
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

#: 模型预设。键是给人看的中文名,值是 driver 需要的环境变量。
#: 本地模型排第一 —— 它是默认值,零成本、零网络、秒级响应,演示时最可靠。
def model_presets() -> dict:
    env = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    presets = {
        "本地 Qwen3.5-9B(vLLM,免费、秒回)": {
            "HG_AGENT_BACKEND": "openai",
            "HG_AGENT_MODEL": "qwen3.5-9b-local",
            "HG_AGENT_BASE_URL": "http://127.0.0.1:8002/v1",
            "HG_AGENT_API_KEY": "local",
            "note": "需要先启动本地服务:./tools/serve_local_model.sh --model <模型>",
        },
        "mock(零成本冒烟,不调模型)": {
            "HG_AGENT_BACKEND": "mock",
            "note": "用来验证流程通不通,分数不代表任何模型",
        },
    }
    if env.get("HG_AGENT_API_KEY"):
        presets[f"DeepSeek {env.get('HG_AGENT_MODEL', 'flash')}(远程 API)"] = {
            "HG_AGENT_BACKEND": "openai",
            "HG_AGENT_MODEL": env.get("HG_AGENT_MODEL", "deepseek-flash"),
            "HG_AGENT_BASE_URL": env.get("HG_AGENT_BASE_URL", "https://api.deepseek.com"),
            "HG_AGENT_API_KEY": env["HG_AGENT_API_KEY"],
            "note": "走网络,按量计费",
        }
    return presets


# ------------------------------------------------------------------ 选项扫描 ---

def method_catalogue() -> list[dict]:
    """`methods/` 目录就是方法清单,`method.json` 补充说明它是什么。

    抽成函数是因为目录是唯一事实来源,而它同时被「改进方法」下拉和 /api/options 用。
    以前还有一个"方法一览"页读它,那一页删掉了 —— 它只是把 methods/ 的内容重新排了一遍,
    对着它做不了任何决定。两份各自扫描
    目录迟早会漂移,而漂移的表现是下拉里有一个方法、一览页里没有 —— 读的人没法
    判断哪个是真的。
    """
    out = []
    for d in sorted((ROOT / "methods").iterdir()):
        entry = d / "run.py"
        if not (d.is_dir() and entry.exists()):
            continue
        meta = {}
        meta_path = d / "method.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                meta = {}
        out.append({
            "name": d.name,
            "entrypoint": str(entry),
            "kind": meta.get("kind", "other"),
            "title": meta.get("title", d.name),
            "note": meta.get("note", ""),
            # --- 解耦之后,一个方法要说清楚它用哪个改进器、有没有自带 skill ---
            #
            # 平台把 method 定义成"一条规则 + 一个改进器"(INTERFACE.md §4.49),而不
            # 是"一个自带动辄全套的东西"。所以这三格是选的,不是可选的装饰:没有它们,
            # 读的人分不清"这个方法自带改进器"和"它借用默认那个"。
            #
            # `improver` 留空 = 用 `improvers/improvers.json` 的默认。
            "improver": meta.get("improver") or "",
            "improver_note": meta.get("improver_note", ""),
            "skill": (str(d / "skill.md") if (d / "skill.md").is_file() else ""),
        })
    return out


def _dotenv(path: Path) -> dict[str, str]:
    """`.env` 的最小读取器 —— 面板需要知道 driver 会看到什么。

    不是通用 dotenv:只处理 `KEY=value`、`#` 注释和最外层引号。为读自己仓库里的一个
    文件引入一个第三方依赖不值得,而解析失败又会让面板报出一个和 run 不一致的配置。
    """
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def improver_options() -> dict:
    """Configured improvers, each resolved on **this** machine.

    解析在这里做一次、缓存一次:它是几秒钟的活(要真的执行每个候选二进制),而表单每次
    打开、每个 /api/options 调用都会读它。缓存的是"这台机器上有什么",不是"这次运行
    用了什么" —— 后者在曲线点上,由 driver 解析并记录。
    """
    global _IMPROVER_CACHE
    with _IMPROVER_LOCK:
        if _IMPROVER_CACHE is not None:
            return _IMPROVER_CACHE
    try:
        import importlib
        mod = importlib.import_module("tools.improver")
        config = mod.load_config()
        entries = config.get("improvers") or {}
        out = {}
        for key, entry in entries.items():
            # 每个条目单独解析:一个坏条目不掩盖另一个。
            view = {"name": entry.get("name") or key,
                    "kind": entry.get("kind", "cli"),
                    "version_requested": entry.get("version", "") or "",
                    "modified": bool(entry.get("modified")),
                    "note": entry.get("note", ""),
                    "default": key == config.get("default")}
            try:
                cfg = dict(config)
                cfg["default"] = key
                got = mod.resolve(cfg)
                view.update({"ok": bool(got.get("ok")),
                             "version": got.get("version", ""),
                             "model": got.get("model", ""),
                             "path": got.get("path", ""),
                             "reason": got.get("reason", "")})
            except Exception as exc:                            # noqa: BLE001
                view.update({"ok": False, "reason": str(exc)[:200]})
            out[key] = view
    except Exception as exc:                                    # noqa: BLE001
        out = {"_error": {"ok": False, "reason": f"improver config unreadable: {exc}"}}
    with _IMPROVER_LOCK:
        _IMPROVER_CACHE = out
    return out


#: 解析结果只在这台机器变化时需要失效;面板重启即重算。
_IMPROVER_CACHE: dict | None = None
_IMPROVER_LOCK = threading.Lock()


def scan_options() -> dict:
    """从磁盘列出可选项。加一个 harness 或数据集不需要改这个文件。"""
    harnesses = []
    for d in sorted((ROOT / "base_harness").iterdir()):
        manifest = d / "harness.json"
        if not (d.is_dir() and manifest.exists()):
            continue
        try:
            m = json.loads(manifest.read_text())
        except json.JSONDecodeError:
            continue
        harnesses.append({"dir": d.name, "name": m.get("name"),
                          "version": m.get("version"),
                          # What this harness can run, and whether it is a subject of
                          # measurement at all. Without these two the form offers five
                          # harnesses with no way to tell that only one of them can run
                          # the dataset you picked -- which is exactly what happened:
                          # a run was refused for a missing *image* while the real
                          # problem was that the harness declared `files` only.
                          "env_kinds": list(m.get("env_kinds") or ["files"]),
                          "role": m.get("role") or "harness",
                          "lines": sum(1 for _ in (d / m.get("entrypoint", "agent.py"))
                                       .read_text().splitlines())
                          if (d / m.get("entrypoint", "agent.py")).exists() else None})

    datasets = []
    # **Only declared datasets.** `data/` also holds the platform's own fixtures -- `demo`,
    # `probe_set`, `broken_service` -- and globbing the directory offered all five in the
    # same list, with no way to tell that three of them are scaffolding. The files stay
    # (`demo` alone is used by 21 test modules); they are simply not choices. The
    # declaration is `KIND` in the module; see `data/scaffolds/registry.py`.
    for f in (ROOT / "data" / "datasets" / f"{n}.py" for n in registry.published()):
        try:
            sys.path.insert(0, str(ROOT))
            import importlib
            mod = importlib.import_module(f"data.datasets.{f.stem}")
            tasks, scorable = mod.load()
            # What this dataset needs from an environment, so the form can say which
            # harnesses can run it instead of letting the driver refuse after the click.
            # `registry.load_envs` can legitimately refuse a dataset (a contradictory
            # `services` block, an unknown field), and then "files" is the honest
            # answer because nothing was declared. But a *programming* error here --
            # a missing import, say -- would look identical and would silently make
            # every dataset claim it needs only `files`, which turns the compatibility
            # warning below into a warning that never fires. So the fallback is narrow.
            try:
                envs = registry.load_envs(f.stem)
            except ValueError:
                needed = ["files"]
            else:
                needed = sorted({(e or {}).get("kind", "files") for e in envs.values()})
            datasets.append({"name": f.stem, "count": len(tasks),
                             "env_kinds": needed,
                             "sample": (tasks[0].get("goal", "")[:60] if tasks else "")})
        except Exception:                                       # noqa: BLE001
            continue

    # 目录是唯一的事实来源;每个方法可以用一个 `method.json` 说明自己是什么。
    #
    # 「改进方法」下拉以前只是把目录名平铺出来,于是 `echo_base`、`llm_improver` 和
    # 移植过来的已发表方法混在同一列里 —— 看不出哪个是平台自带的契约自检,哪个是
    # 真能用的方法,哪个只是模板。目录仍然是真相,`kind` 只是把它写出来。
    methods = method_catalogue()
    return {"harnesses": harnesses, "datasets": datasets, "methods": methods,
            # 改进器是实验装置,不是每个方法的自由度。列表给表单和身份卡片用:
            # 每个条目都带"在这台机器上解析到了什么",因为机器上可能有多个 codex,
            # 而坏的那个就在 PATH 上(docs 见 tools/improver.py)。
            "improvers": improver_options(),
            "models": {k: {kk: vv for kk, vv in v.items() if kk != "note"}
                       for k, v in model_presets().items()},
            "model_notes": {k: v.get("note", "") for k, v in model_presets().items()}}


# -------------------------------------------------------------------- 实验 jobs ---

#: 正在跑(或刚跑完)的实验。进程对象不跨请求保存,状态靠日志文件和 returncode。
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def start_job(cfg: dict) -> dict:
    """按表单配置启动一次 driver 运行,日志写到 runs/<id>/console.log。

    用子进程而不是在线程里 import driver:一次实验可能跑几分钟,而且 driver 会加载
    .env、改环境变量、起沙箱。隔一个进程边界,这些都不会串到 UI 这边来。
    """
    harness = cfg.get("harness") or "loop"
    dataset = cfg.get("dataset") or "demo"
    method = cfg.get("method") or "noop"
    rounds = int(cfg.get("rounds") or 1)
    mode = cfg.get("mode") or "A"
    model_key = cfg.get("model") or next(iter(model_presets()))
    # A run covers one side (INTERFACE.md §2.6). An eval run measures a state a train
    # run left behind, so it names that run and evolves nothing.
    side = cfg.get("side") or "train"
    if side not in ("train", "eval"):
        return {"error": f"未知的侧别:{side}"}
    from_run = (cfg.get("from_run") or "").strip()
    if side == "eval" and not from_run:
        return {"error": "eval 要指名测哪个训练状态;先在 train 侧跑一次"}

    run_id = (cfg.get("run_id") or "").strip()

    if not SAFE_ID.match(run_id or "x"):
        return {"error": "实验名称只能用字母、数字、点、下划线和短横线"}
    if not run_id:
        run_id = f"{harness}-{dataset}-{int(time.time()) % 100000}"

    run_dir = RUNS / run_id
    if run_dir.exists():
        if not cfg.get("overwrite"):
            return {"error": f"实验 {run_id} 已存在,换个名字或勾选覆盖"}
        # 覆盖 = 真的重来,不是往里续。
        #
        # 以前只截断 console.log(`"w"`),而 `round*_base/`、`round*_ws/`、
        # `round*_trajectory.json`、`diffs/` 会留着上一次的。新的一次轮数更少时,那些
        # 残留就成了这次记录的一部分,读的人分不出哪些是本次产生的 —— 那正是
        # "我选的是 RRSI,为什么在跑 DGM" 这类困惑的来源。
        shutil.rmtree(run_dir)

    presets = model_presets()
    if model_key not in presets:
        return {"error": f"未知模型预设:{model_key}"}
    model_env = presets[model_key]

    method_dir = ROOT / "methods" / method
    if not (method_dir / "run.py").exists():
        return {"error": f"方法 {method} 没有 run.py"}

    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "console.log"

    env = dict(os.environ)
    env.update({k: v for k, v in model_env.items() if k.startswith("HG_AGENT")})
    env.setdefault("PYTHONUNBUFFERED", "1")

    argv = [sys.executable, str(ROOT / "driver.py"),
            "--harness", harness, "--dataset", dataset, "--mode", mode,
            "--rounds", str(rounds), "--run-id", run_id, "--sampling", "all",
            "--side", side,
            "--method-entrypoint", f"{sys.executable} {method_dir / 'run.py'}"]
    if side == "eval":
        argv += ["--from-run", from_run]
    # 哪个改进器 —— 命令行传,不是环境变量碰运气。driver 会把它解析出的版本与哈希
    # 记在每个曲线点上(INTERFACE.md §4.49),所以这一格选什么必须出现在运行记录里。
    improver = (cfg.get("improver") or "").strip()
    improver_model = (cfg.get("improver_model") or "").strip()
    if improver:
        argv += ["--improver", improver]
    if improver_model:
        argv += ["--improver-model", improver_model]

    tasks = [t for t in (cfg.get("tasks") or []) if str(t).strip()]
    if tasks:
        argv += ["--tasks", ",".join(str(t) for t in tasks)]
    if cfg.get("no_sandbox"):
        argv.append("--no-sandbox")

    # 日志里只留一行命令,配置进 launch.json。
    #
    # 这里以前把整个模型环境变量块以 `#` 注释写进 console.log:结果是配置(以及一个
    # 被打码的 key 名字)夹在运行的证据中间,而面板想把它渲染成字段,就得先把这段
    # 注释从终端文本里反解析出来。命令本身还留在日志第一行,因为它是最直接的复现方式。
    log = log_path.open("w")
    log.write(f"$ {' '.join(argv)}\n")
    log.flush()

    (run_dir / "launch.json").write_text(json.dumps({
        "argv": argv,
        "model_preset": model_key,
        "model_env": {k: ("***" if "KEY" in k else v)
                      for k, v in sorted(model_env.items())
                      if k.startswith("HG_AGENT")},
        "sandbox": not cfg.get("no_sandbox"),
        "started": time.time(),
    }, indent=1, ensure_ascii=False))

    proc = subprocess.Popen(argv, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                            env=env, start_new_session=True)
    with JOBS_LOCK:
        JOBS[run_id] = {"pid": proc.pid, "log": str(log_path), "started": time.time(),
                        "run_id": run_id, "argv": argv, "model": model_key,
                        "harness": harness, "dataset": dataset, "method": method,
                        "improver": improver, "improver_model": improver_model}
    return {"started": run_id}


def job_status(run_id: str) -> dict:
    if not SAFE_ID.match(run_id):
        return {"error": "bad id"}
    with JOBS_LOCK:
        job = JOBS.get(run_id)
    log_path = (Path(job["log"]) if job else RUNS / run_id / "console.log")
    text = log_path.read_text() if log_path.exists() else ""

    alive = False
    if job:
        try:
            os.kill(job["pid"], 0)
            alive = True
        except OSError:
            alive = False
    if not alive:
        # 不在 JOBS 里(面板重启过,或这次运行不是面板启动的):查进程表。
        alive = driver_pid(run_id) is not None

    curve = RUNS / run_id / "curve.jsonl"
    points = _read_jsonl(curve)

    # driver 写的事件流。面板按事件渲染,不按终端文本渲染 —— 文本是给人扫的,
    # 事件是给面板排版的,两者都来自 driver 的同一次叙述,所以不会互相漂移。
    events = _read_jsonl(RUNS / run_id / "events.jsonl")

    # 进度优先从事件里数。旧的 console.log 里表头和数据行的正则仍然保留作回退:
    # 事件流是本轮才有的,之前跑出来的 run 目录里只有文本。
    rounds_seen = sum(1 for e in events if e.get("kind") == "round")
    if not events:
        rounds_seen = len(re.findall(r"^\s*\d+\s+[\d.]+", text, re.M))
    return {"run_id": run_id, "running": alive, "log": text[-6000:],
            "events": events,
            "rounds_seen": rounds_seen, "points": len(points),
            "started": job["started"] if job else None,
            "config": {k: job.get(k) for k in
                       ("model", "harness", "dataset", "method", "improver",
                        "improver_model")} if job else None}


def driver_pid(run_id: str) -> int | None:
    """面板外启动(或面板重启前启动)的那次运行,进程还在不在。

    为什么必须查进程而不是只信内存:`JOBS` 是这个 HTTP 服务进程里的字典,面板一旦重启
    就空了 —— 而 driver 是 `setsid` 出去的独立进程,它会继续跑完。实测:重启面板之后
    `/api/job/<id>` 对一次**正在跑**的 run 回 `running: false`,于是页面把"还在跑"和
    "被拒"显示成同一句话,用户看到的是"在门口被拒"(而它其实在跑第 3 题)。

    只看 `/proc/<pid>/cmdline`,不做任何推断:命令行里同时出现 `driver.py` 与这个
    run-id 才算它。只读,不写任何东西。
    """
    if not SAFE_ID.match(run_id or ""):
        return None
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"driver.py" not in raw:
            continue
        argv = raw.split(b"\0")
        if any(a == run_id.encode() for a in argv) or \
                any(run_id.encode() in a for a in argv):
            try:
                return int(entry.name)
            except ValueError:
                continue
    return None


def stop_job(run_id: str) -> dict:
    """停一次运行。面板重启过之后也能停 —— 靠进程表找到它。

    以前只认 `JOBS` 里的 pid,于是"面板重启前启动的那次"停不掉,而它可能还要跑几小时。
    """
    with JOBS_LOCK:
        job = JOBS.get(run_id)
    pid = job.get("pid") if job else None
    if pid is None:
        pid = driver_pid(run_id)
    if pid is None:
        return {"error": "这个 run 没有在跑(或找不到它的进程)"}
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return {"stopped": run_id}
    except OSError as exc:
        return {"error": str(exc)}




def step_diff(run_id: str, round_no: int) -> dict:
    """一个 step 的改动。优先读持久化的 patch,回退到 git。

    `diffs/` 是 driver 写的实验记录的一部分。回退路径存在是因为早期的 run 没有它,
    而那些 commit 在 mode A 结束时被 `reset_hard` 变成了游离对象 —— 通常还能取到,
    直到某次 `git gc` 把它们收走。所以回退是尽力而为,持久化的才是可靠的。
    """
    if not SAFE_ID.match(run_id):
        return {"error": "bad id"}
    run = RUNS / run_id
    meta_file = run / "diffs" / f"round-{round_no}.json"
    patch_file = run / "diffs" / f"round-{round_no}.patch"

    meta, patch = {}, ""
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
        except json.JSONDecodeError:
            meta = {}
    if patch_file.exists():
        patch = patch_file.read_text()
    else:
        # 回退:从 git 现算
        work = None
        for base in WORK_ROOTS:
            cand = base / run_id / "workspace"
            if (cand / ".git").exists():
                work = cand
                break
        points = _read_jsonl(run / "curve.jsonl")
        if work and 0 <= round_no - 1 < len(points) - 1:
            a = points[round_no - 1]["identity"]["harness_sha"]
            b = points[round_no]["identity"]["harness_sha"]
            r = subprocess.run(["git", "-C", str(work), "diff", a, b],
                               capture_output=True, text=True, errors="replace")
            if r.returncode == 0:
                patch = r.stdout

    if not patch and not meta:
        return {"found": False, "round": round_no}
    return {"found": True, "round": round_no, "patch": patch[:40000],
            "meta": meta, "truncated": len(patch) > 40000}


def explain_diff(run_id: str, round_no: int) -> dict:
    """让一个模型用自然语言解释这次改动。

    只有人点了才跑 —— 这是刻意的。解释要花一次模型调用和几秒钟,而大多数 step
    是不需要解释的;自动为每个 step 生成,既慢又浪费,还会把页面变成一堵没人读的
    散文墙。手动触发意味着解释出现在有人真的想问的时候。

    模型来源依次尝试:本地服务(免费、快)> `HG_METHOD_*`(方法模型)。
    """
    if not SAFE_ID.match(run_id):
        return {"error": "bad id"}
    info = step_diff(run_id, round_no)
    if not info.get("found"):
        return {"error": "这一轮没有 diff"}
    patch = info.get("patch") or ""
    if not patch.strip():
        return {"explanation": "这一轮没有任何改动(方法报告 no change)。"}

    points = _read_jsonl(RUNS / run_id / "curve.jsonl")
    before = points[round_no - 1] if round_no - 1 < len(points) else {}
    after = points[round_no] if round_no < len(points) else {}

    system = ("You explain code changes to a language-model agent harness, for an "
              "audience of researchers. Be concrete and short. Say what changed, what "
              "failure it is meant to address, and any risk it introduces. No preamble.")
    user = (
        f"这是第 {round_no} 步对 agent harness 的改动。\n"
        f"改动前后同一批任务上的得分:{before.get('score')} -> {after.get('score')}\n"
        f"改动的文件:{json.dumps(info.get('meta', {}).get('files', []))}\n\n"
        f"```diff\n{patch[:12000]}\n```\n\n"
        "用中文写 3-5 句话:改了什么、针对什么问题、有什么风险。")

    # 先试本地服务:免费且快,演示时最稳
    candidates = [("http://127.0.0.1:8002/v1", "local",
                   os.environ.get("HARNESSGRAD_EXPLAIN_MODEL", "qwen3.5-9b-local"))]
    env = {}
    ef = ROOT / ".env"
    if ef.exists():
        for line in ef.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    if env.get("HG_METHOD_BASE_URL") and env.get("HG_METHOD_API_KEY"):
        candidates.append((env["HG_METHOD_BASE_URL"], env["HG_METHOD_API_KEY"],
                           env.get("HG_METHOD_MODEL", "")))

    last_error = "没有可用的模型"
    for base_url, key, model in candidates:
        if not model:
            continue
        try:
            body = json.dumps({
                "model": model, "max_tokens": 700, "temperature": 0.2,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            }).encode()
            req = urllib.request.Request(
                base_url.rstrip("/") + "/chat/completions", data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            text = (data["choices"][0]["message"]["content"] or "").strip()
            if text:
                return {"explanation": text, "model": model}
            last_error = f"{model} 返回了空内容"
        except Exception as exc:                              # noqa: BLE001
            last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
    return {"error": f"解释失败:{last_error}"}






#: 预设的模型提供方。照 DSH 的设置页学的关键一点:**用户不该需要知道 base_url**。
#: 填一个 endpoint 字符串是最容易出错的一步(漏 `/v1`、多个斜杠、写成网页地址),
#: 而它的失败要到第一次真实实验才出现。所以给一组带默认值的目录,选一个就填好了;
#: base_url 仍然可改,用于自建网关或没列在这里的服务。
#:
#: `local` 标记的项不需要 key —— 本地 vLLM 不鉴权,而把它显示成"缺 API key"会让
#: 人以为配置不完整。DSH 的做法是允许"无凭据的 provider 原生配置",这里照做。
PROVIDERS = {
    "本地 vLLM(免费,秒回)": {
        "base_url": "http://127.0.0.1:8002/v1",
        "models": ["qwen3.5-9b-local", "qwen3-30b-local", "qwen2.5-7b-local"],
        "local": True,
        "note": "需要先在下面启动本地模型服务;首次加载约 2 分钟",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-flash", "deepseek-reasoner"],
        "local": False,
        "note": "按量计费",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "local": False,
        "note": "需要能访问外网",
    },
    "自建 / 其他 OpenAI 兼容服务": {
        "base_url": "",
        "models": [],
        "local": False,
        "note": "自己填 base_url;任何兼容 /chat/completions 的服务都行",
    },
}


def provider_catalog() -> dict:
    """给设置页的目录:每个提供方的默认值,以及本机当前可用的本地模型。"""
    catalog = {k: dict(v) for k, v in PROVIDERS.items()}

    # 本地服务真在跑的话,把它自报的模型名填进去 —— 这比预设的猜测准
    try:
        with urllib.request.urlopen("http://127.0.0.1:8002/v1/models", timeout=4) as r:
            names = [m["id"] for m in json.load(r).get("data", [])]
        if names:
            catalog["本地 vLLM(免费,秒回)"]["models"] = names
            catalog["本地 vLLM(免费,秒回)"]["running"] = True
    except Exception:                                       # noqa: BLE001
        catalog["本地 vLLM(免费,秒回)"]["running"] = False

    # 有哪些本地模型权重可服务,给"启动服务"用
    candidates = []
    for base in ("/mnt/20t/qzs/PLMs", "/mnt/20t/lhz/models", "/mnt/20t/xuhaoming/models"):
        d = Path(base)
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if (sub / "config.json").exists() and (sub / "model.safetensors.index.json").exists():
                candidates.append(str(sub))
    catalog["_local_model_dirs"] = candidates[:20]
    return catalog


# ------------------------------------------------------------------ 设置 ---

#: 面板会管理的键。刻意是显式清单:`.env` 里可能有别人手写的配置,面板不该去动
#: 它不认识的键。
SETTING_KEYS = (
    "HG_AGENT_BACKEND", "HG_AGENT_MODEL", "HG_AGENT_BASE_URL", "HG_AGENT_API_KEY",
    "HG_AGENT_TIMEOUT_S",
    "HG_METHOD_MODEL", "HG_METHOD_BASE_URL", "HG_METHOD_API_KEY",
    "HG_METHOD_TIMEOUT_S",
)


def read_settings() -> dict:
    """当前配置,**密钥只报"有没有",不回显。**

    这是刻意的:一个把 API key 送回浏览器的页面,等于把它写进了浏览器缓存、开发者
    工具的历史、以及任何一次截图。面板需要知道"填过没有"来决定显示什么,不需要
    知道值本身。
    """
    path = ROOT / ".env"
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()

    out = {}
    for key in SETTING_KEYS:
        raw = values.get(key, "")
        if "API_KEY" in key:
            out[key] = {"set": bool(raw), "hint": ("已设置" if raw else "未设置")}
        else:
            out[key] = {"set": bool(raw), "value": raw}
    out["_env_exists"] = path.exists()
    out["_env_example_exists"] = (ROOT / ".env.example").exists()
    return out


def write_settings(updates: dict) -> dict:
    """把改动写进 `.env`,保留注释、空行和面板不认识的键。

    逐行改写而不是重新生成整个文件:`.env` 里通常有别人写的说明和别的键,用一个
    生成的版本覆盖它,就是在用户不知情的时候删掉他们的东西。

    空字符串表示"不要动这个键" —— 这样密钥框留空提交不会把已存的 key 抹掉,而那
    是这种表单最常见的一次误操作。
    """
    path = ROOT / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    if not lines and (ROOT / ".env.example").exists():
        # 第一次使用:以 .env.example 的注释骨架为底,让新用户看到每项是干什么的
        lines = [l for l in (ROOT / ".env.example").read_text().splitlines()
                 if not _is_placeholder_line(l)]

    applied, seen = [], set()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates and updates[key] != "":
                out.append(f"{key}={updates[key]}")
                applied.append(key)
                seen.add(key)
                continue
        out.append(line)

    # 新键追加到末尾(带一行说明,因为下一个人要读这个文件)
    for key, value in updates.items():
        if key in SETTING_KEYS and key not in seen and value != "":
            out.append(f"{key}={value}")
            applied.append(key)

    path.write_text("\n".join(out).rstrip("\n") + "\n")
    return {"applied": sorted(applied)}


def _is_placeholder_line(line: str) -> bool:
    """`.env.example` 里 `KEY=` 这种空占位行,铺到 `.env` 时没必要保留。"""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#") \
        and "=" in stripped and stripped.split("=", 1)[1].strip() == ""


def test_connection(which: str) -> dict:
    """真的调一次模型,而不是只看配置填没填。

    "配置看起来对"和"能调通"是两件事:endpoint 可能缺 `/v1`、模型名可能和服务的
    `--served-model-name` 不一致、key 可能过期。这几种都会在第一次真实实验时才
    暴露,而那时已经花掉了几分钟和一次评测。所以点一下按钮就当场验证。
    """
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    prefix = "HG_AGENT" if which == "agent" else "HG_METHOD"
    backend = env.get(f"{prefix}_BACKEND", "openai") if which == "agent" else "openai"
    model = env.get(f"{prefix}_MODEL", "")
    base_url = env.get(f"{prefix}_BASE_URL", "")
    api_key = env.get(f"{prefix}_API_KEY", "")

    if not model or not base_url:
        return {"ok": False, "detail": f"{prefix}_MODEL 或 {prefix}_BASE_URL 没填"}
    if backend == "mock":
        return {"ok": True, "detail": "mock 后端,不需要连接(但分数不代表任何模型)"}

    # 先问服务端它到底有什么模型。这一步把三类问题分开:
    #   * endpoint 写错(连不上 / 路径不对)
    #   * endpoint 对了但**模型名不在里面** —— 最常见,而且从 404 的原文里看不出来
    #   * 都对了但调用失败(鉴权、配额)
    # 只做第二步的检查是值得的:模型名和 endpoint 分别来自两个输入框,配错一个
    # 是很容易的事,而它的报错长得很像"服务没起来"。
    # `/models` 也要带鉴权头。第一次写这段时没带,于是 DeepSeek 回 401,而我把
    # 它当成"连不上"报了出去 —— 一个本来能用的配置被判成了坏配置。这类检查必须
    # 自己先是对的,否则它比没有更糟:它会把好配置说成坏的。
    available: list[str] = []
    probe_failed: str | None = None
    try:
        probe = urllib.request.Request(
            base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {api_key or 'local'}"})
        with urllib.request.urlopen(probe, timeout=15) as resp:
            available = [m.get("id") for m in json.load(resp).get("data", [])]
    except Exception as exc:                                # noqa: BLE001
        # 403/404 只说明这个服务不暴露 `/models`,不说明配置错了 —— 所以记下来,
        # 继续走真正的 chat 调用,由它来判定。
        code = getattr(exc, "code", None)
        probe_failed = f"{code or type(exc).__name__}"
        if code not in (401, 403, 404, 405):
            return {"ok": False,
                    "detail": f"连不上 {base_url}/models —— {type(exc).__name__}: "
                              f"{str(exc)[:150]}\n"
                              f"  先确认服务在跑、地址和路径对不对"}
    if available and model not in available:
        return {"ok": False,
                "detail": f"模型名对不上:配置里是 {model!r},但 {base_url} 提供的是 "
                          f"{available[:6]}\n"
                          f"  把模型名改成上面之一,或者换一个提供方"}

    try:
        # 64 而不是 16:推理模型会把配额先花在思考上,cap 太小就只剩空回复 ——
        # 那会让一次成功的连接看起来像失败。实测 harness 模型在
        # max_tokens=16 时返回空串,而 usage 里明明记了 37/16 个 token。
        body = json.dumps({
            "model": model, "max_tokens": 64,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        }).encode()
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key or 'local'}"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.load(resp)
        text = (data["choices"][0]["message"]["content"] or "").strip()
        usage = data.get("usage") or {}
        # 空回复但拿得到 usage,说明连接、鉴权、模型名都是对的 —— 那就算调通,
        # 只是这个模型没在 64 个 token 内说出正文。说清楚,别让人以为配置错了。
        shown = repr(text[:40]) if text else "(模型没在 64 token 内输出正文,但连接正常)"
        extra = f" · 服务端共 {len(available)} 个模型" if available else ""
        return {"ok": True,
                "detail": (f"调通了({model}):{shown}{extra}"
                           f" · tokens {usage.get('prompt_tokens', '?')}"
                           f"/{usage.get('completion_tokens', '?')}")}
    except Exception as exc:                                # noqa: BLE001
        hint = ""
        msg = str(exc)
        if probe_failed and probe_failed in ("401", "403"):
            hint = "\n  key 不对或过期(服务端在 /models 上就拒绝了鉴权)"
        elif "404" in msg:
            hint = "\n  endpoint 常见错法:漏了 /v1,或者少了 /chat/completions"
        elif "model" in msg.lower() and "not" in msg.lower():
            hint = "\n  模型名要和服务端的 --served-model-name 一致"
        elif "401" in msg or "403" in msg:
            hint = "\n  key 不对或过期"
        return {"ok": False, "detail": f"{type(exc).__name__}: {msg[:200]}{hint}"}


def model_service(action: str = "status", model_dir: str = "",
                  port: int = 0) -> dict:
    """本地模型服务的启停与状态。

    启停交给 `tools/harnessgrad.sh`,不在这里重新实现:那个脚本要处理端口占用、
    等就绪、以及这台共享机器上"别杀别人的进程",那些逻辑只有一份才对。面板只是
    调用它并展示结果。
    """
    script = ROOT / "tools" / "harnessgrad.sh"
    if not script.exists():
        return {"error": "tools/harnessgrad.sh 不存在"}

    env = dict(os.environ)
    if model_dir:
        env["HG_MODEL_DIR"] = model_dir
    if port:
        env["HG_MODEL_PORT"] = str(port)

    if action == "status":
        # 直接查端口,不调脚本 —— status 要快,而脚本的 status 会做几次 HTTP 探测
        target = port or int(env.get("HG_MODEL_PORT", 8001))
        pid = subprocess.run(["bash", "-c",
                              f"ss -ltnp 2>/dev/null | grep ':{target} ' | "
                              f"grep -oP 'pid=\\K[0-9]+' | head -1"],
                             capture_output=True, text=True, errors="replace").stdout.strip()
        live = False
        if pid:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{target}/v1/models", timeout=4) as r:
                    live = bool(json.load(r).get("data"))
            except Exception:                               # noqa: BLE001
                live = False
        return {"running": bool(pid), "ready": live, "pid": pid or None,
                "port": target}

    if action in ("start", "stop"):
        # 不等待:启动要两分钟,而一个挂两分钟的 HTTP 请求会被浏览器先掐掉。
        # 面板轮询 /api/model-service 看它什么时候就绪。
        subprocess.Popen(["bash", str(script), action], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {"started": action, "note": "本地模型服务首次加载约 2 分钟"}

    return {"error": f"未知动作 {action}"}


# ------------------------------------------------------------------ 读数(同前) ---

def _read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def promote(run_id: str, round_no: int, as_name: str, overwrite: bool = False) -> dict:
    """Copy a run's harness state into `base_harness/`, to keep evolving from it.

    Why this is a copy and not a pointer: `base_harness/` is what a *new run* starts
    from, and a new run stages it into its own workspace and commits it. A pointer would
    make the start of a run depend on a record that a later cleanup could remove -- and
    the run's own record already says which state it came from, so the copy loses
    nothing.

    Refuses a name that exists unless asked, and **refuses to overwrite a harness that
    has no provenance of its own** only in the sense that it says where the old one came
    from: replacing somebody's base harness by typing a name twice is the kind of thing
    that should take one extra sentence to confirm.
    """
    if not SAFE_ID.match(run_id or "") or not SAFE_ID.match(as_name or ""):
        return {"error": "运行名和目标名只能用字母、数字、点、下划线和短横线"}
    src = RUNS / run_id / f"state_after_round{int(round_no)}"
    if not src.is_dir():
        return {"error": f"{run_id} 没有第 {round_no} 轮的状态;"
                         f"先跑一次带轮次的 train(状态在每轮结束时保存)"}
    if not (src / "harness.json").exists():
        return {"error": f"{src} 里没有 harness.json,不是一个 harness"}

    dest = ROOT / "base_harness" / as_name
    if dest.exists() and not overwrite:
        prev = {}
        prov = dest / "PROVENANCE.json"
        if prov.exists():
            try:
                prev = json.loads(prov.read_text())
            except json.JSONDecodeError:
                prev = {}
        origin = ""
        if prev.get("from_run"):
            origin = f"(现有的来自 {prev['from_run']} 第 {prev.get('round')} 轮)"
        return {"error": f"base_harness/{as_name} 已存在{origin};勾选覆盖才会替换",
                "exists": True}

    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".state"))
        # Where it came from, so a reader of the new base harness can find the run that
        # made it. Kept beside the harness rather than inside `harness.json`, which is
        # the manifest and is validated against a known field list.
        curve = RUNS / run_id / "curve.jsonl"
        sha = None
        if curve.exists():
            for line in reversed(curve.read_text().splitlines()):
                if not line.strip():
                    continue
                try:
                    pt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if pt.get("round") == int(round_no):
                    sha = (pt.get("identity") or {}).get("harness_sha")
                    break
        (dest / "PROVENANCE.json").write_text(json.dumps({
            "from_run": run_id, "round": int(round_no), "harness_sha": sha,
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, indent=1))
    except OSError as exc:
        return {"error": f"复制失败:{exc}"}
    return {"ok": True, "path": str(dest), "from_run": run_id, "round": int(round_no)}


def version_graph() -> dict:
    """Every run as a lane of rounds, with **only the links the record actually contains**.

    Two kinds of edge, both read rather than inferred:

    * **within a run** — round N to round N+1. That is what a round *is*: the state the
      next one starts from.
    * **across runs** — an eval point to the train round it measured, which is
      `evaluated_from` (§2.6). This is the edge that makes the graph worth drawing at
      all: it is the train-vs-exam pair, and before the split it did not exist.

    Nothing else is drawn, and in particular no edge is invented from "these two runs
    used the same harness name". A graph whose edges are guesses is worse than a table,
    because it looks like evidence.

    **Lanes are `(harness, agent_model)`.** Different base models are different
    instruments (§3), so they are never drawn on one line -- the same rule `plot_curve`
    enforces for curves.
    """
    lanes: dict[str, list[dict]] = {}
    for r in list_runs():
        if r.get("refused"):
            continue
        detail = run_detail(r["run_id"]) or {}
        pts = detail.get("points") or []
        if not pts:
            continue
        lane = f"{r.get('harness') or '?'} · {r.get('agent_model') or '?'}"
        nodes, edges = [], []
        for p in pts:
            nodes.append({
                "id": f"{r['run_id']}:{p.get('round')}",
                "round": p.get("round"), "score": p.get("score"),
                "kind": p.get("score_kind") or ("exam" if p.get("side") == "eval"
                                                else "training"),
                "label": p.get("label") or "",
                "sha": ((p.get("identity") or {}).get("harness_sha") or "")[:8],
                "n_tasks": len(p.get("per_task") or {}),
            })
        for a, b in zip(nodes, nodes[1:]):
            edges.append({"from": a["id"], "to": b["id"], "kind": "round"})
        for p in pts:
            ef = p.get("evaluated_from") or {}
            if ef.get("run_id"):
                edges.append({"from": f"{ef['run_id']}:{ef.get('round')}",
                              "to": f"{r['run_id']}:{p.get('round')}",
                              "kind": "measured"})
        lanes.setdefault(lane, []).append({
            "run_id": r["run_id"], "side": r.get("side") or "?",
            "model": r.get("agent_model"), "harness": r.get("harness"),
            "nodes": nodes, "edges": edges, "mtime": r["mtime"],
        })
    for v in lanes.values():
        v.sort(key=lambda x: x["mtime"])
    return {"lanes": [{"lane": k, "runs": v} for k, v in sorted(lanes.items())]}


def task_options(dataset: str) -> dict:
    """Every task of one dataset, with the side it is on and a preview of its goal.

    The preview is the *goal text* and nothing else. A task dict carries no grade and no
    initial state by construction (`registry.FORBIDDEN_IN_TASKS`), so building a picker
    out of it cannot leak an answer -- which is the property that lets this list exist
    at all while the eval side stays hidden from the method.
    """
    if not dataset or not SAFE_ID.match(dataset):
        return {"error": "bad dataset name"}
    try:
        tasks, _ = registry.load(dataset)
        split = registry.load_split(dataset)[2]
        envs = registry.load_envs(dataset)
    except (ValueError, ImportError) as exc:
        return {"error": str(exc)[:300]}
    side_of = {}
    for side in ("train", "eval"):
        for tid in (split or {}).get(side, []):
            side_of[tid] = side

    # **Is this task runnable here?** Without this the picker offered every task with a
    # green checkbox including the ones whose image cannot be fetched, and the first
    # thing a user met was a refusal for a task the UI had called fine. A picker that
    # claims more than it knows is worse than no picker: it moves the discovery of the
    # problem to after the click.
    #
    # `resolve_digest` caches per image, so a 89-task dataset costs one `docker image
    # inspect` per distinct image rather than per task.
    unavailable: dict[str, str] = {}
    for tid, spec in (envs or {}).items():
        if (spec or {}).get("kind") != "exec":
            continue
        image = spec.get("image")
        try:
            container.resolve_digest(image)
        except container.ContainerUnavailable:
            unavailable[tid] = image or "?"
        except Exception:                                       # noqa: BLE001
            unavailable[tid] = image or "?"

    return {"dataset": dataset, "has_split": split is not None,
            "n_unavailable": len(unavailable),
            "tasks": [{"task_id": t["task_id"],
                       "side": side_of.get(t["task_id"], "eval"),
                       "available": t["task_id"] not in unavailable,
                       "why": (f"镜像 {unavailable[t['task_id']]} 不在本地"
                               if t["task_id"] in unavailable else ""),
                       "goal": " ".join((t.get("goal") or "").split())[:140]}
                      for t in tasks]}


#: Where a "prepare the environment" job keeps its log. Its own directory, not a run
#: directory: it produces no curve, and a reader looking at `runs/` must not find an
#: experiment there that never happened.
PREPARE_ROOT = RUNS / "_prepare"


def _harness_dir(name: str) -> Path | None:
    """The harness a name refers to, from `base_harness/` or as a path."""
    candidate = ROOT / "base_harness" / name
    if candidate.is_dir():
        return candidate
    path = Path(str(name)).expanduser()
    return path.resolve() if path.is_dir() else None


def prepare_report(dataset: str, harness: str) -> dict:
    """What this (dataset, harness) pair still needs before it can run. **Read-only.**

    Two different things are missing before a run can start, and they belong to different
    owners, which is why they are reported apart:

    * **task images** -- the environment. 89 images, ~104 GB, pulled from a registry.
      §2.5.5 forbids pulling during a run, so this must be a deliberate act; a button is
      that act, which is why one exists. Sizes are reported because 21.6 GB for one
      image is a decision, not a detail.
    * **the harness's own dependencies** -- nothing to do with the task. One overlay per
      `(install contents, Python minor version)`, ~12 s and ~42 MB each, and the number
      of them is a property of the *dataset*, not of the harness.

    Deliberately cheap: it never probes an interpreter. Which Python versions a dataset's
    images use is discovered by the configure job, not by a panel that has to render.
    """
    if not SAFE_ID.match(dataset or ""):
        return {"error": "bad dataset name"}
    try:
        envs = registry.load_envs(dataset)
    except (ValueError, ImportError) as exc:
        return {"error": str(exc)[:300]}

    images: dict[str, str] = {}                       # image -> one task id that needs it
    for tid, spec in sorted((envs or {}).items()):
        if (spec or {}).get("kind") == "exec" and spec.get("image"):
            images.setdefault(spec["image"], tid)

    local, missing = [], []
    for image, tid in images.items():
        try:
            container.resolve_digest(image)
            local.append({"task_id": tid, "image": image})
        except container.ContainerUnavailable as exc:
            missing.append({"task_id": tid, "image": image, "why": str(exc)[:200]})
        except Exception:                                       # noqa: BLE001
            missing.append({"task_id": tid, "image": image, "why": "unavailable"})

    dependencies: dict = {"declared": None, "install_sha256": None, "configured": []}
    hdir = _harness_dir(harness)
    if hdir is not None:
        install = harness_runtime.install_spec(hdir)
        if install:
            root = harness_runtime.overlays_root() / install[0][:16]
            built = sorted(p.name for p in root.iterdir()
                           if (p / "PROVENANCE.json").is_file()) if root.is_dir() else []
            dependencies = {"declared": install[1],
                            "install_sha256": install[0][:16], "configured": built}

    return {
        "dataset": dataset, "harness": harness,
        "images": {"total": len(images), "local": len(local), "missing": missing},
        "dependencies": dependencies,
        "actions": [
            {"id": "overlay",
             "label": "配置 harness 依赖",
             "detail": ("一次构建一个 Python 版本，约 12 秒 / 42 MB；已配 "
                        + (", ".join(dependencies["configured"]) or "无")),
             "runnable": bool(dependencies["declared"]),
             "reason": "" if dependencies["declared"] else
                       "这只 harness 没有声明 install，不需要配置"},
            {"id": "images",
             "label": f"拉取缺失的 {len(missing)} 个镜像",
             "detail": ("§2.5.5 禁止运行中途拉取，所以这一步必须由人触发；"
                        "实测这套数据集约 104 GB，最大的一个 21.6 GB"),
             "runnable": bool(missing),
             "reason": "" if missing else "镜像都已在本地"},
        ],
    }


def start_prepare(cfg: dict) -> dict:
    """Run one preparation step as a background job, streaming to its own log.

    A job rather than a request handler because pulling ~104 GB can take hours and can
    fail partway -- measured: three Terminal-Bench images fail with a TLS handshake
    timeout. A spinner over that is a lie about progress; a log that ends is not.
    """
    what = str(cfg.get("what") or "")
    dataset = str(cfg.get("dataset") or "")
    harness = str(cfg.get("harness") or "")
    if what not in ("overlay", "images"):
        return {"error": f"unknown prepare step: {what!r}"}
    if not SAFE_ID.match(dataset):
        return {"error": "bad dataset name"}

    mirrors = [m.strip() for m in
               (os.environ.get("HG_REGISTRY_MIRRORS") or "").split(",") if m.strip()]
    if what == "overlay":
        hdir = _harness_dir(harness)
        if hdir is None:
            return {"error": f"no such harness: {harness!r}"}
        argv = [sys.executable, str(ROOT / "tools" / "configure_harness.py"),
                "--harness", str(hdir), "--dataset", dataset]
    else:
        argv = [sys.executable, str(ROOT / "tools" / "import_env.py"),
                "--dataset", dataset]
        for mirror in mirrors:
            argv += ["--mirror", mirror]

    job_id = f"{what}-{dataset}-{int(time.time()) % 100000}"
    job_dir = PREPARE_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "console.log"
    log_path.write_text(f"$ {' '.join(argv)}\n", encoding="utf-8")
    proc = subprocess.Popen(argv, cwd=str(ROOT), stdout=log_path.open("ab"),
                            stderr=subprocess.STDOUT, env=dict(os.environ),
                            start_new_session=True)
    with JOBS_LOCK:
        JOBS[job_id] = {"pid": proc.pid, "log": str(log_path), "started": time.time(),
                        "run_id": job_id, "argv": argv, "prepare": what,
                        "harness": harness, "dataset": dataset, "method": "-",
                        "model": "-"}
    return {"started": job_id, "what": what}


def prepare_status(job_id: str) -> dict:
    if not SAFE_ID.match(job_id or ""):
        return {"error": "bad id"}
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    log_path = Path(job["log"]) if job else (PREPARE_ROOT / job_id / "console.log")
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    alive = False
    if job:
        try:
            os.kill(job["pid"], 0)
            alive = True
        except OSError:
            alive = False
    return {"run_id": job_id, "running": alive, "log": text[-8000:],
            "events": [], "prepare": (job or {}).get("prepare"),
            "started": (job or {}).get("started"), "points": 0, "rounds_seen": 0,
            "config": {"harness": (job or {}).get("harness"),
                       "dataset": (job or {}).get("dataset"),
                       "method": "-", "model": "-"} if job else None}


def _refused_run(d: Path) -> dict:
    """A run that produced no curve, with the reason it produced none."""
    reason = ""
    for e in reversed(_read_jsonl(d / "events.jsonl")):
        if e.get("level") in ("error", "warn") and e.get("message"):
            reason = e["message"]
            break
    if not reason:
        # No structured event: fall back to the console log, skipping the argv line.
        try:
            lines = [x.strip() for x in (d / "console.log").read_text().splitlines()]
            reason = next((x for x in reversed(lines) if x and not x.startswith("$")), "")
        except OSError:
            reason = ""
    meta = {}
    if (d / "launch.json").exists():
        try:
            meta = json.loads((d / "launch.json").read_text())
        except json.JSONDecodeError:
            pass
    argv = meta.get("argv") or []
    def _arg(flag: str) -> str | None:
        return argv[argv.index(flag) + 1] if flag in argv else None
    return {
        "run_id": d.name, "harness": _arg("--harness"), "dataset": _arg("--dataset"),
        "version": None, "agent_model": None, "method_model": None,
        "rounds": 0, "first_score": None, "last_score": None, "scores": [],
        "task_count": 0, "sandbox": None, "refused": True,
        # Never empty. A refused run with no stated reason is the same dead end the
        # silent skip was, just one layer further in -- so the fallback names where the
        # reason would be if there is one.
        "reason": (reason.strip()[:400] if reason.strip() else
                   f"没有留下原因:{d.name}/console.log 里可能有"),
        "mtime": max((d / f).stat().st_mtime
                     for f in ("console.log", "events.jsonl", "launch.json")
                     if (d / f).exists()),
    }


def list_runs() -> list[dict]:
    runs = []
    if not RUNS.is_dir():
        return runs
    for d in sorted(RUNS.iterdir()):
        curve_file = d / "curve.jsonl"
        if not d.is_dir():
            continue
        if not curve_file.exists():
            # A run that wrote a log but no curve. It refused at the door, or it died.
            #
            # These used to be skipped silently, so the UI showed nothing at all while
            # `runs/<id>/console.log` held the reason -- measured: a refused run looked
            # identical to a button that did nothing, and the first question it produced
            # was "is it stuck?". A refusal the user cannot see is worse than a failure,
            # because there is nothing to react to.
            if not (d / "console.log").exists() and not (d / "events.jsonl").exists():
                continue
            runs.append(_refused_run(d))
            continue
        points = _read_jsonl(curve_file)
        if not points:
            continue
        meta = {}
        if (d / "run_meta.json").exists():
            try:
                meta = json.loads((d / "run_meta.json").read_text())
            except json.JSONDecodeError:
                pass
        ident = points[-1].get("identity") or {}
        scores = [p.get("score") for p in points]
        # 一个 run 的"方法"不在曲线点上,而在 `run_meta.json` 的入口路径里。把它取出来
        # 是为了让面板能提供"重跑" —— 换了改进器之后最想做的事就是这个,而让用户照着
        # 记忆把表单再填一遍正是他会填错的地方。
        method = ""
        entry = str(meta.get("method_entrypoint") or "")
        if entry:
            parts = Path(entry.replace("\\", "/")).parts
            for i, part in enumerate(parts):
                if part == "methods" and i + 1 < len(parts):
                    method = parts[i + 1]
                    break
        runs.append({
            "run_id": d.name, "harness": ident.get("harness_name"),
            "version": ident.get("harness_version"),
            "agent_model": ident.get("agent_model"),
            "method_model": ident.get("method_model"),
            "rounds": len(points), "first_score": scores[0], "last_score": scores[-1],
            "scores": scores, "task_count": len(points[-1].get("per_task") or {}),
            "sandbox": (meta.get("sandbox") or {}).get("active"),
            # Which rounds left a harness state an eval run could measure. Listed rather
            # than inferred: "there is nothing to evaluate" is the refusal an eval run
            # gives, and the form should not offer a run that will produce it.
            "state_rounds": sorted(
                int("".join(c for c in p.name if c.isdigit()))
                for p in d.glob("state_after_round*") if p.is_dir()),
            "side": points[-1].get("side"),
            "method": method,
            "dataset": meta.get("dataset"),
            "tasks": sorted((points[-1].get("per_task") or {}).keys()),
            "mtime": curve_file.stat().st_mtime,
        })
    return sorted(runs, key=lambda r: r["mtime"], reverse=True)


def run_detail(run_id: str) -> dict | None:
    if not SAFE_ID.match(run_id):
        return None
    points = _read_jsonl(RUNS / run_id / "curve.jsonl")
    if not points:
        return None
    meta = {}
    mp = RUNS / run_id / "run_meta.json"
    if mp.exists():
        try:
            meta = json.loads(mp.read_text())
        except json.JSONDecodeError:
            pass
    # Which rounds left a state that could be promoted. Read from disk rather than
    # assumed from the point count: the save can fail (it says so and carries on), and a
    # button that offers a round with no state behind it produces a refusal the user
    # cannot act on.
    state_rounds = []
    for p in sorted((RUNS / run_id).glob("state_after_round*")):
        if p.is_dir():
            digits = "".join(c for c in p.name if c.isdigit())
            if digits:
                state_rounds.append(int(digits))
    return {"run_id": run_id, "points": points, "meta": meta,
            "state_rounds": sorted(state_rounds)}


def find_trace_dir(run_id: str) -> Path | None:
    if not SAFE_ID.match(run_id):
        return None
    for base in WORK_ROOTS:
        for candidate in (base / run_id / "workspace" / "_harnessgrad" / "traces",
                          base / run_id / "_harnessgrad" / "traces"):
            if candidate.is_dir():
                return candidate
    return None


from eval.observe.tracing import first_object as _trace_first_object  # noqa: E402


def task_detail(run_id: str, task_id: str) -> dict:
    """一道题的完整解剖:生命周期、判分理由、harness 的轨迹、每轮得分。

    这是把参考图那套(sandbox 运行详情页)**放对层**的地方。它的生命周期时间线原本
    被我放在"整轮"上,那是失真的:我们真正的生命周期在**一道题内部**,而且每轮重复
    一次。压成一条线就看不出"20 题里哪几题在判分上烧掉了时间"。

    数据全部来自已有记录,没有新增落盘:
      * `verdicts/round-N/<task>.json` —— 判分说了什么、harness 打印了什么;
      * `events.jsonl` 里的 `phase` 事件 —— 每个阶段的耗时(按轮聚合);
      * `round<N>_traces/<task>.jsonl` —— harness 每一步做了什么;
      * 曲线点的 `per_task` —— 每轮这道题得了多少分。
    """
    if not (SAFE_ID.match(run_id) and SAFE_ID.match(task_id)):
        return {"found": False}
    events = _read_jsonl(RUNS / run_id / "events.jsonl")
    points = _read_jsonl(RUNS / run_id / "curve.jsonl")
    if not points:
        return {"found": False}

    # 每题每轮:得分 + 判分理由 + harness 输出
    rounds = []
    for p in points:
        rnd = p.get("round")
        per = p.get("per_task") or {}
        if task_id not in per:
            continue
        entry = {"round": rnd, "score": per.get(task_id)}
        vf = RUNS / run_id / "verdicts" / f"round-{rnd}" / f"{task_id}.json"
        if vf.exists():
            try:
                v = json.loads(vf.read_text())
                entry.update({"kind": v.get("kind"), "passed": v.get("passed"),
                              "detail": v.get("detail"),
                              "harness": v.get("harness")})
            except json.JSONDecodeError:
                pass
        rounds.append(entry)

    # 生命周期:phase 事件里这道题的每个阶段,按轮分组并累加
    lifecycle: dict[int, list[dict]] = {}
    order = ["create", "prepare", "agent", "snapshot", "verify", "collect", "teardown"]
    for e in events:
        if e.get("kind") != "phase" or e.get("task") != task_id:
            continue
        if e.get("status") == "start":
            continue
        slot = lifecycle.setdefault(e.get("round", 0), {})
        name = e.get("phase")
        slot[name] = round((slot.get(name) or 0) + (e.get("elapsed_s") or 0), 1)
    for rnd, phases in lifecycle.items():
        lifecycle[rnd] = [{"phase": k, "seconds": phases[k]}
                          for k in order if k in phases]

    trace = trace_for(run_id, task_id)
    return {"found": True, "run_id": run_id, "task_id": task_id,
            "side": points[-1].get("side"),
            "rounds": rounds,
            "lifecycle": {str(k): v for k, v in sorted(lifecycle.items())},
            "phase_names": {"create": "创建容器", "prepare": "拷入 SETUP",
                            "agent": "harness 做题", "snapshot": "停机+快照",
                            "verify": "题目检查判分", "collect": "取回产物",
                            "teardown": "清理容器", "setup": "准备题目文件"},
            "trace": trace}


def trace_paths(run_id: str, task_id: str) -> list[Path]:
    """这道题在所有轮次里的 trace 文件,新的一轮在前。

    两个来源,顺序有讲究:
      * `runs/<id>/round<N>_traces/` —— 每轮留下的存档,**运行结束后唯一还在的**;
      * 方法通道里的 `_harnessgrad/traces/` —— 只有最近一轮,而且 run 结束后可能已消失。

    以前只看后者(`find_trace_dir`),所以**跑完的 run 点开一道题永远是"没有轨迹"** ——
    而存档明明就在 run 目录里。这也是"运行详情"页一直没什么可看的原因。
    """
    out = []
    run = RUNS / run_id
    if not SAFE_ID.match(task_id):
        return out
    for d in sorted(run.glob("round*_traces"), reverse=True):
        p = d / f"{task_id}.jsonl"
        if p.is_file():
            out.append(p)
    channel = find_trace_dir(run_id)
    if channel is not None:
        p = channel / f"{task_id}.jsonl"
        if p.is_file():
            out.append(p)
    return out


def trace_for(run_id: str, task_id: str) -> dict:
    paths = trace_paths(run_id, task_id)
    if not paths:
        return {"found": False}
    path = paths[0]
    steps, commands, parse_errors, extra, usage = [], [], [], 0, None
    for r in _read_jsonl(path):
        if "usage" in r:
            usage = r["usage"]
        if "reply" in r:
            reply = r["reply"]
            steps.append({"step": r.get("step"), "reply": reply})
            # 从回复里取出**第一个** JSON 对象,和 harness 自己的 `_parse` 一致。
            # 一个回复里给两三个 JSON 是实测会发生的事(而且曾经让整个任务在第 0 步死掉),
            # 所以"多余数据"要单独计数并显示 —— 它是 harness 侧的信号,不是噪声。
            action = _trace_first_object(reply)
            # "多余数据" = 第一个对象之后还有别的对象。和 `eval/tracing.loop_facts` 的
            # `multi_object_replies` 是同一条规则的同一个判断来源。
            tail = ""
            if action is not None:
                close = reply.find("}")
                tail = reply[close:] if close != -1 else ""
            if action is not None and "command" in action:
                commands.append({"step": r.get("step"), "command": action["command"],
                                 "exit": None, "output": ""})
            if tail:
                extra += 1
        if "command" in r:
            for c in commands:
                if c.get("step") == r.get("step") and c.get("exit") is None:
                    c["exit"] = r.get("exit")
                    c["output"] = (r.get("output") or "")[:1500]
                    break
            else:
                commands.append({"step": r.get("step"), "command": r["command"],
                                 "exit": r.get("exit"),
                                 "output": (r.get("output") or "")[:1500]})
        if "parse_error" in r:
            parse_errors.append({"step": r.get("step"), "error": r["parse_error"]})
    return {"found": True, "task_id": task_id, "steps": steps, "commands": commands,
            "parse_errors": parse_errors, "extra_data": extra, "usage": usage,
            "n_commands": len(commands), "n_parse_errors": len(parse_errors)}


def experiments() -> list[dict]:
    if not (RUNS / "cmp-base").exists() or not (RUNS / "cmp-rrsi").exists():
        return []
    return [{
        "title": "harness 的解析能力值五个任务(六题里)",
        "question": "同一个模型、同一批任务、同样的采样,只换 harness 的 JSON 解析实现。",
        "rows": [{"label": "原版 loop(严格 json.loads)", "run": "cmp-base"},
                 {"label": "loop-rrsi-parse(容错,RRSI 第 0 轮候选 A)", "run": "cmp-rrsi"}],
        "finding": "0.167 → 0.833。原版产生 5 次解析错误、只执行了 3 条命令;"
                   "容错版 0 次解析错误、执行了 15 条。不是模型不会做题,是 harness 把"
                   "它做出来的东西丢掉了。",
        "why": "只看分数分不清\"模型弱\"和\"harness 读不懂输出\"——两条曲线都是平的,"
               "只有轨迹能分开。这个区分正是平台要记录的东西。",
    }]


# ------------------------------------------------------------------------ 页面 ---

#: **The console's own assets are gone, and this is what is left of serving them.**
#:
#: This file used to hold a `PAGE = r"""<!doctype html>..."""` literal (2447 lines, 114 KB),
#: then three files under `tools/ui/` served from here. Both were replaced by the SPA in
#: `web/` (stage 5 of `docs/refactor_plan.md`): Vite+React+TS, four screens, its own read
#: API in `web/server/readapi.py`, 107 vitest cases and a `serve.py` that puts the built SPA
#: and `/api` on one origin. Measured 2026-10-06: `tools/ui/` was 140 KB of
#: HTML/CSS/JS that nothing had loaded since `web/` landed.
#:
#: What is left here is **not a UI**. Everything above is the platform's operations layer --
#: `prepare_report`/`start_prepare` (importing docker images *before* a run, because the
#: platform refuses to pull during one), `start_job`/`job_status`/`stop_job`,
#: `model_service`, `promote`, `version_graph`, `step_diff`, the provider catalogue -- and
#: the read API does not replace any of it: its Launch screen says on the page that the
#: write API does not exist yet. So the module stays and the name is now wrong; renaming it
#: is a separate change, and deleting it would delete the only way to prepare an environment.


# ----------------------------------------------------------------------- 服务 ---

class Handler(BaseHTTPRequestHandler):
    server_version = "HarnessGradUI"

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(json.dumps(obj, default=str).encode(), "application/json", code)

    def do_GET(self) -> None:                              # noqa: N802
        u = urlparse(self.path)
        path, query = u.path, u.query

        if path in ("/", "/index.html") or path.startswith("/ui/"):
            # Two cockpits have been deleted from this spot: `tools/ui/`, then the SPA in
            # `web/` (2026-10-19, when the repository dropped 22k lines of frontend and kept
            # the console). Saying so is better than a bare 404: the failure a reader
            # actually hits is "I started the old server and got a blank page", and the
            # answer is a path that exists.
            return self._send(
                b"The UI that lived here is gone (tools/ui/, then web/). What reads the run\n"
                b"records now is console/:\n"
                b"  python3 console/server.py --port 4460\n"
                b"  then open http://127.0.0.1:4460/\n"
                b"This service still answers /api/settings and /api/providers; it serves no page.\n",
                "text/plain; charset=utf-8", 410)
        if path == "/api/settings":
            return self._json(read_settings())
        if path == "/api/providers":
            return self._json(provider_catalog())
        if path == "/api/settings/save":
            from urllib.parse import parse_qs
            raw = parse_qs(query).get("cfg", ["{}"])[0]
            try:
                updates = json.loads(raw)
            except json.JSONDecodeError:
                return self._json({"error": "bad cfg"}, 400)
            return self._json(write_settings(updates))
        if path == "/api/test-connection":
            from urllib.parse import parse_qs
            which = parse_qs(query).get("which", ["agent"])[0]
            return self._json(test_connection(which))
        if path == "/api/model-service":
            from urllib.parse import parse_qs
            q = parse_qs(query)
            return self._json(model_service(
                q.get("action", ["status"])[0],
                q.get("model_dir", [""])[0],
                int(q.get("port", ["0"])[0] or 0)))
        if path == "/api/promote":
            from urllib.parse import parse_qs
            q = parse_qs(query)
            return self._json(promote(
                q.get("run_id", [""])[0],
                int(q.get("round", ["0"])[0] or 0),
                q.get("as", [""])[0],
                q.get("overwrite", [""])[0] in ("1", "true", "yes")))
        if path == "/api/graph":
            return self._json(version_graph())
        if path == "/api/prepare":
            from urllib.parse import parse_qs
            q = parse_qs(query)
            return self._json(prepare_report(q.get("dataset", [""])[0],
                                             q.get("harness", [""])[0]))
        if path == "/api/prepare/run":
            from urllib.parse import parse_qs
            raw = parse_qs(query).get("cfg", ["{}"])[0]
            try:
                cfg = json.loads(raw)
            except json.JSONDecodeError:
                return self._json({"error": "bad cfg"}, 400)
            return self._json(start_prepare(cfg))
        if path.startswith("/api/prepare/job/"):
            return self._json(prepare_status(path.rsplit("/", 1)[-1]))
        if path == "/api/tasks":
            from urllib.parse import parse_qs
            return self._json(task_options(parse_qs(query).get("dataset", [""])[0]))
        if path == "/api/options":
            return self._json(scan_options())
        if path == "/api/runs":
            return self._json(list_runs())
        if path == "/api/experiments":
            return self._json(experiments())
        if path == "/api/run":                              # 启动(用 GET 便于演示)
            from urllib.parse import parse_qs
            raw = parse_qs(query).get("cfg", ["{}"])[0]
            try:
                cfg = json.loads(raw)
            except json.JSONDecodeError:
                return self._json({"error": "bad cfg"}, 400)
            return self._json(start_job(cfg))
        if path.startswith("/api/stop/"):
            return self._json(stop_job(path.rsplit("/", 1)[-1]))
        if path.startswith("/api/job/"):
            return self._json(job_status(path.rsplit("/", 1)[-1]))

        m = re.match(r"^/api/run/([^/]+)/diff/(\d+)$", path)
        if m:
            return self._json(step_diff(m.group(1), int(m.group(2))))

        if path == "/api/explain":
            from urllib.parse import parse_qs
            q = parse_qs(query)
            return self._json(explain_diff(q.get("run", [""])[0],
                                           int(q.get("round", ["0"])[0])))

        m = re.match(r"^/api/run/([^/]+)/task/([^/]+)$", path)
        if m:
            return self._json(task_detail(m.group(1), m.group(2)))
        m = re.match(r"^/api/run/([^/]+)/trace/([^/]+)$", path)
        if m:
            return self._json(trace_for(m.group(1), m.group(2)))
        m = re.match(r"^/api/run/([^/]+)$", path)
        if m:
            d = run_detail(m.group(1))
            return self._json(d) if d else self._json({"error": "no such run"}, 404)
        return self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:                             # noqa: N802
        # 表单也可以走 POST;目前前端用 GET 传 cfg,保持简单。
        return self.do_GET()


def main() -> int:
    ap = argparse.ArgumentParser(description="HarnessGrad 控制台")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()

    opts = scan_options()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"HarnessGrad 控制台  http://{args.host}:{args.port}")
    print(f"  {len(opts['harnesses'])} 个 harness,{len(opts['datasets'])} 个任务集,"
          f"{len(opts['methods'])} 个方法,{len(opts['models'])} 个模型预设")
    print(f"  历史实验 {len(list_runs())} 个")
    print("  Ctrl-C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
