"""Trusted private verification inputs; never part of an actor launch payload."""
from __future__ import annotations

from dataclasses import dataclass

from eval.containers.immutable_runtime import projections, targets


@dataclass(frozen=True)
class VerifySpec:
    declaration: dict | None
    environment: dict
    sandbox: bool
    timeout_s: int
    task_id: str
    run_id: str
    trial_id: str | None
    immutable_actor_runtimes: tuple


def with_tool_recording(workdir, declaration, tool, *, archive_root):
    entry = tool.recording_input(archive_root=archive_root)
    targets(workdir, projections([entry]))
    (workdir / entry['path']).mkdir(exist_ok=True)
    return {**declaration, 'inputs': [*(declaration.get('inputs') or []), entry]}


def verify_output(workdir, spec: VerifySpec, answer, *, service_specs, running, verify):
    # The canonical referee remains the only authority interpreting/scoring checks.
    return verify(workdir, spec.declaration, answer, sandbox=spec.sandbox, task_id=spec.task_id,
                  run_id=spec.run_id, trial_id=spec.trial_id, timeout_s=spec.timeout_s,
                  env=spec.environment, service_specs=service_specs, running=running,
                  immutable_runtimes=spec.immutable_actor_runtimes)
