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


@pytest.fixture(scope='module', params=['direct', 'absolute-alias', 'relative-alias'])
def fresh_venv(request):
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
        store = directory / 'runtime-store'; store.mkdir()
        (store / 'private-other-runtime').write_text('not part of the Python runtime')
        relative = request.param == 'relative-alias'
        bridge = directory / 'aliases/deep/store-link'; bridge.parent.mkdir(parents=True)
        bridge.symlink_to(os.path.relpath(store, bridge.parent) if relative else store,
                          target_is_directory=True)
        alias = directory / 'uv'
        alias.symlink_to(os.path.relpath(bridge, alias.parent) if relative else bridge,
                         target_is_directory=True)
        version = store / 'python-selected'
        base = Path(sys.base_prefix).resolve()
        version.symlink_to(os.path.relpath(base, store) if relative else base,
                           target_is_directory=True)
        extra = directory / 'extra-runtime'; extra.mkdir()
        (extra / 'marker.txt').write_text('explicit extra runtime')
        (store / 'extra-alias').symlink_to(extra, target_is_directory=True)
        if request.param != 'direct':
            executable = alias / version.name / Path(sys.executable).resolve().relative_to(base)
            python.unlink()
            python.symlink_to(executable)
            config = venv / 'pyvenv.cfg'
            config.write_text('\n'.join(
                f'home = {executable.parent}' if line.startswith('home = ') else line
                for line in config.read_text().splitlines()) + '\n')
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


@pytest.mark.parametrize('mount', ['none', 'version-alias', 'physical-version-alias',
                                  'store-alias', 'store', 'alias-bin', 'links-parent', 'extra-alias',
                                  'version-and-store', 'store-and-version'])
def test_fresh_venv_executes_with_own_dependency_and_readonly_runtime(fresh_venv, tmp_path, mount):
    python, prefix = fresh_venv
    if shutil.which('bwrap') is None:
        pytest.skip('bubblewrap is not provisioned')
    task = tmp_path / 'task'; task.mkdir()
    work = tmp_path / 'work'; work.mkdir()
    script = '''import json,sys
from pathlib import Path
from eval.containers import sandbox
root,task,work=map(Path,sys.argv[1:4])
placement=sys.argv[4]
directory=Path(sys.prefix).parent
paths={'none':(), 'version-alias':(directory/'uv/python-selected',),
       'physical-version-alias':(directory/'runtime-store/python-selected',),
       'store-alias':(directory/'uv',), 'store':(directory/'runtime-store',),
       'alias-bin':(directory/'uv/python-selected/bin',), 'links-parent':(directory,),
       'extra-alias':(directory/'uv/extra-alias',),
       'version-and-store':(directory/'uv/python-selected',directory/'uv'),
       'store-and-version':(directory/'uv',directory/'uv/python-selected')}
code="""import json,os,pathlib,sys,hg_venv_marker
p=pathlib.Path(hg_venv_marker.__file__)
try:p.write_text('changed')
except OSError:readonly=True
else:readonly=False
print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,
                 'marker':hg_venv_marker.VALUE,'readonly':readonly,
                 'base_readonly':bool(os.statvfs(sys.base_prefix).f_flag & os.ST_RDONLY),
                 'platform_visible':pathlib.Path(PLATFORM + '/driver.py').exists(),
                 'foreign_tmp_visible':pathlib.Path(FOREIGN).exists(),
                 'foreign_runtime_visible':pathlib.Path(SIBLING).exists(),
                 'aliased_foreign_runtime_visible':pathlib.Path(ALIAS_SIBLING).exists(),
                 'extra_runtime_visible':pathlib.Path(EXTRA).is_file(),
                 'declared_readonly':all(os.statvfs(p).f_flag & os.ST_RDONLY for p in DECLARED)}))
""".replace('PLATFORM',repr(str(root))).replace('FOREIGN',repr(str(work.parent/'secret-other-run')))
code=code.replace('ALIAS_SIBLING',repr(str(Path(sys.prefix).parent/'uv/private-other-runtime')))
code=code.replace('SIBLING',repr(str(Path(sys.prefix).parent/'runtime-store/private-other-runtime')))
code=code.replace('EXTRA',repr(str(directory/'uv/extra-alias/marker.txt')))
code=code.replace('DECLARED',repr([str(p) for p in paths[placement]]))
(work.parent/'secret-other-run').write_text('private other run')
result=sandbox.run([sys.executable,'-I','-c',code],platform=root,work_root=work,
    task_dir=task,repo=task,env={},timeout_s=20,cwd=task,network='none',runtime_paths=paths[placement])
print(json.dumps({'code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
                 'aliased':str(directory/'uv') in str(Path(sys.executable).readlink())}))
'''
    result = from_venv(python, script, ROOT, task, work, mount)
    assert result.returncode == 0, result.stderr
    wrapped = json.loads(result.stdout)
    assert wrapped['code'] == 0, wrapped
    observed = json.loads(wrapped['stdout'])
    aliased = wrapped['aliased']
    store_alias = mount in ('store-alias', 'version-and-store', 'store-and-version')
    assert observed == {'prefix': str(prefix), 'base_prefix': sys.base_prefix,
        'marker': 'installed-only-in-fresh-venv', 'readonly': True, 'base_readonly': True,
        'platform_visible': False, 'foreign_tmp_visible': False,
        'foreign_runtime_visible': mount in ('store', 'links-parent') or (aliased and store_alias),
        'aliased_foreign_runtime_visible': store_alias or mount == 'links-parent' or (aliased and mount == 'store'),
        'extra_runtime_visible': mount in ('links-parent', 'extra-alias'), 'declared_readonly': True}


