#!/usr/bin/env python3
"""实验台 · UAT 冒烟（测试台 B：只读实测，无 GPU、不起 driver）。

把 `hg-mockup/UAT_STANDARD.md §2` 里能在**只读实测**下自动化的判定项变成可重复执行的
yes/no 检查，并把结果写成 `uat-record-template.md` 的形状。

    python3 uat/smoke.py --port 4470                     # 自己起一个 console（默认 4470）
    python3 uat/smoke.py --url http://127.0.0.1:4450      # 测一个已经在跑的 console
    python3 uat/smoke.py --url http://127.0.0.1:4450 --repeat 3   # 连跑 3 遍，看逐项稳定性

硬约束（脚本本身遵守，也断言被测应用遵守）：
  * 只发 GET。整个会话里出现的第一个非 GET 请求就是 S0。
  * 不起 driver、不 `pkill`、不写 runs 目录、不碰 `Study/HarnessGrad/`。
  * 自己起的服务器在 `finally` 里关掉；端口被占就报错退出，绝不 kill 别人的进程。

覆盖：§2 的 1 / 5 / 6 / 7 / 8 / 10，加上覆盖矩阵（13）—— 也就是现在的四屏
（实验 / 覆盖 / 轨迹 / 版本）。场景 10 的
步骤数与长轨迹由 `/trace.html`（`GET .../traces` 与 `.../trace?name=`）自动化，数字全部
对照磁盘上的 jsonl/verdicts。
不再覆盖：3 / 4 / 9 / 11。它们判定的屏（对话 + 技能参数、解释面板、方法页）已从 console 删除，
对应的判定项随屏一起删掉了（见 `uat/uat-2026-10-19.md` 的记录条目与 README 的「删除」一节）。
仍然跳过：2 / 12（需要 API 配置写入路径或真起跑，测试台 B 不做，见 `--` 之后的阻塞表）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
import pathlib
import re
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CONSOLE = HERE.parent
SERVER_PY = CONSOLE / "server.py"
STATIC = CONSOLE / "static"
DEFAULT_RUNS = pathlib.Path("/mnt/20t/xubuqiang/hg-work/rsi10/runs")
PLATFORM = pathlib.Path("/mnt/20t/xubuqiang/Study/HarnessGrad")


# ─────────────────────────────────────────────────────── 小工具
def trunc(text, n: int = 200) -> str:
    """证据里出现的 DOM 文本：压平空白、截断。"""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[:n] + "…"


def md_cell(text, n: int = 220) -> str:
    s = trunc(text, n).replace("|", "\\|")
    return s or "—"


class Fail(AssertionError):
    """判定项不过。消息就是证据。"""


class Blocked(Exception):
    """本版无法自动化。"""


CHECK_LIST: list["Check"] = []

#: 拦截 /api/runs、/api/runs?kind=… 与 /api/runs/<id>（Playwright 的 glob `*` 不跨 `/`）
RE_RUNS = re.compile(r".*/api/runs($|[/?].*)")


class Check:
    __slots__ = ("sid", "scenario", "item", "sev", "fn")

    def __init__(self, sid, scenario, item, sev, fn):
        self.sid, self.scenario, self.item, self.sev, self.fn = sid, scenario, item, sev, fn


def check(sid: str, scenario: str, item: str, sev: str):
    def deco(fn):
        CHECK_LIST.append(Check(sid, scenario, item, sev, fn))
        return fn

    return deco


# ─────────────────────────────────────────────────────── 拦截载荷（无 driver 也能测的状态）
_BASE_ROW = {
    "stamp": "rsi10", "tail": "", "pretty": "", "dataset": "alfworld", "pool": "train",
    "jobs": 10, "trials": 1, "agent_model": "qwen3.5-9b-local", "harness": "loop",
    "harness_sha": "deadbeef", "steps": 32, "improver": None, "n_tasks": 100,
    "mtime": "01-01 00:00", "kind": "study", "live": None,
}
_FIX_RUNNING = {
    **_BASE_ROW, "id": "rsi10-20260101T000000Z-fixture-running-train",
    "pretty": "fixture · running · train", "rounds": 3, "best": 0.75, "best_round": 1,
    "last": 0.75, "invalid": 0, "status": "running", "progress": {"round": "2", "done": 7},
    "live": {"pid": 1, "elapsed_s": 60},
}
_FIX_INVALID = {
    **_BASE_ROW, "id": "rsi10-20260101T000000Z-fixture-invalid-train",
    "pretty": "fixture · invalid · train", "rounds": 2, "best": None, "best_round": None,
    "last": None, "invalid": 3, "status": "done", "progress": None,
}
_FIX_OK = {
    **_BASE_ROW, "id": "rsi10-20260101T000000Z-fixture-ok-train",
    "pretty": "fixture · ok · train", "rounds": 2, "best": 0.6, "best_round": 1,
    "last": 0.6, "invalid": 0, "status": "done", "progress": None,
}


def _fix_rounds(scores: list) -> list:
    out = []
    for i, s in enumerate(scores):
        out.append({
            "round": i, "score": s, "n_scored": 100 if s is not None else 0, "n_invalid": 0,
            "n_harness_failed": 0, "harness_sha": f"sh{i:06d}",
            "tokens": 1000 if s is not None else None, "calls": 10, "wall_s": 10,
            "has_diff": i > 0, "has_state": True, "traces": 0,
        })
    return out


_FIX_DETAIL = {
    _FIX_RUNNING["id"]: {"row": _FIX_RUNNING, "rounds": _fix_rounds([0.5, 0.75, None]),
                         "meta": {"dataset": "alfworld"}},
    _FIX_INVALID["id"]: {"row": _FIX_INVALID, "rounds": _fix_rounds([None, None]),
                          "meta": {"dataset": "alfworld"}},
    _FIX_OK["id"]: {"row": _FIX_OK, "rounds": _fix_rounds([0.4, 0.6]),
                    "meta": {"dataset": "alfworld"}},
}


# ─────────────────────────────────────────────────────── 主类
class UAT:
    def __init__(self, url: str, runs_root: pathlib.Path, timeout_ms: int,
                 evidence: pathlib.Path, headed: bool = False, local_server: bool = True):
        self.url = url.rstrip("/")
        self.local_server = local_server
        self.runs_root = runs_root
        self.timeout_ms = timeout_ms
        self.evidence = evidence
        self.headed = headed
        self.proc: subprocess.Popen | None = None
        self.server_log = ""
        self.browser = None
        self.pw = None
        self.contexts: list = []
        self.console: list[dict] = []      # 真实 console error / pageerror（含拦截页）
        self.reqs: list[tuple[str, str]] = []
        self.reqfail: list[tuple[str, str]] = []
        self.results: dict[str, dict] = {}
        self.last_page = None
        self.health: dict = {}
        self.samples: dict = {}
        self.obs: dict = {}
        self.console_from = 0                # 本轮 repeat 的 console 起点
        self.history: dict[str, list[str]] = {}   # sid -> 每轮的 res（--repeat 稳定性）
        self.repeat_total = 1

    # ─────────────────────────────── HTTP（只 GET）
    def api(self, path: str, timeout: float = 15.0):
        last = None
        for _ in range(3):
            try:
                with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
                    return json.loads(r.read())
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(0.3)
        raise Fail(f"GET {path} 失败：{last}")

    def raw_get(self, path: str):
        """原样发 path（不做客户端归一化），用来测穿越。"""
        u = urllib.parse.urlsplit(self.url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            body = r.read()
            return r.status, body
        finally:
            conn.close()

    # ─────────────────────────────── Playwright 管道
    def page(self, routes=None, viewport=(1440, 900)):
        assert self.browser is not None
        ctx = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        self.contexts.append(ctx)
        page = ctx.new_page()
        self._instrument(page)
        if routes:
            for pattern, handler in routes:
                ctx.route(pattern, handler)
        self.last_page = page
        return page

    def _instrument(self, page):
        def on_console(msg):
            try:
                if msg.type != "error":
                    return
                text = msg.text
                loc = ""
                try:
                    loc = (msg.location or {}).get("url") or ""
                except Exception:  # noqa: BLE001
                    loc = ""
                if "favicon" in text or loc.endswith("favicon.ico"):
                    return  # 没放图标产生的 404 不算场景缺陷
                where = loc or getattr(page, "url", "")
                self.console.append({"url": where, "kind": "console", "text": text})
            except Exception:  # noqa: BLE001
                pass

        def on_pageerror(err):
            try:
                self.console.append({"url": getattr(page, "url", ""), "kind": "pageerror", "text": str(err)})
            except Exception:  # noqa: BLE001
                pass

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("request", lambda r: self.reqs.append((r.method, r.url)))
        page.on("requestfailed", lambda r: self.reqfail.append((r.url, str(r.failure))))

    def close_since(self, mark: int):
        """关掉这次 check 里新建的 context：残留页面会持续轮询，把服务器拖成瓶颈。"""
        for ctx in self.contexts[mark:]:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        del self.contexts[mark:]

    def open(self, path: str = "/#/runs", routes=None, wait_ms: int = 0, page=None):
        page = page or self.page(routes=routes)
        page.goto(self.url + path, wait_until="domcontentloaded", timeout=self.timeout_ms)
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        return page

    # ─────────────────────────────── 等待 / 读取
    def wait(self, page, expr: str, desc: str = "", timeout_ms: int | None = None):
        end = time.time() + (timeout_ms or self.timeout_ms) / 1000.0
        last = None
        while time.time() < end:
            try:
                last = page.evaluate(expr)
            except Exception:  # noqa: BLE001
                last = None
            if last:
                return last
            page.wait_for_timeout(100)
        raise Fail(f"等待超时（{desc or expr}）| url={page.url} sel={expr} dom={trunc(_body(page))}")

    def stable(self, page, expr: str, samples: int = 2, timeout_ms: int = 6000):
        """读数稳定（连续 samples 次相同）才返回：应用有并发渲染，读早了会读到中间态。"""
        end = time.time() + timeout_ms / 1000.0
        prev, same = object(), 0
        cur = None
        while time.time() < end:
            try:
                cur = page.evaluate(expr)
            except Exception:  # noqa: BLE001
                cur = None
            if cur == prev:
                same += 1
                if same >= samples - 1:
                    return cur
            else:
                same = 0
                prev = cur
            page.wait_for_timeout(120)
        return cur

    def agree(self, page, expr: str, want_fn, retry=None, tries: int = 3):
        """DOM 读数 == 接口读数。runs 目录有 driver 在写，允许重取一次。"""
        got = want = None
        for i in range(tries):
            want = want_fn()
            got = self.stable(page, expr)
            if got == want:
                return got, want
            if i < tries - 1:
                if retry:
                    retry()
                else:
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_timeout(600)
        return got, want

    # ─────────────────────────────── 页面读数
    def list_rows(self, page):
        return self.stable(page, """() => [...document.querySelectorAll('#runs-table tbody tr[data-run]')]
            .map(tr => ({id: tr.getAttribute('data-run'),
                         cells: [...tr.querySelectorAll('td')].map(td => td.innerText.replace(/\\s+/g,' ').trim()),
                         pill: (tr.querySelector('.pill') || {}).textContent || ''}))""")

    def rail(self, page):
        return self.stable(page, """() => {
            const r = document.querySelector('#run-rail') || document.body;
            const head = r.querySelector('.card-head');
            const sub = head ? head.querySelector('.sub') : null;
            return {
              id: sub ? sub.textContent.trim() : '',
              head: head ? head.innerText.replace(/\\s+/g,' ').trim() : '',
              pts: r.querySelectorAll('svg.curve circle.pt').length,
              hasCurve: !!r.querySelector('svg.curve'),
              rows: [...r.querySelectorAll('table.grid tbody tr')].map(tr =>
                      [...tr.querySelectorAll('td')].map(td => td.innerText.replace(/\\s+/g,' ').trim())),
              notice: [...r.querySelectorAll('.disclaimer, .load, .err')].map(e => e.innerText.replace(/\\s+/g,' ').trim()),
              text: r.innerText.replace(/\\s+/g,' ').trim(),
            }; }""")

    def editor(self, page):
        return self.stable(page, """() => {
            const ed = document.querySelector('#h-editor') || document.body;
            const lns = [...ed.querySelectorAll('.ln')];
            const texts = lns.map(el => { const c = el.cloneNode(true);
                c.querySelectorAll('.no').forEach(n => n.remove());
                c.querySelectorAll('.sig').forEach(n => n.remove());
                return c.textContent; });
            const h = ed.querySelector('.diff-head');
            return {head: h ? h.innerText.replace(/\\s+/g,' ').trim() : '',
                    add: ed.querySelectorAll('.ln.add').length,
                    del: ed.querySelectorAll('.ln.del').length,
                    hunk: ed.querySelectorAll('.ln.hunk').length,
                    total: lns.length, texts: texts,
                    files: [...ed.querySelectorAll('#h-patch-file option')].map(o => o.value),
                    note: [...ed.querySelectorAll('.empty-state, .notice, .load, .err')]
                            .map(e => e.innerText.replace(/\\s+/g,' ').trim())}; }""")

    def cols(self, page, table: str):
        return self.stable(page, f"""() => {{ const t = document.querySelector('{table}');
            if (!t) return [];
            return [...t.querySelectorAll('thead th')].map(th => th.innerText.replace(/\\s+/g,' ').trim()); }}""")

    def empty_title(self, page, sel: str):
        return self.stable(page, f"""() => {{ const t = document.querySelector('{sel}');
            const e = t ? t.querySelector('.empty-title') : null;
            return e ? e.innerText.trim() : ''; }}""")

    def harness(self, page):
        return self.stable(page, """() => {
            const pick = (s) => document.querySelector(s);
            const tree = pick('#h-tree');
            const ex = pick('#h-explain');
            const kv = {};
            ex && ex.querySelectorAll('dl.kv').forEach(dl => {
                const kids = [...dl.children];
                for (let i = 0; i < kids.length - 1; i++) {
                    if (kids[i].tagName === 'DT' && kids[i+1].tagName === 'DD')
                        kv[kids[i].innerText.trim()] = kids[i+1].innerText.replace(/\\s+/g,' ').trim();
                }
            });
            return {
              run: pick('#h-run') ? pick('#h-run').value : null,
              rounds: pick('#h-round') ? [...pick('#h-round').options].map(o => Number(o.value)) : [],
              roundVal: pick('#h-round') ? Number(pick('#h-round').value) : null,
              runOpts: pick('#h-run') ? [...pick('#h-run').options].map(o => o.value) : [],
              tree: tree ? tree.innerText.replace(/\\s+/g,' ').trim() : '',
              kv: kv, mode: [...document.querySelectorAll('#h-mode button')]
                        .filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.mode),
            }; }""")

    def body_text(self, page):
        return self.stable(page, "() => document.body.innerText.replace(/\\s+/g,' ').trim()")

    # ─────────────────────────────── 事故现场
    def shot(self, page, sid: str) -> str:
        try:
            p = self.evidence / f"{sid}.png"
            page.screenshot(path=str(p), full_page=False)
            return str(p.relative_to(CONSOLE))
        except Exception:  # noqa: BLE001
            return ""

    def where(self, page, sel: str = "") -> str:
        txt = ""
        try:
            txt = page.eval_on_selector(sel, "e => e ? e.innerText : ''") if sel else _body(page)
        except Exception:  # noqa: BLE001
            txt = _body(page)
        return f"url={page.url} sel={sel or 'body'} dom={trunc(txt)}"

    # ─────────────────────────────── 选样（真实数据）
    def prepare(self):
        self.health = self.api("/api/health")
        # 记录用：elapsed_s 是否真的是“已跑时长”（不是判定项，见记录的手工补充）
        self.samples["live_elapsed"] = [
            (r.get("run_id"), int(r["elapsed_s"])) for r in (self.health.get("running") or [])
            if isinstance(r.get("elapsed_s"), (int, float)) and r["elapsed_s"] > 7 * 86400]
        root = self.health.get("runs_root")
        if root and pathlib.Path(root).resolve() != self.runs_root.resolve():
            # 以服务器实际读的目录为准；--runs-root 只在 health 没给时兜底
            self.runs_root = pathlib.Path(root)
        study = self.api("/api/runs?kind=study")
        every = self.api("/api/runs")
        evals = self.api("/api/runs?kind=eval")
        self.samples = {"study": study, "all": every, "eval": evals}

        multi = sorted([r for r in study["rows"] if r["rounds"] > 1],
                       key=lambda r: (-r["rounds"], r["id"]))
        self.samples["multi"] = multi
        if multi:
            tgt = multi[0]
            rounds = [x for x in self.api(f"/api/runs/{tgt['id']}")["rounds"]]
            have = [x["round"] for x in rounds]
            patched = [n for n in have
                       if (self.runs_root / tgt["id"] / "diffs" / f"round-{n}.patch").is_file()]
            bare = [n for n in have
                    if not (self.runs_root / tgt["id"] / "diffs" / f"round-{n}.patch").is_file()]
            rnd = next((n for n in (2, 1, 3) if n in patched), patched[0] if patched else None)
            self.samples.update({"target": tgt, "detail": rounds, "diffRound": rnd,
                                 "bareRound": bare[0] if bare else None})
        singles = [r for r in evals["rows"] if r["rounds"] == 1]
        self.samples["single"] = singles[0] if singles else None
        self.samples["invalid"] = next((r for r in every["rows"] if r.get("invalid")), None)
        self.samples["running"] = next((r for r in every["rows"] if r.get("status") == "running"), None)
        self.samples["kinds"] = self.health.get("kinds") or {}
        self.samples["datasets"] = sorted({r["dataset"] for r in study["rows"] if r.get("dataset")})
        self.samples["harnesses"] = sorted({r["harness"] for r in study["rows"] if r.get("harness")})
        self.samples["trace"] = _pick_trace_sample(self, study["rows"])
        self.version = app_version(self.url, local_server=self.local_server)
        self.commit = "hg-console@" + hashlib.sha256(self.version.encode()).hexdigest()[:8]


def _body(page) -> str:
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:  # noqa: BLE001
        return ""


def app_version(url: str, local_server: bool) -> str:
    """指纹：优先哈希**服务器真的返回了**的静态文件（--url 指向别的副本时不会冒充本 checkout）。

    server.py 不对外提供，只有自己起服务器时才用本地文件的哈希。
    """
    parts = []
    if local_server:
        sp = CONSOLE / "server.py"
        if sp.is_file():
            parts.append(f"server.py:{hashlib.sha256(sp.read_bytes()).hexdigest()[:10]}")
    else:
        parts.append("server.py:?(--url 模式读不到)")
    for rel in ("/index.html", "/console.js", "/app.css", "/tokens.css"):
        try:
            body = urllib.request.urlopen(url + rel, timeout=10).read()
            parts.append(f"{rel.lstrip('/')}:{hashlib.sha256(body).hexdigest()[:10]}")
        except Exception:  # noqa: BLE001
            parts.append(f"{rel.lstrip('/')}:?")
    return " ".join(parts)


def _pick_trace_sample(t: "UAT", study_rows: list) -> dict | None:
    """挑一个真的有轨迹的 study run/round，并**从磁盘**挑两条有代表性的轨迹。

    · steps_task：reply 事件最多的那条（步骤卡数最容易数错的就是它）
    · long_task：最长 command output 的那条（“展开”要展开得动）
    选样只读磁盘；检查里的数字仍然在检查当时重新从磁盘读一遍。
    """
    target = t.samples.get("target")
    order = ([target] if target else []) + [
        r for r in study_rows if not target or r["id"] != target["id"]]
    for row in order:
        try:
            rounds = t.api(f"/api/runs/{row['id']}")["rounds"]
        except Fail:
            continue
        have = [x for x in rounds if x.get("traces")]
        if not have:
            continue
        rnd = t.samples.get("diffRound")
        if not any(x["round"] == rnd for x in have):
            rnd = max(have, key=lambda x: (x["traces"], x["round"]))["round"]
        d = t.runs_root / row["id"] / f"round{rnd}_traces"
        files = sorted(d.glob("*.jsonl"))
        if not files:
            continue
        best_steps = None
        best_long = None
        for f in files:
            events, _bad = _read_jsonl(f)
            if not events:
                continue
            n_reply = sum(1 for e in events if "reply" in e)
            long_lines, long_chars = 0, 0
            for e in events:
                if "command" not in e:
                    continue
                out = str(e.get("output") or "")
                n = len(out[:6000].rstrip().split("\n"))
                if (n, len(out)) > (long_lines, long_chars):
                    long_lines, long_chars = n, len(out)
            if best_steps is None or (n_reply, f.name) > best_steps:
                best_steps = (n_reply, f.name)
            cand = (long_lines, n_reply, f.name)
            if best_long is None or cand > best_long:
                best_long = cand
        if best_steps and best_long:
            return {"run": row["id"], "round": rnd, "count": len(files),
                    "steps_task": best_steps[1], "steps": best_steps[0],
                    "long_task": best_long[2], "long_lines": best_long[0]}
    return None


def _read_jsonl(path: pathlib.Path) -> tuple[list[dict], int]:
    """按行解析 jsonl，返回 (事件, 坏行数)；坏行跳过但不隐藏。"""
    events: list[dict] = []
    bad = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events, 1
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events, bad


def _task_of(stem: str) -> tuple[str, int]:
    """同 server.py：`<task-id>` 或 `<task-id>.trial<k>` → (task_id, trial)。"""
    if ".trial" in stem:
        head, _, tail = stem.rpartition(".trial")
        try:
            return head, int(tail)
        except ValueError:
            return stem, 1
    return stem, 1


def _verdict_of(t: "UAT", run_id: str, rnd: int, task_id: str) -> dict:
    p = t.runs_root / run_id / "verdicts" / f"round-{rnd}" / f"{task_id}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ═══════════════════════════════════════════════════════ 场景 1：冷启动
@check("1-1", "1 冷启动", "控制台无报错（四个屏 + 两个别名，全新 profile）", "S1")
def c1_1(t: UAT):
    for path in ("/#/runs", "/#/coverage", "/#/version", "/#/trace", "/#/harness", "/trace.html"):
        p = t.open(path, wait_ms=1800)
        p.close()
    errs = [e for e in t.console[t.console_from:] if e["kind"] in ("console", "pageerror")]
    if errs:
        e = errs[0]
        raise Fail(f"{e['kind']}: {trunc(e['text'])} | url={e['url']} "
                   f"（本轮共 {len(errs)} 条）")
    return f"4 个屏 + 2 个别名（version/trace 的深链接与 /trace.html），本轮 0 条 console error / pageerror"


@check("1-2", "1 冷启动", "每个视图到达渲染态，不卡在“读取…”", "S1")
def c1_2(t: UAT):
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table table.grid tbody tr')", "runs 列表")
    for link, sel, desc in (
        ("harness", "#h-editor .ln", "版本的三栏（diff/文件）"),
        ("trace", "#trace-root #tasks [data-name]", "轨迹"),
    ):
        p.evaluate("l => document.querySelector('a[data-view-link=' + l + ']').click()", link)
        t.wait(p, f"() => document.querySelectorAll('{sel}').length > 0", desc)
    stuck = p.evaluate("""() => [...document.querySelectorAll('.view[data-active=true] .load')]
        .map(e => e.innerText.trim())""")
    if stuck:
        raise Fail(f"视图里还留着加载态 {stuck} | {t.where(p, '.view[data-active=true]')}")
    return "runs / 覆盖 / 版本 / 轨迹 四屏都渲染出内容"


@check("1-4", "1 冷启动", "空结果说是空，不是崩（拦截空载荷）", "S2")
def c1_4(t: UAT):
    def empty(route):
        if "/api/runs" in route.request.url:
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"rows": [], "count": 0}))
        return route.continue_()

    p = t.open("/#/runs", routes=[(RE_RUNS, empty)], wait_ms=1500)
    title = t.empty_title(p, "#runs-table")
    txt = p.inner_text("#runs-table")
    if not title:
        raise Fail(f"空载荷下没有空态标题 | {t.where(p, '#runs-table')}")
    if t.console:
        raise Fail(f"空载荷触发控制台报错：{trunc(t.console[0]['text'])} | url={t.console[0]['url']}")
    return f"空态标题：{trunc(title)}（{trunc(txt, 80)}）"


@check("1-5", "1 冷启动", "无 NaN / undefined 冒充数字", "S0")
def c1_5(t: UAT):
    bad = ("NaN", "undefined", "Infinity", "null")
    for path in ("/#/runs", "/#/coverage", "/#/version", "/#/trace"):
        p = t.open(path, wait_ms=1500)
        txt = t.body_text(p)
        hits = [b for b in bad if b in txt]
        if hits:
            raise Fail(f"页面出现 {hits} | {t.where(p)}")
    return "四屏正文无 NaN / undefined / Infinity / null"


# ═══════════════════════════════════════════════════════ 场景 5：实验列表
@check("5-1", "5 实验列表", "study 筛选行数 = /api/runs?kind=study count", "S1")
def c5_1(t: UAT):
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    got, want = t.agree(p, "() => document.querySelectorAll('#runs-table tbody tr[data-run]').length",
                        lambda: t.api("/api/runs?kind=study")["count"])
    if not got:
        raise Fail(f"kind=study 一行都没渲染 | {t.where(p, '#runs-table')}")
    if got != want:
        raise Fail(f"DOM {got} 行 != API count {want} | {t.where(p, '#runs-table')}")
    return f"{got} 行 == count {want}"


@check("5-2", "5 实验列表", "eval 筛选行数 = /api/runs?kind=eval count", "S1")
def c5_2(t: UAT):
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    p.evaluate("() => document.querySelector('[data-kind=\"eval\"]').click()")
    t.wait(p, "() => (document.querySelector('#runs-sub') || {}).textContent.includes('单点')", "eval 文案")
    got, want = t.agree(p, "() => document.querySelectorAll('#runs-table tbody tr[data-run]').length",
                        lambda: t.api("/api/runs?kind=eval")["count"],
                        retry=lambda: p.evaluate("() => document.querySelector('[data-kind=\"eval\"]').click()"))
    if not got:
        raise Fail(f"kind=eval 一行都没渲染 | {t.where(p, '#runs-table')}")
    if got != want:
        raise Fail(f"DOM {got} 行 != API count {want} | {t.where(p, '#runs-table')}")
    return f"{got} 行 == count {want}"


@check("5-3", "5 实验列表", "三档 kind 各自计数一致，且之和 = 全部", "S2")
def c5_3(t: UAT):
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    kinds = p.evaluate("() => [...document.querySelectorAll('[data-kind]')].map(b => b.dataset.kind)")
    if not kinds:
        raise Fail(f"没有 kind 筛选 chip | {t.where(p, '#runs-table')}")
    counts = {}
    for kind in kinds:
        p.evaluate("k => document.querySelector('[data-kind=\"' + k + '\"]').click()", kind)
        p.wait_for_timeout(900)
        exp = t.api(f"/api/runs{'?kind=' + kind if kind else ''}")["count"]
        got, want = t.agree(p, "() => document.querySelectorAll('#runs-table tbody tr[data-run]').length", lambda: exp)
        if got != want:
            raise Fail(f"kind={kind or '全部'}：DOM {got} != count {want} | {t.where(p, '#runs-table')}")
        counts[kind] = got
    total = counts.get("", 0)
    parts = sum(v for k, v in counts.items() if k)
    if total != parts:
        raise Fail(f"count 不自洽：全部 {total} != " + " + ".join(f"{k} {v}" for k, v in counts.items() if k))
    return " / ".join(f"{k or '全部'} {v}" for k, v in counts.items()) + f"（{total} = {parts}）"


@check("5-4", "5 实验列表", "benchmark / harness / status 下拉与手算一致", "S2")
def c5_4(t: UAT):
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#f-dataset option[value]:not([value=\"\"])')", "筛选项")
    study = t.api("/api/runs?kind=study")["rows"]

    def dom_ids():
        return sorted(p.evaluate("() => [...document.querySelectorAll('#runs-table tbody tr[data-run]')].map(tr => tr.dataset.run)"))

    ds = p.evaluate("() => [...document.querySelectorAll('#f-dataset option')].map(o => o.value).filter(Boolean)[0]")
    p.select_option("#f-dataset", ds)
    want = sorted(r["id"] for r in study if r["dataset"] == ds)
    got = t.stable(p, "() => [...document.querySelectorAll('#runs-table tbody tr[data-run]')].map(tr => tr.dataset.run).sort()")
    if got != want:
        raise Fail(f"benchmark={ds} DOM {len(got)} != 期望 {len(want)} | {t.where(p, '#runs-table')}")
    ds_n = len(want)
    p.select_option("#f-dataset", "")

    hs = p.evaluate("() => [...document.querySelectorAll('#f-harness option')].map(o => o.value).filter(Boolean)[0]")
    p.select_option("#f-harness", hs)
    want = sorted(r["id"] for r in study if r["harness"] == hs)
    got = t.stable(p, "() => [...document.querySelectorAll('#runs-table tbody tr[data-run]')].map(tr => tr.dataset.run).sort()")
    if got != want:
        raise Fail(f"harness={hs} DOM {len(got)} != 期望 {len(want)} | {t.where(p, '#runs-table')}")
    hs_n = len(want)
    p.select_option("#f-harness", "")

    statuses = p.evaluate("() => [...document.querySelectorAll('#f-status option')].map(o => o.value).filter(Boolean)")
    st_counts = {}
    for st in statuses:
        p.select_option("#f-status", st)
        want = sorted(r["id"] for r in study if r["status"] == st)
        got = t.stable(p, "() => [...document.querySelectorAll('#runs-table tbody tr[data-run]')].map(tr => tr.dataset.run).sort()")
        if got != want:
            raise Fail(f"status={st} DOM {len(got)} != 期望 {len(want)} | {t.where(p, '#runs-table')}")
        st_counts[st] = len(want)
    p.select_option("#f-status", "")
    st_txt = " ".join(f"{k}({v})" for k, v in st_counts.items())
    return f"benchmark={ds}({ds_n}) harness={hs}({hs_n}) status={st_txt} 均一致"


@check("5-5", "5 实验列表", "同一筛选两次刷新结果相同", "S2")
def c5_5(t: UAT):
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    first = t.stable(p, "() => [...document.querySelectorAll('#runs-table tbody tr[data-run]')].map(tr => tr.dataset.run)")
    p.reload(wait_until="domcontentloaded")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "刷新后的行")
    second = t.stable(p, "() => [...document.querySelectorAll('#runs-table tbody tr[data-run]')].map(tr => tr.dataset.run)")
    if first != second:
        diff = [x for x in first if x not in second][:3] or [x for x in second if x not in first][:3]
        raise Fail(f"两次刷新行不同（{len(first)} vs {len(second)}）差异示例 {diff} | {t.where(p, '#runs-table')}")
    return f"两次一致，{len(first)} 行"


@check("5-6", "5 实验列表", "空结果说是空、不崩（搜一个不存在的 id）", "S2")
def c5_6(t: UAT):
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    total = t.stable(p, "() => document.querySelectorAll('#runs-table tbody tr[data-run]').length")
    p.fill("#f-q", "zzz-no-such-run-000")
    p.wait_for_timeout(600)
    title = t.empty_title(p, "#runs-table")
    n = t.stable(p, "() => document.querySelectorAll('#runs-table tbody tr[data-run]').length")
    cnt = p.inner_text("#f-count")
    rail = p.inner_text("#run-rail")
    if n != 0:
        raise Fail(f"搜不存在的字符串还有 {n} 行 | {t.where(p, '#runs-table')}")
    if not title:
        raise Fail(f"空结果没有空态标题 | {t.where(p, '#runs-table')}")
    if "读取失败" in p.inner_text("#runs-table") or "读取失败" in rail:
        raise Fail(f"空结果报错 | {t.where(p, '#runs-table')}")
    if not re.match(r"0\s*/\s*\d+", cnt.replace("个 run", "").strip()):
        raise Fail(f"计数没归零：f-count={cnt!r} | {t.where(p, '#f-count')}")
    return f"{total} 行里筛掉全部 → 空态“{trunc(title, 20)}”，计数 {cnt}，右栏 {trunc(rail, 40)}"


@check("5-7", "5 实验列表", "按 run id 过滤与手算一致", "S2")
def c5_7(t: UAT):
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    study = t.api("/api/runs?kind=study")["rows"]
    tok = study[0]["id"].split("-")[-1].replace("train", "") or study[0]["id"][:6]
    tok = "tthe" if any("tthe" in r["id"] for r in study) else tok
    p.fill("#f-q", tok)
    want = sorted(r["id"] for r in study if tok.lower() in r["id"].lower())
    if not want:
        raise Fail(f"取样 token {tok!r} 在真实数据里匹配 0 行，无法验证")
    got = t.stable(p, "() => [...document.querySelectorAll('#runs-table tbody tr[data-run]')].map(tr => tr.dataset.run).sort()")
    if got != want:
        raise Fail(f"q={tok!r} DOM {len(got)} != 手算 {len(want)} | {t.where(p, '#runs-table')}")
    return f"q={tok!r} → {len(got)} 行，与手算一致"


# ═══════════════════════════════════════════════════════ 场景 6：实验详情
@check("6-1", "6 实验详情", "点一行，右栏更新到该 run", "S1")
def c6_1(t: UAT):
    p = t.open("/#/runs")
    rows = t.wait(p, "() => document.querySelectorAll('#runs-table tbody tr[data-run]').length > 1 && [...document.querySelectorAll('#runs-table tbody tr[data-run]')].map(tr => tr.dataset.run)")
    seen = []
    for rid in rows[1:4]:
        p.evaluate("id => document.querySelector('#runs-table tr[data-run=\"' + id + '\"]').click()", rid)
        got = t.wait(p, f"() => {{const s=document.querySelector('#run-rail .card-head .sub'); return s && s.textContent.trim() === {json.dumps(rid)} ? true : false;}}",
                     f"右栏切到 {rid}")
        seen.append(rid)
        rail = t.rail(p)
        detail = t.api(f"/api/runs/{rid}")
        if len(rail["rows"]) != len(detail["rounds"]):
            raise Fail(f"{rid}: 每轮表 {len(rail['rows'])} 行 != 接口 {len(detail['rounds'])} 轮 | {t.where(p, '#run-rail')}")
    return f"点了 {len(seen)} 行，右栏每次都跟着换成该 run（每轮表行数也对得上）"


@check("6-2", "6 实验详情", "曲线点数 = 每轮表行数（rounds>1）", "S1")
def c6_2(t: UAT):
    tgt = t.samples.get("target")
    if not tgt:
        raise Blocked("真实数据里没有 rounds>1 的 study run")
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    p.evaluate("id => document.querySelector('#runs-table tr[data-run=\"' + id + '\"]').click()", tgt["id"])
    t.wait(p, f"() => {{const s=document.querySelector('#run-rail .card-head .sub'); return s && s.textContent.trim() === {json.dumps(tgt['id'])};}}", "右栏")
    rail = t.rail(p)
    detail = t.api(f"/api/runs/{tgt['id']}")
    scored = len([x for x in detail["rounds"] if x["score"] is not None])
    if rail["pts"] != len(rail["rows"]):
        raise Fail(f"{tgt['id']}: 曲线 {rail['pts']} 点 != 每轮表 {len(rail['rows'])} 行 | {t.where(p, '#run-rail')}")
    if rail["pts"] != scored:
        raise Fail(f"{tgt['id']}: 曲线 {rail['pts']} 点 != 接口有分的轮数 {scored} | {t.where(p, '#run-rail')}")
    if rail["pts"] < 2:
        raise Fail(f"{tgt['id']}: 曲线只有 {rail['pts']} 点 | {t.where(p, '#run-rail')}")
    return f"{tgt['id']}：{rail['pts']} 点 = {len(rail['rows'])} 行 = {scored} 个有分轮次"


@check("6-3", "6 实验详情", "分数旁有可点入口到该轮 harness（并核对 sha）", "S2")
def c6_3(t: UAT):
    tgt = t.samples.get("target")
    if not tgt or t.samples.get("diffRound") is None:
        raise Blocked("没有带 patch 的多轮 run")
    rnd = t.samples["diffRound"]
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    p.evaluate("id => document.querySelector('#runs-table tr[data-run=\"' + id + '\"]').click()", tgt["id"])
    t.wait(p, f"() => {{const s=document.querySelector('#run-rail .card-head .sub'); return s && s.textContent.trim() === {json.dumps(tgt['id'])};}}", "右栏")
    detail = t.api(f"/api/runs/{tgt['id']}")
    row = next((x for x in detail["rounds"] if x["round"] == rnd), None)
    if not row:
        raise Fail(f"{tgt['id']} 的接口没有 round {rnd}")
    t.obs["rail_sha_link"] = bool(p.evaluate(
        "() => !!document.querySelector('#run-rail table.grid tbody td.row-id a')"))
    # 右栏会被并发刷新重建。先等这一行真的在；点完等 hash 真的变了，3 s 没变就重点
    # （最多 3 次）。不用固定 sleep：慢的时候 sleep 会读成“点没生效”，快的时候又白等。
    t.wait(p, f"() => [...document.querySelectorAll('#run-rail table.grid tbody tr')]"
             f".some(tr => tr.dataset.round === {json.dumps(str(rnd))})", f"右栏第 {rnd} 行")
    for attempt in range(1, 4):
        clicked = p.evaluate("""n => { const tr = [...document.querySelectorAll('#run-rail table.grid tbody tr')]
            .find(tr => tr.dataset.round === String(n)); if (tr) { tr.click(); return true; } return false; }""", rnd)
        if not clicked:
            raise Fail(f"右栏没有 round {rnd} 的行可点 | {t.where(p, '#run-rail')}")
        try:
            t.wait(p, "() => location.hash.startsWith('#/harness')", "跳到改 harness",
                   timeout_ms=3000)
            break
        except Fail:
            if attempt == 3:
                raise Fail(f"点了 3 次 r{rnd} 的入口，hash 一直没变（{page_round(p)}）| url={p.url}")
            # 右栏可能刚被重建：等它再次稳定到同一个 run，再点一次
            t.wait(p, f"() => {{const s=document.querySelector('#run-rail .card-head .sub');"
                     f" return s && s.textContent.trim() === {json.dumps(tgt['id'])};}}", "右栏")
    # hash 变了不等于编辑器已经画到这一轮：显式等 diff 头出现且写着 round-N
    ed = t.wait(p, f"() => {{ const h = document.querySelector('#h-editor .diff-head');"
                   f" const v = h ? h.innerText.replace(/\\s+/g,' ').trim() : '';"
                   f" return v.includes('round-{rnd}') ? v : ''; }}", f"r{rnd} 的 diff 头")
    t.wait(p, f"() => {{ const r = document.querySelector('#h-run');"
             f" const n = document.querySelector('#h-round');"
             f" return !!r && !!n && r.value === {json.dumps(tgt['id'])}"
             f" && Number(n.value) === {rnd}; }}", "run/轮次选中")
    h = t.harness(p)
    sha = (row.get("harness_sha") or "")[:8]
    if h["run"] != tgt["id"] or h["roundVal"] != rnd:
        raise Fail(f"点 r{rnd} 后落到的不是同一 run/轮：run={h['run']} round={h['roundVal']} | url={p.url} dom={trunc(ed)}")
    if not sha:
        raise Fail(f"该轮没有 harness_sha（接口为空）| url={p.url}")
    if sha not in ed:
        raise Fail(f"diff 头没有出现该轮 harness sha {sha} | url={p.url} sel=#h-editor .diff-head dom={trunc(ed)}")
    return f"点 r{rnd} → #/harness，run/round 未变，diff 头含 sha {sha}（sha 文本本身不是 <a>）"


@check("6-4", "6 实验详情", "running 的 run 不显示分数（列表只显示进度）", "S2")
def c6_4(t: UAT):
    rows = [_FIX_RUNNING, _FIX_INVALID, _FIX_OK]

    def route(r):
        path = urllib.parse.urlsplit(r.request.url).path
        if re.search(r"/api/runs/[^/?]+$", path):
            body = _FIX_DETAIL.get(path.rsplit("/", 1)[-1], _FIX_DETAIL[_FIX_OK["id"]])
        else:
            body = {"rows": rows, "count": len(rows)}
        return r.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    p = t.open("/#/runs", routes=[(RE_RUNS, route)], wait_ms=1500)
    got = t.list_rows(p)
    cols = t.cols(p, "#runs-table")
    live = next((r for r in got if r["id"] == _FIX_RUNNING["id"]), None)
    if live is None:
        raise Fail(f"拦截载荷里的 running 行没渲染出来（拿到 {[r['id'] for r in got]}）| {t.where(p, '#runs-table')}")
    if "最好" not in cols:
        raise Fail(f"列表没有“最好”列（表头 {cols}）| {t.where(p, '#runs-table')}")
    idx = cols.index("最好")
    best = live["cells"][idx] if len(live["cells"]) > idx else ""
    if re.search(r"\d+\.\d+", best):
        rail0 = t.rail(p)
        prog0 = (rail0["text"].split("进度")[1:] or [""])[0]
        raise Fail(f"running 行显示了分数 最好={best!r}（应只显示进度 {trunc(prog0, 20)!r}；"
                   f"真实数据当前无 running 行，本项用拦截载荷）"
                   f" | url={p.url} sel=#runs-table tr[data-run] dom={trunc(live['cells'])}")
    if "running" not in live["pill"]:
        raise Fail(f"running 行状态不是 running 而是 {live['pill']!r} | url={p.url}")
    if "7" not in live["pill"]:
        raise Fail(f"running 行没有显示进度（progress.done=7）而是 {live['pill']!r} | url={p.url}")
    rail = t.rail(p)
    prog = next((x for x in rail["text"].split("进度")[1:]), "")
    return (f"running 行：最好={best!r}（应不显示分数）、状态={live['pill']!r}；"
            f"右栏进度={trunc(prog, 30)}（拦截载荷，未起 driver）")


@check("6-5", "6 实验详情", "invalid 的 run 不显示 0", "S0")
def c6_5(t: UAT):
    rows = [_FIX_RUNNING, _FIX_INVALID, _FIX_OK]

    def route(r):
        path = urllib.parse.urlsplit(r.request.url).path
        if re.search(r"/api/runs/[^/?]+$", path):
            rid = path.rsplit("/", 1)[-1]
            body = _FIX_DETAIL.get(rid, _FIX_DETAIL[_FIX_OK["id"]])
        else:
            body = {"rows": rows, "count": len(rows)}
        return r.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    p = t.open("/#/runs", routes=[(RE_RUNS, route)], wait_ms=1500)
    got = t.list_rows(p)
    cols = t.cols(p, "#runs-table")
    inv = next((r for r in got if r["id"] == _FIX_INVALID["id"]), None)
    if inv is None:
        raise Fail(f"拦截载荷里的 invalid 行没渲染 | {t.where(p, '#runs-table')}")
    if "最好" not in cols or "状态" not in cols:
        raise Fail(f"列表缺列（表头 {cols}）| {t.where(p, '#runs-table')}")
    i_best, i_state = cols.index("最好"), cols.index("状态")
    best = inv["cells"][i_best] if len(inv["cells"]) > i_best else ""
    if re.search(r"\d", best):
        raise Fail(f"invalid 行（score=null, n_invalid=3）显示了 {best!r}，应为 —"
                   f" | url={p.url} sel=#runs-table tr[data-run] dom={trunc(inv['cells'])}")
    if not re.search(r"invalid\s*3", inv["pill"]):
        raise Fail(f"invalid 行状态徽章是 {inv['pill']!r}，应含 'invalid 3' | url={p.url}")
    # 真实数据交叉核对（当前只有一个 invalid run）
    real = t.samples.get("invalid")
    real_note = "真实数据里没有 invalid run"
    if real:
        p2 = t.open("/#/runs", wait_ms=1200)
        p2.evaluate("() => document.querySelector('[data-kind=\"\"]').click()")
        p2.wait_for_timeout(1200)
        cells = p2.evaluate("""id => { const tr = document.querySelector('#runs-table tr[data-run="' + id + '"]');
            return tr ? [...tr.querySelectorAll('td')].map(td => td.innerText.replace(/\\s+/g,' ').trim()) : null; }""", real["id"])
        c2 = t.cols(p2, "#runs-table")
        pick = (lambda cs: (cs[i_state] if cs and len(cs) > i_state else cs))
        if "状态" in c2:
            i_st2 = c2.index("状态")
            pick = lambda cs, i=i_st2: (cs[i] if cs and len(cs) > i else cs)  # noqa: E731
        state_cell = pick(cells)
        if cells and not re.search(r"invalid", str(state_cell)):
            raise Fail(f"真实 invalid run {real['id']} 的状态列是 {state_cell!r}，没有标 invalid"
                       f" | url={p2.url} sel=#runs-table dom={trunc(cells)}")
        real_note = f"真实 invalid run {real['id']}: 状态列={trunc(state_cell)}"
    return f"拦截载荷 invalid 行最好={best!r}、徽章={inv['pill']!r}；{real_note}"


@check("6-6", "6 实验详情", "单点 run 说明“没有曲线”，不画假曲线", "S2")
def c6_6(t: UAT):
    single = t.samples.get("single")
    if not single:
        raise Blocked("真实数据里没有 rounds=1 的 eval run")
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    p.evaluate("() => document.querySelector('[data-kind=\"eval\"]').click()")
    t.wait(p, f"() => !!document.querySelector('#runs-table tr[data-run=\"{single['id']}\"]')", "eval 行")
    p.evaluate("id => document.querySelector('#runs-table tr[data-run=\"' + id + '\"]').click()", single["id"])
    t.wait(p, f"() => {{const s=document.querySelector('#run-rail .card-head .sub'); return s && s.textContent.trim() === {json.dumps(single['id'])};}}", "右栏")
    rail = t.rail(p)
    if rail["hasCurve"]:
        raise Fail(f"单点 run 画了曲线 | {t.where(p, '#run-rail')}")
    if not any("没有曲线" in n or "一个测点" in n for n in rail["notice"]):
        raise Fail(f"单点 run 没有说明“只有一个测点，没有曲线” | {t.where(p, '#run-rail')}")
    return f"{single['id']}：无曲线，提示={trunc([n for n in rail['notice'] if '曲线' in n or '测点' in n][0])}"


# ═══════════════════════════════════════════════════════ 场景 7：深链接
@check("7-1", "7 深链接", "直接打开 #/runs/<id> 落到同一 run", "S1")
def c7_1(t: UAT):
    tgt = t.samples.get("target")
    if not tgt:
        raise Blocked("真实数据里没有多轮 run")
    rid = tgt["id"]
    p = t.open(f"/#/runs/{rid}", wait_ms=1500)
    t.wait(p, "() => { const s = document.querySelector('#run-rail .card-head .sub'); return !!(s && s.textContent.trim()); }",
           "右栏头", timeout_ms=8000)
    rail = t.rail(p)
    if rail["id"] != rid:
        raise Fail(f"深链接 {rid} 落到的是 {rail['id']!r}（闪回了列表）| url={p.url} "
                   f"sel=#run-rail .card-head .sub dom={trunc(rail['head'])}")
    return f"{rid} 落到同一 run"


@check("7-2", "7 深链接", "刷新后仍在同一 run", "S1")
def c7_2(t: UAT):
    tgt = t.samples.get("target")
    if not tgt:
        raise Blocked("真实数据里没有多轮 run")
    rid = tgt["id"]
    p = t.open(f"/#/runs/{rid}", wait_ms=1500)
    p.reload(wait_until="domcontentloaded")
    p.wait_for_timeout(1200)
    t.wait(p, "() => { const s = document.querySelector('#run-rail .card-head .sub'); return !!(s && s.textContent.trim()); }",
           "右栏头", timeout_ms=8000)
    rail = t.rail(p)
    if rail["id"] != rid:
        raise Fail(f"刷新后落到 {rail['id']!r} | url={p.url} sel=#run-rail .card-head .sub "
                   f"dom={trunc(rail['head'])}")
    return f"刷新后仍是 {rid}"


@check("7-3", "7 深链接", "新标签打开同一 URL 落到同一 run", "S2")
def c7_3(t: UAT):
    tgt = t.samples.get("target")
    if not tgt:
        raise Blocked("真实数据里没有多轮 run")
    rid = tgt["id"]
    p = t.open(f"/#/runs/{rid}", wait_ms=2000)
    p2 = p.context.new_page()
    t._instrument(p2)
    p2.goto(f"{t.url}/#/runs/{rid}", wait_until="domcontentloaded", timeout=t.timeout_ms)
    p2.wait_for_timeout(1500)
    t.wait(p2, "() => { const s = document.querySelector('#run-rail .card-head .sub'); return !!(s && s.textContent.trim()); }",
           "右栏头", timeout_ms=8000)
    rail = t.rail(p2)
    if rail["id"] != rid:
        raise Fail(f"新标签落到 {rail['id']!r} | url={p2.url} sel=#run-rail .card-head .sub "
                   f"dom={trunc(rail['head'])}")
    return f"新标签仍是 {rid}"


@check("7-4", "7 深链接", "直接打开 #/harness 可用（不是“没有 run”）", "S1")
def c7_4(t: UAT):
    p = t.open("/#/harness", wait_ms=2500)
    h = t.harness(p)
    if "没有 run" in h["tree"] or not h["runOpts"]:
        raise Fail(f"直接打开 #/harness 时视图是空的（run 下拉 {len(h['runOpts'])} 项），"
                   f"而 /api/runs 有 {t.api('/api/runs')['count']} 个 run | url={p.url} "
                   f"sel=#h-tree dom={trunc(h['tree'])}")
    return f"直接打开可用，run 下拉 {len(h['runOpts'])} 项"


@check("7-5", "7 深链接", "深链接到 eval 的 run 会自动切到对应 kind", "S2")
def c7_5(t: UAT):
    ev = t.samples.get("single") or (t.samples.get("eval_rows") or [None])[0]
    if not ev:
        raise Blocked("真实数据里没有 eval run")
    if ev.get("kind") not in (None, "eval"):
        raise Blocked("取样 run 不是 eval")
    rid = ev["id"]
    p = t.open(f"/#/runs/{rid}", wait_ms=1500)
    t.wait(p, "() => { const s = document.querySelector('#run-rail .card-head .sub'); return !!(s && s.textContent.trim()); }",
           "右栏头", timeout_ms=8000)
    rail = t.rail(p)
    if rail["id"] != rid:
        raise Fail(f"深链接 {rid} 落到 {rail['id']!r} | url={p.url} sel=#run-rail .card-head .sub dom={trunc(rail['head'])}")
    chip = p.evaluate("""() => [...document.querySelectorAll('[data-kind]')]
        .filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.kind)""")
    if chip != ["eval"]:
        raise Fail(f"落到 run 了，但 kind 还停在 {chip} | url={p.url} sel=[data-kind][aria-pressed=true]")
    row = p.evaluate("id => !!document.querySelector('#runs-table tr[data-run=\"' + id + '\"]')", rid)
    if not row:
        raise Fail(f"右栏是 {rid}，但列表里没有这一行（kind 筛选没切）| {t.where(p, '#runs-table')}")
    return f"{rid}：右栏、列表、kind chip（eval）一致"


@check("7-6", "7 深链接", "深链接到不存在的 id 有明确说明、不拿别的 run 顶替", "S2")
def c7_6(t: UAT):
    bogus = "rsi10-20260101T000000Z-no-such-run-train"
    p = t.open(f"/#/runs/{bogus}", wait_ms=1500)
    t.wait(p, "() => { const r = document.querySelector('#run-rail'); return !!(r && r.innerText.trim().length > 4); }",
           "右栏说明", timeout_ms=8000)
    rail = t.rail(p)
    errs = [e for e in t.console if bogus in (e.get("url") or "")] or list(t.console)
    if rail["id"]:
        raise Fail(f"不存在的 run 却显示了另一个 run 的卡片（{rail['id']}）| url={p.url} "
                   f"sel=#run-rail .card-head .sub dom={trunc(rail['head'])}")
    txt = rail["text"]
    if bogus not in txt and "找不到" not in txt and "没有" not in txt:
        raise Fail(f"右栏没有说明“找不到这个 run”| url={p.url} sel=#run-rail dom={trunc(txt)}")
    if errs:
        e = errs[0]
        raise Fail(f"深链接不存在的 id 触发控制台报错：{trunc(e['text'])} | url={e['url']}")
    return f"右栏说明：{trunc(txt, 80)}（当前 hash={p.evaluate('location.hash')}）"


@check("7-7", "7 深链接", "#/harness/<run>/<round> 刷新后仍在同一轮", "S2")
def c7_7(t: UAT):
    tgt, rnd = t.samples.get("target"), t.samples.get("diffRound")
    if not tgt or rnd is None:
        raise Blocked("没有带 patch 的多轮 run")
    url = f"/#/harness/{tgt['id']}/{rnd}"
    p = t.open(url, wait_ms=1500)
    for phase in ("直接打开", "刷新后"):
        if phase == "刷新后":
            p.reload(wait_until="domcontentloaded")
            p.wait_for_timeout(1200)
        t.wait(p, "() => [...document.querySelectorAll('#h-run option')].length > 1", "run 下拉")
        t.wait(p, "() => document.querySelector('#h-run').value === " + json.dumps(tgt["id"]), f"{phase} run")
        t.wait(p, f"() => Number(document.querySelector('#h-round').value) === {rnd}", f"{phase} round",
               timeout_ms=8000)
        ed = t.editor(p)
        if f"round-{rnd}" not in ed["head"]:
            raise Fail(f"{phase}：落到 r{page_round(p)} 而不是 r{rnd} | url={p.url} "
                       f"sel=#h-editor .diff-head dom={trunc(ed['head'])}")
    return f"{url}：直接打开与刷新都落在 r{rnd}（hash={p.evaluate('location.hash')}）"


def page_round(page):
    try:
        return page.evaluate("() => document.querySelector('#h-round') ? document.querySelector('#h-round').value : '?'")
    except Exception:  # noqa: BLE001
        return "?"


# ═══════════════════════════════════════════════════════ 场景 8：diff / 文件
def _patch_files(t: UAT, run_id: str, n: int):
    """按前端的口径（parsePatch）解析 patch：每个文件的 +/− 行、@@ 段、路径。

    前端把文件头（diff --git / index / --- / +++ / mode…）丢掉，进入 hunk 之后
    才把 "+"/"-" 行算作 add/del；这里照抄同一套状态机，保证是同一把尺子。
    """
    p = t.runs_root / run_id / "diffs" / f"round-{n}.patch"
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    files, cur, in_hunks = [], None, False

    def new_file():
        return {"path": "", "old": "", "new": "", "add": 0, "del": 0, "hunks": 0}

    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            cur, in_hunks = new_file(), False
            m = re.match(r"^diff --git a/(.+?) b/(.+)$", raw)
            if m:
                cur["path"] = m.group(2)
            files.append(cur)
            continue
        if cur is None:
            cur = new_file()
            files.append(cur)
        if not in_hunks and raw.startswith("--- "):
            cur["old"] = raw[4:].strip()
            continue
        if not in_hunks and raw.startswith("+++ "):
            cur["new"] = raw[4:].strip()
            continue
        if raw.startswith(("index ", "old mode ", "new mode ", "new file mode ",
                           "deleted file mode ", "similarity index ",
                           "dissimilarity index ", "rename from ", "rename to ",
                           "Binary files ", "GIT binary patch")):
            continue
        if raw.startswith("@@"):
            in_hunks = True
            cur["hunks"] += 1
            continue
        if raw.startswith("\\"):
            continue
        if not in_hunks:
            continue
        if raw.startswith("+"):
            cur["add"] += 1
        elif raw.startswith("-"):
            cur["del"] += 1
    for f in files:
        if f["new"] and f["new"] != "/dev/null":
            f["path"] = f["new"][2:] if f["new"].startswith("b/") else f["path"]
        elif f["old"] and f["old"] != "/dev/null":
            f["path"] = f["old"][2:] if f["old"].startswith("a/") else f["path"]
        if not f["path"]:
            f["path"] = "(unknown)"
    stat = {}
    sp = p.parent / f"round-{n}.json"
    if sp.is_file():
        try:
            stat = json.loads(sp.read_text(encoding="utf-8"))
        except ValueError:
            stat = {}
    return {
        "files": files,
        "plus": sum(f["add"] for f in files),
        "minus": sum(f["del"] for f in files),
        "hunks": sum(f["hunks"] for f in files),
        "stat": stat,
        "text": text,
    }


def _pick_round(t: UAT, page, run_id: str, rnd: int, timeout_ms: int = 15000) -> bool:
    """把改 harness 视图稳定地选到 (run, round)。

    应用会并发重渲染、也会把选中项写回 URL，所以不能靠“选一次就算数”：
    这里按 deadline 轮询，必要时强制重渲染一次。
    """
    end = time.time() + timeout_ms / 1000.0
    last_err = ""
    while time.time() < end:
        try:
            h = t.harness(page)
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(200)
            continue
        if not h["runOpts"]:
            page.wait_for_timeout(200)
            continue
        if h["run"] != run_id:
            if run_id not in h["runOpts"]:
                last_err = f"run 下拉里没有 {run_id}（{len(h['runOpts'])} 项）"
            else:
                page.select_option("#h-run", run_id)
            page.wait_for_timeout(400)
            continue
        has = page.evaluate(f"() => [...document.querySelectorAll('#h-round option')]"
                            f".some(o => o.value === '{rnd}')")
        if not has:
            # 视图可能还停在上一轮的渲染上：点一次导航强制重渲染
            page.evaluate("() => document.querySelector('a[data-view-link=harness]').click()")
            page.wait_for_timeout(500)
            continue
        if h["roundVal"] == rnd:
            page.wait_for_timeout(450)
            if t.harness(page)["roundVal"] == rnd:
                return True
        else:
            page.select_option("#h-round", str(rnd))
        page.wait_for_timeout(300)
    print(f"    （选轮次没稳住：{last_err or '详见证据'}）", flush=True)
    return False


def _goto_diff(t: UAT, page, run_id: str, rnd: int):
    """从列表页走 UI 进 harness（不靠深链接），选中 run + 轮，并等到渲染稳定。"""
    if not page.evaluate("() => location.hash.startsWith('#/harness')"):
        page.evaluate("() => document.querySelector('a[data-view-link=harness]').click()")
    t.wait(page, "() => [...document.querySelectorAll('#h-run option')].length > 1", "run 下拉")
    if not _pick_round(t, page, run_id, rnd):
        ed = page.evaluate("() => { const h = document.querySelector('#h-editor .diff-head');"
                           " return h ? h.innerText.replace(/\\s+/g,' ').trim() : ''; }")
        raise Fail(f"{run_id} 选不到 r{rnd}（选择被并发渲染重置）| url={page.url} "
                   f"sel=#h-round dom={trunc(ed)}")
    head = t.wait(page, "() => { const h = document.querySelector('#h-editor .diff-head');"
                        f" return h && h.innerText.includes('round-{rnd}') ? "
                        "h.innerText.replace(/\\s+/g,' ').trim() : ''; }", f"r{rnd} 的 diff 头", timeout_ms=6000)
    tree = page.evaluate("() => { const t = document.querySelector('#h-tree'); return t ? t.innerText : ''; }")
    if "state_after_round" in tree and f"state_after_round{rnd}" not in tree:
        raise Fail(f"选了 r{rnd}，左树却是 {trunc(tree)} | url={page.url} sel=#h-tree")
    return head


@check("8-1", "8 diff", "行数 = 真实 patch 的 +/− 内容行（且 = round-N.json）", "S1")
def c8_1(t: UAT):
    tgt, rnd = t.samples.get("target"), t.samples.get("diffRound")
    if not tgt or rnd is None:
        raise Blocked("没有带 patch 的多轮 run")
    want = _patch_files(t, tgt["id"], rnd)
    if not want:
        raise Fail(f"{tgt['id']} round-{rnd}.patch 不存在，无法比较")
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    _goto_diff(t, p, tgt["id"], rnd)
    t.obs["harness_hash"] = p.evaluate("() => location.hash")
    ed = t.editor(p)
    files = want["files"]
    if len(files) <= 1:
        exp = files[0] if files else {"add": 0, "del": 0, "hunks": 0, "path": "?"}
        if (ed["add"], ed["del"]) != (exp["add"], exp["del"]):
            raise Fail(f"{exp['path']}: 渲染 +{ed['add']}/−{ed['del']} != patch +{exp['add']}/−{exp['del']} | "
                       f"url={p.url} sel=#h-editor .ln.add/.ln.del dom={trunc(ed['head'])}")
        if ed["hunk"] != exp["hunks"]:
            raise Fail(f"{exp['path']}: 渲染 {ed['hunk']} 个 @@ 段 != patch 的 {exp['hunks']} 个 | "
                       f"url={p.url} sel=#h-editor .ln.hunk")
        note = f"{exp['path']}：+{exp['add']}/−{exp['del']}，{exp['hunks']} 段"
    else:
        if not ed["files"]:
            raise Fail(f"patch 有 {len(files)} 个文件，但页面没有文件切换器 | {t.where(p, '#h-editor')}")
        seen = []
        for i, f in enumerate(files):
            got = None
            for _try in range(3):
                p.select_option("#h-patch-file", str(i))
                p.wait_for_timeout(350)
                got = t.editor(p)
                if (got["add"], got["del"]) == (f["add"], f["del"]):
                    break
            if (got["add"], got["del"]) != (f["add"], f["del"]):
                raise Fail(f"patch 第 {i+1} 个文件 {f['path']}: 渲染 +{got['add']}/−{got['del']} != "
                           f"+{f['add']}/−{f['del']} | url={p.url} sel=#h-editor dom={trunc(got['head'])}")
            seen.append(f"{f['path']} +{f['add']}/−{f['del']}")
        ed = got
        note = f"{len(files)} 个文件逐个核对：" + "；".join(seen)
    st = want["stat"]
    if st and (st.get("added"), st.get("removed")) != (want["plus"], want["minus"]):
        raise Fail(f"patch 文件合计 +{want['plus']}/−{want['minus']} 与 round-{rnd}.json "
                   f"added={st.get('added')}/removed={st.get('removed')} 不一致（数据侧）")
    if f"+{want['plus']}" not in ed["head"] or f"−{want['minus']}" not in ed["head"]:
        raise Fail(f"diff 头显示的总计不是 +{want['plus']}/−{want['minus']} | "
                   f"url={p.url} sel=#h-editor .diff-head dom={trunc(ed['head'])}")
    return f"{tgt['id']} r{rnd}：{note}；头 +{want['plus']}/−{want['minus']}（= round-{rnd}.json）"


@check("8-2", "8 diff", "切换轮次/run 不串内容", "S2")
def c8_2(t: UAT):
    tgt, rnd = t.samples.get("target"), t.samples.get("diffRound")
    if not tgt or rnd is None:
        raise Blocked("没有带 patch 的多轮 run")
    other = next((r for r in t.samples["multi"] if r["id"] != tgt["id"]), None)
    rounds = [x["round"] for x in t.samples["detail"]]
    nxt = next((n for n in (rnd + 1, rnd - 1, rnd + 2) if n in rounds
                and _patch_files(t, tgt["id"], n)), None)
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    _goto_diff(t, p, tgt["id"], rnd)
    first = t.editor(p)
    if nxt is None:
        return "没有第二个带 patch 的轮次可切换，只验证了同轮渲染"
    _goto_diff(t, p, tgt["id"], nxt)
    second = t.editor(p)
    w = _patch_files(t, tgt["id"], nxt)
    if (second["add"], second["del"]) != (w["plus"], w["minus"]):
        raise Fail(f"切到 r{nxt} 后 +{second['add']}/−{second['del']} != patch +{w['plus']}/−{w['minus']} | "
                   f"url={p.url} dom={trunc(second['head'])}")
    if f"round-{nxt}" not in second["head"]:
        raise Fail(f"切到 r{nxt} 后 diff 头还是 {trunc(second['head'])} | url={p.url} sel=#h-editor .diff-head")
    h = t.harness(p)
    if "state_after_round" in h["tree"] and f"state_after_round{nxt}" not in h["tree"]:
        raise Fail(f"切到 r{nxt} 后左树还是 {trunc(h['tree'])} | url={p.url} sel=#h-tree")
    if other:
        o_rounds = [x["round"] for x in t.api(f"/api/runs/{other['id']}")["rounds"]]
        o_rnd = next((n for n in o_rounds if _patch_files(t, other["id"], n)), None)
        if o_rnd is not None:
            _goto_diff(t, p, other["id"], o_rnd)
            h2 = t.harness(p)
            third = t.editor(p)
            if h2["run"] != other["id"]:
                raise Fail(f"切到另一个 run 后下拉是 {h2['run']!r} | url={p.url} sel=#h-run")
            w2 = _patch_files(t, other["id"], h2["roundVal"])
            if (third["add"], third["del"]) != (w2["plus"], w2["minus"]):
                raise Fail(f"{other['id']} r{h2['roundVal']} 渲染 +{third['add']}/−{third['del']} != patch "
                           f"+{w2['plus']}/−{w2['minus']} | url={p.url} dom={trunc(third['head'])}")
    return (f"r{rnd}(+{first['add']}/−{first['del']}) → r{nxt}(+{second['add']}/−{second['del']})"
            f"，跨 run 切换后头/树/计数都跟着换")


@check("8-3", "8 diff", "from→to sha = round-N.json，且与曲线一致", "S2")
def c8_3(t: UAT):
    tgt, rnd = t.samples.get("target"), t.samples.get("diffRound")
    if not tgt or rnd is None:
        raise Blocked("没有带 patch 的多轮 run")
    stat = _patch_files(t, tgt["id"], rnd)["stat"]
    frm, to = (stat.get("from") or "")[:8], (stat.get("to") or "")[:8]
    if not frm or not to:
        raise Fail(f"round-{rnd}.json 没有 from/to")
    prev = next((x for x in t.samples["detail"] if x["round"] == rnd - 1), None)
    cur = next((x for x in t.samples["detail"] if x["round"] == rnd), None)
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    _goto_diff(t, p, tgt["id"], rnd)
    ed = t.editor(p)
    if frm not in ed["head"] or to not in ed["head"]:
        raise Fail(f"diff 头没显示 {frm} → {to}（实际 {trunc(ed['head'])}）| url={p.url} sel=#h-editor .diff-head")
    if cur and cur.get("harness_sha") and to != cur["harness_sha"][:8]:
        raise Fail(f"diff 头 to={to} != 曲线 r{rnd} harness_sha {cur['harness_sha']} | url={p.url}")
    if prev and prev.get("harness_sha") and frm != prev["harness_sha"][:8]:
        raise Fail(f"diff 头 from={frm} != 曲线 r{rnd-1} harness_sha {prev['harness_sha']} | url={p.url}")
    return f"r{rnd}: {frm} → {to}；to = 曲线 r{rnd} sha，from = 曲线 r{rnd-1} sha"


def _pick_text_file(t: UAT, run_id: str, rnd: int):
    info = t.api(f"/api/runs/{run_id}/rounds/{rnd}")
    cand = [f for f in info.get("files") or []
            if f["size"] < 400_000 and (f["path"].endswith((".py", ".json", ".txt", ".md", ".toml", ".cfg"))
                                        or f["path"].split("/")[-1].startswith(".gitignore"))]
    if not cand:
        return None
    return max(cand, key=lambda x: x["size"])


def _real_lines(t: UAT, run_id: str, rnd: int, rel: str):
    path = t.runs_root / run_id / f"state_after_round{rnd}" / rel
    return path.read_text(encoding="utf-8", errors="replace")


def _norm_lines(text: str, rendered=None):
    """编辑器口径的行数：以 \n 结尾的文件不把最后一个空串算一行（与 wc -l / 前端 splitLines 一致）。"""
    real = text.splitlines()
    ui = list(rendered or [])
    if len(ui) == len(real) + 1 and ui[-1] == "":
        ui = ui[:-1]
    return real, ui


@check("8-4", "8 diff", "文件视图行数 = 真实文件行数", "S1")
def c8_4(t: UAT):
    tgt, rnd = t.samples.get("target"), t.samples.get("diffRound")
    if not tgt or rnd is None:
        raise Blocked("没有带 patch 的多轮 run")
    f = _pick_text_file(t, tgt["id"], rnd)
    if not f:
        raise Blocked("该轮状态目录里没有可读文本文件")
    real_txt = _real_lines(t, tgt["id"], rnd, f["path"])
    real, _ = _norm_lines(real_txt)
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    _goto_diff(t, p, tgt["id"], rnd)
    ok = p.evaluate("""path => { const el = [...document.querySelectorAll('#h-tree [data-file]')]
        .find(e => e.dataset.file === path); if (el) { el.click(); return true; } return false; }""", f["path"])
    if not ok:
        raise Fail(f"左树里找不到 {f['path']} | {t.where(p, '#h-tree')}")
    t.wait(p, f"() => (document.querySelector('#h-editor .diff-head') || {{}}).innerText.includes({json.dumps(f['path'])})",
           "文件视图标题")
    ed = t.editor(p)
    _, ui = _norm_lines(real_txt, ed["texts"])
    if len(ui) != len(real):
        raise Fail(f"{f['path']}: 渲染 {len(ui)} 行 != 真实 {len(real)} 行 | "
                   f"url={p.url} sel=#h-editor .ln dom={trunc(ed['head'])}")
    return f"{f['path']}：渲染 {len(ui)} 行 = 真实 {len(real)} 行（{f['size']} B）"


@check("8-5", "8 diff", "文件视图内容逐行 = 真实文件", "S0")
def c8_5(t: UAT):
    tgt, rnd = t.samples.get("target"), t.samples.get("diffRound")
    if not tgt or rnd is None:
        raise Blocked("没有带 patch 的多轮 run")
    f = _pick_text_file(t, tgt["id"], rnd)
    if not f:
        raise Blocked("该轮状态目录里没有可读文本文件")
    real_txt = _real_lines(t, tgt["id"], rnd, f["path"])
    real, _ = _norm_lines(real_txt)
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    _goto_diff(t, p, tgt["id"], rnd)
    ok = p.evaluate("""path => { const el = [...document.querySelectorAll('#h-tree [data-file]')]
        .find(e => e.dataset.file === path); if (el) { el.click(); return true; } return false; }""", f["path"])
    if not ok:
        raise Fail(f"左树里找不到 {f['path']} | {t.where(p, '#h-tree')}")
    t.wait(p, f"() => (document.querySelector('#h-editor .diff-head') || {{}}).innerText.includes({json.dumps(f['path'])})",
           "文件视图标题")
    ed = t.editor(p)
    _, ui = _norm_lines(real_txt, ed["texts"])
    if len(ui) != len(real):
        raise Fail(f"{f['path']}: 渲染 {len(ui)} 行 != 真实 {len(real)} 行 | url={p.url}")
    idx = next((i for i, (a, b) in enumerate(zip(ui, real)) if a != b), None)
    if idx is not None:
        raise Fail(f"{f['path']} 第 {idx+1} 行不一致：页面 {trunc(ui[idx], 80)!r} != 文件 {trunc(real[idx], 80)!r} | "
                   f"url={p.url} sel=#h-editor .ln:nth-of-type({idx+1})")
    return f"{f['path']}：{len(real)} 行逐字一致（{f['size']} B）"


@check("8-6", "8 diff", "无 patch 的轮次给出说明，不空白", "S3")
def c8_6(t: UAT):
    tgt, bare = t.samples.get("target"), t.samples.get("bareRound")
    if not tgt:
        raise Blocked("没有多轮 run")
    if bare is None:
        return "该 run 每轮都有 patch，本项无处触发"
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    p.evaluate("() => document.querySelector('a[data-view-link=harness]').click()")
    t.wait(p, "() => [...document.querySelectorAll('#h-run option')].length > 1", "run 下拉")
    t.wait(p, "() => [...document.querySelectorAll('#h-round option')].some(o => Number(o.value) === 0)",
           "轮次下拉有 r0")
    if not _pick_round(t, p, tgt["id"], bare):
        raise Fail(f"选不到没有 patch 的 r{bare} | {t.where(p, '#h-round')}")
    # _pick_round 只确认了 select 的值；编辑器是另一段异步渲染。这里显式等 diff 头
    # 写着 round-N、空态连续两次读数一致，再取文本 —— 不靠固定 sleep。
    settled = t.stable(p, f"""() => {{
        const sel = document.querySelector('#h-round');
        const ed = document.querySelector('#h-editor');
        const head = ed && ed.querySelector('.diff-head');
        const title = ed && ed.querySelector('.empty-title');
        return {{val: sel ? Number(sel.value) : null,
                 head: head ? head.innerText.replace(/\\s+/g,' ').trim() : '',
                 title: title ? title.innerText.trim() : ''}};
    }}""", samples=3, timeout_ms=6000)
    if settled["val"] != bare or f"round-{bare}" not in settled["head"]:
        raise Fail(f"选了 r{bare}，编辑器却停在 {settled} | url={p.url} sel=#h-round")
    ed = t.editor(p)
    note = "；".join(ed["note"]) or trunc(ed["head"])
    if not ed["note"] and (ed["add"] or ed["del"] or ed["total"]):
        raise Fail(f"r{bare} 没有 patch 却渲染了改动内容 | url={p.url} sel=#h-editor dom={trunc(ed['head'])}")
    if "没有 diff" not in note and "未记录" not in note and "不存在" not in note:
        raise Fail(f"r{bare} 没有明确说明“这一轮没有 diff” | url={p.url} sel=#h-editor dom={trunc(note)}")
    return f"r{bare}：{trunc(note)}"


@check("8-7", "8 diff", "文件接口拒绝路径穿越", "S0")
def c8_7(t: UAT):
    tgt, rnd = t.samples.get("target"), t.samples.get("diffRound")
    if not tgt or rnd is None:
        raise Blocked("没有带 state 的多轮 run")
    base = f"/api/runs/{tgt['id']}/rounds/{rnd}/file?path="
    probes = ["../../run_meta.json", "..%2F..%2Frun_meta.json", "../run_meta.json",
              "/etc/passwd", "....//....//run_meta.json"]
    leaked = []
    for q in probes:
        status, body = t.raw_get(base + q)
        bad = status == 200 and body.strip()
        if bad:
            leaked.append((q, status, trunc(body, 80)))
    # 顺带测 run id 与静态目录
    for q in ("/api/runs/..%2F..%2Fetc", "/%2e%2e/server.py", "/../server.py"):
        status, body = t.raw_get(q)
        if status == 200 and b"#!/usr/bin/env" in body:
            leaked.append((q, status, trunc(body, 80)))
    if leaked:
        q, status, body = leaked[0]
        raise Fail(f"穿越被放行：{q} → {status} {body} | url={t.url}{base}{q}")
    return f"{len(probes)+3} 个穿越探测全部非 200/空体"


# ═══════════════════════════════════════════════════════ 场景 10：轨迹
@check("10-1", "10 轨迹", "轨迹/判定条数 = 真实 jsonl/json 文件数", "S1")
def c10_1(t: UAT):
    tgt, rnd = t.samples.get("target"), t.samples.get("diffRound")
    if not tgt or rnd is None:
        raise Blocked("没有多轮 run")
    run = t.runs_root / tgt["id"]
    tr = len(list((run / f"round{rnd}_traces").glob("*.jsonl"))) if (run / f"round{rnd}_traces").is_dir() else 0
    vd = len(list((run / "verdicts" / f"round-{rnd}").glob("*.json"))) if (run / "verdicts" / f"round-{rnd}").is_dir() else 0
    p = t.open("/#/runs")
    t.wait(p, "() => !!document.querySelector('#runs-table tbody tr[data-run]')", "study 行")
    p.evaluate("() => document.querySelector('a[data-view-link=harness]').click()")
    t.wait(p, "() => [...document.querySelectorAll('#h-run option')].length > 1", "run 下拉")
    _goto_diff(t, p, tgt["id"], rnd)
    h = t.harness(p)
    kv = h["kv"]
    cell = kv.get("轨迹 / 判定", "")
    m = re.match(r"(\d+)\s*条\s*/\s*(\d+)\s*条", cell)
    if not m:
        raise Fail(f"右栏没有“轨迹 / 判定”计数（kv={list(kv)}）| url={p.url} sel=#h-explain dom={trunc(h['kv'])}")
    got_tr, got_vd = int(m.group(1)), int(m.group(2))
    if (got_tr, got_vd) != (tr, vd):
        raise Fail(f"页面 {got_tr}/{got_vd} 条 != 磁盘 {tr}/{vd} | url={p.url} sel=#h-explain dl.kv dom={trunc(cell)}")
    if f"{tr} 条" not in h["tree"]:
        raise Fail(f"左树没显示 {tr} 条轨迹 | url={p.url} sel=#h-tree dom={trunc(h['tree'])}")
    return f"r{rnd}: 页面轨迹 {got_tr} / 判定 {got_vd} = 磁盘 {tr}/{vd}；左树也写了 {tr} 条"


# ─────────────────────────────────────────────────────── 轨迹视图（/trace.html）
def _trace_sample(t: UAT) -> dict:
    s = t.samples.get("trace")
    if not s:
        raise Blocked("真实数据里没有带轨迹的 study run/round")
    return s


def _open_trace(t: UAT, run_id: str, rnd: int):
    """打开轨迹页，等清单与自动打开的首条轨迹都落地。

    页面在 loadTasks 末尾会自动 openTask(第一条)；不等它落地就点别的任务，
    两个 fetch 会抢着写 #vhead，读到的可能不是点的那条。这是等界面，不是等时间。
    """
    p = t.open(f"/trace.html?run={urllib.parse.quote(run_id)}&round={rnd}")
    t.wait(p, "() => document.querySelectorAll('#tasks .task').length > 0", "任务清单")
    t.wait(p, "() => { const tid = document.querySelector('#vhead .tid');"
             " return !!(tid && tid.textContent.trim()); }", "首条轨迹渲染")
    return p


def _click_task(t: UAT, page, name: str, task_id: str):
    ok = page.evaluate("""name => { const b = [...document.querySelectorAll('#tasks .task')]
        .find(x => x.dataset.name === name); if (b) { b.click(); return true; } return false; }""", name)
    if not ok:
        raise Fail(f"任务清单里找不到 {name} | {t.where(page, '#tasks')}")
    t.wait(page, f"() => (document.querySelector('#vhead .tid') || {{}}).textContent.trim()"
                 f" === {json.dumps(task_id)}", f"头部换成 {task_id[:40]}")


@check("10-2", "10 轨迹", "清单条数 = 接口 count；选中任务步骤卡数 = 真实 jsonl 的 step 事件", "S1")
def c10_2(t: UAT):
    s = _trace_sample(t)
    api = t.api(f"/api/runs/{s['run']}/rounds/{s['round']}/traces")
    p = _open_trace(t, s["run"], s["round"])
    got = t.stable(p, "() => document.querySelectorAll('#tasks .task').length", samples=2)
    if got != api["count"]:
        raise Fail(f"清单 {got} 条 != 接口 count {api['count']} | url={p.url} sel=#tasks")
    chip = t.stable(p, "() => (document.querySelector('#count') || {}).textContent.trim()")
    m = re.match(r"(\d+)\s*/\s*(\d+)\s*条\s*·\s*r(\d+)", chip or "")
    if not m or (int(m.group(1)), int(m.group(2)), int(m.group(3))) != (
            api["count"], api["count"], s["round"]):
        raise Fail(f"计数 chip {chip!r} != 清单/接口（应 {api['count']} / {api['count']} 条 · "
                   f"r{s['round']}）| url={p.url} sel=#count")

    chosen = next((x for x in api["tasks"] if x["name"] == s["steps_task"]), None)
    if not chosen:
        raise Fail(f"接口清单里没有选样任务 {s['steps_task']} | url={p.url}")
    _click_task(t, p, chosen["name"], chosen["task_id"])
    # 页面上真正的“步骤卡”：.step-no 写着 step N（identity 那张不算一步）
    ui = t.stable(p, """() => {
        const nums = [...document.querySelectorAll('#tl .step-card .step-no')]
            .map(e => e.textContent.trim())
            .filter(s => /^step\\s+\\d+$/.test(s))
            .map(s => Number(s.split(/\\s+/)[1]));
        return {cards: nums.length, distinct: new Set(nums).size,
                first: nums.length ? nums[0] : null,
                last: nums.length ? nums[nums.length - 1] : null};
    }""", samples=2)
    events, bad = _read_jsonl(
        t.runs_root / s["run"] / f"round{s['round']}_traces" / chosen["name"])
    n_reply = sum(1 for e in events if "reply" in e)
    n_cmd = sum(1 for e in events if "command" in e)
    n_step = sum(1 for e in events if "step" in e)
    want_cards = n_reply + n_cmd          # 每个 reply / command 各渲染一张步骤卡
    want_steps = len({e.get("step") for e in events if "reply" in e or "command" in e})
    if ui["cards"] != want_cards:
        raise Fail(f"渲染 {ui['cards']} 张步骤卡 != 真实 jsonl {n_reply} 个 reply + "
                   f"{n_cmd} 个 command = {want_cards} | url={p.url} sel=#tl .step-no "
                   f"dom={trunc(ui)}")
    if ui["distinct"] != want_steps:
        raise Fail(f"渲染 {ui['distinct']} 个不同步号 != 真实 jsonl {want_steps} 个"
                   f"（reply/执行事件的 step 去重）| url={p.url} sel=#tl .step-no")
    return (f"{chosen['task_id'][:44]}：{ui['cards']} 张步骤卡 = 磁盘 {n_reply} reply + "
            f"{n_cmd} 执行；去重 {ui['distinct']} 步 = {want_steps}；"
            f"jsonl 共 {n_step} 行带 step 字段、坏行 {bad}")


@check("10-3", "10 轨迹", "只看失败 = 0 分任务；长输出有展开且展开后可见更多", "S2")
def c10_3(t: UAT):
    s = _trace_sample(t)
    api = t.api(f"/api/runs/{s['run']}/rounds/{s['round']}/traces")
    p = _open_trace(t, s["run"], s["round"])
    p.click("button[data-filter=fail]")
    t.wait(p, "() => document.querySelector('button[data-filter=fail]')"
             ".getAttribute('aria-pressed') === 'true'", "只看失败")
    shown = t.stable(p, "() => [...document.querySelectorAll('#tasks .task')]"
                        ".map(b => b.dataset.name)", samples=2)
    want = sorted(x["name"] for x in api["tasks"] if x["score"] == 0)
    if sorted(shown) != want:
        raise Fail(f"“只看失败” {len(shown)} 条 != 接口 0 分任务 {len(want)} 条"
                   f"（多 {sorted(set(shown) - set(want))[:3]} 少 "
                   f"{sorted(set(want) - set(shown))[:3]}）| url={p.url} sel=#tasks .task")
    chip = t.stable(p, "() => (document.querySelector('#count') || {}).textContent.trim()")
    if (chip or "") != f"{len(want)} / {api['count']} 条 · r{s['round']}":
        raise Fail(f"筛选后计数 chip {chip!r} != {len(want)} / {api['count']} 条 · "
                   f"r{s['round']} | url={p.url} sel=#count")

    # 回到“全部”，再点长输出任务（选样任务是按磁盘最长 output 挑的）
    p.click("button[data-filter=all]")
    t.wait(p, "() => document.querySelector('button[data-filter=all]')"
             ".getAttribute('aria-pressed') === 'true'", "全部")
    t.wait(p, f"() => document.querySelectorAll('#tasks .task').length === {api['count']}",
           "清单恢复")
    chosen = next((x for x in api["tasks"] if x["name"] == s["long_task"]), None)
    if not chosen:
        raise Fail(f"接口清单里没有长输出任务 {s['long_task']} | url={p.url}")
    _click_task(t, p, chosen["name"], chosen["task_id"])

    folded = t.stable(p, """() => [...document.querySelectorAll('#tl .cmd [data-expand]')]
        .map(b => { const pre = b.closest('.cmd').querySelector('.out');
            return {label: b.textContent.trim(), h: pre.clientHeight, sh: pre.scrollHeight,
                    text: pre.scrollHeight > pre.clientHeight + 8 ? pre.textContent : ''}; })
        .filter(x => x.text)""", samples=2)
    if not folded:
        raise Fail(f"长输出任务 {chosen['task_id'][:44]} 没有“展开全部”控件 | "
                   f"url={p.url} sel=#tl .cmd [data-expand] dom={trunc(_body(p))}")
    target = max(folded, key=lambda x: (x["sh"], len(x["text"])))
    events, _bad = _read_jsonl(
        t.runs_root / s["run"] / f"round{s['round']}_traces" / chosen["name"])
    lm = re.search(r"(\d+)\s*行\s*/\s*(\d+)\s*字符", target["label"])
    if not lm:
        raise Fail(f"展开按钮没有“N 行 / M 字符”标注：{target['label']!r} | "
                   f"url={p.url} sel=button[data-expand]")
    want_lines, want_chars = int(lm.group(1)), int(lm.group(2))
    lines = chars = None
    for e in events:
        if "command" not in e:
            continue
        out = str(e.get("output") or "")
        # 服务端最多给 6000 字符；前端只去掉尾部空白，然后把**全文**放进 DOM
        raw = out[:6000].rstrip()
        if raw != target["text"]:
            continue
        parts = raw.split("\n")
        if lines is None:
            lines, chars = len(parts), len(out)
        if (want_lines, want_chars) == (len(parts), len(out)):
            lines, chars = len(parts), len(out)
            break
    if lines is None:
        raise Fail(f"折叠区文字（{len(target['text'])} 字符）!= 真实 jsonl 里任何一条 "
                   f"command output —— 页面没有如实渲染原文 | url={p.url} sel=#tl .cmd .out "
                   f"dom={trunc(target['text'], 160)}")
    if (want_lines, want_chars) != (lines, chars):
        raise Fail(f"展开按钮写 {target['label']!r} != 磁盘 {lines} 行 / {chars} 字符 | "
                   f"url={p.url} sel=button[data-expand]")
    before = {"h": target["h"], "sh": target["sh"], "len": len(target["text"])}
    if before["sh"] <= before["h"] + 8:
        raise Fail(f"选中的长输出在折叠态没有被裁（scrollHeight {before['sh']} <= "
                   f"clientHeight {before['h']}）| url={p.url}")
    hit = p.evaluate("""text => { for (const b of document.querySelectorAll('#tl .cmd [data-expand]')) {
        const pre = b.closest('.cmd').querySelector('.out');
        if (pre && pre.textContent === text) { b.click(); return true; } } return false; }""",
                     target["text"])
    if not hit:
        raise Fail(f"找不到要展开的那一段（{len(target['text'])} 字符）| url={p.url}")
    t.wait(p, "() => document.querySelectorAll('#tl .cmd .out[data-full=true]').length > 0",
           "展开")
    after = t.stable(p, """() => { const pre = document.querySelector('#tl .cmd .out[data-full=true]');
        return {h: pre.clientHeight, sh: pre.scrollHeight, len: pre.textContent.length}; }""",
                     samples=2)
    if after["h"] <= before["h"] + 8:
        raise Fail(f"展开后可见高度 {after['h']}px 没有比折叠的 {before['h']}px 更高 | "
                   f"url={p.url} sel=.out[data-full=true]")
    if after["sh"] > after["h"] + 2:
        raise Fail(f"展开后 scrollHeight {after['sh']} 仍大于 clientHeight {after['h']}，"
                   f"内容仍被裁 | url={p.url} sel=.out[data-full=true]")
    if after["len"] != before["len"]:
        raise Fail(f"展开后文字从 {before['len']} 字符变成 {after['len']} 字符 | url={p.url}")
    return (f"只看失败 {len(shown)} 条 = 接口 0 分 {len(want)} 条；长输出 {lines} 行/"
            f"{chars} 字符：折叠可见 {before['h']}/{before['sh']}px → "
            f"展开 {after['h']}/{after['sh']}px（{after['len']} 字符 = jsonl 原文）")


@check("10-4", "10 轨迹", "轨迹接口拒绝路径穿越（trace?name=../../run_meta.json）", "S0")
def c10_4(t: UAT):
    s = _trace_sample(t)
    base = f"/api/runs/{s['run']}/rounds/{s['round']}/trace?name="
    # 先证明正常名字打得开，否则“全部 404”会假装通过
    name = s["steps_task"]
    status, body = t.raw_get(base + urllib.parse.quote(name))
    if status != 200 or not body.strip():
        raise Fail(f"正常轨迹名 {name} 也打不开（{status}，{len(body)} B）| "
                   f"url={t.url}{base}{name}")
    normal = len(body)
    probes = ["../../run_meta.json", "..%2F..%2Frun_meta.json", "../run_meta.json",
              "/etc/passwd", "....//....//run_meta.json"]
    leaked = []
    for q in probes:
        status, body = t.raw_get(base + q)
        if status == 200:
            leaked.append((q, status, trunc(body, 80)))
    if leaked:
        q, status, body = leaked[0]
        raise Fail(f"穿越被放行：{q} → {status} {body} | url={t.url}{base}{q}")
    return f"正常名 200（{normal} B），{len(probes)} 个穿越探测全部非 200"


@check("10-5", "10 轨迹", "任务头部判定文案 = verdict 文件 detail", "S1")
def c10_5(t: UAT):
    s = _trace_sample(t)
    api = t.api(f"/api/runs/{s['run']}/rounds/{s['round']}/traces")
    chosen = next((x for x in api["tasks"] if x["name"] == s["steps_task"]), None)
    if not chosen:
        raise Fail(f"接口清单里没有选样任务 {s['steps_task']} | url={t.url}")
    task_id, _trial = _task_of(pathlib.Path(chosen["name"]).stem)
    verdict = _verdict_of(t, s["run"], s["round"], task_id)
    detail = verdict.get("detail")
    if not isinstance(detail, str) or not detail.strip():
        raise Fail(f"verdicts/round-{s['round']}/{task_id}.json 没有 detail（"
                   f"{t.runs_root / s['run']}）")
    p = _open_trace(t, s["run"], s["round"])
    _click_task(t, p, chosen["name"], chosen["task_id"])
    ui = t.wait(p, "() => { const d = document.querySelector('#vhead .detail');"
                   " return d ? d.textContent.trim() : ''; }", "头部判定文案")
    norm = lambda x: re.sub(r"\s+", " ", x).strip()   # noqa: E731
    want, got = norm(detail), norm(ui)
    if not got:
        raise Fail(f"头部没有判定文案（verdict.detail {len(detail)} 字）| "
                   f"url={p.url} sel=#vhead .detail")
    if not (got == want or want.startswith(got)):
        raise Fail(f"头部文案不是 verdict detail 的前缀：页面 {trunc(got, 120)!r} / "
                   f"文件 {trunc(want, 120)!r} | url={p.url} sel=#vhead .detail")
    if len(got) < min(len(want), 40):
        raise Fail(f"头部文案只有 {len(got)} 字，短到不足以核对 | url={p.url} "
                   f"dom={trunc(got)}")
    kind = "全文" if got == want else f"前 {len(got)} 字（全文 {len(want)} 字）"
    return f"{task_id[:44]}：页面 {len(got)} 字 = verdict detail {kind}（{trunc(got, 90)}）"


# ═══════════════════════════════════════════════════════ 阻塞的场景（占位，不执行）
BLOCKED = [
    ("2", "2 API 配置", "未配置时说明“只能读”", "S1", "需要 API 配置写入路径（只读台不做写操作）"),
    ("2", "2 API 配置", "错 key 有明确失败文案", "S1", "需要真实/错误 key 与一次外部调用"),
    ("2", "2 API 配置", "刷新后配置还在", "S2", "需要写 localStorage 的配置流程"),
    ("2", "2 API 配置", "清空后回到未配置", "S2", "同上"),
    ("12", "12 起跑演练", "≤10 s 出现 running", "S0", "测试台 C：会占 GPU/endpoint，且禁止起 driver"),
    ("12", "12 起跑演练", "结束后曲线多一个点", "S1", "同上"),
    ("12", "12 起跑演练", "失败是 failed 不是消失", "S1", "同上"),
]


# ═══════════════════════════════════════════════════════ 场景 13：覆盖矩阵
#: 已删除的判定项：它们判定的**屏**已经从 console 删掉了，所以连同判定项一起删。
#: 这不是放宽任何断言 —— 保留的 36 项一字未改，改的只是被删屏的入口。
#: 这一节会原样写进每次运行生成的记录，所以分母从 41 变 36 是可见、可核对的。
DELETED = [
    ("13-1", "13 覆盖", "覆盖矩阵 = /api/coverage：方法行数、头条 有效/应有、格子点到 run",
     "覆盖屏已删除：导航回到 实验 / 轨迹 / 版本，#/coverage 重定向到 #/runs。"
     "/api/coverage 仍在服务端但无屏读取；171/171 这个事实留在数据集卡与 HF 上。"),
    ("1-3", "1 冷启动", "对话是确定性的技能台：两张卡能开出、不调用模型、起跑/应用禁用",
     "对话屏 + 两张技能卡（造 harness / 提方法）已删除；导航只剩 实验 / 轨迹 / 版本。"),
    ("11-1", "11 方法页", "方法表行数/名字 = /api/methods",
     "方法页已删除（#/methods 现在重定向到 #/runs）；/api/methods 保留但不在任何屏上使用。"),
    ("11-2", "11 方法页", "“规则测试 有/缺”与磁盘一致",
     "同上：方法表不存在了，判定的那几列也随之不存在。"),
    ("11-3", "11 方法页", "只读：无起跑控件、全程无非 GET 请求",
     "同上。只读边界仍由 S0 项（8-7 / 10-4）与 `server.py` 无 do_POST 覆盖。"),
    ("11-4", "11 方法页", "方法表只列真实方法（不含 __pycache__ 等）",
     "同上：没有方法表可查。/api/methods 的过滤仍在服务端，留着以后重接。"),
]


# ─────────────────────────────────────────────────────── 跑
def run(t: UAT, holder: dict | None = None, out: str | None = None,
        write: bool = True) -> int:
    """跑一遍全部判定项。`write=False` 时先不写记录（--repeat 只在最后一轮写一次）。"""
    holder = holder or {"sig": 0}
    aborted = 0
    for c in CHECK_LIST:
        if holder.get("sig"):
            aborted = holder["sig"]
            print(f"· 中止：还剩 {len(CHECK_LIST) - len(t.results)} 项没跑", flush=True)
            break
        t0 = time.time()
        mark = len(t.contexts)
        res, ev = "过", ""
        try:
            ev = c.fn(t) or ""
        except Blocked as exc:
            res, ev = "阻塞", str(exc)
        except AssertionError as exc:
            res, ev = "不过", str(exc)
            shot = t.shot(t.last_page, c.sid) if t.last_page else ""
            if shot:
                ev += f" | shot={shot}"
        except Exception as exc:  # noqa: BLE001
            res = "不过"
            tail = traceback.format_exc().strip().splitlines()[-1]
            ev = f"{type(exc).__name__}: {exc} | {tail}"
            shot = t.shot(t.last_page, c.sid) if t.last_page else ""
            if shot:
                ev += f" | shot={shot}"
        t.close_since(mark)
        dt_s = time.time() - t0
        t.results[c.sid] = {"check": c, "res": res, "ev": ev, "s": dt_s}
        mark = {"过": "PASS", "不过": "FAIL", "阻塞": "BLOCK"}[res]
        line = f"{mark} {c.sid} [{c.sev}] {c.scenario} · {c.item}"
        if res == "过":
            print(f"{line} — {trunc(ev, 160)}", flush=True)
        else:
            print(f"{line}\n     → {trunc(ev, 400)}", flush=True)

    # 全局兜底：任何地方出现过 console error / pageerror，都记到 1-1 头上（只看本轮）
    errs = [e for e in t.console[t.console_from:] if e["kind"] in ("console", "pageerror")]
    r11 = t.results.get("1-1")
    if errs and r11 and r11["res"] == "过":
        e = errs[0]
        r11.update({"res": "不过",
                    "ev": f"{e['kind']}: {trunc(e['text'])} | url={e['url']}（本轮共 {len(errs)} 条）"})
        print(f"FAIL 1-1 [S1] (后置复核) 本轮出现控制台报错 → {trunc(e['text'], 300)}", flush=True)

    # 重复运行的稳定性：本轮每项的结果都进 history（write_record 要用）
    for sid, v in t.results.items():
        t.history.setdefault(sid, []).append(v["res"])

    # 汇总
    auto = [v for v in t.results.values() if v["res"] != "阻塞"]
    passed = [v for v in auto if v["res"] == "过"]
    blocked = len(t.results) + len(BLOCKED) - len(auto)
    hard = [v for v in auto if v["res"] == "不过" and v["check"].sev in ("S0", "S1")]
    if write or aborted:
        record = write_record(t, auto, passed, blocked, out=out,
                              partial=dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
                              if aborted else "")
    else:
        record = "（等最后一轮写完再写）"
    if aborted:
        print(f"· 被信号 {aborted} 中止：记录只覆盖已跑的 {len(auto)} 项", flush=True)
    rate = (100.0 * len(passed) / len(auto)) if auto else 0.0
    print(f"\n过/总 = {len(passed)}/{len(auto)} ({rate:.0f}%) · FAIL {len(auto) - len(passed)} · "
          f"BLOCK {blocked} · S0/S1 不过 {len(hard)} · 记录 {record}")
    for v in auto:
        if v["res"] == "不过":
            print(f"  {v['check'].sev} {v['check'].sid} {v['check'].item} — {trunc(v['ev'], 120)}")
    if aborted:
        return 130
    return 1 if hard else 0


def write_record(t: UAT, auto, passed, blocked: int, partial: str = "",
                 out: str | None = None) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    day = now.strftime("%Y-%m-%d")
    if out:
        target = pathlib.Path(out)
    else:
        target = HERE / (f"uat-{day}.md" if not partial
                         else f"uat-{day}-partial-{partial}.md")
    order = {sid: i for i, sid in enumerate(x.sid for x in CHECK_LIST)}
    rows = sorted(auto, key=lambda v: (order[v["check"].sid],))
    lines = [
        f"# UAT 记录 — {t.commit} — {day} — uat/smoke.py（自动，测试台 B）",
        *([] if not partial else ["> **这次运行被信号中止，本文件只覆盖已跑完的判定项，不是完整记录。**", ""]),
        "",
        "> 自动生成：`python3 uat/smoke.py --port 4470`（或 `--url`）"
        + (f"，本次 `--repeat {t.repeat_total}`" if t.repeat_total > 1 else "")
        + "。判定项原文取自 `hg-mockup/UAT_STANDARD.md §2`，只保留能在**只读实测**下判定的部分；"
        "需要 API 配置 / 起跑窗口的判定项记“阻塞”，不计入通过率。",
        "",
        "## 环境",
        "",
        "| | |",
        "| --- | --- |",
        f"| 版本 / commit | `{t.commit}`（server.py + 四个静态文件的 sha256 前 10 位） |",
        f"| 文件指纹 | `{trunc(t.version, 400)}` |",
        "| 测试台 | B 只读实测（不起 driver、不发 POST） |",
        f"| 浏览器 + 版本 | Chromium {t.browser.version if t.browser else '?'} / Playwright（headless） |",
        "| 屏幕分辨率 | 1440 × 900 |",
        f"| 被测地址 | `{t.url}`（{'由本脚本起在 --port 上，跑完即关' if t.local_server else '--url 指向已有实例，未启停'}） |",
        f"| runs 根目录 | `{t.runs_root}`（{len(t.samples.get('all', {}).get('rows', []))} 个 run 可见） |",
        f"| 时间（UTC） | {now.strftime('%Y-%m-%d %H:%M:%S')} |",
        f"| 在跑的 driver | {len((t.health or {}).get('running') or [])} 个（读取 `/proc`，未启停） |",
        *([f"| 重复运行 | {t.repeat_total} 轮（同一套判定项连跑，看逐项是否稳定） |"]
          if t.repeat_total > 1 else []),
        "",
        "## 场景结果",
        "",
        "| 场景 | 判定项 | 结果 | 严重度 | 证据 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for v in rows:
        c = v["check"]
        lines.append(f"| {c.scenario} | {md_cell(c.item, 80)} | {v['res']} | {c.sev} | {md_cell(v['ev'])} |")
    rate = (100.0 * len(passed) / len(auto)) if auto else 0.0
    lines += [
        "",
        "## 通过率",
        "",
        f"`过/总 = {len(passed)}/{len(auto)} ({rate:.0f}%)`",
        "",
        f"（另有 {blocked} 项阻塞未计入；阻塞项见下表。）",
        "",
        "## 阻塞（本版无法自动化）",
        "",
        "| 场景 | 判定项 | 原因 |",
        "| --- | --- | --- |",
    ]
    for sid, scen, item, sev, why in BLOCKED:
        lines.append(f"| {scen} | {md_cell(item, 60)} | {md_cell(why)} |")
    lines += [
        "",
        "## 已删除的判定项（屏已删除，不是放宽）",
        "",
        "这些判定项连同它们判定的屏一起删掉了：保留的判定项没有一条被改弱。",
        "",
        "| 判定项 | 场景 | 原判定 | 为什么删 |",
        "| --- | --- | --- | --- |",
    ]
    for sid, scen, item, why in DELETED:
        lines.append(f"| `{sid}` | {scen} | {md_cell(item, 60)} | {md_cell(why)} |")
    if t.repeat_total > 1:
        lines += ["", f"## 重复运行稳定性（{t.repeat_total} 轮）", "",
                  "| 判定项 | 过 | 不过 | 阻塞 | 结论 |",
                  "| --- | --- | --- | --- | --- |"]
        unstable = 0
        for c in CHECK_LIST:
            hist = t.history.get(c.sid)
            if not hist:
                continue
            n_ok, n_bad, n_bl = hist.count("过"), hist.count("不过"), hist.count("阻塞")
            verdict = "稳定" if len(set(hist)) <= 1 else "**不稳定**"
            unstable += verdict != "稳定"
            lines.append(f"| `{c.sid}` {md_cell(c.item, 50)} | {n_ok} | {n_bad} | {n_bl} | {verdict} |")
        lines += ["", f"（{len(t.history)} 项有重复记录，其中 {unstable} 项结果不稳定；"
                  f"另有 {len(BLOCKED)} 项固定阻塞不参与重复判定。）"]
    lines += ["", "## 按严重度排序的发现", ""]
    fails = [v for v in rows if v["res"] == "不过"]
    for sev in ("S0", "S1", "S2", "S3"):
        for v in fails:
            if v["check"].sev == sev:
                lines.append(f"- **{sev}** `{v['check'].sid}` {v['check'].scenario} · "
                             f"{v['check'].item} — {md_cell(v['ev'], 300)}")
    if not fails:
        lines.append("- 无（全部自动化判定项通过）")
    lines += [
        "",
        "## 与另一位测试人的分歧",
        "",
        "不适用：本文件由脚本单人跑出，没有第二个测试人；分歧一节留给人工复核。",
        "",
        "## 手工补充（自动检查之外，本次观察）",
        "",
        *(["- 分数旁的 harness sha " + ("是可点的 `<a>`。" if t.obs.get("rail_sha_link")
            else "是纯文本（`td.row-id`），没有 `<a>`；能点的是整行，点下去会跳到该轮的 diff"
                 "（可点路径在，但 sha 本身不可聚焦）。")] if "rail_sha_link" in t.obs else []),
        *([f"- 在改 harness 里选中 run + 轮次后，URL 是 `{t.obs['harness_hash']}`"
           + ("：run/轮次只存在内存里，刷新会回到默认 run 的最后一轮，"
              "所以没法把“某一轮的 diff”发给别人。" if t.obs.get("harness_hash") == "#/harness"
              else "：run/轮次已经写进 URL，可以分享。")] if t.obs.get("harness_hash") else []),
        *(["- `GET /api/health` 的 `running[].elapsed_s` 是**进程开始时间（自开机秒数）**，"
           "不是已跑时长：" + "、".join(
               f"`{rid}` 报 {sec} s（≈{sec / 86400:.1f} d）" for rid, sec in t.samples["live_elapsed"])
           + f"；本机 `/proc/uptime` 是 {pathlib.Path('/proc/uptime').read_text().split()[0]} s，"
             "`ps -o etime` 显示只有几分钟。复现：`curl -s " + t.url + "/api/health`。"
             "`server.py` 读了 `/proc/<pid>/stat` 第 22 项却没减去开机时间"
             "（只记录，未改应用）。"]
          if t.samples.get("live_elapsed") else []),
        "",
        "## 自动化说明（与手工记录的差别）",
        "",
        "- 一次运行覆盖四屏（实验 / 覆盖 / 轨迹 / 版本）：冷启动、实验列表筛选、实验详情/曲线、"
        "深链接、diff/文件视图、轨迹计数与轨迹浏览视图（`/trace.html`：清单条数、步骤卡数、"
        "只看失败、长输出展开、判定文案、路径穿越）、只读边界。",
        "- 轨迹浏览视图的数字来自**磁盘上的 jsonl / verdicts**（`--runs-root`），"
        "不是从页面反推：步骤卡数对 reply+command 事件、步号去重对 reply 事件、"
        "展开按钮的行数/字符数对同一条 command output、判定文案对 verdict 的 `detail`。",
        "- `running` / `invalid` 两个状态用 Playwright 拦截 `/api/runs*` 的载荷构造"
        "（**没有**起 driver、没有写 runs 目录），因为真实数据此刻没有 running 行；"
        "证据里注明“拦截载荷”。",
        "- 证据里的 DOM 文本截断到 200 字符；失败时另存截图与 `console.log` 到 "
        "`uat/evidence/`。",
        "- 不判定分数是否正确、方法是否有效、性能（见标准 §7）。",
        "",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    (t.evidence / "console.log").write_text(
        "\n".join(f"{e['url']}\t{e['kind']}\t{e['text']}"
                  for e in t.console[t.console_from:]) or "(no console errors)\n",
        encoding="utf-8")
    try:
        return str(target.resolve().relative_to(CONSOLE))
    except ValueError:
        return str(target)


# ─────────────────────────────────────────────────────── 服务器生命周期
def port_free(port: int) -> bool:
    """有没有**活着的**服务器在听这个端口（TIME_WAIT 不算占用）。"""
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def port_holder(port: int) -> str:
    """谁占着端口：只读 /proc，不 kill。"""
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = [a.decode("utf-8", "replace")
                    for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if any(a.endswith("server.py") for a in args) and str(port) in args:
            return f"pid {entry.name}（{pathlib.Path(args[0]).name} server.py --port {port}）"
    return "未知进程"


def start_server(port: int) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, str(SERVER_PY), "--port", str(port)],
                            cwd=str(CONSOLE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise SystemExit(f"console 启动失败（exit {proc.returncode}）：{trunc(out, 400)}")
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    proc.terminate()
    raise SystemExit("console 15 s 内没有起来")


def stop_server(proc: subprocess.Popen | None):
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass


def print_stability(t: UAT) -> int:
    """逐项打印 --repeat 的结果分布；返回不稳定的项数。"""
    unstable = 0
    print(f"\n· 稳定性（{t.repeat_total} 轮 × {len(CHECK_LIST)} 项自动判定）", flush=True)
    for c in CHECK_LIST:
        hist = t.history.get(c.sid) or []
        if not hist:
            print(f"  --   {c.sid} 未跑（被中止或首轮就退出）", flush=True)
            continue
        counts = " ".join(f"{r}×{hist.count(r)}" for r in ("过", "不过", "阻塞") if hist.count(r))
        if len(set(hist)) > 1:
            unstable += 1
            print(f"  !!   {c.sid} {counts} — 结果不稳定", flush=True)
        else:
            print(f"  ok   {c.sid} {counts}", flush=True)
    print(f"· 有重复记录的 {len(t.history)} 项里，稳定 {len(t.history) - unstable}、"
          f"不稳定 {unstable}", flush=True)
    return unstable


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="hg-console UAT 冒烟（只读实测）")
    ap.add_argument("--port", type=int, default=4470, help="自己起 console 的端口（默认 4470）")
    ap.add_argument("--url", default=None, help="测一个已经在跑的 console，例如 http://127.0.0.1:4450")
    ap.add_argument("--runs-root", default=str(DEFAULT_RUNS), help="真实 runs 目录（对照 patch/文件用）")
    ap.add_argument("--timeout", type=int, default=20000, help="单次等待毫秒（默认 20000）")
    ap.add_argument("--headed", action="store_true", help="显示浏览器（调试用）")
    ap.add_argument("--out", default=None, help="记录写到哪（默认 uat/uat-<UTC 日期>.md）")
    ap.add_argument("--repeat", type=int, default=1,
                    help="整套判定项连跑 N 轮，末尾打印逐项稳定性（默认 1）")
    args = ap.parse_args(argv)

    if args.repeat < 1:
        ap.error("--repeat 至少为 1")
    if args.url and args.port != 4470:
        ap.error("--url 与 --port 只能二选一")

    urls = args.url or f"http://127.0.0.1:{args.port}"
    proc = None
    if not args.url:
        if not port_free(args.port):
            print(f"端口 {args.port} 已被占用（{port_holder(args.port)}）；换一个 --port，"
                  f"或用 --url 指向已有的 console。", file=sys.stderr)
            return 2
        if not DEFAULT_RUNS.is_dir() and not pathlib.Path(args.runs_root).is_dir():
            print(f"runs 根目录不存在：{args.runs_root}", file=sys.stderr)
            return 2
        proc = start_server(args.port)

    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
    evidence = HERE / "evidence" / day / stamp
    evidence.mkdir(parents=True, exist_ok=True)

    # Ctrl-C / SIGTERM：先把**自己起的**服务器关掉，再让人退出。
    # 只靠 finally 不够——信号可能在 Playwright 的阻塞调用里落地，清理路径会被跳过。
    holder = {"proc": proc, "sig": 0}

    def _bye(signum, frame):  # noqa: ARG001
        if holder["sig"]:  # 第二次：立刻走
            print(f"· 第二个信号 {signum}：立即退出", flush=True)
            stop_server(holder["proc"])
            holder["proc"] = None
            os._exit(128 + signum)
        holder["sig"] = signum
        print(f"· 收到信号 {signum}：跑完当前检查就退出（再按一次立刻退出）", flush=True)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, _bye)
        except (ValueError, OSError):
            pass

    from playwright.sync_api import sync_playwright

    t = UAT(urls, pathlib.Path(args.runs_root), args.timeout, evidence,
            headed=args.headed, local_server=not args.url)
    t.repeat_total = args.repeat
    codes: list[int] = []
    try:
        with sync_playwright() as pw:
            t.pw = pw
            t.browser = pw.chromium.launch(headless=not args.headed)
            try:
                t.prepare()
            except Fail as exc:
                print(f"FAIL 0-0 [S0] 环境准备 — {exc}", flush=True)
                return 1
            print(f"· {t.commit} · {urls} · runs={t.runs_root} · "
                  f"study={t.samples['study']['count']} eval={t.samples['eval']['count']} "
                  f"live_drivers={len(t.health.get('running') or [])}"
                  + (f" · repeat={args.repeat}" if args.repeat > 1 else ""), flush=True)
            for i in range(args.repeat):
                if holder.get("sig"):
                    break
                t.results = {}
                t.obs = {}
                t.console_from = len(t.console)
                t.evidence = evidence if args.repeat == 1 else evidence / f"r{i + 1}"
                t.evidence.mkdir(parents=True, exist_ok=True)
                if args.repeat > 1:
                    print(f"\n· 第 {i + 1}/{args.repeat} 轮", flush=True)
                codes.append(run(t, holder, args.out, write=(i == args.repeat - 1)))
                if holder.get("sig"):
                    break
            if args.repeat > 1 and codes:
                print_stability(t)
    finally:
        for ctx in t.contexts:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        if t.browser:
            try:
                t.browser.close()
            except Exception:  # noqa: BLE001
                pass
        stop_server(holder["proc"])
        holder["proc"] = None
        if not args.url and not port_free(args.port):
            print(f"警告：端口 {args.port} 还没释放", file=sys.stderr)
    if not codes:
        return 130
    # 任一轮 S0/S1 不过 → 失败；有中止 → 中止码；否则 0
    return max(codes)


if __name__ == "__main__":
    raise SystemExit(main())
