"""Read-only identity capture and task admission decisions for evaluation.

No progress writes, service/process acquisition, native scorer FD ownership or
actor execution lives here. Existing providers may read and validate artifacts;
the caller retains declaration maps and the lifetime of every receipt.
"""
from __future__ import annotations

from dataclasses import dataclass

from harnessgrad import candidate_runtime, dataset_runtime_identity, task_python, verification_context
from eval.containers import external_runtime
from eval.run import chess_stepwise
from eval.run.outcomes import _invalid


@dataclass(frozen=True)
class IdentityCheck:
    value: dict | None
    error: str | None = None


@dataclass(frozen=True)
class AdmissionErrors:
    context: str | None
    dataset: str | None
    harness: str | None
    external: str | None


@dataclass(frozen=True)
class TaskRefusal:
    reason: dict
    diagnostic: dict | None = None
    record_enforcement: bool = False
    record_runtime: bool = True

    def progress_fields(self, runtime_fields: dict, enforcement) -> dict:
        fields = dict(runtime_fields) if self.record_runtime else {}
        if self.diagnostic is not None:
            fields['diagnostic'] = self.diagnostic
        if self.record_enforcement:
            fields['enforcement'] = enforcement
        return fields


def capture_verification(tasks, scorable, setups, verifiers, envs, *, sandbox, expected):
    context = None
    error = None
    try:
        context = verification_context.capture(tasks, scorable, setups, verifiers, envs, sandbox=sandbox)
        if expected is not None:
            projected = verification_context.project(expected, [task['task_id'] for task in tasks])
            if verification_context.problems(projected, context):
                raise ValueError('verification context differs from recorded plan')
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        error = str(exc)
    return IdentityCheck(context, error)


def capture_dataset(tasks, setups, verifiers, envs, *, sandbox, expected):
    identity = None
    error = None
    try:
        # Cache admission must validate the same task artifacts as a fresh launch.
        for task in tasks:
            task_python.validate((envs or {}).get(task['task_id']), sandbox=sandbox,
                                 setup=(setups or {}).get(task['task_id']))
        identity = dataset_runtime_identity.for_evaluation(expected, tasks, setups, verifiers, envs)
    except (OSError, TypeError, ValueError) as exc:
        error = str(exc)
    return IdentityCheck(identity, error)


def capture_harness(repo, tasks, setups, envs, *, sandbox, expected):
    identity = None
    error = None
    try:
        identity = candidate_runtime.admit(
            repo, expected, envs={task['task_id']: (envs or {}).get(task['task_id']) or {} for task in tasks},
            setups={task['task_id']: (setups or {}).get(task['task_id']) or {} for task in tasks},
            sandbox=sandbox)
    except (OSError, TypeError, ValueError) as exc:
        error = str(exc)
    return IdentityCheck(identity, error)


def protocols_for(tasks):
    protocols = {task['task_id']: protocol for task in tasks
                 if (protocol := chess_stepwise.protocol_of(task)) is not None}
    if len(set(protocols.values())) > 1:
        raise ValueError('cannot aggregate interactive and offline ChessBench protocols')
    return protocols


def capture_external(repo, *, harness_error):
    identities, error = {}, None
    try:
        if harness_error is None:
            identities = external_runtime.identities(repo)
    except external_runtime.RuntimeUnavailable as exc:
        error = str(exc)
    return IdentityCheck(identities, error)


def check_current_context(task, scorable, setups, verifiers, envs, *, sandbox, expected):
    tid = task['task_id']
    try:
        task_python.admit((envs or {}).get(tid), sandbox=sandbox, setup=(setups or {}).get(tid))
        current = verification_context.capture([task], scorable, setups, verifiers, envs, sandbox=sandbox)
        if verification_context.problems(expected, current):
            raise ValueError('verification context changed before task admission')
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return TaskRefusal({'stage': 'verification context', 'detail': str(exc)})
    return None


def check_policy_and_protocol(task, setups, verifiers, envs, *, sandbox, run_id,
                              authorization, observation, protocol, task_runtime, policy_refusal):
    tid = task['task_id']
    refusal = policy_refusal(task, (envs or {}).get(tid), (verifiers or {}).get(tid),
                             run_id=run_id, diagnostic_authorization=authorization, observation=observation)
    if refusal is not None:
        return TaskRefusal(refusal['invalid'], diagnostic=refusal, record_enforcement=True)
    if protocol == chess_stepwise.STEPWISE:
        try:
            chess_stepwise.session_for(task, (verifiers or {}).get(tid), sandbox=sandbox,
                                       env=(envs or {}).get(tid) or {}, dataset_runtime_identity=task_runtime)
        except ValueError as exc:
            refusal = _invalid(task, exc, stage='stepwise admission', service=None)
            return TaskRefusal(refusal['invalid'], diagnostic=refusal)
    return None


def admit_before_cache(repo, task, scorable, setups, verifiers, envs, *, sandbox, task_context,
                       task_runtime, errors: AdmissionErrors, native_errors, external_identities,
                       run_id, authorization, observations, protocol, policy_refusal):
    """Return a refusal with exact journal metadata; never write or consume a cache hit."""
    if errors.context is not None:
        return TaskRefusal({'stage': 'verification context', 'detail': errors.context}), None
    refusal = check_current_context(task, scorable, setups, verifiers, envs,
                                    sandbox=sandbox, expected=task_context)
    if refusal is not None:
        return refusal, None
    if errors.dataset is not None:
        return TaskRefusal({'stage': 'dataset runtime identity', 'detail': errors.dataset}), None
    if errors.harness is not None:
        return TaskRefusal({'stage': 'harness runtime preparation', 'detail': errors.harness}), None
    observation = observations.get(task['task_id'])
    refusal = check_policy_and_protocol(
        task, setups, verifiers, envs, sandbox=sandbox, run_id=run_id,
        authorization=authorization, observation=observation, protocol=protocol,
        task_runtime=task_runtime, policy_refusal=policy_refusal)
    if refusal is not None:
        return refusal, observation
    if errors.external is not None:
        return TaskRefusal({'stage': 'harness runtime preparation', 'detail': errors.external}), observation
    if task['task_id'] in native_errors:
        return TaskRefusal({'stage': 'native runtime preparation', 'detail': native_errors[task['task_id']]},
                           record_runtime=False), observation
    try:
        external_runtime.require_current(repo, external_identities)
    except external_runtime.RuntimeUnavailable as exc:
        return TaskRefusal({'stage': 'harness runtime identity', 'detail': str(exc)}), observation
    return None, observation
