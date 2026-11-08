#!/usr/bin/env python3
"""Translate the installed OpenClaw CLI into the HarnessGrad task/answer/trace contract.

The external package is a declared read-only dependency. The candidate owns this
adapter, prompt.md and config.json; changing them changes what actually executes.
No gateway daemon, user config, messaging channel or benchmark scorer is used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parent


class AdapterError(ValueError):
    pass


class AdapterProfile(NamedTuple):
    configure: Callable[[dict, str, str, int, int], None]
    session_option: str
    session_prefix: str
    read_session: Callable[[Path, str, list[str], dict, float], list[dict]]


def submission_paths(task: dict) -> list[str] | None:
    spec = task.get("submission")
    if spec is None or spec == {"kind": "text"}:
        return None
    if not isinstance(spec, dict) or set(spec) != {"kind", "paths"} or spec["kind"] != "files":
        raise AdapterError("invalid public submission descriptor")
    paths = spec["paths"]
    if not isinstance(paths, list) or not paths:
        raise AdapterError("file submission paths must be a nonempty list")
    for value in paths:
        if (not isinstance(value, str) or not value.strip() or "\\" in value or ":" in value
                or any(part in ("", ".", "..") for part in value.split("/"))):
            raise AdapterError("file submission paths must be relative paths")
    return paths


def task_prompt(task: dict) -> str:
    goal = task.get("goal") or task.get("instruction")
    if not isinstance(goal, str) or not goal.strip():
        raise AdapterError("task requires a non-empty goal or instruction")
    paths = submission_paths(task)
    if paths:
        delivery = "Leave these files in the working directory for scoring: " + json.dumps(paths) + "."
    else:
        delivery = "Write every file the task asks for exactly as asked."
    text = (ROOT / "prompt.md").read_text(encoding="utf-8")
    result = text + "\n\nTask delivery:\n" + delivery + "\n\nTask:\n" + goal
    if task.get("inputs") is not None:
        result += "\n\nTask inputs:\n" + json.dumps(task["inputs"], ensure_ascii=False)
    return result


def runtime_identity(env: dict | None = None) -> dict:
    # Load the helper owned by this candidate even when the platform is hidden.
    spec = importlib.util.spec_from_file_location("_task_openclaw_runtime", ROOT / "openclaw_runtime.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    prepared = helper.resolve(ROOT, dict(os.environ) if env is None else env)
    package = Path(prepared["package_root"])
    launcher_digest = hashlib.sha256()
    for path in (package / "package.json", Path(prepared["entrypoint"])):
        launcher_digest.update(path.relative_to(package).as_posix().encode() + b"\0")
        launcher_digest.update(path.read_bytes())
        launcher_digest.update(b"\0")
    return {**prepared, "source": prepared["source_identity"]["source"],
            "upstream_commit": prepared["source_identity"]["upstream_commit"],
            "launcher_package_sha256": launcher_digest.hexdigest(),
            "node_sha256": next(row["sha256"] for row in prepared["files"] if row["path"] == prepared["node"]),
            "dependency_scope": "prepared Node, package.json and launcher; not a transitive dependency tree digest"}


def child_environment(parent: dict, state: Path, config: Path, work: Path) -> dict:
    blocked = {"HOME", "USERPROFILE", "CODEX_HOME", "NODE_OPTIONS", "NODE_PATH", "BUN_OPTIONS",
               "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
               "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "MISTRAL_API_KEY"}
    prefixes = ("OPENCLAW_", "CLAWDBOT_", "PI_", "OPENAI_", "CODEX_", "HG_METHOD_", "HG_ENV_", "HG_JUDGE_")
    child = {k: v for k, v in parent.items() if k not in blocked and not k.startswith(prefixes)}
    for name, folder in {"HOME": "home", "USERPROFILE": "home", "XDG_CONFIG_HOME": "config",
                         "XDG_DATA_HOME": "data", "XDG_CACHE_HOME": "cache", "XDG_STATE_HOME": "state",
                         "TMPDIR": "tmp", "TEMP": "tmp", "TMP": "tmp"}.items():
        target = state / folder
        target.mkdir(parents=True, exist_ok=True)
        child[name] = str(target)
    child.update({"OPENCLAW_STATE_DIR": str(state), "OPENCLAW_CONFIG_PATH": str(config),
                  "OPENCLAW_AGENT_DIR": str(state / "agents" / "main" / "agent"),
                  "OPENCLAW_NO_BANNER": "1", "NODE_DISABLE_COMPILE_CACHE": "1", "PWD": str(work)})
    return child


def _list_configuration(config: dict, model: str, workspace: str, context: int, timeout: int) -> None:
    config["models"]["providers"]["harnessgrad"]["models"][0]["contextWindow"] = context
    agents = config["agents"]
    agents["defaults"].update({"models": {model: {}}, "contextTokens": context})
    agents["list"] = [{"id": "main", "default": True, "workspace": workspace, "skills": []}]
    execution = config["tools"]["exec"]
    execution.setdefault("security", "full")
    execution.setdefault("ask", "off")
    execution["timeoutSec"] = timeout


def _entries_configuration(config: dict, model: str, workspace: str, context: int, timeout: int) -> None:
    config["models"]["providers"]["harnessgrad"]["models"][0]["contextTokens"] = context
    agents = config["agents"]
    agents["defaults"].setdefault("modelPolicy", {})["allow"] = [model]
    agents["entries"] = {"main": {**agents.get("entries", {}).get("main", {}),
                                  "workspace": workspace, "skills": []}}
    execution = config["tools"]["exec"]
    execution.setdefault("mode", "full")
    execution["timeoutSeconds"] = timeout


def configuration(env: dict, base_url: str, model: str, work: Path, timeout: float, *,
                  profile: AdapterProfile | None = None) -> dict:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    output = int(env.get("HG_AGENT_MAX_OUTPUT_TOKENS", env.get("HG_AGENT_MAX_TOKENS", "2048")))
    context = int(env.get("HG_AGENT_CONTEXT_WINDOW", "32768"))
    if output <= 0 or context <= 0 or output > context:
        raise AdapterError("invalid HG_AGENT output/context token limits")
    config.setdefault("models", {})["providers"] = {"harnessgrad": {
        "baseUrl": base_url, "apiKey": env.get("HG_AGENT_API_KEY") or "local",
        "api": "openai-completions", "models": [{"id": model, "name": model,
        "reasoning": False, "input": ["text"], "maxTokens": output,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        # **Usage must be asked for on a non-OpenAI endpoint.** The installed CLI's
        # `normalizeModelCompat` forces `supportsUsageInStreaming: false` for every
        # OpenAI-completions model whose `baseUrl` is not api.openai.com, and
        # `@mariozechner/pi-ai` only sends `stream_options: {include_usage: true}` when that
        # flag is not false. Measured 2026-11-01: vLLM returns a usage block when asked
        # (checked with curl), while the session this adapter read carried
        # `{"input": 0, "output": 0, ...}` -- so a real call was reported as
        # `input_tokens: null` and the harness looked like one that cannot count. Declaring
        # it is configuration, not an override of a measurement. `supportsDeveloperRole` is
        # left unset on purpose: this chat template has no `developer` role.
        "compat": {"supportsUsageInStreaming": True}}]}}
    config["models"]["mode"] = "replace"
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    model_ref = "harnessgrad/" + model
    seconds = max(1, int(timeout))
    defaults.update({"model": {"primary": model_ref, "fallbacks": []}, "workspace": str(work),
                     "skipBootstrap": True, "timeoutSeconds": seconds})
    config.setdefault("tools", {}).setdefault("exec", {})
    (profile or _PROFILES["list"]).configure(config, model_ref, str(work), context, seconds)
    return config


def timeout_seconds(env: dict) -> float:
    # HG_AGENT_TIMEOUT_S is a per-request provider timeout, not the task deadline.
    timeout = float(env.get("HG_AGENT_OPENCLAW_TIMEOUT_S", "300"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise AdapterError("OpenClaw timeout must be finite and positive")
    if env.get("HG_AGENT_DEADLINE_S"):
        deadline = float(env["HG_AGENT_DEADLINE_S"])
        if not math.isfinite(deadline) or deadline <= 0:
            raise AdapterError("HG_AGENT_DEADLINE_S must be finite and positive")
        margin = min(float(env.get("HG_AGENT_STOP_MARGIN_S", "5")), deadline / 5)
        timeout = min(timeout, max(0.1, deadline - margin))
    return timeout


def _descendants(pid: int) -> set[int]:
    parents = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            text = path.read_text()
            parents[int(path.parent.name)] = int(text[text.rfind(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
    found = set()
    frontier = {pid}
    while frontier:
        frontier = {child for child, parent in parents.items() if parent in frontier and child not in found}
        found.update(frontier)
    return found


def stop_process(proc: subprocess.Popen) -> None:
    # exec tools can detach shell sessions; snapshot descendants before killing Node.
    descendants = _descendants(proc.pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in descendants:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


#: The contract's `observation.kind` (`docs/adapter-spec.md` section 4): the same four
#: words the codex, opencode, deepseek-harness and miniswe adapters write, so a method can
#: read one field across harnesses instead of learning each tool's name. The tool's own
#: name still travels beside it (`tool`), which is what a reader needs for the detail.
_OBSERVATION_KINDS = {
    "exec": "shell", "shell": "shell", "bash": "shell", "terminal": "shell",
    "read": "filesystem", "write": "filesystem", "edit": "filesystem",
    "apply_patch": "filesystem", "glob": "filesystem", "grep": "filesystem",
    "web_fetch": "api", "web_search": "api", "fetch": "api", "search": "api",
}


def observation_kind(tool: str) -> str:
    return _OBSERVATION_KINDS.get((tool or "").lower(), "other")


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _session_rows(state: Path, session_id: str, _argv: list[str], _env: dict, _timeout: float) -> list[dict]:
    paths = sorted(state.glob(f"agents/*/sessions/{session_id}.jsonl"))
    return _read_jsonl(paths[-1]) if paths else []


def _export_session_rows(state: Path, session_key: str, argv: list[str], env: dict, timeout: float) -> list[dict]:
    if timeout <= 0:
        raise AdapterError("OpenClaw task deadline expired before session export")
    output = "harnessgrad"
    command = [*argv, "sessions", "export-trajectory", "--agent", "main", "--session-key", session_key,
               "--workspace", str(state), "--output", output, "--json"]
    result = subprocess.run(command, cwd=state, env=env, capture_output=True, text=True,
                            errors="replace", timeout=min(30, timeout))
    if result.returncode:
        raise AdapterError("OpenClaw session export failed: " + result.stderr[-2000:])
    bundle = state / ".openclaw" / "trajectory-exports" / output
    events = bundle / "events.jsonl"
    if not events.resolve().is_relative_to(state):
        raise AdapterError("OpenClaw session export escaped its private state")
    # Runtime events duplicate transcript tool calls. Only transcript messages
    # have the session format shared with the older JSONL reader.
    return [event["data"] for event in _read_jsonl(events)
            if event.get("source") == "transcript" and event.get("sessionKey") == session_key
            and event.get("type") in ("assistant.message", "tool.result")
            and isinstance(event.get("data"), dict) and isinstance(event["data"].get("message"), dict)]


_PROFILES = {
    "list": AdapterProfile(_list_configuration, "--session-id", "", _session_rows),
    "entries": AdapterProfile(_entries_configuration, "--session-key", "agent:main:", _export_session_rows),
}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(item.get("text", "") for item in content or [] if isinstance(item, dict))


def redact(value, env: dict):
    text = json.dumps(value, ensure_ascii=False)
    secrets = sorted({v for k, v in env.items() if v and any(word in k.upper() for word in
                      ("API_KEY", "SECRET", "PASSWORD", "ACCESS_TOKEN"))}, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "<redacted>")
    return json.loads(text)


def translate_trace(rows: list[dict], record, *, backend: str) -> dict:
    calls = 0
    totals = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    complete = {name: True for name in totals}
    tools = {}
    final = ""
    for row in rows:
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            calls += 1
            usage = message.get("usage") or {}
            for name in totals:
                value = usage.get(name)
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[name] += value
                else:
                    complete[name] = False
            content = message.get("content") or []
            text = _text(content)
            record({"reply": text, "openclaw_stop_reason": message.get("stopReason")})
            if text.strip():
                final = text
            for item in content if isinstance(content, list) else []:
                if not isinstance(item, dict) or item.get("type") != "toolCall":
                    continue
                args = item.get("arguments") or {}
                name = item.get("name", "tool")
                command = args.get("command") if isinstance(args, dict) else None
                tools[item.get("id", "")] = {"command": command or name + " " + json.dumps(args, ensure_ascii=False, sort_keys=True),
                                              "name": name, "args": args}
        elif role == "toolResult":
            call = tools.pop(message.get("toolCallId"), None)
            if call is None:
                continue
            details = message.get("details") or {}
            text = _text(message.get("content"))
            obs = {"kind": observation_kind(call["name"]),
                   "returncode": details.get("exitCode"), "output_chars": len(text),
                   "output": text[-4000:], "truncated": len(text) > 4000,
                   "is_error": bool(message.get("isError"))}
            record({"command": call["command"], "observation": obs,
                    "tool": call["name"], "tool_args": call["args"]})
    for call in tools.values():
        # A requested call without a result is not evidence that an action executed.
        record({"openclaw_pending_tool": call})
    # Pi serializes missing provider usage as zero. For real endpoints, all-zero is
    # ambiguous; preserve unknown rather than asserting a free real model call.
    ambiguous = backend != "mock" and not any(totals.values())
    usage = {"calls": calls,
             "input_tokens": totals["input"] if calls and complete["input"] and not ambiguous else None,
             "output_tokens": totals["output"] if calls and complete["output"] and not ambiguous else None,
             "cache_read_tokens": totals["cacheRead"] if calls and complete["cacheRead"] and not ambiguous else None,
             "cache_write_tokens": totals["cacheWrite"] if calls and complete["cacheWrite"] and not ambiguous else None,
             "source": "openclaw_session"}
    record({"usage": usage})
    return {"final": final, "calls": calls}


def _answer_bytes(work: Path) -> bytes | None:
    path = work / "answer.txt"
    if path.is_symlink() or not path.resolve().is_relative_to(work):
        raise AdapterError("answer.txt must be an owned regular file")
    if path.exists() and not path.is_file():
        raise AdapterError("answer.txt must be a regular file")
    return path.read_bytes() if path.is_file() else None


def save_answer(work: Path, task: dict, before: bytes | None, final: str) -> tuple[str, str]:
    paths = submission_paths(task)
    if paths:
        missing = [name for name in paths if not (work / name).is_file()
                   or (work / name).is_symlink() or not (work / name).resolve().is_relative_to(work)]
        if missing:
            raise AdapterError("missing or unsafe submission files: " + ", ".join(missing))
        return "", "files"
    now = _answer_bytes(work)
    if now is not None and now != before and now.strip():
        return now.decode("utf-8", "replace"), "artifact"
    if not final.strip():
        raise AdapterError("OpenClaw produced no final message and no new answer.txt")
    (work / "answer.txt").write_text(final, encoding="utf-8")
    return final, "final_message"


def run(task: dict, work: Path) -> int:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    backend = env.get("HG_AGENT_BACKEND", "mock")
    model = env.get("HG_AGENT_MODEL", "")
    rows = []
    server = None
    proc = None
    timeout = timeout_seconds(env)
    previous_signals = {}
    def interrupted(signum, _frame):
        raise AdapterError(f"OpenClaw interrupted by signal {signum}")
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_signals[signum] = signal.signal(signum, interrupted)
    record = lambda value: rows.append(redact(value, env))
    task_id = str(task.get("task_id", "?"))
    try:
        prompt = task_prompt(task)
        before = _answer_bytes(work)
        runtime = runtime_identity(env)
        if backend == "mock":
            sys.path.insert(0, str(ROOT))
            from mock_model import start
            base, server = start()
            model = "openclaw-mock"
        else:
            base = env.get("HG_AGENT_BASE_URL")
            if not base or not model or not env.get("HG_AGENT_API_KEY"):
                raise AdapterError("HG_AGENT_BASE_URL, HG_AGENT_MODEL and HG_AGENT_API_KEY are required")
        record({"harness_identity": {"harness": "openclaw", "agent_model": model,
                "agent_backend": backend, "config": {"runtime": runtime, "timeout_s": timeout,
                "editable": ["agent.py", "prompt.md", "config.json"], "mode": "local"}}, "task_id": task_id})
        with tempfile.TemporaryDirectory(prefix="hg-openclaw-state-") as temporary:
            state = Path(temporary)
            config = state / "openclaw.json"
            profile_name = runtime["source_identity"].get("adapter_profile", "list")
            profile = _PROFILES.get(profile_name)
            if profile is None:
                raise AdapterError(f"unknown OpenClaw adapter profile {profile_name!r}")
            cfg = configuration(env, base, model, work, timeout, profile=profile)
            config.write_text(json.dumps(cfg), encoding="utf-8")
            config.chmod(0o600)
            child = child_environment(env, state, config, work)
            child["PATH"] = os.pathsep.join((str(Path(runtime["node"]).parent), child.get("PATH", os.defpath)))
            session_id = "hg-" + hashlib.sha256((task_id + str(time.time_ns())).encode()).hexdigest()[:20]
            session = profile.session_prefix + session_id
            command = [*runtime["argv"], "agent", "--local", "--json",
                       "--agent", "main", profile.session_option, session, "--timeout", str(max(1, int(timeout))),
                       "--message", prompt]
            timed_out = False
            stderr = ""
            deadline = time.monotonic() + timeout
            try:
                proc = subprocess.Popen(command, cwd=work, env=child, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, errors="replace", start_new_session=True)
                try:
                    stdout, stderr = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    stop_process(proc)
                    stdout, stderr = proc.communicate(timeout=2)
            finally:
                if proc is not None:
                    stop_process(proc)
            try:
                native = profile.read_session(state, session, runtime["argv"], child, deadline - time.monotonic())
            except (OSError, ValueError, subprocess.SubprocessError):
                if not (timed_out or proc.returncode):
                    raise
                native = []
            summary = translate_trace(native, record, backend=backend)
            final = summary["final"]
            if timed_out:
                raise AdapterError(f"OpenClaw timed out after {timeout:g}s")
            if proc.returncode:
                raise AdapterError(f"OpenClaw exited {proc.returncode}: " + stderr[-2000:])
            if not native:
                raise AdapterError("OpenClaw left no session trace: " + stderr[-2000:])
            answer, source = save_answer(work, task, before, final)
            record({"submitted": True, "answer": answer, "answer_source": source})
            return 0
    except (AdapterError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        if not rows:
            record({"harness_identity": {"harness": "openclaw", "agent_model": model,
                                         "agent_backend": backend}, "task_id": task_id})
        record({"error": str(exc)})
        print(str(redact(str(exc), env)), file=sys.stderr)
        return 1
    finally:
        for signum, previous in previous_signals.items():
            signal.signal(signum, previous)
        if proc is not None:
            stop_process(proc)
        if server is not None:
            server.shutdown()
            server.server_close()
        # Even a startup failure gets a cost/termination record. Unknown usage stays
        # null; this is distinct from the mock provider's explicit zero usage.
        if not any("usage" in row for row in rows):
            rows.append({"usage": {"calls": 0, "input_tokens": None,
                                    "output_tokens": None, "cache_read_tokens": None,
                                    "cache_write_tokens": None, "source": "adapter_failure"}})
        (work / "trace.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    task = json.loads(Path(args.task).read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise SystemExit("task must be a JSON object")
    return run(task, Path(args.workdir))


if __name__ == "__main__":
    raise SystemExit(main())
