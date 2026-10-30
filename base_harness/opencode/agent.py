#!/usr/bin/env python3
"""OpenCode source CLI -> HarnessGrad task/answer/trace contract.

Follows codex's per-task process/config pattern. Unlike a chat-summary wrapper,
preserves task-created answer.txt, JSON submissions, files and environment state.
No dependency installation, dataset lookup, grading, or improver invocation occurs here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
IGNORED = {".git", "node_modules", ".state", "_harnessgrad", "__pycache__", ".opencode-state"}


def bun_binary(parent: dict) -> str:
    """The Bun executable: named, or found in the harness's own declared runtime paths."""
    named = parent.get("HG_HARNESS_BUN") or parent.get("HG_AGENT_OPENCODE_BUN")
    if named:
        return named
    try:
        declared = json.loads((ROOT / "harness.json").read_text(encoding="utf-8")).get(
            "runtime_paths") or []
    except (OSError, ValueError):
        declared = []
    for entry in declared:
        candidate = Path(str(entry)).expanduser() / "bun"
        if candidate.is_file():
            return str(candidate)
    return "bun"


def source_hash(source: Path) -> str:
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED)
        for name in sorted(files):
            path = Path(directory, name)
            relative = path.relative_to(source).as_posix()
            digest.update(relative.encode() + b"\0")
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            else:
                digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def redact(text: str, env: dict) -> str:
    for key, value in env.items():
        if value and any(word in key.upper() for word in ("API_KEY", "SECRET", "PASSWORD", "ACCESS_TOKEN")):
            text = text.replace(value, "<redacted>")
    return text


def task_prompt(task: dict) -> str:
    goal = task.get("goal") or task.get("instruction")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task requires a non-empty goal or instruction")
    paths = submission_paths(task)
    delivery = (
        "The task submits these files relative to the working directory: "
        + json.dumps(paths) + ". Leave them in place for the platform's scorer. "
        "A final chat message or answer.txt is not required in addition to those files. "
        if paths is not None else
        "If the task specifies answer.txt, write its exact required contents; it will be preserved. "
        "Otherwise your final message is the answer: return the requested answer format, "
        "without a progress summary or extra explanation. "
    )
    prompt = (
        "Complete this task in the current working directory using real tools. "
        "Follow the task's exact submission format and its supplied tool/CLI instructions. "
        "Create all requested artifacts and perform required environment submissions. "
        + delivery + "Do not use subagents.\n\n" + goal
    )
    if task.get("inputs") is not None:
        prompt += "\n\nTask inputs:\n" + json.dumps(task["inputs"], ensure_ascii=False)
    return prompt


def configuration(env: dict) -> dict:
    model = env.get("HG_AGENT_MODEL", "")
    base = env.get("HG_AGENT_BASE_URL", "")
    if not model or not base or not env.get("HG_AGENT_API_KEY"):
        raise ValueError("HG_AGENT_MODEL, HG_AGENT_BASE_URL and HG_AGENT_API_KEY are required")
    wire = env.get("HG_AGENT_OPENCODE_WIRE", "chat")
    if wire not in ("chat", "responses"):
        raise ValueError("HG_AGENT_OPENCODE_WIRE must be chat or responses")
    package = "@opencode/ai/providers/openai-compatible" + ("/responses" if wire == "responses" else "")
    return {
        "model": {"providerID": "harnessgrad", "model": model},
        "default_agent": "build", "update": "disable", "share": "disabled",
        "snapshots": False, "warming": False, "lsp": False, "formatter": False,
        "agents": {"build": {"steps": int(env.get("HG_AGENT_MAX_STEPS", "12"))}},
        "permissions": [{"action": "*", "resource": "*", "effect": "allow"}],
        "policies": [
            {"action": "provider.use", "resource": "*", "effect": "deny"},
            {"action": "provider.use", "resource": "harnessgrad", "effect": "allow"},
            {"action": "tool.use", "resource": "*", "effect": "deny"},
            *({"action": "tool.use", "resource": name + ":*", "effect": "allow"}
              for name in ("read", "write", "edit", "apply_patch", "bash", "shell", "glob", "grep", "list")),
        ],
        "providers": {"harnessgrad": {
            "name": "HarnessGrad task endpoint", "env": ["HG_AGENT_API_KEY"], "package": package,
            "settings": {"baseURL": base, "apiKey": "{env:HG_AGENT_API_KEY}",
                         "timeout": int(float(env.get("HG_AGENT_TIMEOUT_S", "60")) * 1000)},
            "models": {model: {
                "name": model, "capabilities": {"tools": True, "input": ["text"], "output": ["text"]},
                "limit": {"context": int(env.get("HG_AGENT_CONTEXT_WINDOW", "32768")),
                          "output": int(env.get("HG_AGENT_MAX_OUTPUT_TOKENS", "2048"))},
            }},
        }},
    }


