"""Read-only explanation inputs, credential-safe transport and one cache owner.

Input/lookup/config/chat ports are explicit. ExplainCache alone owns memory,
recency ordering, synchronization and optional disk persistence for one app.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
if __package__:
    from .record_reader import as_int, as_num
    from .run_query import _task_of
else:
    from record_reader import as_int, as_num
    from run_query import _task_of

EXPLAIN_PATCH_BYTES = 40 * 1024

EXPLAIN_ZERO_TASKS = 3

EXPLAIN_LAST_EVENTS = 3

EXPLAIN_EVENT_TYPES = {"harness_identity", "usage", "wall_clock_s", "context_trims"}

EXPLAIN_TIMEOUT_S = 60.0

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

EXPLAIN_RANGE_SYSTEM = """你是 HarnessGrad 实验台的只读解释器。用户会给你一次实验里连续几轮\
分数、每轮的改动摘要（文件名与增删行数），以及首尾两轮的 patch 片段。

你的任务：判断这段时间的改动方向——在收敛（每轮更小、更具体）、在震荡（来回改同一处）、
还是在退化（越改越散、或分数下降）。
规则：
1. 只使用用户提供的内容。没有给出的分数或日志不要猜；证据不足就直接说证据不足。
2. 只输出一个 JSON 对象。不要 markdown 代码块，不要前后说明。
3. JSON 必须且只能有这四个键：
   - "claim": 一句中文，说明这段时间的走向（收敛 / 震荡 / 退化 / 证据不足）。
   - "evidence": 2 到 4 条中文短句，每条引用具体的轮次、分数或改动。
   - "risk": 一句中文，说明这个走向继续下去最可能坏在哪。
   - "next": 一句中文，建议一次值得跑的对照或一次该停下来的检查。
4. 所有字符串用中文。不要编造数字。
"""

class ExplainNoPatch(Exception):
    """这一轮没有 patch：没有可解释的改动。"""


class ExplainUpstream(Exception):
    """模型端点返回了错误 / 非 JSON。"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _clip(value, n: int) -> str:
    text = str(value if value is not None else "")
    return text if len(text) <= n else text[:n] + "…"


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


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


def explain_range_prompt(inputs: dict) -> tuple[str, str]:
    lines = [f"- r{row['round']}: score={row['score']} · +{row['added']}/-{row['removed']} · "
             f"{', '.join(row['files'][:6]) or '（没有文件改动）'}" for row in inputs["rows"]]
    parts = ["## 这段时间的事实（来自 run 目录）",
             f"run: {inputs['run_id']}", f"区间: r{inputs['from']} → r{inputs['to']}",
             "每轮：", *lines, ""]
    for n, text in inputs["patches"].items():
        parts += [f"## r{n} 的 patch 片段（前 {len(text)} 字符）", text, ""]
    return EXPLAIN_RANGE_SYSTEM, "\n".join(parts)


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


def _chat_completion(base_url: str, model: str, api_key: str,
                     system: str, user: str, *, timeout_s=EXPLAIN_TIMEOUT_S) -> str:
    """一次 OpenAI 兼容的 /chat/completions 调用。key 只在 header 里，不写日志。"""
    url = base_url.rstrip("/") + "/chat/completions"
    # Require JSON mode; endpoint rejection must not trigger a format fallback.
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        })
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        # Only a valid numeric HTTP status is safe to expose; neither the response
        # body nor exception text is trusted to omit URL credentials or tokens.
        code = exc.code
        if type(code) is int and 100 <= code <= 599:
            raise ExplainUpstream(f"模型端点 HTTP {code}") from None
        raise ExplainUpstream("模型端点 HTTP 错误") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
        raise ExplainUpstream("模型端点请求失败") from None
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


def explain_config(environment) -> tuple[str, str, str, list[str]]:
    """(base_url, model, api_key, missing_names)。只读 env，绝不把值写进日志或响应。"""
    values = [environment.get(name, "").strip() for name in EXPLAIN_ENV]
    missing = [name for name, value in zip(EXPLAIN_ENV, values) if not value]
    return values[0], values[1], values[2], missing


@dataclass(frozen=True)
class InputLimits:
    patch_bytes: int = EXPLAIN_PATCH_BYTES
    zero_tasks: int = EXPLAIN_ZERO_TASKS
    last_events: int = EXPLAIN_LAST_EVENTS
    event_types: frozenset = frozenset(EXPLAIN_EVENT_TYPES)


