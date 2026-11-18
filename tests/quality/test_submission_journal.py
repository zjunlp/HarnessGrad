"""Offline preservation and restoration of a multi-step method submission."""
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from harnessgrad.orchestration.submission_journal import SubmissionJournalError, capture, pending, restore
from eval.ckpt.git_state import commit_state, init_repo, reset_hard
from eval.ckpt.source_tree import copy_harness
from harnessgrad.loops import mode_a
from harnessgrad.provenance import verification_context


def test_error_only_submission_is_not_reserved_as_a_candidate():
    assert not mode_a._has_measurable_submission([
        {'method_reported': {'error': 'historical state missing'}}])
    assert mode_a._has_measurable_submission([
        {'method_reported': {'error': 'failed'}}, {'harness_dir': '/candidate'}])


def _submission(tmp_path):
    run = tmp_path / 'run-a'; run.mkdir()
    scratch = tmp_path / 'scratch'; scratch.mkdir()
    steps = []
    for index in (1, 2, 3):
        source = scratch / f'candidate-{index}'; source.mkdir()
        (source / 'agent.py').write_text(f'VALUE = {index}\n')
        steps.append({'harness_dir': str(source), 'label': f'child-{index}',
                      'method_reported': {'selected_parent': 4}})
    trajectory = {'steps': steps, 'provenance': {'method': 'fake'}, 'nominated': 0}
    reply = {'changed': True, 'stop': False, 'method_usage': {'input': None, 'output': 42}}
    return run, scratch, reply, trajectory


def test_preserves_reply_and_all_steps_without_reinvoking_method(tmp_path):
    run, scratch, reply, trajectory = _submission(tmp_path)
    journal = capture(run, scratch, run_id='run-a', round_no=5, previous_tree='parent',
                      reply=reply, trajectory=trajectory, generation_s=4.25)
    got_reply, got_trajectory, duration = restore(journal, tmp_path / 'restored', run_id='run-a',
        round_no=5, previous_tree='parent')
    assert got_reply == reply and duration == 4.25
    assert [x['label'] for x in got_trajectory['steps']] == ['child-1', 'child-2', 'child-3']
    for index, step in enumerate(got_trajectory['steps'], start=1):
        assert (tmp_path / 'restored' / f'step-{index}' / 'agent.py').read_text() == f'VALUE = {index}\n'
        assert step['method_reported']['selected_parent'] == 4
    assert journal.stat().st_mode & 0o077 == 0
    assert (journal / 'manifest.json').stat().st_mode & 0o077 == 0


def test_refuses_foreign_step_and_does_not_publish_partial_journal(tmp_path):
    run, scratch, reply, trajectory = _submission(tmp_path)
    outsider = tmp_path / 'outside'; outsider.mkdir()
    trajectory['steps'][1]['harness_dir'] = str(outsider)
    with pytest.raises(SubmissionJournalError, match='outside'):
        capture(run, scratch, run_id='run-a', round_no=5, previous_tree='parent',
                reply=reply, trajectory=trajectory, generation_s=1)
    assert not (run / '.pending-method-submissions' / 'round-5').exists()


def test_refuses_changed_snapshot_and_wrong_parent(tmp_path):
    run, scratch, reply, trajectory = _submission(tmp_path)
    journal = capture(run, scratch, run_id='run-a', round_no=5, previous_tree='parent',
                      reply=reply, trajectory=trajectory, generation_s=1)
    with pytest.raises(SubmissionJournalError, match='identity'):
        restore(journal, tmp_path / 'wrong-parent', run_id='run-a', round_no=5, previous_tree='other')
    artifact = journal / 'step-2/snapshots/round-2.json'
    payload = json.loads(artifact.read_text()); payload['harness_sha'] = 'changed'
    artifact.write_text(json.dumps(payload))
    with pytest.raises(SubmissionJournalError):
        restore(journal, tmp_path / 'tampered', run_id='run-a', round_no=5, previous_tree='parent')


