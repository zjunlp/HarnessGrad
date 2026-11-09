#!/usr/bin/env python3
"""OpenCode release CLI -> HarnessGrad task/answer/trace contract.

Follows codex's per-task process/config pattern. Unlike a chat-summary wrapper,
preserves task-created answer.txt, JSON submissions, files and environment state.
No dependency installation, dataset lookup, grading, or improver invocation occurs here.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
RELEASE_COMMANDS = {
    "1": ([], ["export"]),
    "2": (["--standalone"], ["session", "export"]),
}


def prepared_installation(env):
    spec = importlib.util.spec_from_file_location("_task_opencode_runtime", ROOT / "opencode_runtime.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.resolve(ROOT, env)


def redact(text: str, env: dict) -> str:
    for key, value in env.items():
        if value and any(word in key.upper() for word in ("API_KEY", "SECRET", "PASSWORD", "ACCESS_TOKEN")):
            text = text.replace(value, "<redacted>")
    return text


def task_prompt(task: dict) -> str:
    submission_paths(task)
    public = task.get("cli_task")
    if public is None:
        goal = task.get("goal") or task.get("instruction")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("task requires a non-empty goal or instruction")
        public = goal
        if task.get("inputs") is not None:
            public += "\n\nTask inputs:\n" + json.dumps(task["inputs"], ensure_ascii=False)
        paths = submission_paths(task)
        if paths is not None:
            public += "\n\nTask delivery:\nLeave these files in the working directory: " + json.dumps(paths, ensure_ascii=False)
    return ("Complete this task in the current working directory using real tools. "
            "Follow the task's exact submission format and its supplied tool/CLI instructions. "
            "Create all requested artifacts and perform required environment submissions.\n\n" + public)


def configuration(env: dict, profile: str) -> dict:
    model = env.get("HG_AGENT_MODEL", "")
    base = env.get("HG_AGENT_BASE_URL", "")
    if not model or not base or not env.get("HG_AGENT_API_KEY"):
        raise ValueError("HG_AGENT_MODEL, HG_AGENT_BASE_URL and HG_AGENT_API_KEY are required")
    wire = env.get("HG_AGENT_OPENCODE_WIRE", "chat")
    if wire not in ("chat", "responses"):
        raise ValueError("HG_AGENT_OPENCODE_WIRE must be chat or responses")
    settings = {"baseURL": base, "apiKey": "{env:HG_AGENT_API_KEY}",
                "timeout": int(float(env.get("HG_AGENT_TIMEOUT_S", "60")) * 1000)}
    model_config = {"name": model, "limit": {
        "context": int(env.get("HG_AGENT_CONTEXT_WINDOW", "32768")),
        "output": int(env.get("HG_AGENT_MAX_OUTPUT_TOKENS", "2048")),
    }}
    provider = {"name": "HarnessGrad task endpoint", "models": {model: model_config}}
    config = {"model": f"harnessgrad/{model}"}
    agents = {"build": {"steps": int(env.get("HG_AGENT_MAX_STEPS", "12"))}}
    if profile == "1":
        provider.update(npm="@ai-sdk/openai" if wire == "responses" else "@ai-sdk/openai-compatible",
                        options=settings)
        config.update(provider={"harnessgrad": provider}, share="disabled", autoupdate=False,
                      agent=agents)
    else:
        model_config.update(modelID=model, capabilities={"tools": True, "input": ["text"], "output": ["text"]})
        package = "@opencode/ai/providers/openai-compatible"
        provider.update(package=package + ("/responses" if wire == "responses" else ""), settings=settings)
        config.update(providers={"harnessgrad": provider}, agents=agents)
    return config


def child_environment(parent: dict, state: Path, profile: str = "1") -> dict:
    # Retain task service endpoints/PATH; the platform has already selected task credentials.
    excluded = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                "CODEX_HOME", "OPENCODE_SERVER", "OPENCODE_CONFIG", "NODE_OPTIONS", "NODE_PATH"}
    env = {k: v for k, v in parent.items()
           if not k.startswith(("HG_METHOD_", "HG_ENV_", "HG_JUDGE_", "OPENCODE_", "OPENAI_", "CODEX_")) and k not in excluded}
    for key, folder in {
        "HOME": "home", "USERPROFILE": "home", "OPENCODE_TEST_HOME": "home",
        "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data", "XDG_CACHE_HOME": "cache",
        "XDG_STATE_HOME": "state", "APPDATA": "appdata", "LOCALAPPDATA": "localappdata",
        "OPENCODE_CONFIG_DIR": "config/opencode", "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp",
    }.items():
        path = state / folder
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update({
        "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_FILEWATCHER": "1", "OPENCODE_DISABLE_FFF": "1",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_CONFIG_CONTENT": json.dumps(configuration(parent, profile)),
    })
    if parent.get("HG_AGENT_GIT_BASH_PATH"):
        env["OPENCODE_GIT_BASH_PATH"] = parent["HG_AGENT_GIT_BASH_PATH"]
    return env


def timeout_seconds(env: dict) -> float:
    configured = float(env.get("HG_AGENT_OPENCODE_TIMEOUT_S", "300"))
    if configured <= 0:
        raise ValueError("OpenCode timeout must be positive")
    if env.get("HG_AGENT_DEADLINE_S"):
        deadline = float(env["HG_AGENT_DEADLINE_S"])
        margin = min(float(env.get("HG_AGENT_STOP_MARGIN_S", "5")), max(0, deadline / 5))
        return max(0.1, min(configured, deadline - margin))
    return configured


def answer_stamp(work: Path):
    path = work / "answer.txt"
    return (path.stat().st_mtime_ns, path.read_bytes()) if path.is_file() else None


def submission_paths(task: dict) -> list[str] | None:
    """Public delivery metadata only; never infer a contract from IDs or the grader.

    Omission keeps legacy text transport. File presence is a completion check,
    not a correctness check: the platform still owns all content validation.
    """
    if "submission" not in task:
        return None
    spec = task["submission"]
    if spec == {"kind": "text"}:
        return None
    if not isinstance(spec, dict) or set(spec) != {"kind", "paths"} or spec["kind"] != "files":
        raise ValueError("Invalid public submission descriptor")
    paths = spec["paths"]
    if not isinstance(paths, list) or not paths:
        raise ValueError("File submission paths must be a nonempty list")
    for name in paths:
        if (not isinstance(name, str) or not name.strip() or "\\" in name or ":" in name
                or any(part in ("", ".", "..") for part in name.split("/"))):
            raise ValueError("File submission paths must be relative paths within the workdir")
    return paths


def save_answer(work: Path, before, final: str, *, task: dict | None = None) -> tuple[str, str]:
    paths = submission_paths(task or {})
    if paths is not None:
        root = work.resolve()
        missing = []
        for name in paths:
            path = root / name
            if (not path.resolve().is_relative_to(root) or not path.is_file()
                    or any(p.is_symlink() for p in (path, *path.parents) if p != root and root in p.parents)):
                missing.append(name)
        if missing:
            raise ValueError("Missing or unsafe submission files: " + ", ".join(missing))
        return "", "files"
    current = answer_stamp(work)
    if current is not None and current != before and current[1].strip():
        return current[1].decode("utf-8"), "artifact"
    if not final.strip():
        raise ValueError("No completed final answer or changed answer.txt")
    (work / "answer.txt").write_text(final, encoding="utf-8")
    return final, "final_message"


#: Which environment spoke, for the contract's `observation.kind`. A tool name is what the
#: CLI gives us; the three that matter are a shell, the file system, and an HTTP fetch.
def observation_kind(tool: str) -> str:
    name = (tool or "").lower()
    if name in ("bash", "shell"):
        return "shell"
    if name in ("read", "write", "edit", "patch", "glob", "grep", "list", "ls"):
        return "filesystem"
    if name in ("webfetch", "fetch", "search", "websearch"):
        return "api"
    return "other"


def child_reference(part: dict) -> tuple[bool, str | None]:
    """Read only a native task/subagent tool receipt, never arbitrary model text."""
    if part.get("tool", part.get("name")) not in ("task", "subagent"):
        return False, None
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    metadata = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else metadata
    child = metadata.get("sessionID") or metadata.get("sessionId")
    if not child:
        content = state.get("output") or state.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
        match = re.search(r'(?:sessionID|id)=["\'](ses_[A-Za-z0-9_-]+)["\']', str(content))
        child = match.group(1) if match else None
    if isinstance(child, str) and re.fullmatch(r'ses_[A-Za-z0-9_-]{1,128}', child):
        return True, child
    # A rejected call has no child to count. A completed receipt with no ID
    # cannot prove that its child usage was captured.
    return state.get("status") not in ("error", "failed"), None


def snapshot_children(snapshot: dict, profile: str) -> tuple[set[str], bool]:
    children, incomplete = set(), False
    for message in snapshot.get("messages", []):
        if not isinstance(message, dict):
            continue
        parts = message.get("parts", []) if profile == "1" else message.get("content", [])
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict):
                continue
            relevant, child = child_reference(part)
            if child:
                children.add(child)
            elif relevant:
                incomplete = True
    return children, incomplete


def complete_session_tokens(tokens) -> bool:
    if not isinstance(tokens, dict) or not isinstance(tokens.get('cache'), dict):
        return False
    return (all(type(tokens.get(k)) is int and tokens[k] >= 0 for k in ('input', 'output', 'reasoning'))
            and all(type(tokens['cache'].get(k)) is int and tokens['cache'][k] >= 0 for k in ('read', 'write')))


class EventCollector:
    def __init__(self, record):
        self.record = record
        self.text = {}
        self.finishes = []
        self.seen = set()
        self.provider_errors = []
        self.invalid_lines = 0
        self.step_count = 0
        self.sessions = set()
        self.primary_session = None
        self.child_sessions = set()
        self.child_tokens = []
        self.child_messages = 0
        self.child_usage_incomplete = False
        self.session_tokens = None
        self.profile = None

    def feed(self, line: str):
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except ValueError:
            self.invalid_lines += 1
            self.record({"opencode_event_raw": line})
            return
        if not isinstance(event, dict):
            self.invalid_lines += 1
            return
        self.record({"opencode_event": event})
        session = event.get("sessionID")
        if session:
            self.sessions.add(session)
            if self.primary_session is None:
                self.primary_session = session
            elif session != self.primary_session:
                # Child output must not become the parent's final answer or steps.
                self.record({"opencode_child_event": event})
                return
        kind, part = event.get("type"), event.get("part") or {}
        message = part.get("messageID", "")
        if kind == "text":
            self.text[message] = self.text.get(message, "") + str(part.get("text") or "")
        elif kind == "step_start":
            self.step_count += 1
        elif kind in ("step_finish", "tool_use"):
            key = (kind, event.get("sessionID"), message, part.get("id") or part.get("partID"))
            if key in self.seen:
                return
            self.seen.add(key)
            if kind == "step_finish":
                self.finishes.append(part)
                text = self.text.get(message, "")
                reply = {"answer": text} if part.get("reason") == "stop" else {"opencode_finish": part.get("reason")}
                self.record({"step": len(self.finishes), "reply": json.dumps(reply, ensure_ascii=False)})
            else:
                state = part.get("state") or {}
                arguments = state.get("input") or {}
                output = state.get("output") or state.get("error") or ""
                shell = part.get("tool") in ("bash", "shell")
                metadata = state.get("metadata") or {}
                metadata = metadata.get("metadata") or metadata
                if part.get("tool") in ("task", "subagent"):
                    relevant, child = child_reference(part)
                    if child:
                        self.child_sessions.add(child)
                    elif relevant:
                        self.child_usage_incomplete = True
                exit_code = metadata.get("exit")
                record = {"tool": part.get("tool"), "tool_call_id": part.get("id"),
                          "tool_input": arguments, "tool_status": state.get("status"),
                          "output": output,
                          "observation": {"kind": observation_kind(part.get("tool")),
                                          "returncode": exit_code if shell and type(exit_code) is int else None,
                                          "output_chars": len(str(output)),
                                          "truncated": bool(state.get("truncated") or metadata.get("truncated"))}}
                if shell:
                    record["command"] = arguments.get("command", "")
                self.record(record)
        elif kind == "error":
            error = event.get("error") or {}
            self.provider_errors.append(error)
            self.record({"error": error, "opencode_failure": True})

    def final_text(self) -> str:
        if self.provider_errors or not self.finishes or self.finishes[-1].get("reason") != "stop":
            return ""
        return self.text.get(self.finishes[-1].get("messageID", ""), "")

    def reconcile(self, snapshot: dict, profile: str = "2"):
        """The CLI's persisted replay can omit step_finish; use its public export."""
        self.profile = profile
        info = snapshot.get("info") or {}
        if profile == "1":
            messages = [{**m["info"], "content": m["parts"]} for m in snapshot.get("messages", [])
                        if m.get("info", {}).get("role") == "assistant"]
        else:
            messages = [m for m in snapshot.get("messages", []) if m.get("type") == "assistant"]
        if self.primary_session != info.get("id"):
            raise ValueError("OpenCode session export does not match emitted session")
        if self.provider_errors or (profile == "2" and info.get("outcome") != "succeeded"):
            raise ValueError("OpenCode session did not succeed")
        if not messages or messages[-1].get("error") or messages[-1].get("finish") != "stop":
            raise ValueError("OpenCode session has no completed final assistant turn")
        recorded = {p.get("messageID") for p in self.finishes}
        self.finishes = []
        for message in messages:
            mid = message["id"]
            self.text[mid] = "".join(p.get("text", "") for p in message.get("content", [])
                                     if p.get("type") == "text")
            part = {"messageID": mid, "reason": message.get("finish"),
                    "tokens": message.get("tokens")}
            self.finishes.append(part)
            if mid not in recorded:
                reply = ({"answer": self.text[mid]} if part["reason"] == "stop"
                         else {"opencode_finish": part["reason"]})
                self.record({"step": len(self.finishes), "reply": json.dumps(reply, ensure_ascii=False),
                             "source": "session_export"})
        self.session_tokens = info.get("tokens")
        children, incomplete = snapshot_children(snapshot, profile)
        self.child_sessions.update(children)
        self.child_usage_incomplete |= incomplete
        self.record({"opencode_session": {"id": info["id"], "outcome": "succeeded"}})

    @staticmethod
    def token_totals(tokens: list[dict]) -> dict:
        def total(key):
            return sum(t[key] for t in tokens) if tokens and all(isinstance(t.get(key), (int, float)) for t in tokens) else None
        # OpenCode's schema counts cache and reasoning separately from input/output.
        cache_read = sum(t.get("cache", {}).get("read", 0) for t in tokens)
        cache_write = sum(t.get("cache", {}).get("write", 0) for t in tokens)
        reasoning = sum(t.get("reasoning", 0) for t in tokens)
        input_count, output_count = total("input"), total("output")
        return {"input_tokens": input_count + cache_read + cache_write if input_count is not None else None,
                "output_tokens": output_count + reasoning if output_count is not None else None,
                "uncached_input_tokens": input_count,
                "reasoning_tokens": total("reasoning"),
                "cached_input_tokens": cache_read if tokens else None,
                "cache_write_input_tokens": cache_write if tokens else None}

    def usage(self) -> dict:
        tokens = [p["tokens"] for p in self.finishes if isinstance(p.get("tokens"), dict)]
        steps = self.token_totals(tokens)
        usage = self.token_totals([self.session_tokens]) if isinstance(self.session_tokens, dict) else dict(steps)
        scope = "session_including_auxiliary" if self.session_tokens is not None else "task_steps_partial"
        parent_usage = dict(usage)
        if self.sessions - {self.primary_session} - self.child_sessions:
            self.child_usage_incomplete = True
        if self.profile == '1':
            # Native v1's export omits separately generated title/auxiliary
            # requests even when the session's own token fields are present.
            usage = {key: None for key in usage}
            scope = 'native_v1_session_partial'
        if self.child_sessions or self.child_usage_incomplete:
            if (self.child_usage_incomplete or len(self.child_tokens) != len(self.child_sessions)
                    or not complete_session_tokens(self.session_tokens) or self.profile == '1'):
                usage = {key: None for key in usage}
                scope = "subagent_usage_incomplete"
            else:
                usage = self.token_totals([self.session_tokens, *self.child_tokens])
                scope = "session_tree_including_auxiliary"
        return {**usage, "task_steps": steps,
                # The contract's `usage.calls`: completed model turns. The platform's
                # `loop_facts` counts `reply` records, and one is written per `step_finish`,
                # so these two numbers are the same fact written twice -- which is what
                # makes a disagreement worth catching (`tools/check_trace_contract.py`).
                "calls": len(self.finishes),
                "scope": scope, "parent_session": parent_usage,
                "subagent_sessions": len(self.child_sessions),
                "subagent_messages": None if self.child_usage_incomplete else self.child_messages,
                "reported_steps": len(tokens), "completed_steps": len(self.finishes)}