class ExplainInputBuilder:
    def __init__(self, *, read_curve, read_json, verdict, limits):
        self.read_curve, self.read_json, self.verdict = read_curve, read_json, verdict
        self.limits = limits

    def _last_events(self, path: pathlib.Path, n: int) -> list[dict]:
        """最后 n 个**有内容**的事件。

        轨迹末尾常常是 usage / wall_clock 这种记账事件；它们不是失败模式。跳过它们，
        否则模型看到的“最后 3 个事件”全是 tokens 数。仍然最多只发 n 个。
        """
        events = _read_jsonl_tail(path)
        picked: list[dict] = []
        for ev in reversed(events):
            if any(key in ev for key in self.limits.event_types):
                continue
            picked.append(ev)
            if len(picked) >= n:
                break
        picked.reverse()
        return picked


    def _zero_tasks(self, path: pathlib.Path, n: int, point: dict) -> list[dict]:
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
                    if as_num(self.read_json(f).get("score")) == 0:
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
            verdict = self.verdict(path, task_id, n)
            events = [_compact_event(ev) for ev in self._last_events(trace, self.limits.last_events)]
            out.append({
                "task_id": task_id,
                "score": 0.0,
                "kind": str(verdict.get("kind") or ""),
                "detail": _clip(verdict.get("detail") or "", 500),
                "note": "最后几个事件（已裁剪）；不是完整轨迹" if events else "没有可读事件",
                "events": events,
            })
            if len(out) >= self.limits.zero_tasks:
                break
        return out


    def build_explain_inputs(self, path: pathlib.Path, run_id: str, n: int) -> dict:
        """把一次解释需要的全部输入读齐。只读 run 目录，不读平台、不读浏览器。"""
        points, _state, _bad = self.read_curve(path)
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
                raw = fh.read(self.limits.patch_bytes + 1)
        except OSError as exc:
            raise ExplainNoPatch(f"读不了 diffs/round-{n}.patch：{exc}") from None
        truncated = len(raw) > self.limits.patch_bytes
        patch_text = raw[:self.limits.patch_bytes].decode("utf-8", errors="replace")
        if truncated:
            cut = patch_text.rfind("\n")
            if cut > self.limits.patch_bytes // 2:
                patch_text = patch_text[:cut]
            patch_text += f"\n…（patch 已截断：只发送前 40 KB，共 {patch_size} 字节）"

        ident = point.get("identity") if isinstance(point.get("identity"), dict) else {}
        raw_stat = self.read_json(path / "diffs" / f"round-{n}.json")
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
            "zero_tasks": self._zero_tasks(path, n, point),
        }


    def build_explain_range_inputs(self, path: pathlib.Path, run_id: str, first: int, last: int) -> dict:
        """首尾轮之间的事实：每轮的分数与改动规模，以及首尾两轮的 patch 片段。"""
        points, _state, _bad = self.read_curve(path)
        by_round = {as_int(p.get("round")): p for p in points}
        missing = [n for n in (first, last) if n not in by_round]
        if missing:
            raise ValueError(f"round {missing} 不在这个 run 的曲线里")
        if first >= last:
            raise ValueError("from 必须小于 to")
        rows = []
        for n in range(first, last + 1):
            point = by_round.get(n)
            if point is None:
                continue
            stat = self.read_json(path / "diffs" / f"round-{n}.json")
            files = [str(item.get("path") or "") for item in (stat.get("files") or [])
                     if isinstance(item, dict)]
            added = sum(as_int(item.get("added")) or 0 for item in (stat.get("files") or [])
                        if isinstance(item, dict))
            removed = sum(as_int(item.get("removed")) or 0 for item in (stat.get("files") or [])
                          if isinstance(item, dict))
            rows.append({"round": n, "score": as_num(point.get("score")),
                         "files": files, "added": added, "removed": removed})
        if len(rows) < 2:
            raise ValueError("这个区间里少于两轮有记录，无法判断走向")
        patches = {}
        for n in (first, last):
            patch = path / "diffs" / f"round-{n}.patch"
            if patch.is_file():
                patches[n] = patch.read_text(encoding="utf-8", errors="replace")[:self.limits.patch_bytes // 2]
        return {"run_id": run_id, "from": first, "to": last, "rows": rows, "patches": patches}


class ExplainCache:
    def __init__(self):
        self.memory = {}
        self.order = []
        self.lock = threading.Lock()

    def path(self, key: str, *, directory) -> pathlib.Path:
        return directory / (_sha(key)[:40] + ".json")


    def remember(self, key: str, payload: dict) -> None:
        with self.lock:
            self.memory[key] = payload
            try:
                self.order.remove(key)
            except ValueError:
                pass
            self.order.append(key)
            while len(self.order) > 128:
                old = self.order.pop(0)
                self.memory.pop(old, None)


    def get(self, key: str, *, directory) -> dict | None:
        with self.lock:
            hit = self.memory.get(key)
            if hit is not None:
                return json.loads(json.dumps(hit, ensure_ascii=False))
        path = self.path(key, directory=directory)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("key") != key:
            return None
        payload = data.get("response")
        if not isinstance(payload, dict):
            return None
        self.remember(key, payload)
        return json.loads(json.dumps(payload, ensure_ascii=False))


    def put(self, key: str, payload: dict, *, directory) -> None:
        self.remember(key, payload)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = self.path(key, directory=directory)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"key": key, "response": payload},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            # 磁盘缓存失败不该让解释失败；内存缓存还在。
            pass


