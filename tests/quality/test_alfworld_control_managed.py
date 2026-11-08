"""Offline controls for the legacy flags and normal runner wiring."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def control_module():
    spec = importlib.util.spec_from_file_location('alfworld_control_test', ROOT / 'data/datasets/alfworld/control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('oracle,score,expected', [(True, 1, 0), (False, 0, 0), (False, None, 2)])
def test_control_retains_service_native_scorer_and_budget(monkeypatch, tmp_path, oracle, score, expected):
    from eval.run import runner
    control = control_module()
    adapter = control.alfworld
    record = {'task_id': 'authored'}
    setup = {'files': [], 'tool_relay': {'python_runtime': {'role': 'service'},
              'python': '/managed/python', 'content': 'private game', 'budget': 50}}
    verifier = {'argv': ['python3', 'check.py', '--budget', '50'],
                'native_runtime': {'sha256': 'unchanged-native-pin'}}
    monkeypatch.setattr(adapter, 'SETUP', {'authored': setup})
    monkeypatch.setattr(adapter, 'VERIFY', {'authored': verifier})
    monkeypatch.setattr(adapter, 'ENV', {'authored': {'kind': 'files'}})
    monkeypatch.setattr(adapter, 'load', lambda: ([record], {}))
    monkeypatch.setattr(control, '_walkthrough', lambda record: ['one', 'two', 'three'])
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', '/original/work-root')
    def fake_runner(**kwargs):
        assert kwargs['setup']['tool_relay']['python_runtime'] == {'role': 'service'}
        assert kwargs['setup']['tool_relay']['content'] == 'private game'
        assert kwargs['setup']['tool_relay']['budget'] == 2
        assert kwargs['verifier']['native_runtime'] == verifier['native_runtime']
        assert kwargs['verifier']['argv'][-1] == '2'
        files = {item['path']: item['content'] for item in kwargs['setup']['files']}
        assert json.loads(files['.oracle/budget.json']) == 2
        assert (json.loads(files['.oracle/actions.json']) == ['one', 'two']) if oracle else '.oracle/actions.json' not in files
        return {'exit_code': 0, 'verdict': {'score': score}}
    monkeypatch.setattr(runner, 'run_one', fake_runner)
    assert control.run_platform(record, oracle=oracle, workdir=tmp_path, budget=2) == expected
    assert os.environ['HARNESSGRAD_WORK_ROOT'] == '/original/work-root'
    assert setup['tool_relay']['budget'] == 50
    assert verifier['argv'][-1] == '50'


@pytest.mark.parametrize('platform', [False, True])
def test_legacy_flags_select_same_managed_route(monkeypatch, tmp_path, platform):
    control = control_module()
    monkeypatch.setattr(control, '_record', lambda tid: {'task_id': 'authored'})
    calls = []
    monkeypatch.setattr(control, 'run_platform', lambda *args, **kwargs: calls.append(kwargs) or 0)
    argv = ['--noop', '--workdir', str(tmp_path / 'work'), '--budget', '3', '--no-sandbox']
    if platform:
        argv.append('--platform')
    assert control.main(argv) == 0
    assert calls[0]['budget'] == 3
    assert calls[0]['sandbox'] is False
