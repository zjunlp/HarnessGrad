#!/usr/bin/env python3
"""Adapt Codex exec to HarnessGrad's task/answer/trace contract.

Prepare a copy with tools/configure_codex_harness.py before sandbox execution.
HG_AGENT_CODEX_COMMAND / HG_AGENT_CODEX must match that prepared installation.
Credentials come from HG_AGENT_API_KEY; no improver credentials reach Codex.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile


def _runtime() -> dict:
    """Load the helper from this candidate, including when the platform is hidden."""
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("_task_codex_runtime", root / "codex_runtime.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.resolve(root, dict(os.environ))


def _codex_argv() -> list[str]:
    return _runtime()["argv"]


def command(workdir: Path, answer: Path, *, argv: list[str] | None = None) -> list[str]:
    argv = _codex_argv() if argv is None else argv
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


def _answer_stamp(path: Path):
    """What `answer.txt` held before the CLI ran, so a change can be attributed."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def run(task: dict, workdir: Path) -> int:
    selected = _runtime()
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    answer = workdir / "answer.txt"
    # **`answer.txt` belongs to the task first.** The adapter used to delete it before the
    # CLI started and let Codex write it at exit (`-o`), which meant the task's own
    # deliverable -- the file a dataset's goal tells the agent to write -- was replaced by
    # Codex's final message. Measured 2026-11-01: the message Codex hands over is whatever
    # the model said last, and this model narrates before answering
    # ("The user wants me to calculate 17*23...\n\n\n17*23 = 391"), so an exact-content
    # task would have been scored against the narration. The dataset is what asks for an
    # answer (gsm8k's own goal names `answer.txt`), so a file the agent wrote is kept and
    # only the absence of one falls back to the CLI's channel.
    before = _answer_stamp(answer)
    goal = task.get("goal") or task.get("instruction")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task requires a non-empty goal")
    # **The final message is the hand-over, so do not ask for a description of it.**
    # Measured 2026-11-01 on `demo/t01` ("Answer with the word alpha"): this prompt said
    # "your final message should briefly describe the result", the model obliged with
    # `Done. I can see the command successfully returned "alpha" as the output.`, and the
    # exact-match verifier scored 0 -- for a reason the harness had created. The four
    # harnesses that scored 1.000 on the same task all say "answer only" instead
    # (`opencode`, `miniagent`, `openclaw`, and DSH's knobs.json preamble).
    prompt = ("Complete the following task in the current working directory. "
              "Use your tools to inspect and modify the actual task files. "
              "Write every file the task asks for exactly as asked.\n\n"
              + goal)
    if task.get("inputs"):
        prompt += "\n\nTask inputs:\n" + json.dumps(task["inputs"], ensure_ascii=False)
    # Daily CLI defaults and other model roles must not select this task's
    # provider, credentials or profile. CODEX_HOME is supplied privately below.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HG_METHOD_", "HG_ENV_", "HG_JUDGE_", "OPENAI_", "CODEX_"))
           and k not in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "NODE_OPTIONS", "NODE_PATH",
                         "LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH")}
    env["PATH"] = os.pathsep.join(dict.fromkeys([*selected["path"],
                                               *env.get("PATH", os.defpath).split(os.pathsep)]))
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
                    "agent_backend": "codex", "agent_model": model,
                    "codex_runtime": selected}, "task_id": task.get("task_id")})
            trace_events = CodexTrace(record)
            # `-o` writes Codex's final message, and it must not write it over the task's
            # own file: a scratch path inside the private home, copied in only if the agent
            # left no answer of its own.
            last_message = Path(home, "last-message.txt")
            proc = subprocess.Popen(command(workdir, last_message, argv=selected["argv"]), cwd=workdir, env=env,
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
            if proc.returncode:
                record({"error": "codex failed", "exit_code": proc.returncode})
                return proc.returncode
            try:
                record(submitted(answer, before, last_message))
            except ValueError as exc:
                record({"error": str(exc), "exit_code": proc.returncode})
                return 1
    return 0


def submitted(answer: Path, before, last_message: Path) -> dict:
    """The `submitted` record: the task's file if the agent wrote one, else the message.

    Separated from `run` so the precedence is testable without the CLI binary: the rule is
    the whole content, and getting it wrong is invisible in a score (a task graded on an
    exact file is graded on the model's prose instead).
    """
    current = _answer_stamp(answer)
    if current is not None and current != before and current.strip():
        return {"submitted": True, "answer": current.decode("utf-8", "replace"),
                "answer_source": "artifact"}
    text = (last_message.read_text(encoding="utf-8", errors="replace")
            if last_message.is_file() else "")
    if not text.strip():
        raise ValueError("codex produced no final message and the task wrote no answer.txt")
    answer.write_text(text, encoding="utf-8")
    return {"submitted": True, "answer": text, "answer_source": "final_message"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    return run(json.loads(args.task.read_text()), args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
