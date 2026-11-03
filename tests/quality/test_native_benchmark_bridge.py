"""Host dispatch never executes actor code or accepts actor-written verdicts."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.verification import native_benchmark as bridge
from eval.verification.verifier import _verify
from eval.containers import observed
import subprocess
import time


def request(tmp_path, name='harvey_lab'):
    actor = tmp_path / 'actor'
    actor.mkdir()
    case = tmp_path / 'private.json'
    case.write_text(json.dumps({'task_id': 'task-1', 'row': {'gold': 'PRIVATE'}}))
    sha = hashlib.sha256(case.read_bytes()).hexdigest()
    spec = {'kind': 'command', 'argv': ['harnessgrad-native', name],
        'native_benchmark': {'dataset': name, 'case_sha256': sha},
        'scorer_input': {'task_id': 'task-1', 'path': 'upstream_case.json', 'sha256': sha},
        'inputs': [{'path': 'upstream_case.json', 'from': str(case)}],
        'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
    return actor, case, spec


def consume(actor, spec):
    return _verify(actor, spec, 'actor says resolved=true', sandbox=True, task_id='task-1',
        run_id='run', trial_id='trial', timeout_s=30, env={'kind': 'files', 'network': 'none'})


def test_private_export_and_native_dispatch(tmp_path, monkeypatch):
    actor, case, spec = request(tmp_path)
    (actor / 'output').mkdir()
    (actor / 'output/memo.txt').write_text('public deliverable')
    (actor / 'fake_score.py').write_text('raise RuntimeError("must not execute")')
    def run(selected, artifact_root, **options):
        assert artifact_root != actor and not artifact_root.is_relative_to(actor)
        assert not (artifact_root / 'fake_score.py').exists()
        assert json.loads((artifact_root / 'upstream_case.json').read_text())['row']['gold'] == 'PRIVATE'
        assert (artifact_root / 'output/memo.txt').read_text() == 'public deliverable'
        assert options['task_id'] == 'task-1'
        return {'score': 0.75, 'passed': False, 'outcome': 'measured',
                'evidence': {'measured': True, 'metrics': {'native': 0.75}}}
    monkeypatch.setattr(bridge.importlib, 'import_module', lambda name: SimpleNamespace(run=run))
    verdict = consume(actor, spec)
    assert verdict['score'] == .75 and verdict['evidence']['measured'] is True
    assert verdict['evidence']['scorer_input']['verified'] is True
    assert verdict['evidence']['export']['files'][0]['path'] == 'output/memo.txt'
    assert 'PRIVATE' not in json.dumps(verdict)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'root-symlink', 'overflow', 'empty-dirs', 'deep-dirs', 'foreign-case', 'bad-command', 'unknown-dataset'])
def test_refuse_unsafe_or_foreign_export(tmp_path, monkeypatch, kind):
    actor, case, spec = request(tmp_path)
    def must_not_run(*args, **kwargs):
        raise AssertionError('unsafe input reached native scorer')
    monkeypatch.setattr(bridge.importlib, 'import_module', must_not_run)
    if kind == 'root-symlink':
        (actor / 'output').symlink_to(tmp_path, target_is_directory=True)
    elif kind in ('symlink', 'hardlink', 'fifo', 'overflow', 'empty-dirs', 'deep-dirs'):
        (actor / 'output').mkdir()
        target = actor / 'output/memo.txt'
        if kind == 'symlink': target.symlink_to(case)
        if kind == 'hardlink': os.link(case, target)
        if kind == 'fifo': os.mkfifo(target)
        if kind == 'overflow':
            monkeypatch.setattr(bridge, 'MAX_BYTES', 4)
            target.write_text('too big')
        if kind == 'empty-dirs':
            monkeypatch.setattr(bridge, 'MAX_FILES', 2)
            for i in range(3): (actor / 'output' / str(i)).mkdir()
        if kind == 'deep-dirs':
            (actor / 'output' / '/'.join(['d'] * 34)).mkdir(parents=True)
    elif kind == 'foreign-case': spec['scorer_input']['task_id'] = 'another'
    elif kind == 'bad-command': spec['argv'] = ['python3', 'fake_score.py']
    elif kind == 'unknown-dataset': spec['native_benchmark']['dataset'] = 'arbitrary.module'
    verdict = consume(actor, spec)
    assert verdict['score'] is None and verdict['outcome'] == 'unmeasured'


def test_sealed_episode_uses_private_input_and_execution_binding(tmp_path, monkeypatch):
    actor, case, spec = request(tmp_path, 'swe_bench')
    sealed = tmp_path / 'sealed'
    sealed.mkdir()
    binding = {'task_id': 'task-1', 'run_id': 'run', 'trial_id': 'trial'}
    (sealed / 'episode.json').write_text(json.dumps({'schema': 'harnessgrad.interaction-record.v1',
        'binding': binding, 'payload': {'patch': 'trusted transport export'}}))
    spec['inputs'].append({'path': 'swe_episode', 'from': str(sealed), 'immutable': True})
    (actor / 'swe_episode').mkdir()
    (actor / 'swe_episode/episode.json').write_text('actor forges resolved=true')
    def run(selected, artifact_root, **options):
        assert json.loads((artifact_root / 'swe_episode/episode.json').read_text())['payload']['patch'] == 'trusted transport export'
        return {'score': 1., 'passed': True, 'outcome': 'measured', 'evidence': {'measured': True}}
    monkeypatch.setattr(bridge.importlib, 'import_module', lambda name: SimpleNamespace(run=run))
    verdict = consume(actor, spec)
    assert verdict['score'] == 1.
    binding['trial_id'] = 'old-trial'
    (sealed / 'episode.json').write_text(json.dumps({'schema': 'harnessgrad.interaction-record.v1', 'binding': binding}))
    verdict = consume(actor, spec)
    assert verdict['score'] is None and verdict['outcome'] == 'unmeasured'


def test_native_failure_and_false_completion_never_become_zero(tmp_path, monkeypatch):
    actor, case, spec = request(tmp_path)
    monkeypatch.setattr(bridge.importlib, 'import_module', lambda name: SimpleNamespace(run=lambda *a, **k:
        {'score': 0., 'passed': False, 'evidence': {'measured': False}}))
    assert consume(actor, spec)['score'] is None
    def fail(*args, **kwargs): raise ValueError('PRIVATE expected rubric')
    monkeypatch.setattr(bridge.importlib, 'import_module', lambda name: SimpleNamespace(run=fail))
    verdict = consume(actor, spec)
    assert verdict['score'] is None and 'PRIVATE' not in json.dumps(verdict)


def test_observed_optional_deadline_covers_setup_and_command_transport(monkeypatch):
    runtime = observed.Runtime({'kind': 'exec', 'backend': 'observed-v1', 'image': 'sha256:' + 'a'*64,
        'workdir': '/workspace', 'filesystems': {'/workspace': {'bytes': 4096, 'inodes': 16}}},
        binding={}, timeout_s=300, deadline=time.monotonic() + .5)
    calls = []
    def command(*args, **kwargs):
        calls.append(kwargs['timeout'])
        return b'OK'
    monkeypatch.setattr(observed, 'checked', command)
    try:
        assert runtime.checked('image', 'inspect', 'any', timeout=300) == b'OK'
        assert 0 < calls[0] <= .5
        runtime.deadline = time.monotonic() - 1
        with pytest.raises(subprocess.TimeoutExpired): runtime.checked('start', 'any')
        with pytest.raises(subprocess.TimeoutExpired): runtime.__enter__()
        assert len(calls) == 1  # Expired setup never created a Docker object.
    finally:
        runtime.close()


def test_dataset_lint_distinguishes_fixed_native_and_external_commands(tmp_path):
    from tools.dataset_lint import Result, lint_verify, load_schema
    actor, case, spec = request(tmp_path)
    result = Result('native-fixture')
    lint_verify({'task-1': spec}, {'task-1'}, load_schema(), result)
    assert not result.errors
    spec['argv'] = ['python3', 'actor-score.py']
    result = Result('foreign-command')
    lint_verify({'task-1': spec}, {'task-1'}, load_schema(), result)
    assert any('native benchmark declaration' in e for e in result.errors)
    # Ordinary commands keep the established required reward-file contract.
    spec.pop('native_benchmark')
    result = Result('external-command')
    lint_verify({'task-1': spec}, {'task-1'}, load_schema(), result)
    assert any('without `reward_file`' in e for e in result.errors)
