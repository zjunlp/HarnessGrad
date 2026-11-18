"""Offline checks for interrupted-measurement evidence admission."""
import fcntl
import json
import shutil

import pytest

from eval.observe import task_verdicts
from eval.run import runner
from eval.run.evaluation_plan import IdentityCheck
from harnessgrad.datasets import runtime as dataset_runtime
from harnessgrad.datasets import runtime_identity as dataset_runtime_identity
from harnessgrad.core import snapshot
from harnessgrad.provenance import verification_context
from harnessgrad.orchestration.interrupted_measurement import (
    InterruptedMeasurementRefused,
    admit,
    admit_execution_identity,
    inspect_partial,
    resume_inputs,
    measurement_projection,
    recovered_evaluation,
    make_recovery_loader,
)
from harnessgrad.orchestration.partial_round_resume import resume_missing_samples
from harnessgrad.loops.partial_round_evaluation import evaluate_missing_samples


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _fixture(tmp_path):
    original, supplement = tmp_path / 'original', tmp_path / 'supplement'
    original.mkdir(); supplement.mkdir()
    context = verification_context.create({'alpha': 'a' * 64, 'beta': 'b' * 64})
    runtime = {'provider': 'test', 'tasks': {'alpha': {'service': 'a'}, 'beta': {'service': 'b'}},
               'artifacts': {'a': {'identity': 'a'}, 'b': {'identity': 'b'}}}
    config = {'run': {'seed': 'stable', 'trials': 1},
              'agent': {'backend': 'openai', 'model': 'fake', 'base_url': 'http://local'},
              'sandbox': {'cpus': 2, 'memory_mb': 4096}}
    _write(original / 'run_meta.json', {'run_id': 'original', 'agent_model': 'fake', 'agent_backend': 'openai', 'dataset': 'test', 'split_sha': 'same',
        'verification_context': context, 'dataset_runtime_identity': runtime, 'config': config})
    _write(supplement / 'run_meta.json', {'run_id': 'supplement', 'agent_model': 'fake', 'agent_backend': 'openai', 'dataset': 'test', 'split_sha': 'same', 'config': config})
    state = original / 'state_after_round2'; state.mkdir(); (state / 'agent.py').write_text('pass\n')
    snapshot.write(original, 2, state, run_id='original', harness_sha='tree-identity')
    for root, number, tid, score in [(original, 2, 'alpha', 0.2), (supplement, 0, 'beta', 0.8)]:
        result = {'verdict': {'score': score, 'passed': False}, 'exit_code': 0, 'stderr': '', 'sandbox': True}
        receipt = task_verdicts.save(root, number, tid, [result])
        _write(root / 'progress' / f'round-{number}' / f'{tid}.json', {
            'task_id': tid, 'round': number, 'complete': True, 'n_trials': 1, 'score': score,
            'verification_context': verification_context.project(context, [tid]),
            'dataset_runtime_identity': {'provider': 'test', 'tasks': {tid: runtime['tasks'][tid]},
                'artifacts': {tid[0]: runtime['artifacts'][tid[0]]}},
            'verdict_receipt': receipt, 'trial_usage': None, 'trial_env_usage': None,
            'process_outcome': 'completed', 'exit_code': 0,
        })
        trace = root / 'progress' / f'round-{number}' / 'traces' / f'{tid}.trial1.jsonl'
        trace.parent.mkdir(parents=True, exist_ok=True); trace.write_text('{"event":"test"}\n')
    point = {'identity': {'harness_sha': 'tree-identity'}, 'n_scored': 1,
             'n_invalid': 0, 'per_task': {'beta': 0.8}}
    (supplement / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    return original, supplement


def test_combines_complementary_records_without_inventing_usage(tmp_path):
    original, supplement = _fixture(tmp_path)
    view = admit(original, supplement, 2)
    assert view['score'] == 0.5
    assert view['tasks']['alpha']['source'] == 'original'
    assert view['tasks']['beta']['source'] == 'supplement'
    assert view['tasks']['alpha']['trial_usage'] is None
    assert view['status'] == 'admitted_view_not_training_continuation'


def test_admits_fully_measured_batch_candidate_without_original_progress(tmp_path):
    original, supplement = _fixture(tmp_path)
    source_path = original / 'progress/round-2/alpha.json'
    record = json.loads(source_path.read_text())
    journal, _ = task_verdicts.load(original, 2, 'alpha', record['verdict_receipt'])
    record['round'] = 0
    record['verdict_receipt'] = task_verdicts.save(supplement, 0, 'alpha', journal['trials'])
    _write(supplement / 'progress/round-0/alpha.json', record)
    target = supplement / 'progress/round-0/traces/alpha.trial1.jsonl'
    target.write_text((original / 'progress/round-2/traces/alpha.trial1.jsonl').read_text())
    shutil.rmtree(original / 'progress/round-2')
    point = {'identity': {'harness_sha': 'tree-identity'}, 'n_scored': 2,
             'n_invalid': 0, 'per_task': {'alpha': 0.2, 'beta': 0.8}}
    (supplement / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    view = admit(original, supplement, 2)
    assert {row['source'] for row in view['tasks'].values()} == {'supplement'}
    assert view['score'] == 0.5


def test_execution_identity_requires_matching_recorded_settings(tmp_path):
    original, supplement = _fixture(tmp_path)
    for run in (original, supplement):
        path = run / 'run_meta.json'; meta = json.loads(path.read_text())
        meta['config'] = {'run': {'seed': 'stable', 'trials': 1},
                          'agent': {'backend': 'openai', 'model': 'fake', 'base_url': 'http://local'},
                          'sandbox': {'cpus': 2, 'memory_mb': 4096}}
        _write(path, meta)
    assert admit_execution_identity(original, supplement)['status'].endswith('not_authorized')
    path = supplement / 'run_meta.json'; meta = json.loads(path.read_text())
    meta['config']['run']['seed'] = 'changed'; _write(path, meta)
    with pytest.raises(InterruptedMeasurementRefused, match='changed.*seed'):
        admit_execution_identity(original, supplement)
    del meta['config']['agent']['base_url']; _write(path, meta)
    meta['config']['run']['seed'] = 'stable'; _write(path, meta)
    with pytest.raises(InterruptedMeasurementRefused, match='missing.*base_url'):
        admit_execution_identity(original, supplement)
    with pytest.raises(InterruptedMeasurementRefused, match='missing.*base_url'):
        measurement_projection(admit(original, supplement, 2), original, supplement)
    meta['config']['agent']['base_url'] = 'http://local'
    meta['agent_model'] = 'different'; _write(path, meta)
    with pytest.raises(InterruptedMeasurementRefused, match='inconsistent recorded agent model'):
        admit_execution_identity(original, supplement)


def test_rehydrates_task_evidence_and_rejects_later_change(tmp_path):
    original, supplement = _fixture(tmp_path)
    view = admit(original, supplement, 2)
    payload = resume_inputs(view, original, supplement)
    assert payload['verdicts']['alpha']['score'] == 0.2
    assert payload['prior_records']['alpha']['trial_usage'] is None
    assert payload['measurement_provenance']['beta']['run_id'] == 'supplement'
    assert payload['traces']['alpha'] == '{"event":"test"}\n'
    (original / 'progress/round-2/traces/alpha.trial1.jsonl').write_text('{"event":"changed"}\n')
    with pytest.raises(InterruptedMeasurementRefused, match='changed'):
        resume_inputs(view, original, supplement)


def test_projection_keeps_missing_usage_unknown_and_source_identity(tmp_path):
    original, supplement = _fixture(tmp_path)
    path = original / 'progress/round-2/alpha.json'
    record = json.loads(path.read_text())
    record['trial_usage'] = [{'input_tokens': 7, 'output_tokens': 2, 'calls': 1}]
    _write(path, record)
    view = admit(original, supplement, 2)
    projection = measurement_projection(view, original, supplement)
    assert projection['per_task'] == {'alpha': 0.2, 'beta': 0.8}
    assert projection['per_task_trials'] == {'alpha': [0.2], 'beta': [0.8]}
    assert projection['trial_verdicts']['alpha'][0]['score'] == 0.2
    assert projection['trial_traces']['beta'] == [projection['traces']['beta']]
    assert projection['harness_failed'] == {}
    assert projection['tokens']['input'] is None
    assert projection['tokens']['input_reported'] == 7
    assert projection['tokens']['input_tasks_missing'] == 1
    assert projection['measurement_provenance']['beta']['run_id'] == 'supplement'
    assert projection['status'] == 'projected_not_training_continuation'


def test_projection_preserves_harness_failure_from_trial(tmp_path):
    original, supplement = _fixture(tmp_path)
    path = original / 'progress/round-2/alpha.json'; record = json.loads(path.read_text())
    trial = {'verdict': {'score': 0.2, 'passed': False}, 'exit_code': 2,
             'stderr': 'startup failed', 'sandbox': True}
    (original / record['verdict_receipt']['path']).unlink()
    record['verdict_receipt'] = task_verdicts.save(original, 2, 'alpha', [trial])
    _write(path, record)
    projection = measurement_projection(admit(original, supplement, 2), original, supplement)
    assert projection['harness_failed']['alpha']['exit_code'] == 2
    assert 'beta' not in projection['harness_failed']


def test_recovered_evaluation_requires_exact_context_and_clean_outcomes(tmp_path):
    original, supplement = _fixture(tmp_path)
    view = admit(original, supplement, 2)
    context = json.loads((original / 'run_meta.json').read_text())['verification_context']
    result = recovered_evaluation(view, original, supplement,
        expected_candidate_tree='tree-identity', expected_context=context)
    assert result['per_task'] == {'alpha': 0.2, 'beta': 0.8}
    assert result['tokens']['input'] is None
    assert result['integrity_status'] == 'unknown_interrupted_process'
    assert result['measurement_provenance']['beta']['run_id'] == 'supplement'
    with pytest.raises(InterruptedMeasurementRefused, match='candidate identity'):
        recovered_evaluation(view, original, supplement,
            expected_candidate_tree='different', expected_context=context)
    path = original / 'progress/round-2/alpha.json'; record = json.loads(path.read_text())
    record['scorer_isolation_class'] = 'actor-workdir'; _write(path, record)
    view = admit(original, supplement, 2)
    with pytest.raises(InterruptedMeasurementRefused, match='scorer isolation'):
        recovered_evaluation(view, original, supplement,
            expected_candidate_tree='tree-identity', expected_context=context)
    record.pop('scorer_isolation_class')
    record['process_outcome'] = 'stopped_by_signal'; _write(path, record)
    view = admit(original, supplement, 2)
    with pytest.raises(InterruptedMeasurementRefused, match='non-clean'):
        recovered_evaluation(view, original, supplement,
            expected_candidate_tree='tree-identity', expected_context=context)


def test_recovery_loader_rechecks_live_identity_and_run(tmp_path):
    original, supplement = _fixture(tmp_path)
    meta = json.loads((original / 'run_meta.json').read_text())
    config = meta['config']
    loader = make_recovery_loader(original, supplement, expected_round=2,
                                  live_config=config, expected_context=meta['verification_context'])
    assert loader(original, 2)['per_task'] == {'alpha': 0.2, 'beta': 0.8}
    with pytest.raises(InterruptedMeasurementRefused, match='run or round'):
        loader(original, 3)
    config['agent']['model'] = 'different'
    with pytest.raises(InterruptedMeasurementRefused, match='live execution identity'):
        make_recovery_loader(original, supplement, expected_round=2,
                             live_config=config, expected_context=meta['verification_context'])


def test_inspects_train_round_and_identifies_only_missing_samples(tmp_path):
    original, _ = _fixture(tmp_path)
    result = inspect_partial(original, 2)
    assert result['missing_task_ids'] == ['beta']
    assert set(result['completed']) == {'alpha'}
    assert result['completed']['alpha']['trial_usage'] is None
    assert result['candidate_tree_sha'] == 'tree-identity'
    assert result['status'] == 'inspected_not_execution_authorized'


def test_partial_inspection_refuses_missing_trace(tmp_path):
    original, _ = _fixture(tmp_path)
    (original / 'progress/round-2/traces/alpha.trial1.jsonl').unlink()
    with pytest.raises(InterruptedMeasurementRefused, match='trace'):
        inspect_partial(original, 2)


def _persist_missing_beta(original, supplement):
    source = json.loads((supplement / 'progress/round-0/beta.json').read_text())
    journal, _ = task_verdicts.load(supplement, 0, 'beta', source['verdict_receipt'])
    source['round'] = 2
    source['verdict_receipt'] = task_verdicts.save(original, 2, 'beta', journal['trials'])
    _write(original / 'progress/round-2/beta.json', source)
    target = original / 'progress/round-2/traces/beta.trial1.jsonl'
    target.write_text((supplement / 'progress/round-0/traces/beta.trial1.jsonl').read_text())


def test_resume_runs_only_missing_and_keeps_previous_bytes(tmp_path):
    original, supplement = _fixture(tmp_path)
    old = (original / 'progress/round-2/alpha.json').read_bytes()
    calls = []
    def evaluate(ids):
        calls.append(ids)
        _persist_missing_beta(original, supplement)
    result = resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
        admit_execution=lambda inspection: None, evaluate_missing=evaluate)
    assert calls == [['beta']]
    assert result['status'] == 'complete_not_published'
    assert set(result['inspection']['completed']) == {'alpha', 'beta'}
    assert (original / 'progress/round-2/alpha.json').read_bytes() == old
    assert result['inspection']['completed']['alpha']['trial_usage'] is None


@pytest.mark.parametrize('orphan', ['start', 'verdict'])
def test_resume_refuses_missing_sample_with_unfinished_attempt(tmp_path, orphan):
    original, _ = _fixture(tmp_path)
    if orphan == 'start':
        task_verdicts.start(original, 2, 'beta')
        path = original / 'task-starts/round-2' / task_verdicts.relative_path(2, 'beta').name
    else:
        receipt = task_verdicts.save(original, 2, 'beta', [
            {'verdict': {'score': 0.8}, 'exit_code': 0}])
        path = original / receipt['path']
    before = path.read_bytes()
    with pytest.raises(InterruptedMeasurementRefused, match='unfinished prior attempt'):
        resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
            admit_execution=lambda inspection: None,
            evaluate_missing=lambda ids: pytest.fail('unfinished sample was rerun'))
    assert path.read_bytes() == before
    assert not (original / '.partial-round-2-resume.json').exists()


