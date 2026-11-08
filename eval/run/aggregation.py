"""Ordered outcome, score and evidence reduction; no execution or admission ownership."""
from __future__ import annotations

from dataclasses import dataclass, field

from eval.run import repeated_trials, trial_evidence
from eval.run.outcomes import _harness_failure, _spread, timeout_verdict
from eval.verification.isolation import common_class, observed_class
from eval.verification.verifier import test_rate_of


def score_of(result):
    verdict = result.get('verdict') or {}
    return float(verdict.get('score', 1.0 if verdict.get('passed') else 0.0))


@dataclass
class TestRates:
    per_task: dict = field(default_factory=dict)
    per_task_trials: dict = field(default_factory=dict)
    passed: int = 0
    total: int = 0

    def measured(self, tid, measured, trials):
        rates = [test_rate_of(result.get('verdict')) for result in measured]
        rates = [rate for rate in rates if rate is not None]
        if rates:
            self.per_task[tid] = sum(rates) / len(rates)
            if trials > 1:
                self.per_task_trials[tid] = rates
            for result in measured:
                tally = (result.get('verdict') or {}).get('tests') or {}
                passed, failed = tally.get('passed'), tally.get('failed')
                if isinstance(passed, int) and isinstance(failed, int):
                    self.passed += passed
                    self.total += passed + failed


@dataclass
class Scores:
    trials: int
    per_task: dict = field(default_factory=dict)
    per_task_std: dict = field(default_factory=dict)
    per_task_trials: dict = field(default_factory=dict)
    per_task_isolation: dict = field(default_factory=dict)
    tests: TestRates = field(default_factory=TestRates)

    def cached(self, tid, entry):
        self.per_task[tid] = entry['score']
        verdicts = entry.get('trial_verdicts') or [entry.get('verdict') or {}]
        self.per_task_isolation[tid] = common_class(observed_class(v) for v in verdicts)
        if entry.get('per_task_trials'):
            self.per_task_trials[tid] = list(entry['per_task_trials'])
            self.per_task_std[tid] = float(entry.get('per_task_std') or 0.0)

    def timeout(self, tid):
        self.per_task[tid] = 0.0
        self.per_task_std[tid] = 0.0

    def measured(self, tid, measured):
        scores = [score_of(result) for result in measured]
        self.per_task_isolation[tid] = common_class(observed_class(result.get('verdict')) for result in measured)
        score = sum(scores) / len(scores)
        if self.trials > 1:
            self.per_task_trials[tid] = scores
            self.per_task_std[tid] = _spread(scores)
        self.per_task[tid] = score
        self.tests.measured(tid, measured, self.trials)

    def run_spread(self):
        if self.trials > 1 and self.per_task_trials:
            width = min(len(values) for values in self.per_task_trials.values())
            return _spread([sum(values[i] for values in self.per_task_trials.values()) /
                            len(self.per_task_trials) for i in range(width)])
        return 0.0


@dataclass
class OutcomeCoverage:
    invalid: dict = field(default_factory=dict)
    timeouts: dict = field(default_factory=dict)
    not_run: list = field(default_factory=list)
    cached_tasks: list = field(default_factory=list)

    def classify(self, tid, outcome):
        if outcome.get('not_run'):
            self.not_run.append(tid)
            return 'not_run'
        if 'cached' in outcome:
            self.cached_tasks.append(tid)
            return 'cached'
        if outcome.get('invalid'):
            reason = outcome['invalid']
            if timeout_verdict(reason):
                self.timeouts[tid] = reason if isinstance(reason, dict) else {'outcome': 'timeout', 'detail': str(reason)}
                return 'timeout'
            self.invalid[tid] = reason
            return 'invalid'
        return 'measured'


