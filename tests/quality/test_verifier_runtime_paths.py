"""Explicit scorer runtimes are read-only and do not alter undeclared routes."""
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from eval.verification import verifier


@pytest.fixture(scope='module')
def python_runtime():
    # /tmp is deliberately hidden by the sandbox; a runtime lives outside it.
    with tempfile.TemporaryDirectory(prefix='.hg-verifier-test-', dir=Path.home()) as root:
        runtime = Path(root) / 'venv'
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(runtime)], check=True)
        yield runtime


def spec_for(runtime):
    script = '''
import json, os, sys
from pathlib import Path
Path('verifier/evidence.json').write_text(json.dumps({
    'executable': sys.executable, 'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
    'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY)}))
Path('verifier/reward.txt').write_text('1')
'''
    return {'kind': 'command', 'argv': [str(runtime / 'bin/python'), '-I', 'check.py'],
            'runtime_paths': [str(runtime), sys.base_prefix],
            'inputs': [{'path': 'check.py', 'content': script}],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}


def verify(work, spec, **options):
    return verifier._verify(work, spec, '', **{'sandbox': True, 'timeout_s': 20,
                                            'env': {'kind': 'files'}, **options})


def test_explicit_venv_survives_hostile_path_and_is_readonly(tmp_path, monkeypatch, python_runtime):
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    spec = spec_for(python_runtime)
    result = verify(tmp_path, spec)
    assert result.get('score') == 1, result
    assert result['evidence']['executable'] == str(python_runtime / 'bin/python')
    assert result['evidence']['prefix'] == str(python_runtime)
    assert result['evidence']['base_prefix'] == sys.base_prefix
    assert result['evidence']['readonly']


@pytest.mark.parametrize('case', ['null', 'string', 'relative', 'missing', 'file',
                                  'symlink', 'hidden', 'task', 'platform', 'relative-command'])
def test_invalid_runtime_declarations_are_unmeasured(tmp_path, monkeypatch, python_runtime, case):
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    spec = spec_for(python_runtime)
    if case == 'null':
        spec['runtime_paths'] = None
    elif case == 'string':
        spec['runtime_paths'] = str(python_runtime)
    elif case == 'relative':
        spec['runtime_paths'] = ['relative/runtime']
    elif case == 'missing':
        spec['runtime_paths'] = [str(python_runtime / 'absent')]
    elif case == 'file':
        spec['runtime_paths'] = [str(python_runtime / 'pyvenv.cfg')]
    elif case == 'symlink':
        link = tmp_path / 'link'
        link.symlink_to(python_runtime, target_is_directory=True)
        spec['runtime_paths'] = [str(link)]
    elif case == 'hidden':
        hidden = tmp_path.parent / 'hidden-runtime'
        hidden.mkdir(exist_ok=True)
        spec['runtime_paths'] = [str(hidden)]
    elif case == 'task':
        spec['runtime_paths'] = [str(tmp_path)]
    elif case == 'platform':
        spec['runtime_paths'] = [str(verifier.PLATFORM_ROOT)]
    else:
        spec['argv'][0] = 'python3'
    monkeypatch.setattr(verifier, '_verify_dispatch', lambda *a, **kw: pytest.fail('invalid runtime launched'))
    result = verify(tmp_path, spec)
    assert result['score'] is None and result['outcome'] == 'unmeasured', result
    assert 'runtime_paths' in result['unmeasured'], result


@pytest.mark.parametrize('case', ['no-sandbox', 'exec', 'answer', 'pristine', 'native',
                                  'container-runtime', 'service', 'no-exit-contract'])
def test_runtime_paths_refuse_unsupported_routes(tmp_path, monkeypatch, python_runtime, case):
    spec = spec_for(python_runtime)
    options = {}
    if case == 'no-sandbox':
        options['sandbox'] = False
    elif case == 'exec':
        options['env'] = {'kind': 'exec', 'image': 'fixture'}
    elif case == 'answer':
        spec.update(kind='answer', expected='fixture')
    elif case == 'pristine':
        spec['scorer_bundle'] = {'isolation_class': 'pristine-offline'}
    elif case == 'native':
        spec['native_runtime'] = {}
    elif case == 'container-runtime':
        spec['container_runtime'] = {}
    elif case == 'service':
        options['service_specs'] = [{'name': 'fixture'}]
    else:
        spec.pop('result_contract')
    monkeypatch.setattr(verifier, '_verify_dispatch', lambda *a, **kw: pytest.fail('unsupported runtime launched'))
    result = verify(tmp_path, spec, **options)
    assert result['score'] is None and result['outcome'] == 'unmeasured', result
    assert 'runtime_paths' in result['unmeasured'], result


def test_runtime_paths_refuse_snapshot_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.container_mod, 'require_snapshot_identity',
                        lambda *a, **kw: pytest.fail('unsupported snapshot launched'))
    result = verifier._verify_container_state('unused', {'kind': 'command', 'runtime_paths': []},
        task_mount='/task', staging=tmp_path, timeout_s=5, run_id='control', task_id='task')
    assert result['score'] is None and result['outcome'] == 'unmeasured', result
    assert 'runtime_paths' in result['unmeasured'], result


@pytest.mark.parametrize('code', [0, 1])
def test_undeclared_command_keeps_original_launch_and_verdict(tmp_path, monkeypatch, code):
    spec = {'kind': 'command', 'argv': ['existing-check', 'argument']}
    calls = []
    def run(argv, **options):
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, code, '', '')
    monkeypatch.setattr(verifier.sandbox_mod, 'run', run)
    monkeypatch.setenv('PATH', '/existing/runtime/bin:/usr/bin')
    result = verify(tmp_path, spec)
    assert result['passed'] is (code == 0), result
    assert len(calls) == 1 and calls[0][0] == spec['argv']
    assert 'runtime_paths' not in calls[0][1]
    assert calls[0][1]['env']['PATH'] == '/existing/runtime/bin:/usr/bin'


@pytest.mark.parametrize('answer,score', [('18', 1), ('999', 0), (None, 0)])
def test_legacy_qa_scorer_keeps_its_command_and_scores(tmp_path, monkeypatch,
                                                       synthetic_gsm8k, answer, score):
    # Exercise the existing QA adapter and real sandbox with synthetic task data.
    task = synthetic_gsm8k._RECORDS[0]
    spec = synthetic_gsm8k.VERIFY[task['task_id']]
    assert 'runtime_paths' not in spec
    if answer is not None:
        (tmp_path / 'answer.txt').write_text(answer)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    calls = []
    original = verifier.sandbox_mod.command
    def command(argv, **options):
        calls.append((argv, options))
        return original(argv, **options)
    monkeypatch.setattr(verifier.sandbox_mod, 'command', command)
    result = verify(tmp_path, spec, task_id=task['task_id'])
    assert result.get('score') == score, result
    assert calls[0][0] == spec['argv']
    assert calls[0][1]['runtime_paths'] == ()
