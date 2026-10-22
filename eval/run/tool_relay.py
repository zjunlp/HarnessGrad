"""Start a dataset's host-side environment for ONE task, and stop it again.

This is the runner-side half of the tool relay. A dataset that has an interaction environment
declares it in its `SETUP` with a `tool_relay` key::

    "tool_relay": {
        "program": "<absolute path to the dataset's relay program>",
        "content": "<the environment's own bytes, e.g. a game file>",
        # or "content_path": "<a file the runner copies here at start>"
        "budget":  50,               # optional
    }

`_setup` builds that declaration per task because the content is per task. It is **not** part of
`SETUP["files"]`, so it is never materialised into the agent's working directory: it goes to a
`0700` directory this module owns, outside the work root, and the only reader is the relay
process started here. The agent gets the socket (`eval/containers/tool_route.py` mounts it
read-only at `/run/harnessgrad-tool/tool.sock`) and can ask, never read.

**Two failure modes, and why they are not a zero.** A relay that never came up, died mid-episode,
or left no receipt means the task was not measured: the agent may have been calling into a hole.
`ToolRelay.fault()` reports that, and the runner turns it into `_invalid(..., stage="tool relay")`
rather than letting a truncated episode be scored as a bad answer. A tool call that the
*environment* rejected is a different thing entirely: it is a normal measured step, and it never
appears as a fault here.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import stat
import socket as socket_mod
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from data.scaffolds.runtime_extensions import validate_record as validate_record

#: Fixed destination the sandbox mounts the socket at. Kept in step with
#: `eval/containers/tool_route.py`, which publishes it; a dataset never chooses this.
TOOL_SOCKET = "/run/harnessgrad-tool/tool.sock"

#: How long a relay may take to bind before the task is called unmeasured.
READY_TIMEOUT_S = 60.0


class ToolRelay:
    """One task's environment process. Use as a context manager."""

    def __init__(self, path: Path, directory: Path, proc: subprocess.Popen,
                 receipt_path: Path, log_path: Path):
        self.path = path
        self.directory = directory
        self.proc = proc
        self.receipt_path = receipt_path
        self.log_path = log_path
        self.faults: list[str] = []
        self.receipt: dict | None = None
        self.stopped = False
        self.cleanup_errors: list[str] = []
        self.context: dict = {}
        self.record_spec: dict | None = None
        self.recording: dict | None = None
        self.model = None

    def __enter__(self) -> "ToolRelay":
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
        self.cleanup()

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        was_running = self.proc.poll() is None
        # Popen starts a new session. Stop descendants too, including when the
        # leader exited early. Never signal the runner's own process group.
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.faults.append("relay did not exit after SIGKILL")
        code = self.proc.returncode
        if code not in ((0, -15) if was_running else (0,)):
            self.faults.append(f"relay exited {code}: {self._tail()}")
        try:
            value = json.loads(self.receipt_path.read_text())
            if not isinstance(value, dict):
                raise ValueError("receipt must be an object")
            self.receipt = value
        except (OSError, ValueError):
            self.faults.append("relay wrote no valid receipt, so its call count is unknown")

    def _tail(self, limit: int = 800) -> str:
        try:
            return self.log_path.read_text(errors="replace")[-limit:]
        except OSError:
            return "(no log)"

    def fault(self) -> str | None:
        """`None` when the episode was measured against a live environment."""
        faults = list(self.faults)
        if self.model is not None and self.model.report()["faults"]:
            faults.append("environment model broker failed")
        return "; ".join(faults) if faults else None

    def cleanup(self) -> None:
        """Idempotent cleanup; errors are evidence, never a replacement exception."""
        self.stop()
        if self.model is not None:
            self.model.close()
        try:
            shutil.rmtree(self.directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.cleanup_errors.append(f"{type(exc).__name__}: {exc}")

    def recording_input(self, *, archive_root: Path | None = None) -> dict | None:
        """Seal a service-owned JSON payload after stop; project it only for scoring.

        Trust comes from custody outside the actor namespace, not its SHA256.
        The adapter owns payload semantics; this layer owns binding and transport.
        """
        if self.record_spec is None:
            return None
        if not self.stopped or self.fault():
            raise RuntimeError("cannot seal a running or failed relay")
        source = self.directory / "episode.json"
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError("relay recording must be a private regular file")
            limit = self.record_spec.get("max_bytes", 32 * 1024 * 1024)
            raw = stream.read() if limit is None else stream.read(limit + 1)
        if limit is not None and len(raw) > limit:
            raise RuntimeError("relay recording exceeds max_bytes")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("relay recording must contain a JSON object")
        envelope = {"schema": "harnessgrad.interaction-record.v1",
                    "binding": self.context, "payload": payload}
        sealed = self.directory / "sealed"
        sealed.mkdir(mode=0o700)
        target = sealed / "episode.json"
        data = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()
        target.write_bytes(data)
        self.recording = {"schema": envelope["schema"], "binding": self.context,
                          "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if archive_root is not None:
            archive_root = Path(archive_root)
            archive_root.mkdir(parents=True, exist_ok=True)
            destination = Path(tempfile.mkdtemp(prefix="interaction-", dir=archive_root))
            shutil.copyfile(target, destination / "episode.json")
            self.recording["archive"] = str(destination / "episode.json")
        return {"path": self.record_spec["path"], "from": str(sealed), "immutable": True}

    def report(self) -> dict:
        return {"receipt": self.receipt, "faults": list(self.faults),
                "model": self.model.report() if self.model is not None else None,
                "recording": self.recording,
                "cleanup": {"complete": not self.directory.exists(),
                            "errors": list(self.cleanup_errors)}}


def declaration(setup: dict | None) -> dict | None:
    return (setup or {}).get("tool_relay")


def start(setup: dict | None, *, ready_timeout_s: float = READY_TIMEOUT_S,
          context: dict | None = None) -> ToolRelay | None:
    """Start the relay `setup` declares, or return `None` for a task that needs none.

    A declared relay that cannot start raises `RuntimeError` rather than returning `None`:
    "this dataset needs an environment" and "this dataset has none" must not be the same
    answer, because the first one silently rewards an agent that was never measured.
    """
    decl = declaration(setup)
    if not decl:
        return None
    validate_record(decl.get("record"))
    filename = decl.get("filename", "environment")
    if not isinstance(filename, str) or filename in ("", ".", "..") or Path(filename).name != filename:
        raise RuntimeError("tool relay filename must be one path component")
    program = Path(decl["program"])
    if not program.is_file():
        raise RuntimeError(f"tool relay program is missing: {program}")

    directory = Path(tempfile.mkdtemp(prefix="hg-toolrelay-"))
    handle = None
    model = None
    try:
        os.chmod(directory, 0o700)
        state = directory / "state"
        state.mkdir(mode=0o700)
        # The staged file keeps the name the environment expects. TextWorld guesses the world
        # format from the extension, so a file called `environment` is not a `.tw-pddl` and the
        # relay dies at startup -- which the runner then reports as an unmeasured task.
        payload = state / filename
        if "content" in decl:
            payload.write_text(decl["content"], encoding="utf-8")
        else:
            # `content_path` is for a dataset whose `SETUP` must not read the file: it hands the
            # path and the runner copies it here, so the relay still gets a private copy and the
            # agent still gets nothing.
            payload.write_bytes(Path(decl["content_path"]).read_bytes())
        os.chmod(payload, 0o600)
        sock = directory / "tool.sock"
        receipt_path = directory / "receipt.json"
        log_path = directory / "relay.log"

        # A dataset may need its own interpreter: AgentDojo's environment imports the task suite and
        # the attacks, which live in the dataset's provisioned runtime and not in the driver's python.
        argv = [str(decl.get("python") or sys.executable), str(program), "--socket", str(sock),
                "--receipt", str(receipt_path)]
        # The program names its own input flag; `content_flag` defaults to `--game` because that
        # is what the first migrated dataset uses, and a second dataset states its own.
        argv += [decl.get("content_flag", "--game"), str(payload)]
        for flag, key in (("--budget", "budget"), ("--deadline-s", "deadline_s"),
                          ("--max-calls", "max_calls"), ("--max-line-bytes", "max_line_bytes")):
            if decl.get(key) is not None:
                argv += [flag, str(decl[key])]

        child_env = None
        if decl.get("record") is not None:
            binding = {**(context or {}), "input_sha256": hashlib.sha256(payload.read_bytes()).hexdigest()}
            if not all(isinstance(binding.get(k), str) and binding[k] for k in ("task_id", "run_id", "trial_id")):
                raise RuntimeError("recorded relay requires task_id/run_id/trial_id")
            context_path = directory / "context.json"
            context_path.write_text(json.dumps(binding))
            child_env = {**os.environ, "HG_RELAY_CONTEXT_PATH": str(context_path),
                         "HG_RELAY_RECORD_PATH": str(directory / "episode.json")}
        if decl.get("model") is not None:
            from eval.service.judge import Judge
            model = Judge(decl["model"], role="environment",
                          trial_seed=(context or {}).get("seed")).__enter__()
            child_env = {k: v for k, v in (child_env or os.environ).items()
                         if not k.startswith(("HG_AGENT_", "HG_METHOD_", "HG_JUDGE_", "HG_ENV_"))}
            child_env["HG_ENV_MODEL_SOCKET"] = str(model.path)
        log = log_path.open("w")
        try:
            proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True, env=child_env)
        finally:
            log.close()

        handle = ToolRelay(sock, directory, proc, receipt_path, log_path)
        handle.model = model
        handle.record_spec = decl.get("record")
        if handle.record_spec is not None:
            handle.context = binding
        deadline = time.monotonic() + ready_timeout_s
        while True:
            if proc.poll() is not None:
                handle.faults.append(f"relay exited during startup: {handle._tail()}")
                raise RuntimeError(handle.fault())
            if sock.exists():
                probe = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
                probe.settimeout(2.0)
                try:
                    probe.connect(str(sock))
                    return handle
                except OSError:
                    pass
                finally:
                    probe.close()
            if time.monotonic() > deadline:
                handle.stop()
                handle.faults.append(f"relay did not accept a connection within {ready_timeout_s}s")
                raise RuntimeError(handle.fault())
            time.sleep(0.05)
    except BaseException as exc:
        if model is not None:
            model.close()
        if handle is not None:
            handle.cleanup()
        else:
            try:
                shutil.rmtree(directory)
            except OSError as cleanup_exc:
                raise RuntimeError(f"{exc}; relay cleanup failed: {cleanup_exc}") from exc
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        detail = str(exc)
        if handle is not None and handle.cleanup_errors:
            detail += f"; cleanup: {handle.cleanup_errors}"
        raise RuntimeError(detail) from exc