def collect_child_sessions(stream, export_one, profile: str) -> None:
    """Collect referenced child exports once; never replace the parent's answer.

    If any child is missing or lacks complete usage, the total is unknown rather
    than the parent's partial cost. This also handles resumed and nested sessions.
    """
    pending = list(sorted(stream.child_sessions))
    seen = set()
    while pending:
        if len(seen) >= 64:
            stream.child_usage_incomplete = True
            break
        session_id = pending.pop(0)
        if session_id in seen:
            continue
        if session_id == stream.primary_session:
            stream.child_usage_incomplete = True
            continue
        seen.add(session_id)
        try:
            snapshot = export_one(session_id)
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("info"), dict):
                raise ValueError("invalid child export")
            info = snapshot["info"]
            if info.get("id") != session_id:
                raise ValueError("child export identity mismatch")
            messages = snapshot.get("messages")
            if not isinstance(messages, list):
                raise ValueError("missing child messages")
            assistants = [m.get("info", {}) if profile == "1" else m for m in messages if isinstance(m, dict)]
            assistants = [m for m in assistants if m.get("role", m.get("type")) == "assistant"]
            if (not assistants or assistants[-1].get("error") or assistants[-1].get("finish") != "stop"
                    or (profile == "2" and info.get("outcome") != "succeeded")):
                raise ValueError("child session not complete")
            tokens = info.get("tokens")
            if not complete_session_tokens(tokens):
                raise ValueError("child token usage is incomplete")
            stream.child_tokens.append(tokens)
            stream.child_messages += len(assistants)
            children, incomplete = snapshot_children(snapshot, profile)
            stream.child_usage_incomplete |= incomplete
            stream.child_sessions.update(children)
            pending.extend(sorted(children - seen))
            stream.record({"opencode_subagent_session": {"id": session_id,
                           "outcome": info.get("outcome"), "tokens": tokens, "messages": messages}})
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            stream.child_usage_incomplete = True
            stream.record({"opencode_subagent_session": {"id": session_id, "error": type(exc).__name__}})
    if stream.sessions - {stream.primary_session} - stream.child_sessions:
        stream.child_usage_incomplete = True


