"""Read-only run queries with an explicit authorization lookup and one list cache.

The server supplies configured roots and current procfs observations at call time.
Queries never discover alternate roots or create them. Reader/clock/lookup ports
are explicit; cached rows and their lock belong only to ListCache.
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import threading
import time

if __package__:
    from . import record_reader
    from .path_policy import within
    from .serialization import human_bytes
else:
    import record_reader
    from path_policy import within
    from serialization import human_bytes

as_int = record_reader.as_int
as_num = record_reader.as_num
calls_per_task = record_reader.calls_per_task
model_in_loop = record_reader.model_in_loop
dir_count = record_reader.dir_count

_RUN_METHOD = re.compile(
    r"^[^-]+-[0-9]{8}T[0-9]{4,6}Z-(?P<method>[A-Za-z0-9_]+)"
    r"-(?:train|ckpt-r\d+-valid_(?:unseen|seen|train))$")

HISTORY_MAX_ROUNDS = 1000

HISTORY_MAX_ROUND_NUMBER = 999_999_999


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


def method_of_run(run_id: str, meta: dict | None = None) -> str | None:
    """run 是哪个方法跑出来的：先读 `run_meta.json` 的入口，再看 run id。"""
    entry = (((meta or {}).get("config") or {}).get("run") or {}).get("method_entrypoint")
    if isinstance(entry, str):
        match = re.search(r"/methods/([^/]+)/run\.py$", entry)
        if match:
            return match.group(1)
    match = _RUN_METHOD.match(run_id or "")
    return match.group("method") if match else None


def _task_of(stem: str) -> tuple[str, int]:
    """`<task-id>` 或 `<task-id>.trial<k>` → (task_id, trial)。"""
    if ".trial" in stem:
        head, _, tail = stem.rpartition(".trial")
        try:
            return head, int(tail)
        except ValueError:
            return stem, 1
    return stem, 1


def _valid_history_range(since: int, upto: int) -> bool:
    return (type(since) is int and type(upto) is int
            and 0 <= since <= upto <= HISTORY_MAX_ROUND_NUMBER
            and upto - since + 1 <= HISTORY_MAX_ROUNDS)


def kind_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"study": 0, "eval": 0, "unknown": 0}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return counts


class ListCache:
    """TTL cache and lock owned by one query service, never copied into server."""
    def __init__(self):
        self.at = 0.0
        self.rows = []
        self.lock = threading.Lock()

    def read(self, load, *, force, ttl, clock):
        now = clock()
        with self.lock:
            if not force and now - self.at < ttl:
                return self.rows
            rows = load()
            self.at, self.rows = now, rows
            return rows


class RunQueryService:
    def __init__(self, *, reader=record_reader):
        self.reader = reader
        self.cache = ListCache()

    def all_rows(self, runs, *, live_drivers, force=False, ttl=2.0, clock=time.time):
        return self.cache.read(lambda: self._scan_rows(runs, live_drivers()),
                               force=force, ttl=ttl, clock=clock)

    def _scan_rows(self, runs, live):
        rows: list[dict] = []
        if runs.is_dir():
            try:
                dirs = sorted(runs.iterdir())
            except OSError:
                dirs = []
            for d in dirs:
                try:
                    row = self.reader.run_row(d, live)
                except OSError:
                    continue
                if row:
                    # **能不能重建某一版，是这一行的事实，不是点开才知道的意外。**
                    # 状态目录、单文件快照与补丁都是"这一轮的 harness 被存下来了"的证据；
                    # 三者都没有的 run，曲线上的轮次在磁盘上并不存在，编辑页只能拒绝它。
                    try:
                        row["rounds_stored"] = sum(1 for _ in d.glob("state_after_round*"))
                        row["diffs_stored"] = sum(1 for _ in (d / "diffs").glob("round-*.patch")) \
                            if (d / "diffs").is_dir() else 0
                        snapshots = sorted({int(p.stem[len("round-"):])
                                            for p in (d / "snapshots").glob("round-*.json")
                                            if p.stem[len("round-"):].isdigit()}) \
                            if (d / "snapshots").is_dir() else []
                        row["snapshot_rounds"] = snapshots
                        row["snapshots_stored"] = len(snapshots)
                    except OSError:
                        row["rounds_stored"] = row["diffs_stored"] = 0
                        row["snapshot_rounds"] = []
                        row["snapshots_stored"] = 0
                    rows.append(row)
        rows.sort(key=lambda r: r["mtime_s"] or 0, reverse=True)
        return rows

    def api_run(self, run_id: str, *, lookup, live_drivers) -> dict | None:
        path = lookup(run_id)
        if not path:
            return None
        points, curve_state, curve_errors = self.reader.read_curve(path)
        row = self.reader.run_row(path, live_drivers(), points, curve_state, curve_errors)
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
        meta = self.reader.read_json(path / "run_meta.json")
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


    def api_round(self, run_id: str, n: int, *, lookup, skip_dirs) -> dict | None:
        path = lookup(run_id)
        if not path:
            return None
        patch_path = path / "diffs" / f"round-{n}.patch"
        stat = self.reader.read_json(path / "diffs" / f"round-{n}.json")
        state = path / f"state_after_round{n}"
        files: list[dict] = []
        if state.is_dir():
            try:
                for f in sorted(state.rglob("*")):
                    rel = f.relative_to(state)
                    if f.is_dir() or any(part in skip_dirs for part in rel.parts):
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


    def api_file(self, run_id: str, n: int, rel: str, *, lookup, max_file_bytes) -> dict | None:
        path = lookup(run_id)
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
        if size > max_file_bytes:
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


    def _verdict(self, path: pathlib.Path, task_id: str, n: int) -> dict:
        return self.reader.read_json(path / "verdicts" / f"round-{n}" / f"{task_id}.json")


    def _trace_files(self, path: pathlib.Path, n: int) -> list[pathlib.Path]:
        d = path / f"round{n}_traces"
        return sorted(d.glob("*.jsonl")) if d.is_dir() else []


    def round_exists(self, path: pathlib.Path, n: int) -> bool:
        """这一轮在这个 run 的曲线里存在吗。

        三种情况要分开，因为界面对它们的反应不同：
          · 轮次不存在       → 400：你问了一个不存在的轮次
          · 存在但没留轨迹   → 200 + 空列表：这一轮确实没保留轨迹
          · 存在且有轨迹     → 200 + 列表
        否则 `rounds/999` 会回一个空壳，界面把它读成"这一轮没有编辑/没有轨迹"。
        """
        return any(point.get("round") == n for point in self.reader.read_curve(path)[0])


    def api_traces(self, run_id: str, n: int, *, lookup) -> dict | None:
        """这一轮的轨迹清单。分数来自该轮的判定文件；没有判定就显示 None，不是 0。"""
        path = lookup(run_id)
        if not path:
            return None
        per_task: dict = {}
        for point in self.reader.read_curve(path)[0]:
            if point.get("round") == n:
                raw = point.get("per_task")
                per_task = raw if isinstance(raw, dict) else {}
                break
        tasks = []
        for f in self._trace_files(path, n):
            task_id, trial = _task_of(f.stem)
            verdict = self._verdict(path, task_id, n)
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
                if not isinstance(ev, dict):
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


    def api_trace(self, run_id: str, n: int, name: str, *, lookup) -> dict | None:
        """一条轨迹，按步渲染。长输出裁剪并标注，不静默截断。"""
        path = lookup(run_id)
        base = path / f"round{n}_traces" if path else None
        if not path or not base.is_dir() or "/" in name or "\\" in name:
            return None
        if not within(base, base / name):
            return None
        target = (base / name).resolve()
        if not target.is_file():
            return None
        task_id, trial = _task_of(target.stem)
        verdict = self._verdict(path, task_id, n)
        events: list[dict] = []
        for line_no, line in enumerate(target.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                ev = None
                parsed = False
            else:
                parsed = True
            if not parsed:
                events.append({"kind": "raw", "line": line_no, "text": line})
            elif not isinstance(ev, dict):
                events.append({"kind": "structured", "line": line_no, "value": ev})
            elif "harness_identity" in ev:
                events.append({"kind": "identity", "config": ev["harness_identity"]})
            elif "reply" in ev:
                reply = ev.get("reply")
                text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
                events.append({"kind": "reply", "step": ev.get("step"), "model_ms": ev.get("model_ms"),
                               "text": text[:8000], "truncated": len(text) > 8000,
                               "note": f"显示前 8000 字符（原文 {len(text)} 字符）" if len(text) > 8000 else ""})
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
            else:
                # Preserve newer harness schemas instead of silently dropping their events.
                events.append({"kind": "structured", "line": line_no, "value": ev})
            if len(events) >= 1200:
                events.append({"kind": "meta", "label": "截断", "value": "事件超过 1200 条，只显示前 1200 条"})
                break
        return {"run": run_id, "round": n, "name": name, "task_id": task_id, "trial": trial,
                "events": events, "score": verdict.get("score"),
                "detail": verdict.get("detail") or "", "verdict_kind": verdict.get("kind")}


    def run_verdicts(self, run_id: str, round_no: int, *, lookup) -> dict:
        """一轮里每道题的判定。**只读**：这里不跑任何东西，只把已存在的证据摆出来。"""
        run_path = lookup(run_id)
        if not run_path:
            return {"error": "run-not-found", "detail": run_id}
        folder = run_path / "verdicts" / f"round-{int(round_no)}"
        if not folder.is_dir():
            return {"error": "no-verdicts", "detail": f"verdicts/round-{round_no} 不存在"}
        tasks = {}
        for path in sorted(folder.glob("*.json")):
            data = self.reader.read_json(path)
            if isinstance(data, dict):
                tasks[str(data.get("task_id") or path.stem)] = as_num(data.get("score"))
        return {"run": run_id, "round": int(round_no), "tasks": tasks, "n": len(tasks)}


    def run_compare(self, run_a: str, run_b: str, round_a: int = 0, round_b: int = 0, *, lookup) -> dict:
        """两个 run 的逐题对照：`1→0` 与 `0→1` 各是哪些题。

        这是 S3：它**只报数字**。要不要接受一次合并、掉几分换多少结构，不由平台决定
        （作者的决定，写在 docs/scenario-workspace.md）。
        """
        a = self.run_verdicts(run_a, round_a, lookup=lookup)
        b = self.run_verdicts(run_b, round_b, lookup=lookup)
        if "error" in a:
            return a
        if "error" in b:
            return b
        ids = sorted(set(a["tasks"]) | set(b["tasks"]))
        rows = []
        for task_id in ids:
            left, right = a["tasks"].get(task_id), b["tasks"].get(task_id)
            if left == right:
                continue
            rows.append({"task": task_id, "a": left, "b": right,
                         "direction": f"{int(left or 0)}→{int(right or 0)}"})
        lost = [r for r in rows if (r["b"] or 0) < (r["a"] or 0)]
        gained = [r for r in rows if (r["b"] or 0) > (r["a"] or 0)]
        return {"a": {"run": run_a, "round": round_a, "n": a["n"]},
                "b": {"run": run_b, "round": round_b, "n": b["n"]},
                "lost": lost, "gained": gained, "changed": len(rows),
                "only_in_a": sorted(set(a["tasks"]) - set(b["tasks"])),
                "only_in_b": sorted(set(b["tasks"]) - set(a["tasks"]))}


    def run_range_diff(self, run_id: str, first: int, last: int, *, lookup) -> dict:
        """两轮之间的**净改动**：`diff -ruN state_after_roundA state_after_roundB`。

        git 里最常用的一屏不是"每个提交"，而是"这两点之间到底变了什么"。而分散的每轮补丁
        回答不了"r1 到 r16 一共加了哪些功能" —— 中间有几轮互相覆盖，逐个读补丁要自己在
        脑子里合并。这里直接给出净结果，和 `git diff A B` 一个意思。
        """
        import subprocess
        run_path = lookup(run_id)
        if not run_path:
            return {"error": "run-not-found", "detail": run_id}
        first, last = int(first), int(last)
        if first >= last:
            return {"error": "bad-range", "detail": "from 必须小于 to"}
        a = run_path / f"state_after_round{first}"
        b = run_path / f"state_after_round{last}"
        # Snapshot roots must be ordinary owned directories. Do not turn an
        # explicitly unsupported root link into an apparently empty difference.
        if any(path.is_symlink() or not within(run_path, path) for path in (a, b)):
            return {"error": "unsafe-state", "detail": "snapshot roots must be directories inside the selected run without symlinks"}
        if not (a.is_dir() and b.is_dir()):
            missing = [f"r{n}" for n, path in ((first, a), (last, b)) if not path.is_dir()]
            return {"error": "missing-state", "detail": f"缺 {', '.join(missing)} 的快照",
                    "hint": "先用 /api/harness/from-round 把这轮重放成候选，再比两个候选"}
        # Compare link metadata, never their target contents, throughout both
        # trees (including a root swapped to a link after the check above).
        try:
            done = subprocess.run(["diff", "-ruN", "--no-dereference", "-x", "__pycache__", "-x", "*.pyc",
                                   a.name, b.name], cwd=str(run_path),
                                  capture_output=True, text=True, errors="replace", timeout=30)
            text = (done.stdout or "")
            stat = subprocess.run(["diff", "-ruN", "--no-dereference", "--brief", "-x", "__pycache__", "-x", "*.pyc",
                                   a.name, b.name], cwd=str(run_path),
                                  capture_output=True, text=True, errors="replace", timeout=30)
        except subprocess.TimeoutExpired:
            return {"error": "diff-timeout", "detail": "snapshot diff exceeded the 30 second command limit"}
        changed = [line.split(" and ")[0].split("/", 1)[-1]
                   for line in (stat.stdout or "").splitlines() if line.strip()]
        added = sum(1 for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in text.splitlines() if line.startswith("-") and not line.startswith("---"))
        return {"run": run_id, "from": first, "to": last, "files": changed,
                "added": added, "removed": removed, "diff": text[:60000]}


    def run_history(self, run_id: str, upto: int, since: int = 1, *, lookup) -> dict:
        """一个 run 到第 `upto` 轮为止的**历史**：每轮的改动与补丁。

        这些补丁就是 git 的历史。`harnessgrad/loops/mode_a.py` 每轮 `commit_state(work, ...)`
        提交一次，`_save_round_diff` 写下的 `diffs/round-N.patch` 就是相邻两个提交之间的
        `git diff`，而曲线点上的 `harness_sha` 是那个提交的 tree hash。run 目录里没有留下
        `.git`（提交用的是 staging 工作树），所以历史以"每轮状态 + 补丁"的形式在这里，
        可读、可重放（见 `harness_candidate_from_round`）。

        给助手做合并时要的正是这一串：只看最新版，看不出哪些功能是同一轮加的、哪些是补丁
        摞出来的。
        """
        if not _valid_history_range(since, upto):
            return {"error": "bad-range", "detail": "from/to must be ordered non-negative integers spanning at most 1000 rounds"}
        run_path = lookup(run_id)
        if not run_path:
            return {"error": "run-not-found", "detail": run_id}
        since = max(1, since)
        rows = []
        for n in range(since, upto + 1):
            patch_path = run_path / "diffs" / f"round-{n}.patch"
            stat = self.reader.read_json(run_path / "diffs" / f"round-{n}.json")
            files = [str(item.get("path") or "") for item in (stat.get("files") or [])
                     if isinstance(item, dict)]
            added = sum(as_int(item.get("added")) or 0 for item in (stat.get("files") or [])
                        if isinstance(item, dict))
            removed = sum(as_int(item.get("removed")) or 0 for item in (stat.get("files") or [])
                          if isinstance(item, dict))
            patch = ""
            if patch_path.is_file():
                patch = patch_path.read_text(encoding="utf-8", errors="replace")[:12000]
            rows.append({"round": n, "files": files, "added": added, "removed": removed,
                         "patch": patch, "stored": patch_path.is_file()})
        return {"run": run_id, "from": since, "to": upto, "rounds": rows}