def test_resume_after_interruption_never_reexecutes_durable_sample(tmp_path):
    original, supplement = _fixture(tmp_path)
    def interrupted(ids):
        assert ids == ['beta']
        _persist_missing_beta(original, supplement)
        raise RuntimeError('simulated interruption')
    with pytest.raises(RuntimeError, match='interruption'):
        resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
            admit_execution=lambda inspection: None, evaluate_missing=interrupted)
    result = resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
        admit_execution=lambda inspection: None, evaluate_missing=lambda ids: pytest.fail('duplicate call'))
    assert result['status'] == 'complete_not_published'
    assert result['requested_task_ids'] == []


def test_resume_rejects_changed_existing_evidence(tmp_path):
    original, supplement = _fixture(tmp_path)
    def tamper(ids):
        _persist_missing_beta(original, supplement)
        path = original / 'progress/round-2/alpha.json'
        data = json.loads(path.read_text()); data['extra'] = 'changed'; _write(path, data)
    with pytest.raises(InterruptedMeasurementRefused, match='previously completed'):
        resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
            admit_execution=lambda inspection: None, evaluate_missing=tamper)


def test_resume_detects_original_evidence_changed_across_crash(tmp_path):
    original, supplement = _fixture(tmp_path)
    def interrupted(ids):
        _persist_missing_beta(original, supplement)
        raise RuntimeError('simulated interruption')
    with pytest.raises(RuntimeError):
        resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
            admit_execution=lambda inspection: None, evaluate_missing=interrupted)
    path = original / 'progress/round-2/alpha.json'
    data = json.loads(path.read_text()); data['extra'] = 'changed'; _write(path, data)
    with pytest.raises(InterruptedMeasurementRefused, match='previously completed'):
        resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
            admit_execution=lambda inspection: None, evaluate_missing=lambda ids: pytest.fail('duplicate call'))


