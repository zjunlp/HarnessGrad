"""Sensitivity uses the real evaluator, including opt-in persistence and legacy caching."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from tools import task_sensitivity as ts
from test_declared_trials_continue import PROTOCOL
from test_shared_dataset_runtime import harness


@pytest.mark.parametrize('mode', ['ordinary', 'protocol', 'mixed', 'archived'])
def test_measure_preserves_independent_harness_records_and_legacy_behavior(tmp_path, monkeypatch, mode):
    work = tmp_path / 'work'
    work.mkdir()
    run = tmp_path / 'run'
    run.mkdir()
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    source = harness(work, """(w/'answer.txt').write_text('yes')
(w/'trace.jsonl').write_text('{"text":"ordinary harness output"}\\n')
""")
    tasks = [{'task_id': f'fixture/{i}'} for i in range(3)]
    specs = {t['task_id']: {'kind': 'answer', 'expected': 'yes'} for t in tasks}
    if mode != 'ordinary':
        for spec in specs.values():
            spec['trial_protocol'] = dict(PROTOCOL)
    archived_tasks = set(specs) if mode == 'archived' else {'fixture/0'} if mode == 'mixed' else set()
    for tid in archived_tasks:
        specs[tid]['trial_evidence'] = {'version': 1}
    args = SimpleNamespace(sandbox=True, seed='fixture', trials=3, jobs=1, cache=None)
    calls = []
    first_records = {}
    original = ts.evaluate

    def evaluate(*a, **kw):
        result = original(*a, **kw)
        calls.append((kw, copy.deepcopy(result)))
        if len(calls) == 1:
            first_records.update({p: p.read_bytes() for p in run.rglob('*.json*') if p.is_file()})
        return result

    monkeypatch.setattr(ts, 'evaluate', evaluate)
    cache = {}
    scores, invalid = ts._measure([('first', source), ('identical', source)], tasks, {}, {},
                                  specs, {}, args, work, run, cache)
    assert not invalid and scores == {t['task_id']: {'first': 1, 'identical': 1} for t in tasks}
    assert len(calls) == 2 and calls[0][0]['harness_sha'] == calls[1][0]['harness_sha']
    assert all(p.read_bytes() == data for p, data in first_records.items())
    if not archived_tasks:
        assert not list(run.rglob('trial-evidence')) and not (run/'harnesses').exists()
        assert not (run/'progress').exists()
        assert all('progress_dir' not in kw and kw['round_no'] == 0 for kw, _ in calls)
        if mode == 'ordinary':
            assert cache and calls[1][1]['cached_tasks'] == [t['task_id'] for t in tasks]
            assert all('repeated_trials' not in result for _, result in calls)
        else:
            assert not cache and all(result['repeated_trials']['complete'] for _, result in calls)
        return

    assert not cache and all(not result['cached_tasks'] for _, result in calls)
    identities = [json.loads(p.read_text()) for p in sorted((run/'harnesses').glob('*.json'))]
    assert [identity['label'] for identity in identities] == ['first', 'identical']
    assert len({identity['round'] for identity in identities}) == 2
    assert all(identity['harness_sha'] == calls[0][0]['harness_sha'] for identity in identities)
    records = []
    for kw, result in calls:
        assert kw['progress_dir'] == run and result['repeated_trials']['complete']
        for row in result['repeated_trials']['results']:
            if row['task_id'] not in archived_tasks:
                assert 'trial_evidence' not in row
                continue
            ref = row['trial_evidence']
            content = (run/ref['path']).read_bytes()
            assert hashlib.sha256(content).hexdigest() == ref['sha256']
            record = json.loads(content)
            assert record['binding']['harness_sha'] == kw['harness_sha']
            assert record['binding']['run_id'] == run.name
            assert record['binding']['round'] == kw['round_no']
            assert record['binding']['trial_id'] == row['trial_id']
            assert record['outcome']['score'] == 1
            records.append(record)
    assert len(records) == 2 * 3 * len(archived_tasks)
    assert len({r['binding']['trial_id'] for r in records}) == 6
    reports = [json.loads(p.read_text()) for p in (run/'protocols').rglob('repeated-trials.json')]
    assert len(reports) == 2 and all(r['complete'] and len(r['results']) == 9 for r in reports)


def test_cli_creates_a_fresh_run_even_in_the_same_second(tmp_path, monkeypatch):
    source = harness(tmp_path, 'pass')
    platform = tmp_path / 'platform'
    monkeypatch.setattr(ts, 'ROOT', platform)
    monkeypatch.setattr(ts, 'load_env', lambda *a: None)
    monkeypatch.setattr(ts.time, 'time', lambda: 1000)
    tasks = [{'task_id': f'fixture/{i}'} for i in range(3)]
    monkeypatch.setattr(ts.registry, 'load_split', lambda *a: (tasks, {}, None))
    monkeypatch.setattr(ts.registry, 'load_tasks', lambda *a: (tasks, {}, {}))
    monkeypatch.setattr(ts.registry, 'load_envs', lambda *a: {})
    runs = []

    def measure(specs, tasks, scorable, setups, verifiers, envs, args, work_root, run_dir, cache):
        runs.append(run_dir)
        return {t['task_id']: {'fixture': 1} for t in tasks}, {}

    monkeypatch.setattr(ts, '_measure', measure)
    argv = ['--harnesses', f'fixture={source}', '--dataset', 'fixture']
    assert ts.main(argv) == ts.main(argv) == 0
    assert len(set(runs)) == 2
    assert all((run/'sensitivity.json').is_file() for run in runs)
