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
import shutil
import subprocess
import tempfile


def _codex_argv() -> list[str]:
    """The Codex binary, resolved the way the *sandbox* needs it, not the way `PATH` does.

    The platform binds a harness's declared `runtime_paths` read-only and does **not** put
    them on `PATH` (`eval/containers/sandbox.py`), so a bare `codex` inside the sandbox
    resolves only if some bound directory happens to be on `PATH` -- and it is not. Measured
    2026-10-29: this harness exited 1 with an empty trace on every sandboxed task while the
    same command worked outside the sandbox, and with `--no-sandbox` it ran.

    So the manifest's own `runtime_paths` is consulted **first**: it names the tree the
    platform bound for this harness, and the vendor directory it points at carries a real
    `codex` executable. `HG_AGENT_CODEX` overrides everything (an operator or a test can pin
    a binary), then `HG_AGENT_CODEX_COMMAND` for a wrapper, and `PATH` is the last resort.
    """
    configured = shlex.split(os.environ.get("HG_AGENT_CODEX_COMMAND", ""))
    if configured:
        return configured
    explicit = os.environ.get("HG_AGENT_CODEX")
    if explicit and Path(explicit).expanduser().exists():
        return [str(Path(explicit).expanduser())]
    try:
        declared = json.loads(Path(__file__).with_name("harness.json").read_text())
    except (OSError, ValueError):
        declared = {}
    for entry in declared.get("runtime_paths") or []:
        candidate = Path(str(entry)).expanduser() / "codex"
        if candidate.is_file():
            return [str(candidate)]
    found = shutil.which("codex")
    if found:
        return [found]
    return ["codex"]


def command(workdir: Path, answer: Path) -> list[str]:
    argv = _codex_argv()
    return argv + ["exec", "--json", "--ephemeral", "--skip-git-repo-check",
                   "--dangerously-bypass-approvals-and-sandbox", "-C", str(workdir),
                   "-o", str(answer), "-"]


#: `file_change` items are Codex's own patch tool, not a shell command. The contract's
#: `command` field is "the action that was executed", so the action is named for what it
#: was rather than smuggled in as a shell line that never ran.
PATCH_ACTION = "apply_patch"


class CodexTrace:
    """Translate `codex exec --json` into the adapter contract's records.

    Codex's stream is the CLI's own: `item.completed` carries an `item` whose `type` is
    `command_execution` (`command`, `aggregated_output`, `exit_code`) or `agent_message`
    (`text`), plus `file_change`; `turn.completed` closes a model turn and carries `usage`.
    The platform's one extractor counts `reply` and `command`
    (`eval/observe/tracing.py:loop_facts`), so without this translation a Codex run that
    issued forty commands is reported to the method as `0 steps, 0 commands` -- the initial
    record for this defect is the two shapes in `tests/quality/test_trace_contract.py`.

    The raw event is still recorded under `codex_event`: nothing is lost, and a reader who
    needs a field the contract does not name can still reach it. **Both exits go through
    here** -- the timeout path used to write every line raw, so the three tasks that hit the
    wall in the first Codex run (`cobol-modernization`, `git-multibranch`,
    `large-scale-text-editing`) contributed thousands of opaque strings instead of the
    commands and messages they produced, and a timeout is when a reader most needs them.
    """

    def __init__(self, record):
        self.record = record
        self.turns = 0
        self.reply = ""

    def feed(self, stdout: str) -> None:
        for line in (stdout or "").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                self.record({"codex_event_raw": line})
                continue
            self.record({"codex_event": event})
            if not isinstance(event, dict):
                continue
            kind, item = event.get("type"), event.get("item") or {}
            if kind != "item.completed" or not isinstance(item, dict):
                if kind == "turn.completed" and isinstance(event.get("usage"), dict):
                    self.turns += 1
                    # One `reply` per model turn, which is what `loop_facts` counts as a
                    # step; `calls` says the same thing in the usage record so a reader
                    # comparing the two can see them disagree.
                    self.record({"reply": self.reply})
                    self.record({"usage": {**event["usage"], "calls": self.turns}})
                continue
            if item.get("type") == "agent_message":
                self.reply = str(item.get("text") or "")
            elif item.get("type") == "command_execution":
                self.record({
                    "command": str(item.get("command") or ""),
                    "observation": {
                        "kind": "shell",
                        "returncode": item.get("exit_code"),
                        "output_chars": len(str(item.get("aggregated_output") or "")),
                        "truncated": False,
                    },
                })
            elif item.get("type") == "file_change":
                changes = item.get("changes") or []
                self.record({
                    "command": PATCH_ACTION,
                    "observation": {
                        "kind": "filesystem",
                        # Codex reports what changed, not a status: a patch that applied
                        # cleanly and one that failed are both `null` here, and the item's
                        # own `status` is in the raw event beside this record.
                        "returncode": None,
                        "output_chars": len(json.dumps(changes, ensure_ascii=False)),
                        "truncated": False,
                    },
                })


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
            trace_events = CodexTrace(record)
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
                trace_events.feed(stdout)
                record({"error": "codex timed out", "timeout_s": timeout,
                        "deadline_s": float(deadline) if deadline else None})
                return 1
            trace_events.feed(stdout)
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