def test_resume_requires_admission_before_any_evaluation(tmp_path):
    original, _ = _fixture(tmp_path)
    def refuse(inspection):
        raise InterruptedMeasurementRefused('model identity mismatch')
    with pytest.raises(InterruptedMeasurementRefused, match='model identity'):
        resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
            admit_execution=refuse, evaluate_missing=lambda ids: pytest.fail('evaluation must not start'))
    assert not (original / '.partial-round-2-resume.json').exists()


def test_resume_rejects_concurrent_cooperating_resumer(tmp_path):
    original, _ = _fixture(tmp_path)
    with (original / '.partial-round-resume.lock').open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(InterruptedMeasurementRefused, match='already being resumed'):
            resume_missing_samples(original, 2, expected_candidate_tree='tree-identity',
                admit_execution=lambda inspection: pytest.fail('must not admit'),
                evaluate_missing=lambda ids: pytest.fail('must not evaluate'))


def test_evaluation_adapter_passes_only_missing_tasks_and_original_round(tmp_path, monkeypatch):
    original, supplement = _fixture(tmp_path)
    meta = json.loads((original / 'run_meta.json').read_text())
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL', '1')
    calls = []
    def evaluator(work, tasks, scorable, cache, **kwargs):
        calls.append((tasks, kwargs))
        _persist_missing_beta(original, supplement)
        return {'per_task': {'beta': 0.8}, 'tampered': {}}
    args = dict(run_dir=original, round_no=2, work=tmp_path,
        tasks=[{'task_id': 'alpha'}, {'task_id': 'beta'}], scorable={},
        harness_sha='tree-identity', run_id='original', run_seed='seed', jobs=1,
        sandbox=True, setups={}, verifiers={}, envs={}, dataset_runtime_identity=meta['dataset_runtime_identity'],
        verification_context=meta['verification_context'], harness_runtime_identity=None,
        admit_execution=lambda inspection: None, evaluator=evaluator)
    result = evaluate_missing_samples(**args)
    assert result['status'] == 'samples_complete_not_published'
    assert [x['task_id'] for x in calls[0][0]] == ['beta']
    assert calls[0][1]['run_id'] == 'original' and calls[0][1]['round_no'] == 2
    assert calls[0][1]['run_seed'] == 'seed' and calls[0][1]['trials'] == 1
    result = evaluate_missing_samples(**args)
    assert result['fresh_evaluation'] is None and len(calls) == 1