def test_refuses_replacing_a_prior_submission(tmp_path):
    run, scratch, reply, trajectory = _submission(tmp_path)
    capture(run, scratch, run_id='run-a', round_no=5, previous_tree='parent',
            reply=reply, trajectory=trajectory, generation_s=1)
    with pytest.raises(SubmissionJournalError, match='already exists'):
        capture(run, scratch, run_id='run-a', round_no=5, previous_tree='parent',
                reply=reply, trajectory=trajectory, generation_s=2)


def test_pending_submission_replays_only_uncommitted_batch_steps(tmp_path):
    run, scratch, reply, trajectory = _submission(tmp_path)
    capture(run, scratch, run_id='run-a', round_no=5, previous_tree='parent',
            reply=reply, trajectory=trajectory, generation_s=6)
    shutil.copytree(scratch / 'candidate-1', run / 'state_after_round5')
    curve = [{'round': 4, 'identity': {'harness_sha': 'parent'}},
             {'round': 5, 'identity': {'harness_sha': 'child-1'}}]
    result = pending(run, curve, tmp_path / 'pending-scratch')
    assert result['consumed_steps'] == 1
    assert result['accepted_step_count'] == 3
    assert [step['label'] for step in result['remaining_steps']] == ['child-2', 'child-3']
    assert result['reply'] == reply and result['generation_s'] == 6


def test_pending_submission_rejects_changed_committed_step(tmp_path):
    run, scratch, reply, trajectory = _submission(tmp_path)
    capture(run, scratch, run_id='run-a', round_no=5, previous_tree='parent',
            reply=reply, trajectory=trajectory, generation_s=1)
    state = run / 'state_after_round5'; shutil.copytree(scratch / 'candidate-1', state)
    (state / 'agent.py').write_text('tampered\n')
    curve = [{'round': 4, 'identity': {'harness_sha': 'parent'}},
             {'round': 5, 'identity': {'harness_sha': 'child-1'}}]
    with pytest.raises(SubmissionJournalError, match='differs'):
        pending(run, curve, tmp_path / 'pending-scratch')


def test_pending_submission_does_not_expand_truncated_original_budget(tmp_path):
    run, scratch, reply, trajectory = _submission(tmp_path)
    capture(run, scratch, run_id='run-a', round_no=5, previous_tree='parent',
            reply=reply, trajectory=trajectory, generation_s=1, accepted_step_count=2)
    for i in (1, 2):
        shutil.copytree(scratch / f'candidate-{i}', run / f'state_after_round{i + 4}')
    curve = [{'round': 4, 'identity': {'harness_sha': 'parent'}},
             {'round': 5, 'identity': {'harness_sha': 'child-1'}},
             {'round': 6, 'identity': {'harness_sha': 'child-2'}}]
    assert pending(run, curve, tmp_path / 'pending-scratch') is None


