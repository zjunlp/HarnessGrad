"""Managed candidates cannot enter the validator's legacy smoke interpreter."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from eval.containers import sandbox
from harnessgrad import python_harness_runtime
from tools import validate_harness as validator


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def managed(tmp_path):
    root=tmp_path/'candidate';root.mkdir()
    marker=tmp_path/'host-executed'
    (root/'harness.json').write_text(json.dumps({'name':'managed-smoke-control','version':'1',
        'entrypoint':'agent.py','runtime':{'kind':'python',
        'python':f'{sys.version_info.major}.{sys.version_info.minor}','requirements':'requirements.lock'}}))
    (root/'requirements.lock').write_text('# no third-party packages\n')
    (root/'agent.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n')
    return root,marker


def test_managed_static_validation_remains_available_but_legacy_smoke_never_runs(managed, monkeypatch):
    root,marker=managed
    launch=Mock(side_effect=AssertionError('managed source entered legacy smoke'))
    monkeypatch.setattr(validator,'_smoke',launch)
    assert validator.validate(root,smoke=False)==[]
    problems=validator.validate(root,smoke=True)
    assert len(problems)==1 and 'managed Python smoke is not supported' in problems[0]
    assert '--smoke --work-root <external-dir>' in problems[0]
    launch.assert_not_called()
    assert not marker.exists()


def test_managed_callable_smoke_missing_runtime_never_allocates_or_executes(managed, monkeypatch):
    root,marker=managed
    touched=Mock(side_effect=AssertionError('missing runtime allocated or executed'))
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT',str(root.parent/'missing-store'))
    import py_compile
    py_compile.compile(str(root/'agent.py'), doraise=True)
    monkeypatch.setattr(validator,'_smoke_work_root',touched)
    monkeypatch.setattr(sandbox,'run',touched)
    work=root.parent/'work'
    problems=validator.smoke(root,platform=ROOT,work_root=work,io_budget=touched)
    assert len(problems)==1 and problems[0]['code']=='service-not-ready'
    touched.assert_not_called()
    assert not marker.exists() and not work.exists() and not (root/'.state').exists()


def test_managed_smoke_cli_refuses_with_the_canonical_sandbox_route(managed):
    root,marker=managed
    result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/validate_harness.py'),
        str(root),'--smoke'],capture_output=True,text=True,timeout=20,cwd=ROOT)
    assert result.returncode==1,result.stdout+result.stderr
    assert 'requires --work-root' in result.stdout
    assert 'configure_harness.py' in result.stdout
    assert 'smoke run produced an answer' not in result.stdout
    assert not marker.exists() and not (root/'.state').exists()


@pytest.mark.parametrize("with_exec", [False, True])
def test_managed_contributor_offline_prepare_readonly_check_and_sandbox_smoke(tmp_path, monkeypatch, with_exec):
    """Real canonical CLI flow using an authored offline wheel; no model/benchmark."""
    import os
    import tempfile
    from test_python_harness_store import wheel
    from harnessgrad.python_artifacts import remove_instance
    storage = Path(tempfile.mkdtemp(prefix='hg-contributor-control-', dir=Path.home()/'.cache'))
    try:
        repo = storage/'source'; repo.mkdir()
        work = storage/'work'
        wheels = storage/'wheels'; wheel(wheels)
        (repo/'harness.json').write_text(json.dumps({'name':'authored-contributor', 'version':'1',
            'entrypoint':'agent.py', 'env_kinds':['files'], 'runtime':{'kind':'python',
            'python':f'{sys.version_info.major}.{sys.version_info.minor}', 'requirements':'requirements.lock'}}))
        if with_exec:
            manifest = json.loads((repo/'harness.json').read_text())
            manifest['runtime']['exec'] = {'requirements': {f'{sys.version_info.major}.{sys.version_info.minor}': 'requirements.lock'}}
            (repo/'harness.json').write_text(json.dumps(manifest))
        (repo/'requirements.lock').write_text('hg-store-fixture==1.0\n')
        (repo/'agent.py').write_text('''import argparse, os, sys
from pathlib import Path
import hg_store_fixture
assert hg_store_fixture.VALUE == '1.0'
assert sys.flags.isolated and sys.dont_write_bytecode
assert not (Path(__file__).parent/'__pycache__').exists()
assert not (Path(__file__).parent/'agent.pyc').exists()
assert os.environ['HG_AGENT_BACKEND'] == 'mock'
assert 'OPENAI_API_KEY' not in os.environ
assert os.statvfs(sys.prefix).f_flag & os.ST_RDONLY
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
Path(a.workdir,'answer.txt').write_text('ok')
''')
        env = dict(os.environ, HG_REVIEWED_ENVIRONMENT_ONLY='1', UV_OFFLINE='1',
                   HG_HARNESS_RUNTIME_ROOT=str(storage/'store'), HG_HARNESS_WHEELHOUSE=str(wheels),
                   UV_CACHE_DIR=str(storage/'cache'), OPENAI_API_KEY='never-forward')
        def run(tool, *args):
            return subprocess.run([sys.executable,'-B',str(ROOT/'tools'/tool),*map(str,args)],
                                  cwd=ROOT,env=env,text=True,capture_output=True,timeout=180)
        # Installers compile templates by default: preserve real bytecode on disk,
        # while ensuring neither cached nor legacy adjacent pyc enters the actor.
        import py_compile
        py_compile.compile(str(repo/'agent.py'), doraise=True)
        marker = storage/'bytecode-executed'
        poison = storage/'poison.py'
        poison.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
        py_compile.compile(str(poison), cfile=str(repo/'agent.pyc'), doraise=True)
        before_source = {str(p.relative_to(repo)):p.read_bytes() for p in repo.rglob('*') if p.is_file()}
        prepared = run('configure_harness.py','--harness',repo)
        assert prepared.returncode == 0,prepared.stdout+prepared.stderr
        before = sorted(str(p.relative_to(storage/'store')) for p in (storage/'store').rglob('*'))
        checked = run('validate_harness.py',repo,'--check-runtime')
        assert checked.returncode == 0,checked.stdout+checked.stderr
        assert 'actor source not admitted' in checked.stdout
        assert sorted(str(p.relative_to(storage/'store')) for p in (storage/'store').rglob('*')) == before
        smoked = run('validate_harness.py',repo,'--smoke','--work-root',work)
        assert smoked.returncode == 0,smoked.stdout+smoked.stderr
        assert 'smoke run produced an answer' in smoked.stdout
        assert not (repo/'.state').exists()
        assert list(work.iterdir()) == []
        assert {str(p.relative_to(repo)):p.read_bytes() for p in repo.rglob('*') if p.is_file()} == before_source
        assert not marker.exists()
    finally:
        remove_instance(storage)


@pytest.mark.parametrize("drift_call", [2, 3])
def test_managed_smoke_runtime_drift_refuses_success(managed, monkeypatch, drift_call):
    from harnessgrad import candidate_runtime, python_exec_runtime
    root,marker=managed
    binding = {'authored': 'identity'}
    calls=[]
    def admit(repo, selected=None, **kwargs):
        calls.append(selected)
        if len(calls) == drift_call:
            raise candidate_runtime.RuntimeUnavailable('authored runtime drift')
        return binding
    monkeypatch.setattr(validator,'prepared_dependency_binding',lambda root: binding)
    monkeypatch.setattr(candidate_runtime,'admit',admit)
    monkeypatch.setattr(python_exec_runtime,'placement',lambda *a:
                        {'artifact':{'python':sys.executable,'path':sys.prefix,'runtime_paths':[sys.prefix]}})
    def ready_only(argv, **kwargs):
        if argv != ['/bin/true']:
            assert drift_call == 3, 'drifted subject must never launch'
            (kwargs['task_dir']/'answer.txt').write_text('ok')
        return subprocess.CompletedProcess(argv,0,'','')
    monkeypatch.setattr(sandbox,'run',ready_only)
    problems=validator.smoke(root,platform=ROOT,work_root=root.parent/'work')
    assert problems == [{'code':'service-not-ready','detail':'authored runtime drift'}]
    assert calls == [binding]*drift_call
    assert not marker.exists()


def test_managed_smoke_classifies_manifest_drift_after_copy(managed, monkeypatch):
    from harnessgrad import candidate_runtime, python_exec_runtime
    root, marker = managed
    binding = {'authored': 'identity'}
    original_admit = candidate_runtime.admit
    original_copy = validator._smoke_copy
    original_manifest = (root / 'harness.json').read_bytes()

    def admit(repo, selected=None, **kwargs):
        if selected is None:
            return binding
        return original_admit(repo, selected, **kwargs)

    def copy_with_drift(source, target, charge, **kwargs):
        original_copy(source, target, charge, **kwargs)
        if source == root:
            (target / 'harness.json').write_text('invalid manifest')

    monkeypatch.setattr(validator, 'prepared_dependency_binding', lambda root: binding)
    monkeypatch.setattr(candidate_runtime, 'admit', admit)
    monkeypatch.setattr(validator, '_smoke_copy', copy_with_drift)
    monkeypatch.setattr(python_exec_runtime, 'placement', lambda *a:
                        {'artifact': {'python': sys.executable, 'path': sys.prefix,
                                      'runtime_paths': [sys.prefix]}})
    launch = Mock(side_effect=AssertionError('drifted source must never launch'))
    monkeypatch.setattr(sandbox, 'run', launch)
    work = root.parent / 'work'
    problems = validator.smoke(root, platform=ROOT, work_root=work)
    assert len(problems) == 1 and problems[0]['code'] == 'service-not-ready'
    assert 'Invalid harness manifest' in problems[0]['detail']
    launch.assert_not_called()
    assert (root / 'harness.json').read_bytes() == original_manifest
    assert not marker.exists() and list(work.iterdir()) == []