def test_explicit_runtime_alias_preserves_platform_hides_and_private_checks(fresh_venv, tmp_path):
    python, prefix = fresh_venv
    if shutil.which('bwrap') is None:
        pytest.skip('bubblewrap is not provisioned')
    task = tmp_path / 'task'; task.mkdir()
    work = tmp_path / 'work'; work.mkdir()
    platform = prefix.parent / 'runtime-store/platform'; platform.mkdir()
    (platform / 'driver.py').write_text('# hidden platform sentinel\n')
    script = '''import json,sys
from pathlib import Path
from eval.containers import sandbox
platform,task,work=map(Path,sys.argv[1:])
directory=Path(sys.prefix).parent
aliased=str(directory/'uv') in str(Path(sys.executable).readlink())
runtime=directory/('uv' if aliased else 'runtime-store')
code="import json; from pathlib import Path; print(json.dumps([Path(p).exists() for p in " + repr(
    [str(platform/'driver.py'),str(directory/'uv/platform/driver.py')]) + "]))"
options=dict(platform=platform,work_root=work,task_dir=task,repo=task,env={},runtime_paths=(runtime,))
result=sandbox.run([sys.executable,'-I','-c',code],timeout_s=20,cwd=task,**options)
try:
    sandbox.command(['/bin/true'],private_paths=(directory/'runtime-store/private-other-runtime',),**options)
except ValueError as exc:
    private_rejected='visible through sandbox mount' in str(exc)
else:
    private_rejected=False
print(json.dumps({'code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
                 'private_rejected':private_rejected}))
'''
    try:
        result = from_venv(python, script, platform, task, work)
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed['code'] == 0, observed
        assert json.loads(observed['stdout']) == [False, False]
        assert observed['private_rejected'] is True
    finally:
        (platform / 'driver.py').unlink()
        platform.rmdir()