class ExplainService:
    def __init__(self, *, lookup, config, chat, cache_get, cache_put):
        self.lookup, self.config, self.chat = lookup, config, chat
        self.cache_get, self.cache_put = cache_get, cache_put

    def round(self, run_id: str, n: int, force: bool = False, *, read_curve, build_explain_inputs, explain_prompt) -> tuple[int, dict]:
        """(HTTP status, payload)。服务端缓存命中时不再调用模型。"""
        started = time.perf_counter()
        run_path = self.lookup(run_id)
        if not run_path:
            return 404, {"error": "run not found", "detail": f"没有这个 run：{run_id}"}
        points, _state, _bad = read_curve(run_path)
        if not any(as_int(p.get("round")) == n for p in points):
            return 400, {"error": "round not in run",
                         "detail": f"round {n} 不在这个 run 的曲线里"}

        base_url, model, api_key, missing = self.config()
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
            cached = self.cache_get(key)
            if cached is not None:
                cached["cache_hit"] = True
                cached["elapsed_ms"] = int(round((time.perf_counter() - started) * 1000))
                return 200, cached

        # Retry schema rejection once with its reason; an invalid second reply stays degraded.
        raw = ""
        parsed = None
        why = ""
        for attempt in range(2):
            prompt_system = system if attempt == 0 else (
                system + "\n\n上一次的回复被拒绝：" + why + "\n请按契约重新输出，只输出 JSON。")
            try:
                raw = self.chat(base_url, model, api_key, prompt_system, user)
            except ExplainUpstream as exc:
                return 502, {"error": "explain-upstream", "detail": _clip(exc, 800)}
            parsed, why = _parse_explain_reply(raw)
            if parsed is not None:
                break
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
        self.cache_put(key, {k: v for k, v in payload.items()
                                 if k not in ("cache_hit", "elapsed_ms")})
        return 200, payload


    def range(self, run_id: str, first: int, last: int, force: bool = False, *, build_explain_range_inputs, explain_range_prompt) -> tuple[int, dict]:
        """一次区间解释：POST /api/explain 的 from/to 字段。缓存键含区间两端。"""
        started = time.perf_counter()
        run_path = self.lookup(run_id)
        if not run_path:
            return 404, {"error": "run not found", "detail": f"没有这个 run：{run_id}"}
        base_url, model, api_key, missing = self.config()
        if missing:
            return 503, {"error": "explain-not-configured",
                         "detail": "解释功能没有配置：" + "、".join(missing)}
        try:
            inputs = build_explain_range_inputs(run_path, run_id, first, last)
        except ValueError as exc:
            return 400, {"error": "explain-range", "detail": str(exc)}
        system, user = explain_range_prompt(inputs)
        prompt_sha = _sha(system + "\n\n" + user)
        key = _explain_cache_key(f"{run_id}#range{first}-{last}", 0, "", model, prompt_sha)
        if not force:
            cached = self.cache_get(key)
            if cached is not None:
                cached["cache_hit"] = True
                cached["elapsed_ms"] = int(round((time.perf_counter() - started) * 1000))
                return 200, cached
        raw, parsed, why = "", None, ""
        for attempt in range(2):
            asked = system if attempt == 0 else (
                system + "\n\n上一次的回复被拒绝：" + why + "\n请按契约重新输出，只输出 JSON。")
            try:
                raw = self.chat(base_url, model, api_key, asked, user)
            except ExplainUpstream as exc:
                return 502, {"error": "explain-upstream", "detail": _clip(exc, 800)}
            parsed, why = _parse_explain_reply(raw)
            if parsed is not None:
                break
        elapsed = int(round((time.perf_counter() - started) * 1000))
        if parsed is None:
            return 200, {"claim": "", "evidence": [], "risk": "", "next": "", "model": model,
                         "prompt_sha": prompt_sha, "cache_hit": False, "elapsed_ms": elapsed,
                         "degraded": True, "parse_error": why, "raw": _clip(raw, 8000),
                         "range": {"from": first, "to": last}}
        payload = {**parsed, "model": model, "prompt_sha": prompt_sha, "cache_hit": False,
                   "elapsed_ms": elapsed, "range": {"from": first, "to": last}}
        self.cache_put(key, {k: v for k, v in payload.items()
                                 if k not in ("cache_hit", "elapsed_ms")})
        return 200, payload
