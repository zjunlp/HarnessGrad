"""Per-task trials and their evidence journal; actor resources remain owned by run_one."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import sys
from typing import Callable

from harnessgrad import candidate_runtime, dataset_runtime_identity, task_python, verification_context
from eval.containers import external_runtime
from eval.observe import progress
from eval.observe.tracing import _last_usage
from eval.run import chess_stepwise, repeated_trials, trial_evidence
from eval.run.outcomes import _invalid, _provider_failure, _stopped_by_signals
from eval.service import modelgate
from eval.verification.isolation import declared_class
from eval.verification.pristine import enforce_verdict_binding


@dataclass(frozen=True)
class RunOptions:
    repo: Path
    sandbox: bool
    run_id: str
    run_seed: str
    round_no: int
    harness_sha: str
    trials: int
    task_count: int
    recordings_root: Path | None
    authorization: object
    private_paths: tuple[Path, ...]

    def trial_identity(self, trial):
        return f'round-{self.round_no}.harness-{self.harness_sha}.trial-{trial + 1}'


@dataclass(frozen=True)
class RunBindings:
    harness: dict | None
    external: dict


@dataclass(frozen=True)
class TaskPlan:
    task: dict
    index: int
    runtime: dict | None
    native: dict | None
    protocol: str | None
    observation: dict | None
    runtime_fields: dict
    enforcement: dict | None

    task_id: str = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, 'task_id', self.task['task_id'])


@dataclass(frozen=True)
class TaskInputPort:
    """Task-scoped live declaration reads and final context check, not a world context."""
    read: Callable[[str], dict | None]
    fresh: Callable[[], bool]
    check_context: Callable[[], dict | None]


@dataclass
class TrialState:
    measured: list = field(default_factory=list)
    usage_records: list = field(default_factory=list)
    unmeasured: dict | None = None
    diagnostic: dict | None = None
    last_result: dict | None = None


@dataclass
class TaskEvidenceJournal:
    options: RunOptions
    task_id: str
    progress_dir: Path | None
    protocol: dict | None
    ordinary_plan: dict | None
    evidence_plan: dict
    archive: bool
    rows: list
    marker: Path | None = field(default=None, init=False)
    ordinary_task_plan: dict | None = field(default=None, init=False)

    def seed(self, trial):
        return str(self.protocol['seeds'][trial]) if self.protocol else str(self.options.run_seed)

    def task_protocol_plan(self, task):
        return repeated_trials.plan([task], self.protocol, run_id=self.options.run_id,
                                    round_no=self.options.round_no, harness_sha=self.options.harness_sha)

    def start(self, task):
        if self.protocol and self.progress_dir is not None:
            self.marker = repeated_trials.start_task(
                repeated_trials.report_root(self.progress_dir, self.options.round_no, self.options.harness_sha),
                self.task_protocol_plan(task))
        elif self.ordinary_plan and self.archive:
            self.ordinary_task_plan = {**self.ordinary_plan, 'manifest': [
                row for row in self.ordinary_plan['manifest'] if row['task_id'] == self.task_id]}
            self.marker = repeated_trials.start_task(
                trial_evidence.ordinary_root(self.progress_dir, self.options.round_no, self.options.harness_sha),
                self.ordinary_task_plan)
        if self.progress_dir is not None and os.environ.get('HG_TASK_VERDICT_JOURNAL') == '1':
            from eval.observe import task_verdicts
            task_verdicts.start(self.progress_dir, self.options.round_no, self.task_id)

    def save_raised(self, exc, trial, runtime_fields):
        captured = getattr(exc, 'trial_evidence_result', None)
        if self.archive and captured is not None:
            captured.update(runtime_fields)
            row = {'task_id': self.task_id, 'trial_id': self.options.trial_identity(trial),
                   'seed': self.seed(trial), 'status': 'raised', 'exception_type': type(exc).__name__}
            exc.trial_evidence = trial_evidence.save(self.progress_dir, self.evidence_plan, row, captured)

    def record(self, result, trial):
        if self.protocol or self.ordinary_task_plan:
            plan = {'task_id': self.task_id, 'trial_id': self.options.trial_identity(trial),
                    'seed': self.seed(trial)}
            row = (repeated_trials.result_row(result, plan, provider_failure=_provider_failure(result))
                   if self.protocol else trial_evidence.ordinary_row(plan, result))
            if self.archive:
                row['trial_evidence'] = trial_evidence.save(self.progress_dir, self.evidence_plan, row, result)
            self.rows.append(row)

    def persist_protocol(self, task):
        if self.protocol and self.progress_dir is not None:
            repeated_trials.save_task(
                repeated_trials.report_root(self.progress_dir, self.options.round_no, self.options.harness_sha),
                self.task_protocol_plan(task), self.rows)

    def evidence_fields(self):
        return {'trial_evidence': self.rows} if self.ordinary_task_plan else {}

    def persist_ordinary(self):
        if self.ordinary_task_plan:
            trial_evidence.save_ordinary_task(self.progress_dir, self.ordinary_task_plan, self.rows)

    def complete(self):
        self.persist_ordinary()
        if self.marker is not None:
            self.marker.unlink()


def check_final_context(task, scorable, setups, verifiers, envs, *, sandbox, expected, task_id):
    tid = task_id
    try:
        task_python.admit((envs or {}).get(tid), sandbox=sandbox, setup=(setups or {}).get(tid))
        current = verification_context.capture([task], scorable, setups, verifiers, envs, sandbox=sandbox)
        if verification_context.problems(expected, current):
            raise ValueError('verification context changed during task execution')
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return {'stage': 'verification context', 'detail': str(exc)}
    return None


def trial_usage(result, inputs):
    environment = result.get('env_usage') or {}
    if not environment and (((inputs.read('setup') or {}).get('tool_relay') or {}).get('model')):
        environment = {'coverage_unknown': True}
    return {'usage': _last_usage(result.get('trace') or ''), 'env_usage': environment}


def validate_trial_result(result, plan, bindings, options, inputs, trial):
    try:
        external_runtime.require_current(options.repo, bindings.external)
        external_runtime.stamp(result, bindings.external)
    except external_runtime.RuntimeUnavailable as exc:
        result.setdefault('invalid', {'stage': 'harness runtime identity', 'detail': str(exc)})
    result.update(dataset_runtime_identity.fields(plan.runtime))
    if not result.get('invalid') and declared_class(inputs.read('verifier')) == 'pristine-offline':
        result['verdict'] = enforce_verdict_binding(
            result.get('verdict') or {}, task_id=plan.task_id, run_id=options.run_id,
            trial_id=options.trial_identity(trial))
    native = plan.native
    verdict = result.get('verdict') or {}
    if (native is not None and not result.get('invalid') and not verdict.get('unmeasured')
            and verdict.get('native_runtime') != native):
        result['invalid'] = {'stage': 'native runtime identity',
            'detail': 'Native runtime changed after evaluation admission; refusing mixed measurements'}
    if any((result.get('harness_runtime') or {}).get(kind) != expected
           for kind, expected in bindings.external.items()):
        result.setdefault('invalid', {'stage': 'harness runtime identity',
            'detail': 'Runtime changed after evaluation admission; refusing mixed measurements'})
    actual = result.get('harness_runtime')
    if bindings.harness is not None and (candidate_runtime.problems(
            result.get('harness_runtime_identity'), bindings.harness) or not isinstance(actual, dict)
            or candidate_runtime.problems(actual.get('identity'), bindings.harness)):
        result.setdefault('invalid', {'stage': 'harness runtime identity',
            'detail': 'Actor runtime differs from the exact candidate binding admitted for evaluation'})


@dataclass(frozen=True)
class TrialDecision:
    action: str
    reason: dict | None = None


def classify_trial(result, measured, *, task_id, trial, repeated):
    if result.get('invalid'):
        return TrialDecision('continue' if repeated else 'break', result['invalid'])
    provider = _provider_failure(result)
    if (provider is not None and not (result.get('verdict') or {}).get('passed')
            and (result.get('verdict') or {}).get('protocol') != chess_stepwise.STEPWISE):
        if measured:
            print(f"warning: {task_id}: trial {trial + 1} died reaching the model "
                  f"endpoint ({provider['line']}); keeping the "
                  f"{len(measured)} measured trial(s)", file=sys.stderr)
            return TrialDecision('continue')
        reason = {'stage': 'harness model call',
                  'detail': (f"the harness could not reach the model endpoint and "
                             f"exited {provider['exit_code']}: {provider['line']}")
                            + modelgate.describe(result.get('model_gateway'))}
        return TrialDecision('continue' if repeated else 'break', reason)
    if (result.get('verdict') or {}).get('unmeasured'):
        verdict = result['verdict']
        reason = {'stage': 'check', 'detail': verdict['unmeasured']}
        if verdict.get('outcome'):
            reason['outcome'] = verdict['outcome']
        if verdict.get('protocol'):
            reason.update(protocol=verdict['protocol'], receipt=verdict.get('evidence'))
        return TrialDecision('continue' if repeated else 'break', reason)
    return TrialDecision('measure')


class TaskTrialWorker:
    def __init__(self, options, bindings, *, measure, write_progress):
        self.options, self.bindings = options, bindings
        self.measure, self.write_progress = measure, write_progress

    def run(self, plan: TaskPlan, inputs: TaskInputPort, journal: TaskEvidenceJournal):
        state = TrialState()
        journal.start(plan.task)
        for trial in range(self.options.trials):
            if not self.admit_trial(plan, state):
                break
            if self.run_one_trial(plan, inputs, journal, state, trial) == 'break':
                break
        return self.finish_task(plan, inputs, journal, state)

    def admit_trial(self, plan, state):
        try:
            external_runtime.require_current(self.options.repo, self.bindings.external)
        except external_runtime.RuntimeUnavailable as exc:
            state.diagnostic = _invalid(plan.task, exc, stage='harness runtime identity', service=None)
            state.unmeasured = state.diagnostic['invalid']
            return False
        return True

    def execute_actor(self, plan, inputs, journal, trial, held):
        try:
            return self.measure(self.options.repo, plan.task, sandbox=self.options.sandbox,
                **({'dataset_runtime_identity': plan.runtime} if plan.protocol == chess_stepwise.STEPWISE else {}),
                **candidate_runtime.fields(self.bindings.harness),
                **({'_private_paths': self.options.private_paths} if self.options.private_paths else {}),
                setup=inputs.read('setup'), verifier=inputs.read('verifier'), env=inputs.read('environment'),
                run_id=self.options.run_id, run_seed=(journal.seed(trial) if journal.protocol else self.options.run_seed),
                recordings_root=self.options.recordings_root,
                **({'trial_id': self.options.trial_identity(trial)}
                   if inputs.fresh() or declared_class(inputs.read('verifier')) == 'pristine-offline' else {}),
                **({'diagnostic_authorization': self.options.authorization}
                   if self.options.authorization is not None else {}),
                **({'observation': plan.observation} if plan.observation is not None else {}))
        except BaseException as exc:
            held.failed(f'{type(exc).__name__}: {exc}')
            journal.save_raised(exc, trial, plan.runtime_fields)
            raise

    def run_one_trial(self, plan, inputs, journal, state, trial):
        with progress.task_progress(plan.task_id, round_no=self.options.round_no, index=plan.index,
                                    total=self.options.task_count) as held:
            result = self.execute_actor(plan, inputs, journal, trial, held)
            state.last_result = result
            state.usage_records.append(trial_usage(result, inputs))
            validate_trial_result(result, plan, self.bindings, self.options, inputs, trial)
            journal.record(result, trial)
            decision = classify_trial(result, state.measured, task_id=plan.task_id, trial=trial,
                                      repeated=bool(journal.protocol))
            if decision.reason is not None:
                state.unmeasured, state.diagnostic = decision.reason, result
                held.failed(str(decision.reason.get('detail') or '')[:200])
            if decision.action != 'measure':
                return decision.action
            held.score = float((result.get('verdict') or {}).get(
                'score', 1.0 if (result.get('verdict') or {}).get('passed') else 0.0))
        state.measured.append(result)
        return 'measure'

    def write_task_progress(self, plan, journal, results, reason, **fields):
        self.write_progress(journal.progress_dir, self.options.round_no, plan.task_id,
                            results, reason, **plan.runtime_fields, **fields)

    def finish_task(self, plan, inputs, journal, state):
        if state.unmeasured is None:
            state.unmeasured = _stopped_by_signals(state.measured)
        journal.persist_protocol(plan.task)
        if state.unmeasured is not None:
            return self.finish_invalid(plan, journal, state)
        invalid = inputs.check_context()
        if invalid is not None:
            # Context refusal invalidates the score, not the observed spend or
            # the actor's original runtime/HTTP failure evidence.
            diagnostic = state.diagnostic or state.last_result
            self.write_task_progress(plan, journal, [], invalid, diagnostic=diagnostic,
                                     usage_records=state.usage_records)
            return {'task_id': plan.task_id, 'invalid': invalid, 'usage_records': state.usage_records,
                    'harness_runtime': (diagnostic or {}).get('harness_runtime'),
                    'runtime_services': (diagnostic or {}).get('runtime_services')}
        self.write_task_progress(plan, journal, state.measured, None, enforcement=plan.enforcement,
                                 usage_records=state.usage_records, **journal.evidence_fields())
        journal.complete()
        return {'task_id': plan.task_id, 'measured': state.measured, 'usage_records': state.usage_records}

    def finish_invalid(self, plan, journal, state):
        diagnostic = state.diagnostic or state.last_result
        self.write_task_progress(plan, journal, [], state.unmeasured, diagnostic=diagnostic,
                                 enforcement=plan.enforcement, usage_records=state.usage_records,
                                 **journal.evidence_fields())
        journal.persist_ordinary()
        return {'task_id': plan.task_id, 'invalid': state.unmeasured, 'usage_records': state.usage_records,
                'harness_runtime': (diagnostic or {}).get('harness_runtime'),
                'runtime_services': (diagnostic or {}).get('runtime_services')}
