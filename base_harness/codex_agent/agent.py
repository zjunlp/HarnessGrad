#!/usr/bin/env python3
"""Adapt Codex exec to HarnessGrad's task/answer/trace contract.

HG_AGENT_CODEX_COMMAND may name a native executable or 'node /path/codex.js'.
The deployment must provide that runtime inside the task sandbox. Credentials
come from HG_AGENT_API_KEY; no improver credentials are passed to Codex.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


def command(workdir: Path, answer: Path) -> list[str]:
    argv = shlex.split(os.environ.get("HG_AGENT_CODEX_COMMAND", "codex"))
    if not argv:
        raise ValueError("HG_AGENT_CODEX_COMMAND is empty")
    return argv + ["exec", "--json", "--ephemeral", "--skip-git-repo-check",
                   "--dangerously-bypass-approvals-and-sandbox", "-C", str(workdir),
                   "-o", str(answer), "-"]


def _events(stdout: str, record) -> None:
    """Record Codex's JSONL as **parsed** events, falling back to the raw line.

    **Both exits go through here, and that is the point.** The timeout path used to write
    every line as `codex_event_raw` without trying to parse it, so the three tasks that hit
    the wall in the first Codex-agent run -- `cobol-modernization` (831 lines),
    `git-multibranch` (952), `large-scale-text-editing` (762) -- contributed thousands of
    opaque strings instead of the commands and messages the agent produced. The content was
    there; the shape was not, and the shape is what the platform's index files and facts
    blocks count. A timeout is when a reader most wants to know what the agent was doing.
    """
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            record({"codex_event_raw": line})
            continue
        record({"codex_event": event})
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            record({"usage": event["usage"]})


def run(task: dict, workdir: Path) -> int:
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    answer = workdir / "answer.txt"
    answer.unlink(missing_ok=True)
    goal = task.get("goal") or task.get("instruction")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task requires a non-empty goal")
    prompt = ("Complete the following task in the current working directory. "
              "Use your tools to inspect and modify the actual task files. "
              "Your final message should briefly describe the result.\n\n" + goal)
    if task.get("inputs"):
        prompt += "\n\nTask inputs:\n" + json.dumps(task["inputs"], ensure_ascii=False)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HG_METHOD_")}
    # **Stop before the environment kills the container.** `HG_AGENT_DEADLINE_S` is the
    # platform's own `agent_timeout_s`; the margin lets Codex be reaped and the trace be
    # written, so a run that ran out of time is recorded as a harness that stopped rather
    # than as a container that was killed (which reads like the harness never started).
    deadline = env.get("HG_AGENT_DEADLINE_S")
    if deadline:
        timeout = max(60.0, float(deadline) - float(env.get("HG_AGENT_STOP_MARGIN_S", "90")))
    else:
        timeout = float(env.get("HG_AGENT_CODEX_TIMEOUT_S", "780"))
    model = env.get("HG_AGENT_MODEL", "")
    with tempfile.TemporaryDirectory(prefix="hg-task-codex-") as home:
        # Private per-task config avoids importing the user's rules or conversation.
        env["CODEX_HOME"] = home
        config = ["approval_policy = \"never\"", "sandbox_mode = \"danger-full-access\"",
                  "model_context_window = 32768",
                  "model_auto_compact_token_limit = 16000",
                  # Parity with the known-good local config (`~/.codex-local9b/config.toml`):
                  # both are needed for this endpoint, and a generated config that omits them
                  # is a second, untested configuration.
                  "model_reasoning_effort = \"low\"",
                  "disable_response_storage = true",
                  "suppress_unstable_features_warning = true"]
        if model:
            config.append("model = " + json.dumps(model))
        if env.get("HG_AGENT_BASE_URL"):
            config += ["model_provider = \"task_endpoint\"",
                       "[model_providers.task_endpoint]", "name = \"task_endpoint\"",
                       "base_url = " + json.dumps(env["HG_AGENT_BASE_URL"]),
                       "env_key = \"HG_AGENT_API_KEY\"", "wire_api = \"responses\""]
        Path(home, "config.toml").write_text("\n".join(config) + "\n")
        with (workdir / "trace.jsonl").open("w", buffering=1) as trace, \
                (workdir / "codex.stderr.log").open("w") as errors:
            def record(value):
                trace.write(json.dumps(value, ensure_ascii=False) + "\n")
            record({"harness_identity": {"harness": "codex-agent",
                    "agent_backend": "codex", "agent_model": model}, "task_id": task.get("task_id")})
            proc = subprocess.Popen(command(workdir, answer), cwd=workdir, env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    # NB: `errors` above is the caller's stream object, not this
                                    # kwarg; decoding must still not raise on agent output.
                                    stderr=errors, text=True, errors="replace")
            try:
                stdout, _ = proc.communicate(prompt, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, _ = proc.communicate()
                _events(stdout, record)
                record({"error": "codex timed out", "timeout_s": timeout,
                        "deadline_s": float(deadline) if deadline else None})
                return 1
            _events(stdout, record)
            if proc.returncode or not answer.is_file():
                record({"error": "codex failed or produced no final message",
                        "exit_code": proc.returncode})
                return proc.returncode or 1
            record({"submitted": True, "answer": answer.read_text()})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    return run(json.loads(args.task.read_text()), args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
