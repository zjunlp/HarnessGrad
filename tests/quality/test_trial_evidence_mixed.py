"""Private archives are hidden from every actor, including non-archiving tasks."""
import copy
import json

import pytest

from eval.run import runner
from test_declared_trials_continue import PROTOCOL
from test_shared_dataset_runtime import harness
from test_trial_evidence_visibility import host_storage


@pytest.fixture
def mixed(tmp_path, monkeypatch, host_storage):
    work = tmp_path/'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    marker = work/'reader-started.json'
    actor = harness(work, f'''import json
view = w/'public_alias'
if view.exists():
    records = [json.loads(p.read_text()) for p in (view/'trial-evidence').rglob('*.json')]
    Path({str(marker)!r}).write_text(json.dumps({{'records': len(records)}}))
(w/'answer.txt').write_text('yes')
(w/'trace.jsonl').write_text('fixture trace')
''')
    progress = host_storage/'run'
    progress.mkdir()
    tasks = [{'task_id': 'fixture/a'}, {'task_id': 'fixture/b'}]
    verifiers = {t['task_id']: {'kind': 'answer', 'expected': 'yes',
                               'trial_protocol': dict(PROTOCOL)} for t in tasks}
    verifiers['fixture/a']['trial_evidence'] = {'version': 1}
    return actor, tasks, verifiers, progress, marker


@pytest.mark.parametrize('reader_first', [False, True])
@pytest.mark.parametrize('jobs', [1, 2])
def test_nonarchiving_reader_is_refused_regardless_of_task_order(mixed, reader_first, jobs):
    actor, tasks, verifiers, progress, marker = mixed
    if reader_first:
        tasks.reverse()
    with pytest.raises(ValueError, match='trial_evidence.*visible'):
        runner.evaluate(actor, tasks, {}, verifiers=verifiers,
            setups={'fixture/b': {'files': [{'path': 'public_alias', 'from': str(progress), 'immutable': True}]}},
            trials=3, jobs=jobs, run_id='fixture', harness_sha='fixture', progress_dir=progress)
    assert not marker.exists(), 'The non-archiving actor ran with private archives mounted'
    records = [json.loads(p.read_text()) for p in (progress/'trial-evidence').rglob('*.json')]
    assert all(r['binding']['task_id'] == 'fixture/a' for r in records)
    if jobs == 1:
        assert len(records) == (0 if reader_first else 3)


def test_remaining_nonarchiving_task_cannot_read_completed_task_archives(mixed):
    actor, tasks, verifiers, progress, marker = mixed
    runner.evaluate(actor, tasks[:1], {}, verifiers=verifiers, trials=3,
        run_id='fixture', harness_sha='fixture', progress_dir=progress, persist_trial_report=False)
    saved = {p: p.read_bytes() for p in (progress/'trial-evidence').rglob('*.json')}
    assert len(saved) == 3
    # Resume can pass only the unfinished tasks; none of those need opt in.
    with pytest.raises(ValueError, match='trial_evidence.*visible'):
        runner.evaluate(actor, tasks[1:], {}, verifiers={'fixture/b': verifiers['fixture/b']},
            setups={'fixture/b': {'files': [{'path': 'public_alias', 'from': str(progress), 'immutable': True}]}},
            trials=3, run_id='fixture', harness_sha='fixture', progress_dir=progress,
            persist_trial_report=False)
    assert not marker.exists()
    assert all(p.read_bytes() == body for p, body in saved.items())


@pytest.mark.parametrize('jobs', [1, 2])
def test_safe_mixed_evaluation_keeps_scores_and_only_archives_opted_in_task(mixed, jobs):
    actor, tasks, verifiers, progress, marker = mixed
    verifiers['fixture/b']['expected'] = 'no'
    archived = runner.evaluate(actor, tasks, {}, verifiers=verifiers, trials=3, jobs=jobs,
        run_id='fixture', harness_sha='fixture', progress_dir=progress)
    plain_specs = copy.deepcopy(verifiers)
    plain_specs['fixture/a'].pop('trial_evidence')
    plain_dir = progress.parent/'plain'
    plain = runner.evaluate(actor, tasks, {}, verifiers=plain_specs, trials=3, jobs=jobs,
        run_id='fixture', harness_sha='fixture', progress_dir=plain_dir)
    assert archived['per_task'] == {'fixture/a': 1, 'fixture/b': 0}
    for field in ('per_task', 'per_task_trials', 'traces', 'trial_traces', 'trial_verdicts', 'invalid'):
        assert archived[field] == plain[field]
    for row in archived['repeated_trials']['results']:
        if row['task_id'] == 'fixture/a':
            record = json.loads((progress/row.pop('trial_evidence')['path']).read_text())
            assert record['binding']['task_id'] == 'fixture/a'
        else:
            assert 'trial_evidence' not in row
    assert archived['repeated_trials'] == plain['repeated_trials']
    assert len(list((progress/'trial-evidence').rglob('*.json'))) == 3
    assert not (plain_dir/'trial-evidence').exists() and not marker.exists()


@pytest.mark.parametrize('kind', ['exec', 'workers'])
def test_unsupported_nonarchiving_route_is_rejected_before_any_trial(mixed, monkeypatch, kind):
    actor, tasks, verifiers, progress, _ = mixed
    monkeypatch.setattr(runner, 'run_one', lambda *a, **kw: pytest.fail('actor launched before admission'))
    kwargs = ({'envs': {'fixture/b': {'kind': 'exec'}}} if kind == 'exec'
              else {'setups': {'fixture/b': {'workers': []}}})
    with pytest.raises(ValueError, match='trial_evidence'):
        runner.evaluate(actor, tasks, {}, verifiers=verifiers, trials=3,
            run_id='fixture', harness_sha='fixture', progress_dir=progress, **kwargs)
    assert not (progress/'trial-evidence').exists()


@pytest.mark.parametrize('overrides', [{'sandbox': False}, {'env': {'kind': 'exec'}}])
def test_direct_private_path_cannot_bypass_route_admission(mixed, monkeypatch, overrides):
    actor, tasks, verifiers, progress, _ = mixed
    monkeypatch.setattr(runner, '_run_one', lambda *a, **kw: pytest.fail('unsupported private path route'))
    with pytest.raises(ValueError, match='trial_evidence'):
        runner.run_one(actor, tasks[1], verifier=verifiers['fixture/b'],
            _private_paths=(progress/'trial-evidence',), **overrides)