def stop_process_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(task: dict, work: Path) -> int:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    parent = dict(os.environ)
    with (work / "trace.jsonl").open("w", encoding="utf-8", buffering=1) as trace:
        def record(value):
            trace.write(redact(json.dumps(value, ensure_ascii=False), parent) + "\n")
        started = time.monotonic()
        stream = EventCollector(record)
        try:
            expires = started + timeout_seconds(parent)
            prompt = task_prompt(task)
            installed = prepared_installation(parent)
            profile = installed["profile"]
            startup, export_command = RELEASE_COMMANDS[profile]
            env = child_environment(parent, work / ".opencode-state", profile)
            env["PWD"] = str(work)
            record({"harness_identity": {"harness": "opencode-agent", "agent_backend": "opencode",
                        "agent_model": parent.get("HG_AGENT_MODEL"),
                        "agent_base_url": parent.get("HG_AGENT_BASE_URL"),
                        "wire": parent.get("HG_AGENT_OPENCODE_WIRE", "chat"),
                        "runtime": installed}, "task_id": task.get("task_id")})
            prefix = [*installed["argv"], *startup]
            argv = [*prefix, "run", "--format", "json", "--auto",
                    "--model", "harnessgrad/" + parent["HG_AGENT_MODEL"], "--agent", "build"]
            before = answer_stamp(work)
            process = subprocess.Popen(
                argv, cwd=work, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt")
            timed_out = False
            try:
                stdout, stderr = process.communicate(prompt, timeout=max(0.01, expires - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                stop_process_tree(process)
                stdout, stderr = process.communicate()
            (work / "opencode.events.jsonl").write_text(redact(stdout, parent), encoding="utf-8")
            (work / "opencode.stderr.log").write_text(redact(stderr, parent), encoding="utf-8")
            for line in stdout.splitlines():
                stream.feed(redact(line, parent))
            failed = timed_out or process.returncode != 0 or stream.provider_errors or stream.invalid_lines
            if failed:
                record({"error": "OpenCode timeout" if timed_out else "OpenCode execution failed",
                        "exit_code": process.returncode, "elapsed_s": time.monotonic() - started,
                        "partial_usage": stream.usage()})
                if stream.provider_errors and str(stream.provider_errors[-1].get("type", "")).startswith("provider."):
                    last_error = stream.provider_errors[-1]
                    summary = {k: last_error[k] for k in ("type", "status", "message") if k in last_error}
                    print("OpenCode provider failure: " + redact(json.dumps(summary), parent), file=sys.stderr)
                else:
                    print("OpenCode execution failed; inspect trace.jsonl", file=sys.stderr)
                return 1
            if stream.primary_session is None:
                raise ValueError("OpenCode did not identify its primary session")

            def export_one(session_id):
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    raise ValueError("OpenCode session export exceeded the task deadline")
                export = subprocess.Popen([*prefix, *export_command, session_id], cwd=work, env=env,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, encoding="utf-8", errors="replace",
                                          start_new_session=os.name != "nt")
                try:
                    text, error = export.communicate(timeout=remaining)
                except subprocess.TimeoutExpired:
                    stop_process_tree(export)
                    export.communicate()
                    raise ValueError("OpenCode session export exceeded the task deadline")
                if export.returncode or not text.strip():
                    raise ValueError("OpenCode session export failed: " + redact(error[-800:], parent))
                return json.loads(redact(text, parent))

            try:
                snapshot = export_one(stream.primary_session)
            except (OSError, ValueError, TypeError) as exc:
                record({"export": {"ok": False, "detail": redact(str(exc), parent)}})
                if stream.child_sessions:
                    stream.child_usage_incomplete = True
            else:
                (work / "opencode.session.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
                stream.reconcile(snapshot, profile)
                collect_child_sessions(stream, export_one, profile)
            answer, origin = save_answer(work, before, stream.final_text(), task=task)
            record({"submitted": True, "answer": answer, "answer_source": origin,
                    **({"submitted_files": submission_paths(task)} if origin == "files" else {})})
            return 0
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            record({"error": redact(str(exc), parent)})
            print(redact(str(exc), parent), file=sys.stderr)
            return 1
        finally:
            # A submission/export failure still consumed model tokens. Report what
            # was observed once; missing provider usage stays unknown, never zero.
            record({"usage": stream.usage(), "elapsed_s": time.monotonic() - started})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    return run(json.loads(args.task.read_text(encoding="utf-8")), args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
