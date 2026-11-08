"""Execute one actor payload through existing backend ports without changing its outcome."""
from __future__ import annotations

from eval.run.actor_spec import ActorLaunchSpec


def execute(spec: ActorLaunchSpec, *, container_run, process_run, sandbox_run):
    if spec.backend == 'container':
        return container_run(spec.argv, **spec.options)
    if spec.backend == 'process':
        return process_run(spec.argv, **spec.options)
    if spec.backend == 'sandbox':
        return sandbox_run(spec.argv, **spec.options)
    raise ValueError(f'unknown actor backend: {spec.backend}')