def test_real_training_loop_journals_submission_before_evaluation(tmp_path, monkeypatch):
    class Captured(Exception):
        pass

    work = tmp_path / 'work'; work.mkdir()
    manifest = {'name': 'probe', 'version': '1', 'entrypoint': 'agent.py', 'backend': 'cli'}
    (work / 'harness.json').write_text(json.dumps(manifest))
    (work / 'agent.py').write_text("print('old')\n")
    init_repo(work); sha = commit_state(work, 'initial')
    run = tmp_path / 'runs' / 'run-a'; run.mkdir(parents=True)
    scratch = tmp_path / 'method-scratch'; scratch.mkdir()
    monkeypatch.setattr(mode_a, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(scratch)))
    monkeypatch.setenv('HARNESSGRAD_SEED', '1')
    monkeypatch.setenv('HG_EXPERIMENTAL_ROUND_JOURNAL', '1')

    def method(work_arg, argv, request, *args, **kwargs):
        candidate = Path(request['workspace']) / 'candidate'
        copy_harness(Path(request['base_harness']), candidate)
        (candidate / 'agent.py').write_text("print('new')\n")
        Path(request['trajectory_out']).write_text(json.dumps({'steps': [
            {'harness_dir': str(candidate), 'label': 'candidate'}],
            'provenance': {'method': 'fake'}}))
        return {'changed': True, 'stop': False, 'generation_tokens': 3}

    real_capture = mode_a.submission_journal.capture
    def capture_and_interrupt(*args, **kwargs):
        real_capture(*args, **kwargs)
        raise Captured()

    monkeypatch.setattr(mode_a, '_call_method', method)
    monkeypatch.setattr(mode_a.submission_journal, 'capture', capture_and_interrupt)
    monkeypatch.setattr(mode_a, 'evaluate', lambda *a, **k: pytest.fail('evaluation ran before journal'))
    args = SimpleNamespace(method_entrypoint=['unused'], runs_root=tmp_path / 'runs',
        method_timeout=1, sandbox=False, rounds=1, jobs=1, trials=1, dataset='demo',
        baseline=None, harness='probe', side='train', sampling='all', work_root=tmp_path)
    point = {'round': 0, 'score': 0.0, 'score_kind': 'train', 'identity': {'harness_sha': sha}}
    cumulative = {'wall_clock_s': 0, 'generation_tokens': 0}
    with pytest.raises(Captured):
        mode_a._run_mode_a(args, work, manifest, 'run-a', 'A', [point], sha,
            {'traces': {}}, [{'task_id': 'train', 'goal': 'offline'}], ['train'], None,
            {}, {}, cumulative, train_ids=['train'])
    journal = run / '.pending-method-submissions/round-1'
    reply, trajectory, _ = restore(journal, tmp_path / 'restored-loop', run_id='run-a',
        round_no=1, previous_tree=sha)
    assert reply['generation_tokens'] == 3
    assert Path(trajectory['steps'][0]['harness_dir']).joinpath('agent.py').read_text() == "print('new')\n"


@pytest.mark.parametrize('first_usage_known', [True, False])
@pytest.mark.parametrize('prior_session', [True, False])
def test_failed_method_attempt_does_not_block_later_saved_candidate(tmp_path, monkeypatch, first_usage_known, prior_session):
    class Captured(Exception):
        pass

    work = tmp_path / 'work'; work.mkdir()
    manifest = {'name': 'probe', 'version': '1', 'entrypoint': 'agent.py', 'backend': 'cli'}
    (work / 'harness.json').write_text(json.dumps(manifest))
    (work / 'agent.py').write_text("print('old')\n")
    init_repo(work); sha = commit_state(work, 'initial')
    run = tmp_path / 'runs' / 'run-a'; run.mkdir(parents=True)
    if prior_session:
        from harnessgrad.experiment.method_attempts import MethodAttempts
        ledger = MethodAttempts(run, 'run-a')
        ledger.finish(ledger.begin(1), {'method_usage': {'total_tokens': 4}})
    scratch = tmp_path / 'method-scratch'; scratch.mkdir()
    monkeypatch.setattr(mode_a, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(scratch)))
    monkeypatch.setenv('HARNESSGRAD_SEED', '1')
    monkeypatch.setenv('HG_EXPERIMENTAL_ROUND_JOURNAL', '1')
    calls = []

    def method(work_arg, argv, request, *args, **kwargs):
        calls.append(request['round_index'])
        candidate = Path(request['workspace']) / f'candidate-{len(calls)}'
        copy_harness(Path(request['base_harness']), candidate)
        step = {'harness_dir': str(candidate)}
        if len(calls) == 1:
            step['method_reported'] = {'error': 'unparseable response'}
        else:
            (candidate / 'agent.py').write_text("print('new')\n")
        Path(request['trajectory_out']).write_text(json.dumps({'steps': [step]}))
        return {'changed': len(calls) > 1, 'stop': False,
                'generation_tokens': len(calls),
                'method_usage': {'total_tokens': len(calls) if len(calls) > 1 or first_usage_known else None}}

    real_capture = mode_a.submission_journal.capture
    def capture_and_interrupt(*args, **kwargs):
        real_capture(*args, **kwargs)
        raise Captured()

    monkeypatch.setattr(mode_a, '_call_method', method)
    monkeypatch.setattr(mode_a.submission_journal, 'capture', capture_and_interrupt)
    monkeypatch.setattr(mode_a, 'evaluate', lambda *a, **k: pytest.fail('evaluation ran before journal'))
    args = SimpleNamespace(method_entrypoint=['unused'], runs_root=tmp_path / 'runs',
        method_timeout=1, sandbox=False, rounds=1, jobs=1, trials=1, dataset='demo',
        baseline=None, harness='probe', side='train', sampling='all', work_root=tmp_path,
        resume=prior_session)
    point = {'round': 0, 'score': 0.0, 'score_kind': 'train', 'identity': {'harness_sha': sha}}
    cumulative = {'wall_clock_s': 0, 'generation_tokens': 0}
    with pytest.raises(Captured):
        mode_a._run_mode_a(args, work, manifest, 'run-a', 'A', [point], sha,
            {'traces': {}}, [{'task_id': 'train', 'goal': 'offline'}], ['train'], None,
            {}, {}, cumulative, train_ids=['train'])
    assert calls == [1, 1]
    ledger = json.loads((run / 'method_attempts.json').read_text())
    assert [row['observed_total_tokens'] for row in ledger['attempts']] == (
        ([4] if prior_session else []) + [1 if first_usage_known else None, 2])
    saved = json.loads((run / '.pending-method-submissions/round-1/manifest.json').read_text())
    assert saved['attempt_usage']['generation_tokens_lower_bound'] == 3 + (4 if prior_session else 0)
    assert saved['attempt_usage']['complete'] is first_usage_known
    assert saved['attempt_usage']['generation_s_complete'] is not prior_session
    assert saved['reply']['generation_tokens'] == 2
    assert cumulative['generation_tokens'] == saved['attempt_usage']['generation_tokens_lower_bound']
    assert cumulative.get('generation_tokens_status') == (None if first_usage_known else 'reported_lower_bound')
    reply, trajectory, _ = restore(run / '.pending-method-submissions/round-1',
        tmp_path / 'restored-loop', run_id='run-a', round_no=1, previous_tree=sha)
    assert reply['generation_tokens'] == 2
    assert Path(trajectory['steps'][0]['harness_dir']).joinpath('agent.py').read_text() == "print('new')\n"


