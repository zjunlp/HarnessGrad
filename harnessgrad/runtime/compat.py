"""Compatibility adapter for the historical one-shot method contract.

The adapter is intentionally observational.  It invokes an old entrypoint once,
preserves its single JSON response, and writes a protocol event stream beside
the legacy trajectory.  Missing phase semantics are marked ``custom`` /
``unverified`` and ``unobserved``; no event is inferred from ``changed``.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .events import ArtifactKind, Evidence, EventRecorder, ImplementationMode, Lifecycle, Owner, Scope, Stage, StageID, artifact, sha256_bytes


@dataclass(frozen=True)
class LegacyRun:
    response: Any
    stdout: str
    stderr: str
    returncode: int
    trajectory: Any | None
    events: tuple[dict[str, Any], ...]


def _read_json(path: str | Path | None) -> tuple[Any | None, str | None]:
    if not path: return None, None
    if not Path(path).exists(): return None, None
    try: return json.loads(Path(path).read_text(encoding="utf-8")), None
    except OSError as exc: return None, f"trajectory_io:{exc}"
    except ValueError as exc: return None, f"trajectory_json:{exc}"


def _text(value: str | bytes | None) -> str:
    if value is None: return ""
    if isinstance(value, bytes): return value.decode("utf-8", errors="replace")
    return value


class LegacyCompatibilityWrapper:
    """Run a legacy method and emit typed sidecar events."""

    def __init__(self, entrypoint: Sequence[str], *, timeout: float = 3600,
                 event_stream: str | Path | None = None, env: Mapping[str, str] | None = None) -> None:
        if not entrypoint: raise ValueError("entrypoint is empty")
        self.entrypoint, self.timeout = list(entrypoint), timeout
        self.event_stream, self.env = event_stream, dict(env or {})

    def run(self, request: Mapping[str, Any]) -> LegacyRun:
        req = dict(request)
        run_id = str(req.get("run_id") or "legacy-run")
        session_id = str(req.get("session_id") or "legacy-session")
        owner = Owner("method", str(req.get("method_id") or "legacy-method"))
        recorder = EventRecorder(run_id, session_id, producer=Owner("recorder", "compatibility-wrapper"), stream=self.event_stream)
        recorder.bootstrap(profile_opt_in="legacy.compatibility.v1", evidence_mode="declared_traces",
            stage_modes={stage.value: "custom" for stage in StageID},
            task_set_sha=req.get("task_set_sha") or sha256_bytes(json.dumps(req.get("task_ids", []), sort_keys=True)),
            budget={"unit": "generations", "limit": 1})
        request_bytes = json.dumps(req, sort_keys=True, ensure_ascii=False).encode()
        legacy_art = artifact("legacy-response-" + run_id, ArtifactKind.LEGACY_RESPONSE,
            request_bytes, producer=Owner("recorder", "compatibility-wrapper"), visibility="public_record",
            content_kind="none")
        scope = Scope(run_id, session_id, str(req.get("iteration_id") or "iteration-0"), str(req.get("branch_id") or "main"))
        response_art = None; stdout = stderr = ""; returncode = 1; execution_failure = None; output_error = None
        try:
            env = os.environ.copy(); env.update(self.env)
            proc = subprocess.run(self.entrypoint, input=json.dumps(req), text=True, errors="replace",
                                  capture_output=True, timeout=self.timeout, env=env,
                                  cwd=req.get("cwd") or None, check=False)
            stdout, stderr, returncode = _text(proc.stdout), _text(proc.stderr), proc.returncode
            try: response = json.loads(stdout) if stdout.strip() else {}
            except ValueError as exc:
                response = stdout
                output_error = f"invalid_json:{exc}"
                execution_failure = {"class": "subject_error", "subject_or_infrastructure": "subject",
                                     "detail": output_error}
            if not isinstance(response, dict) and execution_failure is None:
                output_error = "legacy_response_must_be_object"
                execution_failure = {"class": "subject_error", "subject_or_infrastructure": "subject",
                                     "detail": output_error}
            if returncode != 0:
                execution_failure = {"class": "subject_error", "subject_or_infrastructure": "subject",
                                     "detail": f"legacy exit {returncode}"}
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, returncode = _text(exc.stdout), _text(exc.stderr), 124
            response = stdout
            execution_failure = {"class": "subject_timeout", "subject_or_infrastructure": "subject",
                                 "detail": "legacy method timed out"}
        trajectory_path = req.get("trajectory_out")
        trajectory, trajectory_error = _read_json(trajectory_path)
        if trajectory_error and execution_failure is None:
            execution_failure = {"class": "subject_error", "subject_or_infrastructure": "subject", "detail": trajectory_error}
        response_bytes = json.dumps({"response": response, "stdout": stdout, "stderr": stderr,
                                     "returncode": returncode}, sort_keys=True, ensure_ascii=False).encode()
        response_art = artifact("legacy-response-data-" + run_id, ArtifactKind.LEGACY_RESPONSE, response_bytes,
            producer=Owner("recorder", "compatibility-wrapper"), visibility="public_record", parents=(legacy_art.artifact_id,), content_kind="none")
        trajectory_art = None
        if trajectory is not None:
            trajectory_bytes = json.dumps(trajectory, sort_keys=True, ensure_ascii=False).encode()
            trajectory_art = artifact("legacy-trajectory-" + run_id, ArtifactKind.LEGACY_RESPONSE,
                trajectory_bytes, producer=Owner("recorder", "compatibility-wrapper"),
                visibility="public_record", parents=(response_art.artifact_id,), content_kind="none")
        output_artifacts = [response_art] + ([trajectory_art] if trajectory_art else [])
        claims = {"legacy_changed": response.get("changed") if isinstance(response, dict) else None,
                  "legacy_steps": response.get("steps") if isinstance(response, dict) else None,
                  "response_type": type(response).__name__, "raw_stdout_sha256": sha256_bytes(stdout),
                  "raw_stderr_sha256": sha256_bytes(stderr)}
        if output_error: claims["output_error"] = output_error
        if trajectory_error: claims["trajectory_error"] = trajectory_error
        recorder.record(scope=scope, stage=Stage(StageID.PROPOSE_CHANGES, "legacy-proposal", ImplementationMode.CUSTOM),
            payload={"payload_kind": "proposal", "candidate_ids": [], "artifact_kind": "none", "hypothesis": "legacy output is not a typed proposal"},
            owner=owner, producer=owner, implementation={"module": "legacy.compatibility", "compliance": "unverified", "fidelity": "unknown"},
            lifecycle=Lifecycle.FAILED if execution_failure else Lifecycle.COMPLETED,
            failure=execution_failure, evidence=Evidence.MEASURED if execution_failure else Evidence.UNOBSERVED,
            input_artifacts=[legacy_art], output_artifacts=output_artifacts,
            artifacts=[legacy_art, response_art] + ([trajectory_art] if trajectory_art else []),
            reason="legacy_entrypoint_boundary", claims=claims)
        # An old response cannot establish selection or deployment.  Empty result
        # events make the missing semantic boundary explicit for downstream readers.
        for stage in (StageID.SELECT_CONTEXT, StageID.EXECUTE, StageID.ORGANIZE_FEEDBACK,
                      StageID.BUILD_VALIDATE, StageID.EVALUATE_CANDIDATES, StageID.SELECT_DEPLOY, StageID.STOP_CONTINUE):
            recorder.record(scope=scope, stage=Stage(stage, f"legacy-{stage.value}", ImplementationMode.CUSTOM),
                payload={"payload_kind": "empty_result", "reason": "legacy_unobserved"},
                owner=owner, producer=Owner("recorder", "compatibility-wrapper"),
                implementation={"module": "legacy.compatibility", "compliance": "unverified", "fidelity": "unknown"},
                evidence=Evidence.UNOBSERVED, reason="legacy_unobserved", claims={"legacy_boundary": True})
        return LegacyRun(response, stdout, stderr, returncode, trajectory, recorder.events)


def run_legacy(entrypoint: Sequence[str], request: Mapping[str, Any], **kwargs: Any) -> LegacyRun:
    return LegacyCompatibilityWrapper(entrypoint, **kwargs).run(request)