def test_evaluation_adapter_rejects_selection_drift_before_model_call(tmp_path, monkeypatch):
    original, _ = _fixture(tmp_path)
    meta = json.loads((original / 'run_meta.json').read_text())
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL', '1')
    with pytest.raises(InterruptedMeasurementRefused, match='task set'):
        evaluate_missing_samples(run_dir=original, round_no=2, work=tmp_path,
            tasks=[{'task_id': 'beta'}], scorable={}, harness_sha='tree-identity',
            run_id='original', run_seed='seed', jobs=1, sandbox=True, setups={},
            verifiers={}, envs={}, dataset_runtime_identity=meta['dataset_runtime_identity'], verification_context=meta['verification_context'],
            harness_runtime_identity=None, admit_execution=lambda inspection: None,
            evaluator=lambda *args, **kwargs: pytest.fail('model must not be called'))


def test_evaluation_adapter_rejects_runtime_drift_before_model_call(tmp_path, monkeypatch):
    original, _ = _fixture(tmp_path)
    meta = json.loads((original / 'run_meta.json').read_text())
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL', '1')
    with pytest.raises(InterruptedMeasurementRefused, match='dataset runtime changed'):
        evaluate_missing_samples(run_dir=original, round_no=2, work=tmp_path,
            tasks=[{'task_id': 'alpha'}, {'task_id': 'beta'}], scorable={},
            harness_sha='tree-identity', run_id='original', run_seed='seed', jobs=1,
            sandbox=True, setups={}, verifiers={}, envs={}, dataset_runtime_identity={},
            verification_context=meta['verification_context'], harness_runtime_identity=None,
            admit_execution=lambda inspection: pytest.fail('must not admit'),
            evaluator=lambda *args, **kwargs: pytest.fail('model must not be called'))