def test_real_resumed_loop_refuses_to_repeat_partial_measurement(tmp_path, monkeypatch):
    work = tmp_path / 'work'; work.mkdir()
    (work / 'agent.py').write_text('VALUE = 0\n')
    init_repo(work); sha = commit_state(work, 'initial')
    run = tmp_path / 'runs' / 'run-a'; run.mkdir(parents=True)
    source = tmp_path / 'source'; source.mkdir()
    candidate = source / 'candidate'; candidate.mkdir()
    (candidate / 'agent.py').write_text('VALUE = 1\n')
    capture(run, source, run_id='run-a', round_no=2, previous_tree=sha,
            reply={'changed': True}, trajectory={'steps': [{'harness_dir': str(candidate)}]},
            generation_s=1)
    progress = run / 'progress' / 'round-2'; progress.mkdir(parents=True)
    (progress / 'completed.json').write_text('{}')
    monkeypatch.setenv('HG_EXPERIMENTAL_ROUND_JOURNAL', '1')
    monkeypatch.setattr(mode_a, 'reused_measurement', lambda *args: {'traces': {}})
    monkeypatch.setattr(mode_a.snapshot, 'last_id', lambda path: None)
    monkeypatch.setattr(mode_a, '_call_method', lambda *a, **k: pytest.fail('method called again'))
    monkeypatch.setattr(mode_a, 'evaluate', lambda *a, **k: pytest.fail('samples rerun'))
    args = SimpleNamespace(method_entrypoint=['unused'], runs_root=tmp_path / 'runs', rounds=2)
    curve = [{'round': 1, 'identity': {'harness_sha': sha}}]
    with pytest.raises(SubmissionJournalError, match='partial evaluation evidence'):
        mode_a._run_mode_a(args, work, {}, 'run-a', 'A', curve, sha, {'traces': {}},
            [], [], None, {}, {}, {'wall_clock_s': 0, 'generation_tokens': 0}, start_steps=1)
    (run / 'round2_traces').mkdir()
    with pytest.raises(SubmissionJournalError, match='already has final evidence'):
        mode_a._run_mode_a(args, work, {}, 'run-a', 'A', curve, sha, {'traces': {}},
            [], [], None, {}, {}, {'wall_clock_s': 0, 'generation_tokens': 0}, start_steps=1)
    monkeypatch.setattr(mode_a.submission_journal, 'pending', lambda *a: None)
    args._recovered_measurement_loader = lambda *a: pytest.fail('loader should not run')
    with pytest.raises(SubmissionJournalError, match='no pending submission'):
        mode_a._run_mode_a(args, work, {}, 'run-a', 'A', curve, sha, {'traces': {}},
            [], [], None, {}, {}, {'wall_clock_s': 0, 'generation_tokens': 0}, start_steps=1)


