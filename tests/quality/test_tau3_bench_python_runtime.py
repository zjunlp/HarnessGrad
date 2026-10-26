"""τ³ scoring binds its own Python independently of the driver and PATH."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from data.datasets import tau3_bench as dataset
from test_tau3_bench_official_scorer import official


@pytest.mark.parametrize('driver', [sys.executable, '/usr/bin/python3'], ids=['platform-python', 'system-python'])
def test_official_score_uses_selected_runtime_with_system_path(tmp_path, official, driver):
    if not Path(driver).is_file():
        pytest.skip('system Python is unavailable')
    _, episode = official
    tid = 'tau3_bench/airline/8'
    row = dataset.roster()[tid]
    work = tmp_path / 'work'
    work.mkdir()
    record = tmp_path / 'record'
    record.mkdir()
    (record / 'episode.json').write_text(json.dumps({
        'schema': 'harnessgrad.interaction-record.v1',
        'binding': {'task_id': tid, 'run_id': 'python-runtime-test', 'trial_id': 'trial-1',
                    'seed': str(episode['seed'])},
        'payload': {**episode, 'case_sha256': row['private_sha256']},
    }))
    spec = dataset.VERIFY[tid]
    probe = '''
import json, os, runpy, sys
from pathlib import Path
try:
    runpy.run_path('tau3_code/verify/check.py', run_name='__main__')
finally:
    Path('verifier/python.json').write_text(json.dumps({
        'executable': sys.executable, 'prefix': sys.prefix, 'version': list(sys.version_info[:2]),
        'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY),
        'site_paths': [p for p in sys.path if p.endswith('site-packages')],
        'pydantic_origin': getattr(sys.modules.get('pydantic'), '__file__', None),
        'runtime_projection_present': Path('tau3_runtime').exists()}))
'''
    spec['argv'][2] = 'python_probe.py'
    spec['inputs'].extend([{'path': 'trusted_episode', 'from': str(record), 'immutable': True},
                           {'path': 'python_probe.py', 'content': probe}])
    config = tmp_path / 'spec.json'
    config.write_text(json.dumps(spec))
    output = tmp_path / 'result.json'
    script = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from eval.verification.verifier import _verify
result = _verify(Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text()), '',
                 sandbox=True, timeout_s=60, env={'kind': 'files'})
Path(sys.argv[4]).write_text(json.dumps(result))
'''
    env = {**os.environ, 'PATH': '/usr/bin:/bin', 'HARNESSGRAD_WORK_ROOT': str(work),
           'PYTHONDONTWRITEBYTECODE': '1'}
    subprocess.run([driver, '-I', '-c', script, str(dataset.ASSETS.parents[2]),
                    str(work), str(config), str(output)], env=env, check=True,
                   capture_output=True, text=True, timeout=90)
    result = json.loads(output.read_text())
    assert result.get('score') == 1, result
    info = json.loads((work / 'verifier/python.json').read_text())
    assert info['version'] == [3, 12] and info['readonly'], info
    assert info['executable'] == str(dataset.RUNTIME / 'bin/python')
    assert Path(info['prefix']).resolve() == dataset.RUNTIME.resolve()
    site = dataset.RUNTIME / 'lib/python3.12/site-packages'
    assert info['site_paths'] == [str(site)], info
    assert Path(info['pydantic_origin']).is_relative_to(site), info
    assert not info['runtime_projection_present'], info


def test_bootstrap_rejects_wrong_python_before_loading_dependencies(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('tau3_version_guard', dataset.ASSETS / 'lib/bootstrap.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(version_info=(3, 10, 12)))
    with pytest.raises(ValueError, match='Python 3.12'):
        module.bootstrap(tmp_path / 'absent-bundle')


@pytest.mark.parametrize('malformed', [False, True])
def test_selected_runtime_keeps_real_nl_judge_scoring(tmp_path, monkeypatch, official, malformed):
    from eval.verification.verifier import _verify
    tid = 'tau3_bench/retail/2'
    row = dataset.roster()[tid]
    config = json.loads((dataset.DATASET_ROOT / row['path'] / 'private.json').read_text())
    assertions = config['task']['evaluation_criteria']['nl_assertions']
    assert assertions
    response = '{}' if malformed else json.dumps({'results': [
        {'expectedOutcome': value, 'metExpectation': False, 'reasoning': 'local fixture'}
        for value in assertions]})
    record = tmp_path / 'record'
    record.mkdir()
    (record / 'episode.json').write_text(json.dumps({
        'schema': 'harnessgrad.interaction-record.v1',
        'binding': {'task_id': tid, 'run_id': 'python-judge-test', 'trial_id': 'trial-1', 'seed': '123'},
        'payload': {'task_id': config['task']['id'], 'domain': 'retail', 'seed': 123,
                    'case_sha256': row['private_sha256'], 'termination_reason': 'agent_stop',
                    'messages': [{'role': 'assistant', 'content': '###STOP###'}]},
    }))
    work = tmp_path / 'work'
    work.mkdir()
    spec = importlib.util.spec_from_file_location('tau3_runtime_control', dataset.ASSETS / 'control.py')
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    monkeypatch.setenv('HG_TAU3_JUDGE_MODEL', 'tau3-control-judge')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    with control.Endpoint(judge_response=response) as endpoint:
        monkeypatch.setenv('PATH', '/usr/bin:/bin')
        verifier = dataset.VERIFY[tid]
        verifier['inputs'].append({'path': 'trusted_episode', 'from': str(record), 'immutable': True})
        result = _verify(work, verifier, '', sandbox=True, timeout_s=60, env={'kind': 'files'})
        assert len(endpoint.requests) == 1, result
    assert not result['judge']['faults'], result
    if malformed:
        assert result['score'] is None and result['outcome'] == 'unmeasured', result
    else:
        assert result['score'] == 0 and result['evidence']['measured'], result


def test_missing_selected_python_does_not_fall_back_to_driver(tmp_path):
    with pytest.raises(ValueError, match='cannot bind tau3 Python runtime'):
        dataset._python_runtime(tmp_path / 'missing')


def test_wrong_selected_python_is_rejected_at_configuration(tmp_path):
    python = Path('/usr/bin/python3')
    if not python.is_file():
        pytest.skip('system Python is unavailable')
    version = subprocess.check_output([str(python), '-I', '-c',
        'import json, sys; print(json.dumps(list(sys.version_info[:2])))'], text=True)
    if json.loads(version) == [3, 12]:
        pytest.skip('requires a Python other than 3.12')
    runtime = tmp_path / 'wrong-version'
    subprocess.run([str(python), '-m', 'venv', '--without-pip', str(runtime)], check=True)
    with pytest.raises(ValueError, match='Python 3.12'):
        dataset._python_runtime(runtime)
