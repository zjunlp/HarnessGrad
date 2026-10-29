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

import base64
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
import traceback
from pathlib import Path

from data.scaffolds.runtime_extensions import validate_record as validate_record

#: Fixed destination the sandbox mounts the socket at. Kept in step with
#: `eval/containers/tool_route.py`, which publishes it; a dataset never chooses this.
TOOL_SOCKET = "/run/harnessgrad-tool/tool.sock"

#: How long a relay may take to bind before the task is called unmeasured.
READY_TIMEOUT_S = 60.0

# Retention limits follow the transport's default frame limit and the existing
# recording limit. They bound private diagnostics, never task execution or scores.
DIAGNOSTIC_FILE_BYTES = 8 * 1024 * 1024
DIAGNOSTIC_EPISODE_BYTES = 32 * 1024 * 1024


def _diagnostic_file(path: Path, limit: int, *, tail=False) -> dict:
    result = {'state': 'missing', 'bytes': None, 'captured_bytes': 0,
              'sha256': None, 'limit_bytes': limit, 'offset': 0}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('diagnostic source is not a private regular file')
            result['bytes'] = info.st_size
            offset = max(0, info.st_size - limit) if tail else 0
            stream.seek(offset)
            raw = stream.read(limit)
        try:
            content, encoding = raw.decode('utf-8'), 'utf-8'
        except UnicodeDecodeError:
            content, encoding = base64.b64encode(raw).decode('ascii'), 'base64'
        result.update(state='truncated' if len(raw) < info.st_size else 'captured',
                      captured_bytes=len(raw), offset=offset,
                      sha256=hashlib.sha256(raw).hexdigest(),
                      hash_scope='captured_bytes', content=content, encoding=encoding)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        result.update(state='unreadable', error={'type': type(exc).__name__})
    return result


def _diagnostic_metadata(value):
    """Raw files and exception details belong only in private trial evidence."""
    if isinstance(value, dict):
        return {key: _diagnostic_metadata(item) for key, item in value.items()
                if key not in ('content', 'message', 'traceback')}
    if isinstance(value, list):
        return [_diagnostic_metadata(item) for item in value]
    return value


def _snapshot(directory, context, *, stage, record_spec=None, exception=None, recording=None):
    limit = (record_spec or {}).get('max_bytes', DIAGNOSTIC_EPISODE_BYTES)
    limit = min(limit, DIAGNOSTIC_EPISODE_BYTES) if limit is not None else DIAGNOSTIC_EPISODE_BYTES
    result = {'schema': 'harnessgrad.tool-relay-diagnostic.v1', 'binding': dict(context),
              'stage': stage, 'scorable': False, 'files': {
                  'relay.log': _diagnostic_file(directory / 'relay.log', DIAGNOSTIC_FILE_BYTES, tail=True),
                  'receipt.json': _diagnostic_file(directory / 'receipt.json', DIAGNOSTIC_FILE_BYTES),
                  'episode.json': ({'state': 'not_needed', 'recording': dict(recording)} if recording is not None
                                   else _diagnostic_file(directory / 'episode.json', limit))}, 'errors': []}
    if exception is not None:
        message = str(exception)
        trace = ''.join(traceback.format_exception(type(exception), exception, exception.__traceback__))
        result['exception'] = {'type': type(exception).__name__, 'message': message[:8192],
                               'traceback': trace[:32768],
                               'truncated': len(message) > 8192 or len(trace) > 32768}
    return result


class ToolRelayStartError(RuntimeError):
    """Startup failed; metadata is public, original evidence stays private."""
    def __init__(self, message, *, safe_message, report, diagnostic):
        super().__init__(message)
        self.safe_message = safe_message
        self.report = report
        self.diagnostic = diagnostic