@dataclass
class TaskEvidence:
    trials: int
    traces: dict = field(default_factory=dict)
    verdicts: dict = field(default_factory=dict)
    trial_traces: dict = field(default_factory=dict)
    trial_verdicts: dict = field(default_factory=dict)
    harness_failed: dict = field(default_factory=dict)
    harness_output: dict = field(default_factory=dict)

    def cached(self, tid, entry, protocol):
        if entry.get('trial_traces'):
            self.trial_traces[tid] = list(entry['trial_traces'])
        if entry.get('trial_verdicts'):
            self.trial_verdicts[tid] = [dict(v) for v in entry['trial_verdicts']]
            for verdict in self.trial_verdicts[tid]:
                verdict.setdefault('scorer_isolation_class', 'unknown')
        self.traces[tid] = entry['trace']
        if entry.get('harness_failed'):
            self.harness_failed[tid] = entry['harness_failed']
        if entry.get('harness_output'):
            self.harness_output[tid] = entry['harness_output']
        if entry.get('verdict'):
            self.verdicts[tid] = dict(entry['verdict'])
            self.verdicts[tid].setdefault('scorer_isolation_class', 'unknown')
            if protocol is not None:
                self.verdicts[tid]['protocol'] = protocol

    def measured(self, tid, measured, result, verdict):
        self.verdicts[tid] = verdict
        self.traces[tid] = result['trace']
        if self.trials > 1:
            self.trial_traces[tid] = [value['trace'] for value in measured]
            self.trial_verdicts[tid] = [dict(value.get('verdict') or {}) for value in measured]
        failure = _harness_failure(result)
        raw = result.get('stderr') or ''
        self.harness_output[tid] = {'exit_code': result.get('exit_code'),
            'stderr': raw if len(raw) <= 8000 else raw[:2000] + '\n...\n' + raw[-6000:]}
        if failure:
            self.harness_failed[tid] = failure
        return failure


@dataclass
class RuntimeEvidence:
    records: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    declarations: list = field(default_factory=list)
    model_gateway: dict | None = None

    def record(self, tid, value):
        runtime = value.get('harness_runtime')
        if runtime and runtime not in self.records:
            self.records.append(runtime)
        if value.get('runtime_services'):
            self.diagnostics[tid] = value['runtime_services']

    def measured(self, tid, result):
        self.record(tid, result)
        if result.get('model_gateway'):
            self.model_gateway = result['model_gateway']
        declared = result.get('identity')
        if declared:
            self.declarations.append(declared)

    def identities(self):
        variants = []
        for declared in self.declarations:
            if declared not in variants:
                variants.append(declared)
        return (variants[0] if variants else None), variants


def measured_cache_entry(tid, result, failure, scores, evidence, runtime_fields):
    """Describe only measured cache publication; the ordered caller owns the write."""
    return {'score': scores.per_task[tid], 'trace': result['trace'],
            'per_task_trials': scores.per_task_trials.get(tid),
            'per_task_std': scores.per_task_std.get(tid),
            'trial_traces': evidence.trial_traces.get(tid),
            'trial_verdicts': evidence.trial_verdicts.get(tid),
            'harness_failed': failure, 'harness_output': evidence.harness_output.get(tid),
            'verdict': evidence.verdicts.get(tid) or {}, **runtime_fields,
            'harness_runtime': result.get('harness_runtime'), 'runtime_services': result.get('runtime_services')}


def startup_failure(harness_failed, per_task, not_run):
    if harness_failed and not (set(per_task) - set(harness_failed)):
        first = sorted(harness_failed)[0]
        why = harness_failed[first] or {}
        return {'tasks': len(harness_failed), 'first_task': first, 'reason': why.get('reason'),
                'exit_code': why.get('exit_code'),
                'stderr_tail': ' '.join((why.get('stderr') or '').split())[-400:], 'not_run': sorted(not_run)}
    return None


def ordinary_report(plan, tasks, rows, progress_dir, *, persist):
    index = trial_evidence.ordinary_index(plan, [row for task in tasks for row in rows[task['task_id']]])
    if persist:
        trial_evidence.persist_ordinary(progress_dir, index)
    return index


def repeated_report(protocol, tasks, rows, *, trials, trial_identity, run_id, round_no,
                    harness_sha, progress_dir, persist):
    manifest = [{'task_id': task['task_id'], 'trial_id': trial_identity(i), 'seed': str(protocol['seeds'][i])}
                for task in tasks for i in range(trials)]
    report = repeated_trials.aggregate(manifest,
        [row for task in tasks for row in rows[task['task_id']]], protocol['ks'])
    report['configuration'] = protocol
    report['run_id'] = run_id
    report['round'] = round_no
    report['harness_sha'] = harness_sha
    if progress_dir is not None and persist:
        root = repeated_trials.report_root(progress_dir, round_no, harness_sha)
        repeated_trials.persist(root, report)
    return report