@pytest.mark.parametrize('complete', [True, False])
def test_real_resumed_loop_uses_saved_candidate_without_calling_method(tmp_path, monkeypatch, complete):
    class CandidateReached(Exception):
        pass

    work = tmp_path / 'work'; work.mkdir()
    (work / 'agent.py').write_text('VALUE = 0\n')
    init_repo(work); sha = commit_state(work, 'initial')
    run = tmp_path / 'runs' / 'run-a'; run.mkdir(parents=True)
    source = tmp_path / 'source'; source.mkdir()
    candidate = source / 'candidate'; candidate.mkdir()
    (candidate / 'agent.py').write_text('VALUE = 1\n')
    capture(run, source, run_id='run-a', round_no=2, previous_tree=sha,
            reply={'changed': True, 'stop': False, 'generation_tokens': 0},
            trajectory={'steps': [{'harness_dir': str(candidate)}]}, generation_s=1,
            attempt_usage={'generation_tokens_lower_bound': 3, 'complete': complete, 'generation_s': 5.0})
    monkeypatch.setenv('HG_EXPERIMENTAL_ROUND_JOURNAL', '1')
    monkeypatch.setattr(mode_a, 'reused_measurement', lambda *args: {'traces': {}})
    monkeypatch.setattr(mode_a.snapshot, 'last_id', lambda path: None)
    monkeypatch.setattr(mode_a, '_call_method', lambda *a, **k: pytest.fail('method called again'))
    def inspect_candidate(path):
        assert (path / 'agent.py').read_text() == 'VALUE = 1\n'
        raise CandidateReached()
    monkeypatch.setattr(mode_a, '_runtime_declaration_problems', inspect_candidate)
    args = SimpleNamespace(method_entrypoint=['unused'], runs_root=tmp_path / 'runs', rounds=2)
    curve = [{'round': 1, 'identity': {'harness_sha': sha}}]
    cumulative = {'wall_clock_s': 0, 'generation_tokens': 0}
    with pytest.raises(CandidateReached):
        mode_a._run_mode_a(args, work, {}, 'run-a', 'A', curve, sha, {'traces': {}},
            [], [], None, {}, {}, cumulative, start_steps=1)
    assert cumulative['generation_tokens'] == 3
    assert cumulative['wall_clock_s'] == 5.0
    assert cumulative.get('generation_tokens_status') == (None if complete else 'reported_lower_bound')


@pytest.mark.parametrize('phase', ['measurement', 'write', 'next_round', 'next_round_sandbox'])
@pytest.mark.parametrize('saved_usage', [None,
    {'generation_tokens_lower_bound': 9, 'complete': False, 'generation_s': 2.0}])
