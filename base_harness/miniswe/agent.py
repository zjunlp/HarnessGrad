#!/usr/bin/env python3
"""Adapt mini-SWE-agent (`mini`) to HarnessGrad's task/answer/trace contract.

    agent.py --task <task.json> --workdir <dir>   ->  exit 0, answer.txt, trace.jsonl

Why this harness and not another loop
-------------------------------------
`base_harness/loop` is the platform's own minimal ReAct loop; this is **somebody else's
agent** -- mini-SWE-agent 2.4.6, the shell agent of the SWE-agent lineage, which is what
RRSI's published baselines pin (2.4.5) and the implementation most readers will have
actually run. Two very different harness ancestries on one instrument is the point of the
platform, and the two are not interchangeable: `loop` sends one JSON object per turn and
parses a `{"command": ...}`; mini sends an OpenAI **tool call** and keeps a trajectory of
its own.

Three things had to be translated, and each one is a place where a naive wrapper would
have produced a believable wrong number:

* **Dependencies.** mini is a `pip install --user` install, so `minisweagent` lives under
  the operator's home. The sandbox replaces `HOME` with a per-task directory, so Python's
  user-site lookup finds nothing and `import minisweagent` fails with a traceback that
  reads like a broken harness. The manifest declares that tree in `runtime_paths` (the
  platform binds it read-only) and this file puts it on the child's `PYTHONPATH`.
* **Its global config.** `mini` refuses to start without `$HOME/.config/mini-swe-agent`
  and, when it is missing, opens an interactive setup wizard that aborts without a TTY.
  `MSWEA_CONFIGURED`/`MSWEA_SILENT_STARTUP`/`MSWEA_GLOBAL_CONFIG_DIR` move that config into
  this task's own scratch directory, so one task cannot inherit another's settings and
  nothing is written under the operator's home.
* **Cost accounting.** mini prices its calls through litellm, and litellm has no price for
  a local model: without `MSWEA_COST_TRACKING=ignore_errors` the first call raises and the
  run records a harness failure. Measured. The cost *limit* is disabled separately
  (`--cost-limit 0`), because the platform owns the budget, not the harness.

The mock backend exists for the same reason every other harness here has one: the validator
runs a harness on a trivial task with no model configured. See `mock_model.py`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

#: mini's own tool-call config, named explicitly because passing any `--config` replaces
#: the default rather than adding to it.
CONFIG = "mini.yaml"

#: Steps, not seconds: a shell agent with an unlimited step budget spends the platform's
#: `agent_timeout_s` on one task and reports a timeout instead of a harness that looped.
#: Comparable to `base_harness/loop`'s 32.
DEFAULT_STEP_LIMIT = 30


def _manifest() -> dict:
    try:
        return json.loads(Path(__file__).with_name("harness.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _site_for_child() -> str | None:
    """Where `minisweagent` is importable from, for the child's `PYTHONPATH`.

    `None` means the running interpreter already has it, which is the ordinary case
    outside a sandbox; inside one, `HOME` is a per-task directory and the package sits
    under the operator's real home, so the tree has to be named -- and it has to be named
    in `runtime_paths` too, or the platform never binds it and the child finds nothing
    there either.
    """
    if _importable("minisweagent"):
        return None
    candidates = [Path(str(entry)).expanduser()
                  for entry in (_manifest().get("runtime_paths") or [])]
    try:
        import site
        candidates.append(Path(site.getusersitepackages()))
    except Exception:                                            # noqa: BLE001
        pass
    for candidate in candidates:
        if (candidate / "minisweagent" / "__init__.py").is_file():
            return str(candidate)
    raise SystemExit(
        "minisweagent is not importable and no declared tree holds it. Install "
        "`mini-swe-agent` or declare the directory that contains the `minisweagent` "
        f"package in harness.json's runtime_paths (declared: "
        f"{_manifest().get('runtime_paths')}).")


def _importable(module: str) -> bool:
    """`find_spec`, not `__import__`: importing the package to ask whether it imports is a
    side effect, and mini's import prints a startup banner and loads the operator's global
    config -- which then lands in the *harness's* stderr for no reason."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _model(env: dict, backend: str) -> str:
    """mini wants `provider/model`; the platform's `HG_AGENT_MODEL` is usually bare.

    The mock backend names one when the environment does not: the validator runs a harness
    with no model configured, and a harness that refuses to start there cannot prove it
    writes an answer file. What the mock is called never reaches a real endpoint.
    """
    name = (env.get("HG_AGENT_MINISWE_MODEL") or env.get("HG_AGENT_MODEL") or "").strip()
    if not name:
        if backend == "mock":
            return "openai/miniswe-mock"
        raise SystemExit("no model: HG_AGENT_MODEL is empty and mini needs one")
    return name if "/" in name else f"openai/{name}"


