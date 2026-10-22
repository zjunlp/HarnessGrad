"""Opt-in pinned, artifact-only scorer execution. Never used for snapshot checks.

The Docker observer checks the stopped instance, rather than trusting a declaration
or launch flags. This does not prove transitive image/ABI closure or official origin.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from eval.admission import artifact_admission as artifacts
from eval.admission import scorer_bundle as bundles
from eval.admission.pilot_admission import AdmissionError, LOG_CONFIG
from eval.containers import container
from eval.verification.isolation import record_class


def _command_result(result: subprocess.CompletedProcess) -> dict:
    return dict(argv=list(result.args), returncode=result.returncode,
                stdout=result.stdout, stderr=result.stderr)


def _platform_path(value, actor: Path) -> Path:
    path = Path(value)
    artifacts._root(path)
    path = path.resolve(strict=True)
    if path == actor.resolve() or actor.resolve() in path.parents:
        raise AdmissionError("scorer declarations and closure must be outside actor state")
    return path


def enforce_verdict_binding(verdict: dict, *, task_id: str, run_id: str, trial_id: str) -> dict:
    """Refuse foreign/missing native evidence at a verdict consumption boundary.

    This is a trusted-platform consistency check, not authentication of arbitrary
    JSON. Actor code must never write these receipts or choose the expected labels.
    """
    try:
        expected = artifacts.execution_identity(task_id, run_id, trial_id)
        evidence = verdict["scorer_isolation_evidence"]
        binding, exported = evidence["execution_binding"], evidence["export"]
        if ({key: binding[key] for key in expected} != expected
                or exported["execution_binding"] != expected
                or exported["task_id"] != task_id
                or exported["schema"] != "harnessgrad.state-export-receipt.v2"
                or evidence["runtime"]["execution_binding"] != expected
                or binding["runtime_export_run_id"] != exported["run_id"]
                or evidence["cleanup"]["container"] != exported["run_id"]):
            raise AdmissionError("pristine verdict execution binding mismatch")
    except (AdmissionError, KeyError, TypeError, ValueError) as exc:
        # Keep genuine unavailable/timeout reasons; never promote them to a score.
        if verdict.get("score") is not None or not verdict.get("unmeasured"):
            return {**verdict, "passed": False, "score": None, "outcome": "unmeasured",
                    "unmeasured": f"pristine verdict execution binding refused: {exc}"}
    return verdict


def verify(workdir: Path, spec: dict, *, timeout_s: int, task_id: str | None = None,
           run_id: str | None = None, trial_id: str | None = None) -> dict:
    bundle = spec["scorer_bundle"]
    name = "hg-pristine-" + uuid.uuid4().hex
    actual = "unknown"
    evidence = {}
    attempted = False
    verdict = {"kind": "command", "passed": False, "score": None}
    deadline = time.monotonic() + timeout_s

    def docker(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, timeout_s)
        return container.docker(*args, timeout=remaining)
    # Exclusive platform-owned storage, never a projection inside actor state.
    with tempfile.TemporaryDirectory(prefix="hg-pristine-") as scratch:
        try:
            identity = artifacts.execution_identity(task_id, run_id, trial_id)
            raw = _platform_path(bundle["manifest"], workdir).read_bytes()
            pin = bundle["manifest_sha256"]
            root = _platform_path(bundle["root"], workdir)
            manifest = bundles.load_bundle(raw, pin)
            if manifest.get("isolation_class") != "pristine-offline":
                raise AdmissionError("pristine runtime requires explicit pristine-offline bundle")
            if spec.get("argv") != manifest["scorer"]["argv"]:
                raise AdmissionError("verifier command differs from sealed scorer command")
            evidence["bundle"] = bundles.verify_bundle(root, raw, pin)
            contract_raw = _platform_path(bundle["artifact_contract"], workdir).read_bytes()
            contract_pin = bundle["artifact_contract_sha256"]
            contract = bundles.require_contract_bundle(contract_raw, contract_pin, raw, pin)
            if contract["task_id"] != task_id:
                raise AdmissionError("artifact contract task differs from selected task")
            evidence["execution_binding"] = dict(
                **identity, selected_task_checked=True, runtime_export_run_id=name,
                scope="task_contract_runtime_export_and_outer_run_trial",
                outer_run_trial_binding="enforced")
            observation = evidence["source_observation"] = dict(
                scope="selected_source_file_endpoint_probes", status="probe_unavailable",
                quiescence_established=False,
                interval="before_export_through_scorer_cleanup")
            observation["before"] = artifacts.observe_selected_source(workdir, contract_raw, contract_pin)
            artifact_root = Path(scratch) / "input"
            exported = artifacts.export_state(workdir, artifact_root, contract_raw, contract_pin,
                                              run_id=name, execution_binding=dict(identity))
            evidence["export"] = exported
            observation["export_content_matches_before"] = exported["files"] == [
                {key: row[key] for key in ("path", "bytes", "sha256")}
                for row in observation["before"]["files"]]
            if not observation["export_content_matches_before"]:
                raise AdmissionError("selected source differs between observation and export")
            argv = manifest["scorer"]["argv"]
            cmd = ["create", "--pull=never", "--name", name, "--network", "none",
                   "--user", artifacts.scorer_user(), "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--ipc", "private", "--log-driver", LOG_CONFIG["Type"], "--pids-limit", "128",
                   "--memory", "512m", "--cpus", "1", "--workdir", "/tmp",
                   "--entrypoint", argv[0]]
            for env in artifacts.SCORER_ENV:
                cmd += ["--env", env]
            for key, value in LOG_CONFIG["Config"].items():
                cmd += ["--log-opt", f"{key}={value}"]
            for path, options in artifacts.runtime_tmpfs(contract_raw, contract_pin).items():
                cmd += ["--tmpfs", f"{path}:{options}"]
            for source, target in ((artifact_root, "/control-input"), (root, "/control-scorer")):
                if "," in str(source):
                    raise AdmissionError("projection path cannot contain mount option separators")
                cmd += ["--mount", f"type=bind,src={source},dst={target},readonly"]
            cmd += [manifest["runtime_image_id"], *argv[1:]]
            attempted = True
            created = docker(*cmd)
            if created.returncode:
                raise AdmissionError("pristine scorer creation failed")
            inspected = docker("inspect", name)
            if inspected.returncode:
                raise AdmissionError("pristine scorer inspection failed")
            info = json.loads(inspected.stdout)[0]
            evidence["runtime"] = artifacts.require_artifact_runtime(
                info, contract_raw, contract_pin, artifact_root=artifact_root, closure_root=root,
                export_receipt=exported, run_id=name, execution_binding=dict(identity))
            # Exit status alone cannot distinguish initialization from a measured
            # failing predicate. The sealed scorer must explicitly attest completion.
            started = docker("start", "--attach", name)
            evidence["start_result"] = _command_result(started)
            stopped = docker("inspect", name)
            evidence["stopped_result"] = _command_result(stopped)
            if stopped.returncode:
                raise AdmissionError("pristine scorer result inspection failed")
            state = json.loads(stopped.stdout)[0]["State"]
            evidence["stopped_result"]["state"] = state
            if (state["Running"] is not False or state["Status"] != "exited"
                    or state.get("OOMKilled") or state.get("Error")):
                raise AdmissionError(f"pristine scorer did not finish normally: {state}")
            actual = "pristine-offline"
            code = state["ExitCode"]
            diagnostic = f"exit {code}; stderr: {started.stderr[-2048:]}"
            markers = [line for line in started.stdout.splitlines()
                       if line.startswith("completed_check=")]
            if len(markers) != 1 or markers[0] not in ("completed_check=pass", "completed_check=fail"):
                raise AdmissionError(f"scorer completion marker missing, malformed or ambiguous; {diagnostic}")
            expected_code = 0 if markers[0] == "completed_check=pass" else 1
            if type(code) is not int or code != expected_code or started.returncode != code:
                raise AdmissionError(f"scorer completion marker/exit mismatch (attach {started.returncode}); "
                                     f"{diagnostic}")
            verdict.update(passed=code == 0, score=float(code == 0), outcome="measured",
                           detail=f"sealed scorer {markers[0]}, exit {code}")
        except (AdmissionError, OSError, ValueError, KeyError, TypeError, IndexError,
                subprocess.TimeoutExpired) as exc:
            verdict.update(score=None, passed=False,
                           outcome="timeout" if isinstance(exc, subprocess.TimeoutExpired) else "unmeasured",
                           unmeasured=f"pristine scorer unavailable: {exc}")
        finally:
            if attempted:
                try:
                    removed = container.docker("rm", "--force", name, timeout=30)
                    evidence["cleanup"] = {"container": name, "removed": removed.returncode == 0,
                                           **_command_result(removed)}
                except (OSError, subprocess.TimeoutExpired) as exc:
                    evidence["cleanup"] = {"container": name, "removed": False,
                                           "error": f"{type(exc).__name__}: {exc}"}
                if not evidence["cleanup"]["removed"]:
                    verdict.update(passed=False, score=None, outcome="unmeasured",
                                   unmeasured="pristine scorer cleanup failed")
            # Run even on scorer/admission/timeout failures, after any attempted
            # scorer has been removed. Never turn a failed execution into a score.
            observation = evidence.get("source_observation")
            if observation is not None:
                try:
                    observation["after"] = artifacts.observe_selected_source(workdir, contract_raw, contract_pin)
                    if "before" in observation:
                        changed = observation["before"]["files"] != observation["after"]["files"]
                        changed |= observation.get("export_content_matches_before") is False
                        observation["status"] = "difference_observed" if changed else "no_change_observed"
                        if changed and verdict.get("outcome") == "measured":
                            verdict.update(passed=False, score=None, outcome="unmeasured",
                                           unmeasured="selected source change observed across export/scoring interval")
                except (AdmissionError, OSError) as exc:
                    observation.update(status="probe_unavailable", error=f"{type(exc).__name__}: {exc}")
                    if verdict.get("outcome") == "measured":
                        verdict.update(passed=False, score=None, outcome="unmeasured",
                                       unmeasured="selected source post-scoring observation unavailable")
    verdict["scorer_isolation_evidence"] = evidence
    return record_class(verdict, spec, actual)