def test_real_resumed_loop_consumes_recovered_result_without_rewriting_evidence(tmp_path, monkeypatch, phase, saved_usage):
    class ResultReached(Exception):
        pass

    work = tmp_path / 'work'; work.mkdir()
    (work / 'agent.py').write_text('VALUE = 0\n')
    init_repo(work); parent_sha = commit_state(work, 'initial')
    run = tmp_path / 'runs' / 'run-a'; run.mkdir(parents=True)
    source = tmp_path / 'source'; source.mkdir(); candidate = source / 'candidate'; candidate.mkdir()
    (candidate / 'agent.py').write_text('VALUE = 1\n')
    shutil.copy2(work / '.gitignore', candidate / '.gitignore')
    (work / 'agent.py').write_text('VALUE = 1\n'); candidate_sha = commit_state(work, 'candidate')
    mode_a.snapshot.write(run, 2, candidate, run_id='run-a', harness_sha=candidate_sha)
    shutil.copytree(candidate, run / 'state_after_round2')
    mode_a._save_round_diff(run, 2, work, parent_sha, candidate_sha)
    snapshot_path = run / 'snapshots/round-2.json'; diff_path = run / 'diffs/round-2.json'
    snapshot_before, diff_before = snapshot_path.read_bytes(), diff_path.read_bytes()
    reset_hard(work, parent_sha)
    capture(run, source, run_id='run-a', round_no=2, previous_tree=parent_sha,
            reply={'changed': True, 'stop': False, 'generation_tokens': 7,
                   'recovery_provenance': {'generation_tokens_status': 'reported_lower_bound'}},
            trajectory={'steps': [{'harness_dir': str(candidate)}]}, generation_s=1,
            attempt_usage=saved_usage)
    progress = run / 'progress/round-2'; progress.mkdir(parents=True)
    (progress / 'completed.json').write_text('{}')
    context = verification_context.create({'alpha': 'a' * 64})
    result = {'status': 'recovered_evaluation_not_published', 'candidate_tree_sha': candidate_sha,
              'verification_context': context, 'per_task': {'alpha': 0.5},
              'traces': {'alpha': '{"event":"recovered"}\n'},
              'verdicts': {'alpha': {'score': 0.5, 'passed': False}},
              'integrity_status': 'unknown_interrupted_process',
              'measurement_provenance': {'alpha': {'run_id': 'supplement'}}}
    monkeypatch.setenv('HG_EXPERIMENTAL_ROUND_JOURNAL', '1')
    monkeypatch.setattr(mode_a, 'reused_measurement', lambda *args: {'traces': {}})
    monkeypatch.setattr(mode_a.snapshot, 'last_id', lambda path: None)
    monkeypatch.setattr(mode_a, '_call_method', lambda *a, **k: pytest.fail('method called again'))
    monkeypatch.setattr(mode_a, 'evaluate', lambda *a, **k: pytest.fail('task evaluated again'))
    monkeypatch.setattr(mode_a.candidate, 'validate', lambda *a, **k: [])
    monkeypatch.setattr(mode_a, '_runtime_declaration_problems', lambda path: [])
    monkeypatch.setattr(mode_a.candidate_runtime, 'prepare', lambda *a, **k: None)
    monkeypatch.setattr(mode_a.candidate_runtime, 'admit', lambda *a, **k: None)
    def reached(value):
        assert value is result
        if phase == 'measurement':
            raise ResultReached()
    monkeypatch.setattr(mode_a, '_require_untampered', reached)
    args = SimpleNamespace(method_entrypoint=['unused'], runs_root=tmp_path / 'runs', rounds=2,
        trials=1, sandbox=False, verification_context=context, dataset='demo',
        jobs=1, harness='probe', baseline=None,
        _recovered_measurement_loader=lambda path, round_no: result)
    curve = [{'round': 1, 'identity': {'harness_sha': parent_sha}}]
    if phase != 'measurement':
        monkeypatch.setattr(mode_a, '_identity', lambda *a, **k: {'harness_sha': candidate_sha})
        monkeypatch.setattr(mode_a, '_env_record', lambda *a, **k: {'kind': 'files'})
        monkeypatch.setattr(mode_a, '_touched_paths', lambda *a: [])
        monkeypatch.setattr(mode_a.code_metrics, 'measure', lambda path: {})
        monkeypatch.setattr(mode_a.platform_config, 'run_config', lambda *a, **k: {})
        monkeypatch.setattr(mode_a.platform_config, 'dump', lambda **k: {})
        monkeypatch.setattr(mode_a, '_write_run_meta', lambda *a, **k: None)
        if phase in ('next_round', 'next_round_sandbox'):
            args.rounds = 3
            args.method_timeout = 30
            args.sandbox = phase == 'next_round_sandbox'
            method = tmp_path / 'offline_method.py'
            method.write_text('''import json, pathlib, sys
req = json.load(sys.stdin)
channel = pathlib.Path(req["base_harness"]) / "_harnessgrad"
point = json.loads((channel / "round.json").read_text())
trace = (channel / "traces/alpha.jsonl").read_text()
print(json.dumps({"round": req["round_index"], "score": point["score"], "trace": trace}))
''')
            args.method_entrypoint = [sys.executable, str(method)]
            real_stage = mode_a._stage_for_method
            from harnessgrad.execution.method_runner import _call_method as real_method
            def next_round_evidence(work_arg, prior, traces, **kwargs):
                assert prior['round'] == 2 and prior['score'] == 0.5
                assert traces == result['traces'] and kwargs['result'] is result
                real_stage(work_arg, prior, traces, **kwargs)
                channel = work_arg / '_harnessgrad'
                assert json.loads((channel / 'round.json').read_text())['score'] == 0.5
                assert (channel / 'traces/alpha.jsonl').read_text() == result['traces']['alpha']
                assert json.loads((channel / 'history/round-2.json').read_text())['score'] == 0.5
                task = json.loads((channel / 'tasks/alpha.json').read_text())
                assert task['score'] == 0.5 and task['verdict']['score'] == 0.5
            def next_method(*a, **kwargs):
                observed = real_method(*a, **kwargs)
                assert observed == {'round': 3, 'score': 0.5, 'trace': result['traces']['alpha']}
                raise ResultReached()
            monkeypatch.setattr(mode_a, '_stage_for_method', next_round_evidence)
            monkeypatch.setattr(mode_a, '_call_method', next_method)
            with pytest.raises(ResultReached):
                mode_a._run_mode_a(args, work, {}, 'run-a', 'A', curve, parent_sha, {'traces': {}},
                    [{'task_id': 'alpha', 'goal': 'offline'}], ['alpha'], None, {}, {},
                    {'wall_clock_s': 0, 'generation_tokens': 12, 'evaluation_trials': 0},
                    start_steps=1, train_ids=['alpha'])
            output = curve
        else:
            output = mode_a._run_mode_a(args, work, {}, 'run-a', 'A', curve, parent_sha, {'traces': {}},
                [{'task_id': 'alpha'}], ['alpha'], None, {}, {},
                {'wall_clock_s': 0, 'generation_tokens': 12, 'evaluation_trials': 0},
                start_steps=1, train_ids=['alpha'])
        assert output[-1]['score'] == 0.5
        expected_cost = 9 if saved_usage else 7
        assert output[-1]['cumulative']['generation_tokens'] == 12 + expected_cost
        assert output[-1]['cost']['method_generation_tokens'] is None
        assert output[-1]['cost']['method_generation_tokens_lower_bound'] == expected_cost
        assert output[-1]['cost']['generation_tokens_status'] == 'reported_lower_bound'
        assert output[-1]['cost']['harness_usage_scope'] == 'partial_interrupted_process'
        assert output[-1]['integrity_status'] == 'unknown_interrupted_process'
        assert output[-1]['tests_total'] is None
        assert output[-1]['measurement_provenance']['alpha']['run_id'] == 'supplement'
        assert (run / 'round2_traces/alpha.jsonl').read_text() == result['traces']['alpha']
        assert json.loads((run / 'verdicts/round-2/alpha.json').read_text())['score'] == 0.5
        assert json.loads((run / 'curve.jsonl').read_text().splitlines()[-1])['score'] == 0.5
    else:
        with pytest.raises(ResultReached):
            mode_a._run_mode_a(args, work, {}, 'run-a', 'A', curve, parent_sha, {'traces': {}},
                [{'task_id': 'alpha'}], ['alpha'], None, {}, {},
                {'wall_clock_s': 0, 'generation_tokens': 0}, start_steps=1)
    assert snapshot_path.read_bytes() == snapshot_before
    assert diff_path.read_bytes() == diff_before
