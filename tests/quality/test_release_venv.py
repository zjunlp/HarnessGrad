"""Real fresh-venv namespace controls, with an offline-installed tiny dependency.

No model, benchmark data or external package index is used. The environment
lives outside hidden /tmp; only this test's own directory is removed afterwards.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pytest

from eval.containers import sandbox

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def fresh_venv():
    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='hg-release-venv-', dir=cache))
    venv = directory / 'venv'
    try:
        created = subprocess.run([sys.executable, '-m', 'venv', str(venv)],
            capture_output=True, text=True, timeout=90)
        assert created.returncode == 0, created.stderr
        python = venv / 'bin/python'
        assert python.is_symlink(), 'control must exercise normal symlink venv creation'
        wheel = directory / 'hg_venv_marker-0.0.1-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as stream:
            stream.writestr('hg_venv_marker.py', 'VALUE = "installed-only-in-fresh-venv"\n')
            prefix = 'hg_venv_marker-0.0.1.dist-info/'
            stream.writestr(prefix + 'METADATA', 'Metadata-Version: 2.1\nName: hg-venv-marker\nVersion: 0.0.1\n')
            stream.writestr(prefix + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: HG-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            stream.writestr(prefix + 'RECORD', '')
        installed = subprocess.run([str(python), '-m', 'pip', 'install', '--no-index',
            '--disable-pip-version-check', '--no-deps', str(wheel)],
            capture_output=True, text=True, timeout=60)
        assert installed.returncode == 0, installed.stderr
        yield python, venv
    finally:
        shutil.rmtree(directory)


def from_venv(python, script, *args):
    return subprocess.run([str(python), '-c', script, *map(str, args)], cwd=ROOT,
        capture_output=True, text=True, timeout=90,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def test_fresh_venv_executes_with_own_dependency_and_readonly_runtime(fresh_venv, tmp_path):
    python, prefix = fresh_venv
    if shutil.which('bwrap') is None:
        pytest.skip('bubblewrap is not provisioned')
    task = tmp_path / 'task'; task.mkdir()
    work = tmp_path / 'work'; work.mkdir()
    script = '''import json,sys
from pathlib import Path
from eval.containers import sandbox
root,task,work=map(Path,sys.argv[1:])
code="""import json,pathlib,sys,hg_venv_marker
p=pathlib.Path(hg_venv_marker.__file__)
try:p.write_text('changed')
except OSError:readonly=True
else:readonly=False
print(json.dumps({'prefix':sys.prefix,'marker':hg_venv_marker.VALUE,'readonly':readonly,
                 'platform_visible':pathlib.Path(PLATFORM + '/driver.py').exists(),
                 'foreign_tmp_visible':pathlib.Path(FOREIGN).exists()}))
""".replace('PLATFORM',repr(str(root))).replace('FOREIGN',repr(str(work.parent/'secret-other-run')))
(work.parent/'secret-other-run').write_text('private other run')
result=sandbox.run([sys.executable,'-I','-c',code],platform=root,work_root=work,
    task_dir=task,repo=task,env={},timeout_s=20,cwd=task,network='none')
print(json.dumps({'code':result.returncode,'stdout':result.stdout,'stderr':result.stderr}))
'''
    result = from_venv(python, script, ROOT, task, work)
    assert result.returncode == 0, result.stderr
    wrapped = json.loads(result.stdout)
    assert wrapped['code'] == 0, wrapped
    observed = json.loads(wrapped['stdout'])
    assert observed == {'prefix': str(prefix), 'marker': 'installed-only-in-fresh-venv',
        'readonly': True, 'platform_visible': False, 'foreign_tmp_visible': False}


def test_fresh_venv_passes_actual_driver_boundary_self_check(fresh_venv, tmp_path):
    python, _ = fresh_venv
    if shutil.which('bwrap') is None:
        pytest.skip('bubblewrap is not provisioned')
    work = tmp_path / 'work'; work.mkdir()
    script = '''import json,sys
from pathlib import Path
from eval.containers.sandbox import self_check
print(json.dumps(self_check(Path(sys.argv[1]),Path(sys.argv[2]))))
'''
    result = from_venv(python, script, ROOT, work)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['ok'] is True, report


@pytest.mark.parametrize('placement', ['tmp', 'platform', 'work'])
def test_hidden_or_writable_interpreter_prefix_is_refused_before_launch(tmp_path, monkeypatch, placement):
    platform = tmp_path / 'platform'; platform.mkdir()
    work = tmp_path / 'work'; work.mkdir()
    task = tmp_path / 'task'; task.mkdir()
    prefix = (platform / '.venv' if placement == 'platform' else work / '.venv'
              if placement == 'work' else tmp_path / 'venv')
    prefix.mkdir()
    monkeypatch.setattr(sys, 'prefix', str(prefix))
    monkeypatch.setattr(sys, 'base_prefix', str(prefix))
    monkeypatch.setattr(sandbox, '_bwrap', lambda: '/usr/bin/bwrap')
    with pytest.raises(sandbox.SandboxUnavailable, match='create the venv'):
        sandbox.command(['/bin/true'], platform=platform, work_root=work, task_dir=task, env={})
    # self_check turns the same refusal into an actionable report, not a traceback.
    report = sandbox.self_check(platform, work)
    assert report['ok'] is False
    assert 'venv' in report['detail'] and 'No such file' not in report['detail']


def test_interpreter_prefixes_never_broaden_to_entire_host(monkeypatch):
    monkeypatch.setattr(sys, 'prefix', '/')
    with pytest.raises(sandbox.SandboxUnavailable, match='not an absolute directory'):
        sandbox.interpreter_prefixes()