class ToolRelay:
    """One task's environment process. Use as a context manager."""

    def __init__(self, path: Path, directory: Path, proc: subprocess.Popen,
                 receipt_path: Path, log_path: Path, *, private_diagnostics=False):
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
        self.diagnostic: dict | None = None
        self.private_diagnostics = private_diagnostics

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
            suffix = '' if self.private_diagnostics else ': ' + self._tail()
            self.faults.append(f"relay exited {code}{suffix}")
        try:
            value = json.loads(self.receipt_path.read_text())
            if not isinstance(value, dict):
                raise ValueError("receipt must be an object")
            self.receipt = value
        except (OSError, ValueError):
            self.faults.append("relay wrote no valid receipt, so its call count is unknown")

    def _tail(self, limit: int = 800) -> str:
        try:
            with self.log_path.open('rb') as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - limit * 4))
                return stream.read(limit * 4).decode(errors='replace')[-limit:]
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
        if self.private_diagnostics or self.fault():
            self.capture_diagnostic()
        if self.model is not None:
            try:
                self.model.close()
            except Exception as exc:
                self.cleanup_errors.append(f'model cleanup failed: {type(exc).__name__}')
        try:
            shutil.rmtree(self.directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.cleanup_errors.append(f"{type(exc).__name__}: {exc}")

    def capture_diagnostic(self, *, stage='runtime') -> dict:
        """Retain a bounded private snapshot before cleanup, without scoring it.

        The runner places this full value only in private trial evidence; report()
        exposes metadata. Idempotence keeps the evidence available after deletion.
        Collection errors are secondary and must not replace the original fault.
        """
        if self.diagnostic is not None:
            return self.diagnostic
        try:
            self.stop()
            self.diagnostic = _snapshot(self.directory, self.context, stage=stage,
                                        record_spec=self.record_spec,
                                        recording=self.recording if not self.fault() else None)
        except Exception as exc:
            self.diagnostic = {'schema': 'harnessgrad.tool-relay-diagnostic.v1',
                               'binding': dict(self.context), 'stage': stage, 'scorable': False,
                               'files': {}, 'errors': [{'type': type(exc).__name__}]}
        return self.diagnostic

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
        return {"receipt": (_diagnostic_metadata(self.receipt) if self.private_diagnostics else self.receipt),
                "faults": list(self.faults),
                "model": self.model.report() if self.model is not None else None,
                "recording": self.recording,
                "diagnostic": _diagnostic_metadata(self.diagnostic),
                "cleanup": {"complete": not self.directory.exists(),
                            "errors": list(self.cleanup_errors)}}


def declaration(setup: dict | None) -> dict | None:
    return (setup or {}).get("tool_relay")


def start(setup: dict | None, *, ready_timeout_s: float = READY_TIMEOUT_S,
          context: dict | None = None, private_diagnostics=False) -> ToolRelay | None:
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
    binding = dict(context or {})
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

        handle = ToolRelay(sock, directory, proc, receipt_path, log_path,
                           private_diagnostics=private_diagnostics)
        handle.model = model
        handle.record_spec = decl.get("record")
        handle.context = binding
        deadline = time.monotonic() + ready_timeout_s
        while True:
            if proc.poll() is not None:
                handle.faults.append(f"relay exited during startup (code {proc.returncode})")
                raise RuntimeError(f"{handle.fault()}: {handle._tail()}")
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
        if handle is not None:
            handle.capture_diagnostic(stage='startup')
            handle.cleanup()
            diagnostic = handle.diagnostic
            report = handle.report()
            safe_message = (f'tool relay startup failed: {type(exc).__name__}; '
                            f'relay exit code {handle.proc.returncode}')
        else:
            try:
                diagnostic = _snapshot(directory, binding, stage='startup',
                                       record_spec=decl.get('record'), exception=exc)
            except Exception as capture_exc:
                diagnostic = {'schema': 'harnessgrad.tool-relay-diagnostic.v1',
                              'binding': binding, 'stage': 'startup', 'scorable': False, 'files': {},
                              'exception': {'type': type(exc).__name__},
                              'errors': [{'stage': 'capture', 'type': type(capture_exc).__name__}]}
            if model is not None:
                try:
                    model.close()
                except Exception as cleanup_exc:
                    diagnostic['errors'].append({'stage': 'model cleanup', 'type': type(cleanup_exc).__name__})
            try:
                shutil.rmtree(directory)
            except OSError as cleanup_exc:
                diagnostic['errors'].append({'stage': 'cleanup', 'type': type(cleanup_exc).__name__})
            safe_message = f'tool relay startup failed: {type(exc).__name__}'
            report = {'receipt': None, 'faults': [safe_message], 'model': None, 'recording': None,
                      'diagnostic': _diagnostic_metadata(diagnostic),
                      'cleanup': {'complete': not directory.exists(),
                                  'errors': list(diagnostic['errors'])}}
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        detail = str(exc)
        if handle is not None and handle.cleanup_errors:
            detail += f"; cleanup: {handle.cleanup_errors}"
        raise ToolRelayStartError(detail, safe_message=safe_message,
                                  report=report, diagnostic=diagnostic) from exc
