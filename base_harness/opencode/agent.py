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


def cli_version(source: Path) -> str:
    """The version the source tree declares, used to pin the channel it thinks it runs in.

    Running from source leaves `OPENCODE_CHANNEL` undefined, and the CLI then takes its
    `local` branch: `@opencode-ai/plugin@latest` plus an "is it outdated" registry call. Both
    are network calls and the task sandbox has none. Measured 2026-10-30: the CLI started,
    forked `bun add @opencode-ai/plugin@latest`, and blocked for 300 s with 6 s of CPU.
    """
    try:
        return json.loads((Path(source) / "packages/opencode/package.json").read_text(
            encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return "0.0.0"


def write_preload(state: Path, version: str) -> Path:
    """Pin the version globals so the CLI takes its released channel, not its local one."""
    state.mkdir(parents=True, exist_ok=True)
    path = state / "channel-preload.ts"
    path.write_text(
        f"globalThis.OPENCODE_CHANNEL = {json.dumps(version)}\n"
        f"globalThis.OPENCODE_VERSION = {json.dumps(version)}\n", encoding="utf-8")
    return path


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
    """The harness's config, in the schema the CLI on disk actually reads.

    The 2.0 preview used `providers` (plural) with `policies`, and `model` as an object
    `{providerID, model}`. The v1 line uses `provider` (singular) with `npm`/`options`/`models`
    and `model` as the string `"provider/model"`. Measured 2026-10-29: the preview's schema
    against a v1 CLI is accepted as JSON and then ignored, so the CLI starts with no provider,
    prints nothing, and the harness times out.
    """
    model = env.get("HG_AGENT_MODEL", "")
    base = env.get("HG_AGENT_BASE_URL", "")
    if not model or not base or not env.get("HG_AGENT_API_KEY"):
        raise ValueError("HG_AGENT_MODEL, HG_AGENT_BASE_URL and HG_AGENT_API_KEY are required")
    wire = env.get("HG_AGENT_OPENCODE_WIRE", "chat")
    if wire not in ("chat", "responses"):
        raise ValueError("HG_AGENT_OPENCODE_WIRE must be chat or responses")
    # `@ai-sdk/openai-compatible` is the package v1 loads for an OpenAI-compatible provider,
    # and it is already inside the prepared source tree's node_modules -- the sandbox has no
    # network, so a provider package that had to be fetched would be a harness that never
    # starts.
    return {
        "model": f"harnessgrad/{model}",
        "provider": {
            "harnessgrad": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "HarnessGrad task endpoint",
                "options": {
                    "baseURL": base,
                    "apiKey": "{env:HG_AGENT_API_KEY}",
                    "timeout": int(float(env.get("HG_AGENT_TIMEOUT_S", "60")) * 1000),
                },
                "models": {
                    model: {
                        "name": model,
                        "limit": {
                            "context": int(env.get("HG_AGENT_CONTEXT_WINDOW", "32768")),
                            "output": int(env.get("HG_AGENT_MAX_OUTPUT_TOKENS", "2048")),
                        },
                    },
                },
            },
        },
        "agent": {"build": {"steps": int(env.get("HG_AGENT_MAX_STEPS", "12"))}},
        "share": "disabled",
        "autoupdate": False,
    }


def child_environment(parent: dict, state: Path, version: str = "0.0.0") -> dict:
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
    # `needsInstall` decides whether to `bun add @opencode-ai/plugin` by looking for
    # `node_modules` and a `package.json` that already names the plugin at the CLI's version.
    # Neither exists in a fresh state directory, so without this every task tries to install a
    # package it already has -- over a network the sandbox does not have.
    config_dir = state / "config" / "opencode"
    (config_dir / "node_modules").mkdir(parents=True, exist_ok=True)
    (config_dir / "package.json").write_text(
        json.dumps({"dependencies": {"@opencode-ai/plugin": version}}), encoding="utf-8")
    env.update({
        "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_FILEWATCHER": "1", "OPENCODE_DISABLE_FFF": "1",
        # The two default plugins are **auth** plugins for providers this harness never
        # uses (`opencode-anthropic-auth@0.0.13`, `@gitlab/opencode-gitlab-auth@1.3.2`),
        # and v1 loads them by `bun add`-ing them from npm on every start. With no network
        # that is not a failure the CLI reports -- it is a silent hang: measured
        # 2026-11-01, `service=bun ... add --force --exact opencode-anthropic-auth@0.0.13`
        # sat for the whole task budget, the model endpoint received zero requests, and the
        # task was recorded as an OpenCode timeout with 0 steps.
        #
        # Disabling them is also the right thing on its own terms: every model call goes to
        # the endpoint the platform names, so a plugin whose purpose is to obtain
        # third-party credentials has nothing to do here and one more route to the network
        # that the measurement does not need. The package name in that flag's absence is
        # what the comment is for: if a future v1 needs a *behavioural* default plugin, it
        # will not arrive through this switch.
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_CONFIG_CONTENT": json.dumps(configuration(parent)),
    })
    if parent.get("HG_AGENT_GIT_BASH_PATH"):
        env["OPENCODE_GIT_BASH_PATH"] = parent["HG_AGENT_GIT_BASH_PATH"]
    return env


def workdir_tsconfig(package_dir: Path, work: Path) -> Path | None:
    """Give bun an entry-graph tsconfig that works from the **task** directory.

    bun reads one tsconfig as the default for everything it compiles, from
    `$cwd/tsconfig.json`. Two of its fields matter here and neither survives a different
    cwd unchanged:

    * `jsx`/`jsxImportSource` -- the tree compiles `.tsx` with `preserve` and the Solid
      transform, which arrives through a **bunfig `preload`**
      (`packages/opencode/bunfig.toml` -> `@opentui/solid/preload`). Without the tsconfig
      bun falls back to the React runtime and the CLI dies on
      `Cannot find module 'react/jsx-dev-runtime'`.
    * `paths` -- `./src/*` is resolved **relative to the cwd**, so from a task directory it
      points at the task's own `src/` and every `@/...` import fails
      (`Cannot find module '@/bus/bus-event'`). Absolute targets make it
      location-independent.

    Returns the path written, or `None` when the task brought its own `tsconfig.json` --
    in which case the task's file is left alone and the run is recorded as having used it.
    """
    target = work / "tsconfig.json"
    if target.exists():
        return None
    source = package_dir / "tsconfig.json"
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    options = dict(data.get("compilerOptions") or {})
    options.pop("$schema", None)
    paths = {}
    for alias, targets in (options.pop("paths", None) or {}).items():
        paths[alias] = [str((package_dir / target_path).resolve())
                        for target_path in targets]
    if paths:
        options["paths"] = paths
    target.write_text(json.dumps({"compilerOptions": options}, indent=1) + "\n",
                      encoding="utf-8")
    return target


def workdir_bunfig(package_dir: Path, work: Path) -> Path | None:
    """Give bun the tree's `preload` without the tree's cwd.

    `packages/opencode/bunfig.toml` is one line -- `preload = ["@opentui/solid/preload"]`
    -- and it is what registers the Solid JSX transform. bun reads `$cwd/bunfig.toml` and
    resolves the preload from the cwd, so from a task directory the bare package name
    resolves to nothing and the process exits **silently** (measured: exit 0, no stdout, no
    stderr, no log directory) rather than reporting a missing module.

    So the same file is written into the task directory with the preload resolved to an
    absolute path. `--config` is not the answer here: pointing bun at the tree's own bunfig
    with that flag makes the CLI exit 0 with no output at all, in every cwd, including the
    one where it works without the flag.
    """
    solid = package_dir / "node_modules" / "@opentui" / "solid"
    if not solid.exists():
        solid = package_dir.parent.parent / "node_modules" / "@opentui" / "solid"
    preload = solid / "scripts" / "preload.ts"
    if not preload.is_file():
        return None
    target = work / "bunfig.toml"
    if target.exists():
        return None
    target.write_text('preload = [' + json.dumps(str(preload.resolve())) + ']\n',
                      encoding="utf-8")
    return target


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
            # The source is inside the candidate, or under a declared `runtime_paths` entry --
            # the same rule the platform's `bun_runtime.owned_source` applies, so the harness
            # and the platform cannot disagree about which tree is being run.
            declared_roots = [Path(str(entry)).expanduser().resolve()
                              for entry in (json.loads((ROOT / "harness.json").read_text(
                                  encoding="utf-8")).get("runtime_paths") or [])]
            candidates = ([Path(parent["HG_HARNESS_SOURCE"]).expanduser()]
                          if parent.get("HG_HARNESS_SOURCE") else
                          [Path(parent["HG_AGENT_OPENCODE_SOURCE"]).expanduser()]
                          if parent.get("HG_AGENT_OPENCODE_SOURCE") else
                          [ROOT] + declared_roots)
            source = None
            for base in candidates:
                base = base.resolve()
                for guess in ([base] if base.name == declaration["source"] else
                              [base / declaration["source"]]):
                    if (guess / declaration["entrypoint"]).is_file():
                        source = guess
                        break
                if source is not None:
                    break
            source = (source or (ROOT / declaration["source"])).resolve()
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
            version = cli_version(source)
            env = child_environment(parent, work / ".opencode-state", version)
            env["PWD"] = str(work)
            identity = {"harness": "opencode-agent", "agent_backend": "opencode",
                        "agent_model": parent.get("HG_AGENT_MODEL"),
                        "agent_base_url": parent.get("HG_AGENT_BASE_URL"),
                        "wire": parent.get("HG_AGENT_OPENCODE_WIRE", "chat"),
                        "source": str(source), "source_sha256": source_hash(source),
                        "source_in_candidate": owned}
            record({"harness_identity": identity, "task_id": task.get("task_id")})
            # Where the entrypoint lives, used to find the tree's `bunfig.toml` and its
            # tsconfig. Not the process's cwd any more: v1 takes its *project root* from
            # `process.cwd()` (`cli/cmd/run.ts:575`), so running from here made the agent's
            # bash tool, its edits and its snapshots all point at the harness's own source
            # tree. The CLI runs from the task directory and is told where its config is
            # (`--config`) instead.
            package_dir = entry.parent.parent
            # Two files the CLI's compiler needs and the task never asked for. Both are
            # written here and removed in the `finally`: bun reads them from the *cwd*, and
            # the cwd is now the task directory because that is what v1 uses as the project
            # root. `tsconfig.json` carries the tree's JSX settings with absolute `paths`;
            # `bunfig.toml` carries its preload (the Solid JSX transform).
            written = [path for path in (workdir_tsconfig(package_dir, work),
                                         workdir_bunfig(package_dir, work)) if path]
            # `--standalone` existed in the preview CLI and not in v1: v1's `run` accepts
            # `--format json` and rejects an unknown flag outright.
            preload = write_preload(work / ".opencode-state", version)
            # `--config`: bun reads `$cwd/bunfig.toml`, and the tree's own bunfig is what
            # registers the Solid JSX transform (`preload = ["@opentui/solid/preload"]`).
            # Running from the task directory without it makes every `.tsx` fall back to
            # the React runtime -- measured, `Cannot find module 'react/jsx-dev-runtime'`.
            prefix = [bun_binary(parent), "--no-env-file", "--no-install"]
            argv = [*prefix, "--preload", str(preload),
                    str(entry), "run", "--format", "json",
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
            if len(stream.sessions) != 1:
                raise ValueError("Expected exactly one OpenCode session in the JSON stream")
            # Export is a local state read, not another model request. It supplies finish
            # and usage even when noninteractive.ts reconstructed only text/tool events.
            # Built from `prefix` rather than `argv[:4]`: an earlier version sliced the
            # launch arguments, which silently cut `--preload` from its value and pointed
            # the export at the preview's `packages/cli`. It also has to run from the task
            # directory, or it opens a different project than the session it is asked about.
            export_argv = [*prefix, str(entry), "session", "export",
                           next(iter(stream.sessions))]
            export = subprocess.Popen(export_argv, cwd=work, env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8", errors="replace",
                                      start_new_session=os.name != "nt")
            try:
                snapshot_text, export_error = export.communicate(timeout=max(0.01, expires - time.monotonic()))
            except subprocess.TimeoutExpired:
                stop_process_tree(export)
                export.communicate()
                raise ValueError("OpenCode session export exceeded the task deadline")
            if export.returncode or not snapshot_text.strip():
                # **Best effort, and the run continues.** The export supplies `finish` and
                # per-step usage when the JSON stream did not; both are already in the
                # stream here (`step_finish` carries tokens), and the final message comes
                # from the stream too. Failing the task for it would record a plumbing
                # failure as a harness failure -- the one attribution mistake this platform
                # spends most of its machinery avoiding.
                #
                # Measured 2026-11-01: `session export` exits non-zero with **empty stderr**
                # from a task directory, while the same command works when the tree's
                # `bunfig.toml`/`tsconfig.json` are in the cwd -- and the harness removes
                # those before the next task.
                record({"export": {"ok": False,
                                   "exit_code": export.returncode,
                                   "detail": redact(export_error[-800:], parent)}})
            else:
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
            for path in written:
                # Platform plumbing for the CLI's compiler, not part of the task's result:
                # leaving it would hand a verifier a file the harness wrote and the task
                # never asked for.
                path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    return run(json.loads(args.task.read_text(encoding="utf-8")), args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