@pytest.mark.parametrize('placement', ['child', 'parent'])
def test_runtime_alias_cannot_project_hidden_storage_elsewhere(tmp_path, placement):
    with tempfile.TemporaryDirectory(prefix='hg-runtime-private-alias-', dir=Path.home()) as directory:
        root = Path(directory)
        store = root / 'store'; store.mkdir()
        platform = (root if placement == 'child' else store) / 'platform'; platform.mkdir()
        (platform / 'driver.py').write_text('# private platform sentinel\n')
        alias = root / 'deep/nested/alias'; alias.parent.mkdir(parents=True)
        alias.symlink_to(store, target_is_directory=True)
        # A parent bind turns the alias into a real directory. Its relative child
        # link would then name a different namespace path than it names on the host.
        if placement == 'child':
            (store / 'child').symlink_to('../platform', target_is_directory=True)
            paths = (alias, alias / 'child')
        else:
            paths = (alias,)
        work = tmp_path / 'work'; work.mkdir()
        task = tmp_path / 'task'; task.mkdir()
        with pytest.raises(sandbox.SandboxUnavailable, match='would expose hidden storage'):
            sandbox.command(['/bin/true'], platform=platform, work_root=work,
                            task_dir=task, repo=task, env={}, runtime_paths=paths)


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


@pytest.mark.parametrize('placement', ['tmp', 'platform', 'work', 'private'])
def test_interpreter_links_in_protected_storage_are_refused(tmp_path, monkeypatch, placement):
    with tempfile.TemporaryDirectory(prefix='hg-runtime-link-', dir=Path.home()) as root:
        root = Path(root)
        platform = root / 'platform'; platform.mkdir()
        work = root / 'work'; work.mkdir()
        task = tmp_path / 'task'; task.mkdir()
        private = root / 'private'; private.mkdir()
        parent = {'tmp': tmp_path, 'platform': platform, 'work': work, 'private': private}[placement]
        link = parent / 'runtime'
        base = Path(sys.base_prefix).resolve()
        link.symlink_to(base, target_is_directory=True)
        monkeypatch.setattr(sys, 'executable', str(link / Path(sys.executable).resolve().relative_to(base)))
        monkeypatch.setattr(sandbox, '_bwrap', lambda: '/usr/bin/bwrap')
        with pytest.raises(sandbox.SandboxUnavailable, match='Python executable link'):
            sandbox.command(['/bin/true'], platform=platform, work_root=work,
                            task_dir=task, env={}, private_paths=(private,))


@pytest.mark.parametrize('failure', ['self-loop', 'two-link-loop', 'dangling'])
def test_invalid_interpreter_link_is_refused(tmp_path, monkeypatch, failure):
    link = tmp_path / 'python'
    if failure == 'self-loop':
        link.symlink_to(link.name)
    elif failure == 'two-link-loop':
        link.symlink_to('other-python')
        (tmp_path / 'other-python').symlink_to(link.name)
    else:
        link.symlink_to('missing-python')
    monkeypatch.setattr(sys, 'executable', str(link))
    # Already-mounted links still need cycle detection when following the executable.
    if failure != 'dangling':
        with pytest.raises(sandbox.SandboxUnavailable, match='too many symbolic links'):
            sandbox._interpreter_symlinks((tmp_path,), platform=ROOT, work_root=tmp_path / 'work',
                task_dir=tmp_path / 'task', writable_paths=(), state_dirs=(), private_paths=())
    monkeypatch.setattr(sandbox, '_bwrap', lambda: '/usr/bin/bwrap')
    with pytest.raises(sandbox.SandboxUnavailable, match='cannot resolve the Python executable') as exc:
        sandbox.command(['/bin/true'], platform=ROOT, work_root=tmp_path / 'work',
                        task_dir=tmp_path / 'task', env={})
    assert isinstance(exc.value.__cause__, (OSError, RuntimeError))
    report = sandbox.self_check(ROOT, tmp_path / 'work')
    assert report['ok'] is False
    assert 'cannot resolve the Python executable' in report['detail']


@pytest.mark.parametrize('target', ['--bind', '--tmpfs', '--symlink'])
def test_private_mount_checks_skip_symlink_arguments(tmp_path, target):
    from eval.containers.private_mounts import reject_visible
    private = tmp_path / 'private'; private.mkdir()
    plan = ['--symlink', target, str(tmp_path / 'alias')]
    reject_visible(plan, (private,))
    with pytest.raises(ValueError, match='visible through sandbox mount'):
        reject_visible([*plan, '--ro-bind', str(private), str(tmp_path / 'exposed')], (private,))
