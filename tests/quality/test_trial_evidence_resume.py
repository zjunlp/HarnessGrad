"""Resume checks the opt-in archive before reusing a completed task."""
import json

import pytest

from eval.run import runner
from test_repeated_trial_resume import session, interrupted


@pytest.fixture
def archived(session, monkeypatch):
    session.args.sandbox = True
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(session.run.parent / 'harness'))
    for spec in session.verifiers.values():
        spec['trial_evidence'] = {'version': 1}
    interrupted(session)
    return session


def test_resume_reuses_unchanged_archives_without_running_completed_task(archived):
    files = {p: p.read_bytes() for p in (archived.run / 'trial-evidence').rglob('*.json')}
    assert len(files) == 3
    assert archived.execute() == 0
    assert [c[0] for c in archived.calls] == ['a'] * 3 + ['b'] * 3
    assert all(p.read_bytes() == body for p, body in files.items())
    report = archived.results[-1]['repeated_trials']
    assert report['complete'] and len(report['results']) == 6
    assert all((archived.run / row['trial_evidence']['path']).is_file() for row in report['results'])


def test_mixed_resume_protects_remaining_task_without_enabling_its_archive(session, monkeypatch):
    session.args.sandbox = True
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(session.run.parent/'harness'))
    session.verifiers['a']['trial_evidence'] = {'version': 1}
    calls = []
    original = runner.run_one

    def actor(repo, task, **kwargs):
        calls.append((task['task_id'], kwargs.get('_private_paths')))
        return original(repo, task, **kwargs)

    monkeypatch.setattr(runner, 'run_one', actor)
    interrupted(session)
    saved = {p: p.read_bytes() for p in (session.run/'trial-evidence').rglob('*.json')}
    assert len(saved) == 3
    assert session.execute() == 0
    assert calls == [(tid, (session.run/'trial-evidence',)) for tid in ['a'] * 3 + ['b'] * 3]
    report = session.results[-1]['repeated_trials']
    assert report['complete'] and len(report['results']) == 6
    assert all(('trial_evidence' in row) == (row['task_id'] == 'a') for row in report['results'])
    assert {p: p.read_bytes() for p in (session.run/'trial-evidence').rglob('*.json')} == saved


@pytest.mark.parametrize('change', ['missing', 'content', 'reference', 'binding', 'symlink',
                                   'malformed-reference', 'malformed-record'])
def test_missing_or_changed_archive_refuses_resume_before_any_new_trial(archived, change):
    report_path = next((archived.root / 'tasks').glob('*.json'))
    report = json.loads(report_path.read_text())
    ref = report['results'][0]['trial_evidence']
    path = archived.run / ref['path']
    if change == 'missing':
        path.unlink()
    elif change == 'content':
        path.write_text('{}')
    elif change == 'reference':
        ref['path'] = '../outside.json'
        report_path.write_text(json.dumps(report))
    elif change == 'binding':
        import hashlib
        record = json.loads(path.read_text())
        record['binding']['seed'] = 'other'
        path.write_text(json.dumps(record))
        ref['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        report_path.write_text(json.dumps(report))
    elif change == 'symlink':
        outside = archived.run / 'outside.json'
        path.rename(outside)
        path.symlink_to(outside)
    elif change == 'malformed-reference':
        report['results'][0]['trial_evidence'] = 'broken'
        report_path.write_text(json.dumps(report))
    elif change == 'malformed-record':
        import hashlib
        path.write_text('[]')
        ref['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        report_path.write_text(json.dumps(report))
    before = list(archived.calls)
    assert archived.execute() == 2
    assert archived.calls == before