def test_real_evaluator_resumes_only_missing_task_and_persists_evidence(tmp_path, monkeypatch):
    original, _ = _fixture(tmp_path)
    tasks = [{'task_id': 'alpha'}, {'task_id': 'beta'}]
    context = verification_context.capture(tasks, {}, setups={}, verifiers={}, envs={}, sandbox=False)
    binding = {'schema': dataset_runtime.BINDING_SCHEMA, 'dataset': 'fake', 'profile': 'execution',
        'role': 'service', 'runtime_id': 'a' * 64, 'manifest_path': '/tmp/fake/artifact.json',
        'path': '/tmp/fake/venv', 'python': '/tmp/fake/venv/bin/python',
        'runtime_paths': ['/tmp/fake/venv']}
    runtime = dataset_runtime_identity.create(tasks={tid: {'service': binding} for tid in ('alpha', 'beta')})
    meta_path = original / 'run_meta.json'; meta = json.loads(meta_path.read_text())
    meta.update(verification_context=context, dataset_runtime_identity=runtime); _write(meta_path, meta)
    progress = original / 'progress/round-2/alpha.json'; record = json.loads(progress.read_text())
    record.update(verification_context=verification_context.project(context, ['alpha']),
        dataset_runtime_identity=dataset_runtime_identity.project(runtime, ['alpha']))
    _write(progress, record)
    calls = []
    def actor(repo, task, **kwargs):
        calls.append(task['task_id'])
        return {'task_id': task['task_id'], 'trace': '{"event":"offline"}\n',
            'exit_code': 0, 'stderr': '', 'env_usage': {},
            'verdict': {'kind': 'answer', 'passed': True, 'score': 1.0}}
    monkeypatch.setenv('HG_TASK_VERDICT_JOURNAL', '1')
    monkeypatch.setattr(runner, 'run_one', actor)
    # The synthetic binding has no real runtime; the real evaluator and its
    # progress/receipt writers remain in use, while runtime admission is stubbed.
    monkeypatch.setattr(runner.evaluation_plan, 'capture_dataset',
        lambda *args, **kwargs: IdentityCheck(runtime))
    args = dict(run_dir=original, round_no=2, work=original / 'state_after_round2',
        tasks=tasks, scorable={}, harness_sha='tree-identity', run_id='original',
        run_seed='seed', jobs=1, sandbox=False, setups={}, verifiers={}, envs={},
        dataset_runtime_identity=runtime, verification_context=context,
        harness_runtime_identity=None, admit_execution=lambda inspection: None)
    result = evaluate_missing_samples(**args)
    assert result['status'] == 'samples_complete_not_published' and calls == ['beta']
    assert (original / 'progress/round-2/traces/beta.trial1.jsonl').is_file()
    assert evaluate_missing_samples(**args)['fresh_evaluation'] is None
    assert calls == ['beta']


