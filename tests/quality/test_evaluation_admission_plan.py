"""Identity/reuse protocol controls for extracted evaluation planning; all data authored."""
from contextlib import contextmanager
from copy import deepcopy
import json

from eval.run import evaluation_plan, measurement_cache, runner
from eval.verification import native_runtime
from harnessgrad import verification_context


def test_measurement_key_keeps_baseline_wire_format():
    # Golden captured from 2f61b7a's nested measurement_key, including all identity axes.
    identity = measurement_cache.MeasurementIdentity(
        'owned-sha', 't', 3, {'tasks': {'t': 'verified-task'}},
        {'kind': 'selected', 'sha256': 'dataset'}, {'sha256': 'actor'},
        {'codex': {'kind': 'codex', 'cacheable': True}}, {'sha256': 'scorer'},
        {'verified': True, 'sha256': 'native'}, 'chessbench_puzzles.stepwise.v1')
    assert identity.key() == (
        'owned-sha:t:3:verification-context:verified-task'
        ':dataset-runtime:8078b8cef0a5ff648dcdff6cfba2f9c636531518dbe4261bae84daf0fc439db1'
        ':harness-runtime:actor'
        ':external-runtime:b7acf1ef35f96d957cb52041e7e77f60b736c1c0d6da54d4faca28dd8a97e835'
        ':scorer:7994929bfe141555a5929d4fc6b21134bc0db22cfdd1b8399781c04191d53cea'
        ':native:1f5b4420548fa3e0a3c4399549d78e3c7ad8852a075dd25d87bc669cd47eba19'
        ':chessbench_puzzles.stepwise.v1')


def test_cache_hit_is_read_only_and_pristine_coverage_is_not_a_hit():
    context = verification_context.capture([{'task_id': 't', 'goal': 'authored'}], {})
    identity = measurement_cache.MeasurementIdentity('h', 't', 2, context, None, None, {}, None, None, None)
    entry = {'verification_context': context, 'score': 0.5,
             'verdict': {'passed': True}, 'trial_verdicts': [{'passed': True}]}
    cache = {identity.key(): entry}
    before = deepcopy(cache)
    ordinary = measurement_cache.lookup(cache, identity, fresh=False, allowed=True,
                                        verifier={}, run_id='r', trial_identity=lambda i: str(i))
    assert ordinary.hit is entry and ordinary.invalid is None
    pristine = measurement_cache.lookup(
        cache, identity, fresh=False, allowed=True,
        verifier={'scorer_bundle': {'isolation_class': 'pristine-offline'}},
        run_id='r', trial_identity=lambda i: str(i))
    assert pristine.hit is None and pristine.invalid == {
        'stage': 'check', 'detail': 'pristine cached verdict execution binding coverage mismatch'}
    assert cache == before
    assert measurement_cache.lookup(cache, identity, fresh=False, allowed=False,
                                    verifier={}, run_id='r', trial_identity=lambda i: str(i)).hit is None
    foreign = deepcopy(entry)
    foreign['verdict']['native_runtime'] = {'verified': True}
    assert not measurement_cache.native_cache_matches(foreign, None, 2)


def test_native_admission_fd_scope_closes_before_actor_and_positive_cache_hit(tmp_path, monkeypatch):
    repo = tmp_path / 'candidate'
    repo.mkdir()
    (repo / 'harness.json').write_text(json.dumps({'name': 'authored', 'entrypoint': 'agent.py'}))
    task = {'task_id': 't', 'goal': 'authored native scope'}
    receipt = {'verified': True, 'sha256': 'authored'}
    events = []
    active = []

    @contextmanager
    def admit(binding):
        events.append('enter')
        active.append(binding)
        try:
            yield type('Admitted', (), {'receipt': receipt})()
        finally:
            active.pop()
            events.append('exit')

    def actor(repo, task, **kwargs):
        assert not active
        events.append('actor')
        return {'task_id': 't', 'answer': 'authored', 'trace': '', 'exit_code': 0, 'stderr': '',
                'verdict': {'passed': True, 'score': 1.0, 'native_runtime': receipt}}

    monkeypatch.setattr(native_runtime, 'validate_route', lambda *a, **k: {'fixture': 'native'})
    monkeypatch.setattr(native_runtime, 'admit', admit)
    monkeypatch.setattr(runner, 'run_one', actor)  # Public late-bound task port stays patchable.
    monkeypatch.setattr(runner, 'snapshot', lambda *a: {})
    cache = {}
    kwargs = dict(cache=cache, harness_sha='owned', verifiers={'t': {'native_runtime': {'fixture': True}}},
                  persist_trial_report=False)
    first = runner._evaluate(repo, [task], {}, **kwargs)
    second = runner._evaluate(repo, [task], {}, **kwargs)
    assert first['per_task'] == second['per_task'] == {'t': 1.0}
    assert first['cached_tasks'] == [] and second['cached_tasks'] == ['t']
    assert events == ['enter', 'exit', 'actor', 'enter', 'exit'] and not active


def test_context_refusal_keeps_progress_metadata_without_enforcement_or_actor(tmp_path, monkeypatch):
    repo = tmp_path / 'candidate'
    repo.mkdir()
    (repo / 'harness.json').write_text('{"name":"authored","entrypoint":"agent.py"}')
    task = {'task_id': 't', 'goal': 'authored refusal'}
    context = verification_context.capture([task], {})
    monkeypatch.setattr(evaluation_plan, 'capture_verification',
                        lambda *a, **k: evaluation_plan.IdentityCheck(context, 'authored mismatch'))
    monkeypatch.setattr(runner, 'run_one', lambda *a, **k: (_ for _ in ()).throw(AssertionError('actor launched')))
    monkeypatch.setattr(runner, 'snapshot', lambda *a: {})
    progress = []
    monkeypatch.setattr(runner, '_write_progress', lambda *a, **k: progress.append((a, k)))
    result = runner._evaluate(repo, [task], {}, persist_trial_report=False)
    assert result['per_task'] == {} and result['invalid']['t']['stage'] == 'verification context'
    assert len(progress) == 1
    assert progress[0][0][4] == {'stage': 'verification context', 'detail': 'authored mismatch'}
    assert progress[0][1] == {'verification_context': context}


def test_malformed_native_cache_rows_are_misses():
    for entry in (None, False, 42, 'not a measurement', []):
        assert runner._native_cache_matches(entry, None, 1) is False