def child_environment(parent: dict, state: Path) -> dict:
    # Retain task service endpoints/PATH; the platform has already selected task credentials.
    excluded = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                "CODEX_HOME", "OPENCODE_SERVER", "OPENCODE_CONFIG", "BUN_OPTIONS", "NODE_OPTIONS"}
    env = {k: v for k, v in parent.items()
           if not k.startswith(("HG_METHOD_", "HG_ENV_", "OPENCODE_")) and k not in excluded}
    for key, folder in {
        "HOME": "home", "USERPROFILE": "home", "OPENCODE_TEST_HOME": "home",
        "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data", "XDG_CACHE_HOME": "cache",
        "XDG_STATE_HOME": "state", "APPDATA": "appdata", "LOCALAPPDATA": "localappdata",
        "OPENCODE_CONFIG_DIR": "config/opencode", "TEMP": "tmp", "TMP": "tmp",
    }.items():
        path = state / folder
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update({
        "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_FILEWATCHER": "1", "OPENCODE_DISABLE_FFF": "1",
        "OPENCODE_CONFIG_CONTENT": json.dumps(configuration(parent)),
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
        self.session_tokens = None

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
        if event.get("sessionID"):
            self.sessions.add(event["sessionID"])
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
                record = {"tool": part.get("tool"), "tool_call_id": part.get("id"),
                          "tool_input": arguments, "tool_status": state.get("status"),
                          "output": state.get("output") or state.get("error") or ""}
                if part.get("tool") in ("bash", "shell"):
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

    def reconcile(self, snapshot: dict):
        """The CLI's persisted replay can omit step_finish; use its public export."""
        info = snapshot.get("info") or {}
        messages = [m for m in snapshot.get("messages", []) if m.get("type") == "assistant"]
        if self.sessions != {info.get("id")}:
            raise ValueError("OpenCode session export does not match emitted session")
        if self.provider_errors or info.get("outcome") != "succeeded":
            raise ValueError("OpenCode session did not succeed")
        if not messages or messages[-1].get("finish") != "stop":
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
        self.record({"opencode_session": {"id": info["id"], "outcome": info["outcome"]}})

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
        return {**usage, "task_steps": steps,
                "scope": "session_including_auxiliary" if self.session_tokens is not None else "task_steps_partial",
                "reported_steps": len(tokens), "completed_steps": len(self.finishes)}


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
            declaration = json.loads((ROOT / "harness.json").read_text(encoding="utf-8")).get("runtime")
            if not isinstance(declaration, dict) or declaration.get("kind") != "bun":
                raise ValueError("Declare the Bun runtime in harness.json before execution")
            source = Path(parent.get("HG_HARNESS_SOURCE") or parent.get("HG_AGENT_OPENCODE_SOURCE")
                          or ROOT / declaration["source"]).resolve()
            # **Inside the candidate, or declared in `runtime_paths`.** The platform binds
            # those trees read-only, so a declared tree is readable here and nothing else is.
            # `opencode` is a 224 MB monorepo with 4,689 packages: copying it into every
            # candidate would put 224 MB in each `state_after_round<N>/`.
            declared = [Path(str(p)).expanduser().resolve()
                        for p in (json.loads((ROOT / "harness.json").read_text(encoding="utf-8"))
                                  .get("runtime_paths") or [])]
            owned = source.is_relative_to(ROOT.resolve()) or any(
                source.is_relative_to(root) for root in declared)
            if not owned:
                raise ValueError("Source must be inside this candidate or under a declared "
                                 "runtime path")
            entry = (source / declaration["entrypoint"]).resolve()
            if not entry.is_relative_to(source):
                raise ValueError("Runtime entry must belong to the declared source")
            if parent.get("HG_HARNESS_ENTRYPOINT") and Path(parent["HG_HARNESS_ENTRYPOINT"]).resolve() != entry:
                raise ValueError("Runtime entry disagrees with platform declaration")
            if not entry.is_file():
                raise ValueError("OpenCode source missing; prepare this candidate before running")
            env = child_environment(parent, work / ".opencode-state")
            env["PWD"] = str(work)
            identity = {"harness": "opencode-agent", "agent_backend": "opencode",
                        "agent_model": parent.get("HG_AGENT_MODEL"),
                        "agent_base_url": parent.get("HG_AGENT_BASE_URL"),
                        "wire": parent.get("HG_AGENT_OPENCODE_WIRE", "chat"),
                        "source": str(source), "source_sha256": source_hash(source),
                        "source_in_candidate": owned}
            record({"harness_identity": identity, "task_id": task.get("task_id")})
            argv = [bun_binary(parent), "--no-env-file", "--no-install",
                    str(entry), "run", "--standalone", "--format", "json",
                    "--model", "harnessgrad/" + parent["HG_AGENT_MODEL"], "--agent", "build"]
            before = answer_stamp(work)
            process = subprocess.Popen(
                argv, cwd=source / "packages/cli", env=env, stdin=subprocess.PIPE,
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
            if len(stream.sessions) != 1:
                raise ValueError("Expected exactly one OpenCode session in the JSON stream")
            # Export is a local state read, not another model request. It supplies finish
            # and usage even when noninteractive.ts reconstructed only text/tool events.
            export_argv = argv[:4] + ["session", "export", "--standalone", next(iter(stream.sessions))]
            export = subprocess.Popen(export_argv, cwd=source / "packages/cli", env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8", errors="replace",
                                      start_new_session=os.name != "nt")
            try:
                snapshot_text, export_error = export.communicate(timeout=max(0.01, expires - time.monotonic()))
            except subprocess.TimeoutExpired:
                stop_process_tree(export)
                export.communicate()
                raise ValueError("OpenCode session export exceeded the task deadline")
            if export.returncode:
                raise ValueError("OpenCode session export failed: " + redact(export_error[-2000:], parent))
            snapshot_text = redact(snapshot_text, parent)
            (work / "opencode.session.json").write_text(snapshot_text, encoding="utf-8")
            stream.reconcile(json.loads(snapshot_text))
            answer, origin = save_answer(work, before, stream.final_text(), task=task)
            record({"submitted": True, "answer": answer, "answer_source": origin,
                    **({"submitted_files": submission_paths(task)} if origin == "files" else {})})
            return 0
        except (OSError, ValueError, KeyError, TypeError) as exc:
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