def submission_paths(task: dict) -> list[str] | None:
    """The published file-submission descriptor, if the task has one.

    Copied rather than imported: a harness runs with the platform's tree hidden (that is
    the sandbox), so it cannot import the platform's helper. The shape is the one
    `base_harness/opencode` already reads -- `{"kind": "files", "paths": [...]}` -- and it
    is public task metadata, never an inference from the id or the grader.
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


def task_text(task: dict) -> str:
    """The one text mini is given: the goal, plus how the platform expects the result.

    mini's own template ends with "run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`", so
    what is added here is only the part mini cannot know: whether the answer is a file it
    must leave in place or a final message.
    """
    goal = task.get("goal") or task.get("instruction")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task requires a non-empty goal or instruction")
    paths = submission_paths(task)
    delivery = (
        "The task submits these files, relative to the working directory: "
        + json.dumps(paths) + ". Leave them in place for the platform's scorer; do not "
        "write answer.txt instead. "
        if paths is not None else
        "Write every file the task asks for exactly as asked. ")
    # The delivery rule goes last on purpose. Measured 2026-11-01 on `demo/t01`: the rule
    # was already here, and the run still submitted `Wait, I need to re-read the
    # instructions more carefully...` because the model's last message was the musing it
    # ended on. Nothing is extracted or guessed -- the sentence is the only lever, so it is
    # the closing one.
    text = goal.strip() + "\n\n" + delivery
    if task.get("inputs") is not None:
        text += "\nTask inputs:\n" + json.dumps(task["inputs"], ensure_ascii=False)
    return text


def _stamp(workdir: Path):
    path = workdir / "answer.txt"
    return (path.stat().st_mtime_ns, path.read_bytes()) if path.is_file() else None


def save_answer(workdir: Path, task: dict, before, final: str) -> tuple[str, str]:
    """`(answer, source)`, or raise when the run left nothing to score.

    The three sources are distinguished because they are three different behaviours: a
    file the harness wrote, a file the *task* asked it to write, and a final message the
    platform turns into one. Recording which one it was is what lets a reader see that a
    harness which never writes files still answered.
    """
    paths = submission_paths(task)
    if paths is not None:
        root = workdir.resolve()
        missing = [name for name in paths
                   if not (root / name).is_file()
                   or not (root / name).resolve().is_relative_to(root)]
        if missing:
            raise ValueError("missing submission files: " + ", ".join(missing))
        return "", "files"
    current = _stamp(workdir)
    if current is not None and current != before and current[1].strip():
        return current[1].decode("utf-8", "replace"), "artifact"
    if not final.strip():
        raise ValueError("the run produced no final message and no answer.txt")
    (workdir / "answer.txt").write_text(final, encoding="utf-8")
    return final, "final_message"


def write_trace(trajectory: Path, record) -> dict:
    """Turn mini's own trajectory into the platform's trace, keeping what it counts.

    The platform's facts read `command` (one per executed shell command) and `reply` (one
    per model turn), so both are written per step: a trace of opaque blobs would leave the
    platform reporting a harness that did nothing. `usage` is the last message mini keeps
    token counts in -- absent when the endpoint reports none, which is a fact rather than
    a zero.
    """
    try:
        data = json.loads(trajectory.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"steps": 0, "reply": "", "tokens": {}}
    steps, actions, reply, commands = 0, 0, "", []
    tokens = {"input_tokens": 0, "output_tokens": 0}
    for message in data.get("messages") or []:
        role = message.get("role")
        extra = message.get("extra") or {}
        if role == "assistant":
            reply = (message.get("content") or "").strip()
            commands = [action.get("command") for action in (extra.get("actions") or [])
                        if isinstance(action, dict)]
            usage = ((extra.get("response") or {}).get("usage") or {})
            for source, target in (("prompt_tokens", "input_tokens"),
                                   ("completion_tokens", "output_tokens")):
                if isinstance(usage.get(source), int):
                    tokens[target] += usage[source]
            # **One record per model turn**, carrying only `reply`. A record that carried
            # both `reply` and `command` would be counted twice by the platform: once as a
            # turn (`model_calls`) and once as an action (`commands`) -- measured on this
            # harness's first shape, where three commands in one turn reported
            # `model_calls: 4`.
            steps += 1
            record({"step": steps, "reply": reply})
        elif role == "tool":
            output = extra.get("raw_output") or ""
            for command in commands or [None]:
                actions += 1
                # The environment's answer, in the one shape every adapter writes
                # (`docs/adapter-spec.md`): `returncode` reads the same for a shell, an API
                # and a file system, and the platform counts it in `loop_facts` as
                # `observations` / `nonzero_returns`.
                record({"step": steps, "command": command or "",
                        "observation": {"kind": "shell",
                                        "returncode": extra.get("returncode"),
                                        "output_chars": len(output),
                                        "truncated": False},
                        **({"error": extra["exception_info"]}
                           if extra.get("exception_info") else {})})
            commands = []
    stats = (data.get("info") or {}).get("model_stats") or {}
    calls = int(stats.get("api_calls") or 0)
    if calls or tokens["input_tokens"] or tokens["output_tokens"]:
        record({"usage": {**tokens, "calls": calls}})
    return {"steps": steps, "actions": actions, "reply": reply, "tokens": tokens,
            "info": data.get("info") or {}}


def run(task: dict, workdir: Path) -> int:
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HG_METHOD_")}
    backend = env.get("HG_AGENT_BACKEND", "mock")
    model = _model(env, backend)
    server = None
    if backend == "mock":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mock_model
        base_url, server = mock_model.start()
    else:
        base_url = (env.get("HG_AGENT_BASE_URL") or "").strip()
        if not base_url:
            raise SystemExit("HG_AGENT_BASE_URL is empty: mini needs an OpenAI-compatible "
                             "endpoint, and the platform is the one that names it")
    api_key = (env.get("HG_AGENT_API_KEY") or "local").strip()
    step_limit = int(env.get("HG_MINISWE_STEP_LIMIT") or DEFAULT_STEP_LIMIT)
    deadline = env.get("HG_AGENT_DEADLINE_S")
    if deadline:
        timeout = max(30.0, float(deadline) - float(env.get("HG_AGENT_STOP_MARGIN_S", "30")))
    else:
        timeout = float(env.get("HG_MINISWE_TIMEOUT_S", "780"))
    site = _site_for_child()

    before = _stamp(workdir)
    try:
        # One writer for the whole run, opened before the first record and closed after the
        # last: an earlier version wrote the identity through a live handle and the steps
        # through a second one that had already been closed, which failed only after the
        # money had been spent.
        with (workdir / "trace.jsonl").open("w", buffering=1) as trace:
            def record(value):
                trace.write(json.dumps(value, ensure_ascii=False) + "\n")

            record({"harness_identity": {
                "harness": "miniswe", "agent_backend": backend, "agent_model": model,
                "config": {"step_limit": step_limit, "config": CONFIG,
                           "api_base": base_url,
                           # `None` means the running interpreter already had the package;
                           # a path means it came from the tree this manifest declares and
                           # the platform bound. That is the difference between "the
                           # dependency was there" and "the harness found it", and only
                           # one of them survives a sandbox.
                           "pythonpath": site},
                "harness_version": _manifest().get("version")},
                "task_id": task.get("task_id")})
            with tempfile.TemporaryDirectory(prefix="hg-miniswe-") as scratch:
                scratch_dir = Path(scratch)
                (scratch_dir / "config").mkdir()
                trajectory = scratch_dir / "trajectory.json"
                argv = [sys.executable, "-m", "minisweagent.run.mini",
                        "--task", task_text(task),
                        "--model", model,
                        "--yolo", "--exit-immediately",
                        # The platform owns the budget; mini's own price book has no entry
                        # for a local model, so a cost limit here limits nothing.
                        "--cost-limit", "0",
                        "--output", str(trajectory),
                        "--config", CONFIG,
                        "--config", f"model.model_kwargs.api_base={base_url}",
                        "--config", f"model.model_kwargs.api_key={api_key}",
                        "--config", f"agent.step_limit={step_limit}"]
                child = dict(env)
                child.update({
                    # No interactive first-run wizard: it needs a TTY and would abort.
                    "MSWEA_CONFIGURED": "true",
                    "MSWEA_SILENT_STARTUP": "1",
                    # Per task, inside the scratch directory: one task must not inherit
                    # another's settings and nothing is written to the operator's home.
                    "MSWEA_GLOBAL_CONFIG_DIR": str(scratch_dir / "config"),
                    # Without this the first call raises on an unpriced model (measured).
                    "MSWEA_COST_TRACKING": "ignore_errors",
                    "OPENAI_API_KEY": api_key,
                    "OPENAI_BASE_URL": base_url,
                    "OPENAI_API_BASE": base_url,
                })
                if site:
                    existing = child.get("PYTHONPATH")
                    child["PYTHONPATH"] = os.pathsep.join(
                        [site, existing] if existing else [site])
                log_path = scratch_dir / "mini.log"
                with log_path.open("w") as log:
                    proc = subprocess.Popen(argv, cwd=workdir, env=child, stdout=log,
                                            stderr=subprocess.STDOUT, text=True, errors="replace")
                    timed_out = False
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        proc.kill()
                        proc.wait()
                summary = write_trace(trajectory, record)
                tail = " ".join(log_path.read_text(errors="replace").splitlines()[-3:])[-400:]
            if timed_out:
                record({"error": "miniswe timed out", "timeout_s": timeout,
                        "deadline_s": float(deadline) if deadline else None})
            try:
                final = str((summary.get("info") or {}).get("submission") or "")
                answer, source = save_answer(workdir, task, before,
                                             final or summary.get("reply") or "")
            except ValueError as exc:
                record({"error": str(exc), "exit_code": proc.returncode,
                        "child_stderr_tail": tail})
                print(f"miniswe: {exc}\n{tail}", file=sys.stderr)
                return proc.returncode or 1
            info = summary.get("info") or {}
            record({"submitted": True, "answer": answer, "answer_source": source,
                    "exit_code": proc.returncode, "timed_out": timed_out,
                    "steps": summary.get("steps"),
                    # The CLI's own version, from its own trajectory: a harness record
                    # that names only this wrapper cannot tell 2.4.5 from 2.4.6, and the
                    # published baselines pin one.
                    "mini_version": info.get("mini_version"),
                    "exit_status": info.get("exit_status")})
        if proc.returncode:
            # The platform reads *this* process's stderr, not the CLI's: without the tail a
            # harness failure says nothing about why.
            print(f"miniswe exited {proc.returncode}: {tail}", file=sys.stderr)
            return proc.returncode
        if timed_out:
            print(f"miniswe timed out after {timeout:.0f}s: {tail}", file=sys.stderr)
            return 1
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    try:
        task = json.loads(args.task.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"miniswe: cannot read the task: {exc}", file=sys.stderr)
        return 2
    return run(task, args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