@pytest.mark.parametrize('damage', ['missing_trace', 'bad_receipt', 'changed_model',
                                    'changed_checkpoint', 'wrong_curve', 'missing_task',
                                    'changed_runtime_artifact', 'missing_runtime_binding'])
def test_rejects_incomplete_or_inconsistent_evidence(tmp_path, damage):
    original, supplement = _fixture(tmp_path)
    if damage == 'missing_trace':
        (original / 'progress/round-2/traces/alpha.trial1.jsonl').unlink()
    elif damage == 'bad_receipt':
        path = next((original / 'task-verdicts').rglob('*.json')); path.write_text('{}')
    elif damage == 'changed_model':
        _write(supplement / 'run_meta.json', {'run_id': 'supplement', 'agent_model': 'other', 'dataset': 'test', 'split_sha': 'same'})
    elif damage == 'changed_checkpoint':
        (original / 'state_after_round2/agent.py').write_text('changed\n')
    elif damage == 'wrong_curve':
        (supplement / 'curve.jsonl').write_text(json.dumps({'identity': {'harness_sha': 'wrong'}}))
    elif damage in {'changed_runtime_artifact', 'missing_runtime_binding'}:
        path = supplement / 'progress/round-0/beta.json'
        record = json.loads(path.read_text())
        if damage == 'changed_runtime_artifact':
            record['dataset_runtime_identity']['artifacts']['b']['identity'] = 'changed'
        else:
            record['dataset_runtime_identity']['tasks'].pop('beta')
        _write(path, record)
    else:
        (supplement / 'progress/round-0/beta.json').unlink()
    with pytest.raises(InterruptedMeasurementRefused):
        admit(original, supplement, 2)
