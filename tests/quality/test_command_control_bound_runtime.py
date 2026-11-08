"""Owned command controls with synthetic verifier inputs and no external services."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from data.scaffolds import command_control as control
from harnessgrad import dataset_runtime


@pytest.fixture
def spec():
    digest = 'a' * 64
    verifier = {'argv': ['/prepared/bin/python', '-I', '-B', 'check.py'],
                'python_runtime': {'opaque': 'binding'}, 'runtime_paths': ['/prepared'],
                'inputs': [{'path': 'gold.json', 'content': 'trusted'}],
                'scorer_input': {'task_id': 'synthetic', 'path': 'gold.json', 'sha256': digest},
                'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
    return SimpleNamespace(VERIFY={'synthetic': verifier}), {'task_id': 'synthetic'}


def test_bound_runtime_is_admitted_and_private_input_restaged(spec, tmp_path, monkeypatch):
    module, task = spec
    (tmp_path / 'gold.json').write_text('actor substitution')
    calls = []
    def admit(binding, role, python):
        calls.append((binding, role, python))
        return {'python': '/prepared/bin/python', 'runtime_paths': ['/prepared']}
    def run(argv, *, cwd, **kwargs):
        assert argv == ['/prepared/bin/python', '-I', '-B', 'check.py']
        assert (cwd / 'gold.json').read_text() == 'trusted'
        out = cwd / 'verifier'; out.mkdir(exist_ok=True)
        (out / 'reward.txt').write_text('1')
        (out / 'evidence.json').write_text(json.dumps({'measured': True, 'scorer_input': {
            **module.VERIFY['synthetic']['scorer_input'], 'expected_sha256': 'a' * 64, 'verified': True}}))
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admit)
    monkeypatch.setattr(subprocess, 'run', run)
    reward, detail = control.run_check(module, task, tmp_path)
    assert reward == 1 and json.loads(detail)['measured'] is True
    assert calls == [({'opaque': 'binding'}, 'verifier', '/prepared/bin/python')]


@pytest.mark.parametrize('damage', ['flags', 'binding', 'paths'])
def test_bad_runtime_refuses_without_launch_or_materializing(spec, tmp_path, monkeypatch, damage):
    module, task = spec
    verifier = module.VERIFY['synthetic']
    if damage == 'flags': verifier['argv'][1] = '-E'
    if damage == 'paths': verifier['runtime_paths'] = ['/other']
    def admit(*args, **kwargs):
        if damage == 'binding': raise ValueError('changed runtime')
        return {'python': '/prepared/bin/python', 'runtime_paths': ['/prepared']}
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admit)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(ValueError): control.run_check(module, task, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_legacy_uses_platform_python_and_never_accepts_stale_score(tmp_path, monkeypatch):
    module = SimpleNamespace(VERIFY={'t': {'argv': ['python3', '-I', 'check.py'],
             'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}})
    old = tmp_path / 'verifier'; old.mkdir(); (old / 'reward.txt').write_text('1')
    def run(argv, **kwargs):
        assert argv == [sys.executable, '-I', 'check.py']
        return SimpleNamespace(returncode=1, stderr='failed')
    monkeypatch.setattr(subprocess, 'run', run)
    reward, _ = control.run_check(module, {'task_id': 't'}, tmp_path)
    assert reward == -1


def test_bound_scorer_input_mismatch_is_unmeasured(spec, tmp_path, monkeypatch):
    module, task = spec
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *a, **k: {
        'python': '/prepared/bin/python', 'runtime_paths': ['/prepared']})
    def run(*args, **kwargs):
        out = tmp_path / 'verifier'; out.mkdir(); (out / 'reward.txt').write_text('1')
        (out / 'evidence.json').write_text(json.dumps({'measured': True, 'scorer_input': {}}))
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(subprocess, 'run', run)
    reward, detail = control.run_check(module, task, tmp_path)
    assert reward == -1
    assert json.loads(detail)['measured'] is False
    assert 'receipt mismatch' in json.loads(detail)['why']


def test_platform_control_keeps_verifier_gold_out_of_actor_setup(spec, tmp_path, monkeypatch):
    from eval.run import runner
    module, task = spec
    module.__name__ = 'synthetic'
    module.SETUP = {'synthetic': {'files': [{'path': 'public.txt', 'content': 'public'}], 'tool_relay': {'program': '/synthetic/relay'}}}
    module.ENV = {'synthetic': {'kind': 'files'}}
    module.oracle_files = lambda _: {'answer.txt': 'synthetic answer'}
    def run_one(**kwargs):
        assert kwargs['verifier'] is module.VERIFY['synthetic']
        assert kwargs['setup']['tool_relay'] == {'program': '/synthetic/relay'}
        assert kwargs['verifier']['inputs'][0]['content'] == 'trusted'
        assert all(row.get('content') != 'trusted' for row in kwargs['setup']['files'])
        assert any(row['path'] == '.oracle/answer.txt' for row in kwargs['setup']['files'])
        return {'exit_code': 0, 'verdict': {'score': 1.0, 'passed': True}}
    monkeypatch.setattr(runner, 'run_one', run_one)
    assert control.run_platform(module, task, oracle=True, workdir=tmp_path) == 0


def test_nonzero_exit_overrides_forged_measured_evidence(spec, tmp_path, monkeypatch):
    module, task = spec
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *a, **k: {
        'python': '/prepared/bin/python', 'runtime_paths': ['/prepared']})
    def run(*args, **kwargs):
        out = tmp_path / 'verifier'; out.mkdir(); (out / 'reward.txt').write_text('1')
        receipt = dict(module.VERIFY['synthetic']['scorer_input'], expected_sha256='a' * 64, verified=True)
        (out / 'evidence.json').write_text(json.dumps({'measured': True, 'scorer_input': receipt}))
        return SimpleNamespace(returncode=1, stderr='failure')
    monkeypatch.setattr(subprocess, 'run', run)
    reward, detail = control.run_check(module, task, tmp_path)
    assert reward == -1
    assert json.loads(detail)['measured'] is False
    assert json.loads(detail)['why'] == 'verifier exited nonzero'
