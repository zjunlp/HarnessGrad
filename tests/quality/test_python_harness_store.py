"""Managed candidate preparation uses immutable offline wheels without startup hooks."""
from contextlib import contextmanager
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from unittest.mock import Mock
import zipfile

import pytest

from eval.ckpt.source_tree import copy_harness, has_bun_runtime, has_python_runtime, runtime_spec
from eval.ckpt.git_state import init_repo, stage_workspace
from eval.containers import bun_runtime
from harnessgrad import python_harness_runtime as runtime
from harnessgrad.python_artifacts import locked
from tools.validate_harness import check, source_identity

ROOT = Path(__file__).resolve().parents[2]


def test_python_selection_prefers_matching_current_interpreter(monkeypatch):
    minor = f'{sys.version_info.major}.{sys.version_info.minor}'
    monkeypatch.setattr(runtime, '_run', Mock(side_effect=AssertionError('uv discovery used')))
    probe = Mock(return_value={'selected': 'base'})
    monkeypatch.setattr(runtime, '_base_probe', probe)
    assert runtime._physical_python('/mock/uv', {'python': minor}, None, 10) == {'selected': 'base'}
    probe.assert_called_once_with(sys.executable, minor, timeout_s=10)


@pytest.mark.parametrize('requested,minor', [('/explicit/python', '3.12'), (None, '0.0')])
def test_python_selection_uses_controlled_uv_for_explicit_or_mismatched(monkeypatch, requested, minor):
    run = Mock(return_value='/selected/python\n')
    probe = Mock(return_value={'selected': 'base'})
    monkeypatch.setattr(runtime, '_run', run)
    monkeypatch.setattr(runtime, '_base_probe', probe)
    assert runtime._physical_python('/mock/uv', {'python': minor}, requested, 10) == {'selected': 'base'}
    assert run.call_args.args[0][-1] == (requested or minor)
    assert run.call_args.kwargs['env']['PATH'] == os.defpath
    assert '--no-python-downloads' in run.call_args.args[0]
    probe.assert_called_once_with(Path('/selected/python'), minor, timeout_s=10)


def wheel(folder, *, version='1.0', hook=None, name='hg-store-fixture', requires=()):
    """An authored pure wheel; no setuptools/build backend is invoked."""
    folder.mkdir(exist_ok=True)
    normalized = name.replace('-', '_')
    info = f'{normalized}-{version}.dist-info'
    metadata = f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'
    metadata += ''.join(f'Requires-Dist: {requirement}\n' for requirement in requires)
    files = {normalized + '.py': f"VALUE = {version!r}\n".encode(),
             info + '/METADATA': metadata.encode(),
             info + '/WHEEL': b'Wheel-Version: 1.0\nGenerator: authored-control\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    if hook is not None:
        files['authored_startup.pth'] = hook.encode()
    stream = io.StringIO()
    records = csv.writer(stream, lineterminator='\n')
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        records.writerow([name, 'sha256=' + digest, len(data)])
    records.writerow([info + '/RECORD', '', ''])
    files[info + '/RECORD'] = stream.getvalue().encode()
    path = folder / f'{normalized}-{version}-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    repo = tmp_path / 'candidate'
    repo.mkdir()
    (repo / 'harness.json').write_text(json.dumps({
        'name': 'authored-managed-python', 'version': '1', 'entrypoint': 'agent.py',
        'runtime': {'kind': 'python', 'python': f'{sys.version_info.major}.{sys.version_info.minor}',
                    'requirements': 'requirements.lock'}}))
    (repo / 'requirements.lock').write_text('# Empty instrument dependencies\n')
    (repo / 'agent.py').write_text("print('not executed by preparation')\n")
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT', str(tmp_path / 'store'))
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.delenv('HG_HARNESS_WHEELHOUSE', raising=False)
    yield repo
    # Managed artifacts are intentionally immutable; this fixture owns their parents.
    for path in tmp_path.rglob('*'):
        if path.is_dir() and not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o700)


@pytest.fixture
def prepared(candidate):
    if shutil.which('uv') is None or shutil.which('bwrap') is None:
        pytest.skip('real offline preparation needs uv and bubblewrap')
    return candidate, runtime.prepare(candidate)


def test_static_declaration_and_source_copy_ignore_generated_environments(candidate, tmp_path):
    for name in ('.venv', 'venv', '.state', 'node_modules', '__pycache__'):
        (candidate / name).mkdir()
        (candidate / name / 'generated.py').write_text('raise AssertionError()\n')
    (candidate / 'bytecode.pyc').write_bytes(b'not source')
    original = runtime.source_fingerprint(candidate)
    (candidate / '.venv/generated.py').write_text('changed environment bytes')
    assert runtime.source_fingerprint(candidate) == original
    assert has_python_runtime(candidate) and not has_bun_runtime(candidate)
    assert check(candidate) == []
    copy_harness(candidate, tmp_path / 'copy')
    assert runtime.source_fingerprint(tmp_path / 'copy') == original
    assert not (tmp_path / 'copy/.venv').exists() and not (tmp_path / 'copy/bytecode.pyc').exists()
    manifest = json.loads((candidate / 'harness.json').read_bytes())
    assert runtime.declaration_identity(manifest, (candidate / 'requirements.lock').read_bytes()) == runtime.descriptor(candidate)['declaration_sha256']


def test_managed_input_identity_ignores_a_preexisting_linked_venv(candidate):
    expected = source_identity(candidate)
    binary = candidate / '.venv/bin'
    binary.mkdir(parents=True)
    (binary / 'python').symlink_to(Path(sys.executable).resolve())
    assert source_identity(candidate) == expected
    manifest = json.loads((candidate / 'harness.json').read_bytes())
    manifest.pop('runtime')
    (candidate / 'harness.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='unsafe path'):
        source_identity(candidate)


@pytest.mark.parametrize('operation', ['init_repo', 'stage_workspace'])
def test_managed_staging_rejects_gitignore_alias_before_writing(candidate, operation):
    canary = candidate.parent / 'external-ignore-target'
    canary.write_text('operator-owned bytes\n')
    (candidate / '.gitignore').symlink_to(canary)
    work = candidate.parent / 'existing-workspace'
    work.mkdir()
    sentinel = work / 'must-survive-refusal'
    sentinel.write_text('preserved\n')
    with pytest.raises(ValueError, match='owned regular'):
        if operation == 'init_repo':
            init_repo(candidate)
        else:
            stage_workspace(candidate, work)
    assert canary.read_text() == 'operator-owned bytes\n'
    assert not (candidate / '.git').exists()
    assert sentinel.read_text() == 'preserved\n'


def test_managed_staging_ignores_a_preexisting_linked_venv(candidate):
    binary = candidate / '.venv/bin'
    binary.mkdir(parents=True)
    (binary / 'python').symlink_to(Path(sys.executable).resolve())
    work = candidate.parent / 'staged-source'
    stage_workspace(candidate, work)
    assert (work / '.gitignore').is_file()
    assert not (work / '.venv').exists()
    assert runtime.descriptor(work)['declaration_sha256'] == runtime.descriptor(candidate)['declaration_sha256']


def test_managed_static_check_rejects_an_owned_sibling_alias(candidate):
    (candidate / 'ordinary-helper.py').symlink_to('agent.py')
    assert any('owned regular' in item['detail'] for item in check(candidate))


@pytest.mark.parametrize('lock', ['pkg>=1\n', '-r elsewhere.txt\n', 'pkg @ https://example.test/pkg.whl\n',
    './pkg.whl\n', 'pkg[extra]==1\n', 'pkg==1 --hash=sha256:abc\n', 'foo_bar==1\nfoo-bar==2\n'])
def test_lock_rejects_code_sources_options_and_duplicate_pins(candidate, lock):
    (candidate / 'requirements.lock').write_text(lock)
    with pytest.raises(ValueError, match='exact full pins'):
        runtime.descriptor(candidate)
    assert any('exact full pins' in item['detail'] for item in check(candidate))


@pytest.mark.parametrize('field,value', [('python', 3.13), ('python', '3'), ('python', '3.7'),
    ('requirements', '../outside'), ('requirements', '/tmp/lock'), ('requirements', '.venv/lock')])
def test_declaration_rejects_ambiguous_targets_and_paths(candidate, field, value):
    path = candidate / 'harness.json'
    manifest = json.loads(path.read_text())
    manifest['runtime'][field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        runtime_spec(candidate)


def test_runtime_and_install_cannot_disagree(candidate):
    path = candidate / 'harness.json'
    manifest = json.loads(path.read_text())
    manifest['install'] = 'other.txt'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='same lock'):
        runtime_spec(candidate)


@pytest.mark.parametrize('paths', [['/tmp/unbound'], ['relative-root'], 'outside', {}])
def test_python_declaration_refuses_unbound_runtime_paths(candidate, paths):
    path = candidate / 'harness.json'
    manifest = json.loads(path.read_text())
    manifest['runtime_paths'] = paths
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='runtime_paths'):
        runtime_spec(candidate)
    with pytest.raises(ValueError, match='runtime_paths'):
        runtime.descriptor(candidate)
    assert any('runtime_paths' in item['detail'] for item in check(candidate))


@pytest.mark.parametrize('entry', ['.venv/agent.py', 'venv/agent.py', 'node_modules/agent.py',
    '__pycache__/agent.py', '.state/agent.py', '_harnessgrad/agent.py', 'agent.pyc', '../agent.py'])
def test_python_entrypoint_cannot_live_in_ignored_state(candidate, entry):
    path = candidate / 'harness.json'
    manifest = json.loads(path.read_text())
    manifest['entrypoint'] = entry
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='entrypoint'):
        runtime_spec(candidate)


def test_bun_admission_ignores_python_without_invoking_bun(candidate, monkeypatch):
    monkeypatch.setattr(bun_runtime, 'runtime_settings', Mock(side_effect=AssertionError('called Bun')))
    assert bun_runtime.admit(candidate, {'HG_HARNESS_SOURCE': '/unused-bun-source'}) == {}
    assert bun_runtime.prepare(candidate) is None and bun_runtime.identity(candidate) is None


def test_read_only_missing_runtime_does_not_create_store_or_cache(candidate, monkeypatch):
    monkeypatch.setattr(runtime, '_build', Mock(side_effect=AssertionError('built during admission')))
    with pytest.raises(runtime.RuntimeUnavailable, match='configure_harness') as failure:
        runtime.identity(candidate)
    assert failure.value.receipt['seconds'] >= 0 and failure.value.receipt['status'] == 'invalid'
    assert not (candidate.parent / 'store').exists() and not (candidate.parent / 'cache').exists()


def test_preparation_lock_wait_is_bounded(candidate):
    folder = runtime.store_root(candidate) / runtime._key(runtime.descriptor(candidate))
    with locked(folder):
        with pytest.raises(runtime.RuntimeUnavailable, match='artifact lock') as failure:
            runtime.prepare(candidate, timeout_s=.1)
    assert .09 <= failure.value.receipt['seconds'] < 2


def protected_prefix(repo, placement, monkeypatch):
    if placement == 'platform':
        return ROOT
    if placement == 'candidate':
        return repo
    work = repo.parent / 'private-work-root'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    return work


@pytest.mark.parametrize('field', ['base_prefix', 'base_exec_prefix'])
@pytest.mark.parametrize('placement', ['platform', 'candidate', 'work'])
def test_preparation_refuses_protected_base_prefix_before_builder(candidate, monkeypatch, field, placement):
    prefix = protected_prefix(candidate, placement, monkeypatch)
    base = {'base_prefix': sys.base_prefix, 'base_exec_prefix': sys.base_exec_prefix, field: str(prefix)}
    monkeypatch.setattr(runtime, '_physical_python', lambda *args, **kwargs: base)
    original_which = shutil.which
    monkeypatch.setattr(runtime.shutil, 'which', lambda name, *args, **kwargs:
        sys.executable if name == 'uv' else original_which(name, *args, **kwargs))
    builder = Mock(side_effect=AssertionError('mounted protected interpreter prefix'))
    monkeypatch.setattr(runtime, '_builder', builder)
    with pytest.raises(runtime.RuntimeUnavailable, match='outside candidate, platform and work'):
        runtime.prepare(candidate)
    builder.assert_not_called()
    assert not list((candidate.parent / 'store').glob('*/instances'))


@pytest.mark.parametrize('field', ['base_prefix', 'base_exec_prefix'])
@pytest.mark.parametrize('placement', ['platform', 'candidate', 'work'])
def test_exact_admission_refuses_protected_recorded_base_prefix(prepared, monkeypatch, field, placement):
    repo, receipt = prepared
    prefix = protected_prefix(repo, placement, monkeypatch)
    binding = json.loads(json.dumps(receipt['identity']))
    manifest_path = Path(binding['artifact']['manifest_path'])
    original, mode = manifest_path.read_bytes(), stat.S_IMODE(manifest_path.stat().st_mode)
    manifest = json.loads(original)
    manifest['base_python'][field] = str(prefix)
    manifest['runtime_paths'] = list(dict.fromkeys([manifest['path'],
        manifest['base_python']['base_prefix'], manifest['base_python']['base_exec_prefix']]))
    binding['artifact']['runtime_paths'] = manifest['runtime_paths']
    binding['artifact']['runtime_id'] = hashlib.sha256(runtime.canonical(manifest)).hexdigest()
    binding['sha256'] = hashlib.sha256(runtime.canonical({key: value for key, value in binding.items()
                                                       if key != 'sha256'})).hexdigest()
    probe = Mock(side_effect=AssertionError('executed protected base before rejecting prefixes'))
    monkeypatch.setattr(runtime, '_base_probe', probe)
    try:
        manifest_path.chmod(mode | 0o200)
        manifest_path.write_bytes(runtime.canonical(manifest))
        with pytest.raises(runtime.RuntimeUnavailable, match='outside candidate, platform and work'):
            runtime.admit_binding(repo, binding)
        probe.assert_not_called()
    finally:
        manifest_path.write_bytes(original)
        manifest_path.chmod(mode)


def test_source_edit_reuses_artifact_but_has_a_distinct_candidate_binding(prepared, monkeypatch):
    repo, first = prepared
    (repo / 'agent.py').write_text("print('changed source')\n")
    monkeypatch.setattr(runtime, '_build', Mock(side_effect=AssertionError('rebuilt unchanged dependencies')))
    monkeypatch.setattr(runtime.shutil, 'which', lambda *args, **kwargs: None)
    second = runtime.prepare(repo)
    assert second['cache_hit'] and not second['installed']
    assert first['identity']['artifact'] == second['identity']['artifact']
    assert first['identity']['source_sha256'] != second['identity']['source_sha256']
    with pytest.raises(runtime.RuntimeUnavailable, match='source differs'):
        runtime.admit_binding(repo, first['identity'])
    assert runtime.admit_binding(repo, second['identity']) == second['identity']


def test_lock_change_selects_a_new_dependency_artifact(prepared):
    repo, first = prepared
    (repo / 'requirements.lock').write_text('# A new explicit declaration\n')
    second = runtime.prepare(repo)
    assert second['installed']
    assert first['identity']['artifact']['dependency_key'] != second['identity']['artifact']['dependency_key']
    assert first['identity']['artifact']['runtime_id'] != second['identity']['artifact']['runtime_id']


def test_exact_admission_does_not_follow_a_changed_publication_index(prepared):
    repo, first = prepared
    ready = next((repo.parent / 'store').glob('*/ready.json'))
    ready.unlink()
    ready.write_text('{}')
    assert runtime.identity(repo, first['identity']) == first['identity']
    with pytest.raises(runtime.RuntimeUnavailable, match='publication'):
        runtime.identity(repo)


def test_explicit_force_keeps_old_artifact_and_publishes_a_new_one(prepared):
    repo, first = prepared
    second = runtime.prepare(repo, force=True)
    assert first['identity']['artifact']['runtime_id'] != second['identity']['artifact']['runtime_id']
    assert Path(first['identity']['artifact']['manifest_path']).is_file()
    assert runtime.identity(repo, first['identity']) == first['identity']
    assert runtime.identity(repo) == second['identity']


@contextmanager
def tampered(path):
    mode, original = stat.S_IMODE(path.stat().st_mode), path.read_bytes()
    try:
        path.chmod(mode | 0o200)
        path.write_bytes(original + b'\n# changed fixture bytes\n')
        path.chmod(mode)
        yield
    finally:
        path.chmod(mode | 0o200)
        path.write_bytes(original)
        path.chmod(mode)


def test_corrupt_contents_are_refused_without_repair(prepared, monkeypatch):
    repo, receipt = prepared
    binding = receipt['identity']
    build = Mock(side_effect=AssertionError('repaired corrupt artifact'))
    monkeypatch.setattr(runtime, '_build', build)
    with tampered(Path(binding['artifact']['path']) / 'pyvenv.cfg'):
        with pytest.raises(runtime.RuntimeUnavailable, match='inventory'):
            runtime.admit_binding(repo, binding)
        with pytest.raises(runtime.RuntimeUnavailable, match='inventory'):
            runtime.prepare(repo)
    build.assert_not_called()


def test_binding_cannot_silently_drop_its_declaration(prepared):
    repo, receipt = prepared
    manifest = json.loads((repo / 'harness.json').read_text())
    manifest.pop('runtime')
    (repo / 'harness.json').write_text(json.dumps(manifest))
    with pytest.raises(runtime.RuntimeUnavailable, match='disappeared'):
        runtime.identity(repo, receipt['identity'])


def test_installed_wheel_pth_is_not_executed_by_preparation_or_admission(candidate, tmp_path, monkeypatch):
    wheelhouse = tmp_path / 'wheelhouse'
    marker = tmp_path / 'host-startup-was-executed'
    hook = "import builtins; builtins.exec(" + repr(
        "import pathlib,sys\n"
        f"try: pathlib.Path({str(marker)!r}).write_text('host')\n"
        "except OSError: pass\n"
        "try: pathlib.Path(sys.prefix,'pth-was-executed').write_text('startup')\n"
        "except OSError: pass\n") + ")\n"
    wheel(wheelhouse, hook=hook)
    (candidate / 'requirements.lock').write_text('hg-store-fixture==1.0\n')
    monkeypatch.setenv('HG_HARNESS_WHEELHOUSE', str(wheelhouse))
    receipt = runtime.prepare(candidate)
    venv = Path(receipt['identity']['artifact']['path'])
    assert not marker.exists() and not (venv / 'pth-was-executed').exists()
    assert runtime.identity(candidate, receipt['identity']) == receipt['identity']
    assert not marker.exists() and not (venv / 'pth-was-executed').exists()
    manifest = json.loads(Path(receipt['identity']['artifact']['manifest_path']).read_bytes())
    assert manifest['packages'] == {'hg-store-fixture': '1.0'}
    assert manifest['recipe']['wheel_only'] and not manifest['recipe']['downloads_allowed']


def test_failed_offline_build_does_not_publish_partial_dependencies(candidate):
    (candidate / 'requirements.lock').write_text('hg-uncached-instrument-dependency==1.0\n')
    original = runtime.source_fingerprint(candidate)
    with pytest.raises(runtime.RuntimeUnavailable) as failure:
        runtime.prepare(candidate)
    assert failure.value.receipt['status'] == 'invalid'
    assert failure.value.receipt['seconds'] > 0
    assert not list((candidate.parent / 'store').glob('*/ready.json'))
    assert not list((candidate.parent / 'store').glob('*/instances/build-*'))
    assert runtime.source_fingerprint(candidate) == original


@pytest.mark.parametrize('timeout', [None, 0, -1, float('nan'), float('inf'), float('-inf'), True])
def test_invalid_timeout_is_refused_before_creating_a_store_or_process(candidate, monkeypatch, timeout):
    monkeypatch.setattr(runtime, '_build', lambda *a, **k: pytest.fail('invalid timeout reached a builder'))
    with pytest.raises(runtime.RuntimeUnavailable, match='finite and positive') as failure:
        runtime.prepare(candidate, timeout_s=timeout)
    assert failure.value.receipt['status'] == 'invalid'
    assert not (candidate.parent / 'store').exists()


def test_incomplete_transitive_lock_fails_before_installing_candidate_packages(candidate, tmp_path, monkeypatch):
    wheelhouse = tmp_path / 'wheelhouse'
    wheel(wheelhouse, requires=['hg-store-required>=1'])
    wheel(wheelhouse, name='hg-store-required')
    monkeypatch.setenv('HG_HARNESS_WHEELHOUSE', str(wheelhouse))
    (candidate / 'requirements.lock').write_text('hg-store-fixture==1.0\n')
    with pytest.raises(runtime.RuntimeUnavailable, match='transitive'):
        runtime.prepare(candidate)
    assert not list((candidate.parent / 'store').glob('*/ready.json'))
    (candidate / 'requirements.lock').write_text('hg-store-fixture==1.0\nhg-store-required==1.0\n')
    receipt = runtime.prepare(candidate)
    manifest = json.loads(Path(receipt['identity']['artifact']['manifest_path']).read_bytes())
    assert manifest['packages'] == {'hg-store-fixture': '1.0', 'hg-store-required': '1.0'}


def test_source_distribution_build_hooks_are_not_run(candidate, tmp_path, monkeypatch):
    wheelhouse = tmp_path / 'wheelhouse'
    wheelhouse.mkdir()
    path = wheelhouse / 'hg_store_fixture-1.0.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        data = b"raise SystemExit('BUILD_HOOK_EXECUTED')\n"
        info = tarfile.TarInfo('hg_store_fixture-1.0/setup.py')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    monkeypatch.setenv('HG_HARNESS_WHEELHOUSE', str(wheelhouse))
    (candidate / 'requirements.lock').write_text('hg-store-fixture==1.0\n')
    with pytest.raises(runtime.RuntimeUnavailable) as failure:
        runtime.prepare(candidate)
    assert 'BUILD_HOOK_EXECUTED' not in str(failure.value)
    assert not list((candidate.parent / 'store').glob('*/ready.json'))


def test_source_mutation_during_build_is_refused_before_publication(candidate, monkeypatch):
    original = runtime._builder
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        if 'pip' in args[0]:
            (candidate / 'agent.py').write_text('print(2)\n')
        return result
    monkeypatch.setattr(runtime, '_builder', change)
    with pytest.raises(runtime.RuntimeUnavailable, match='source changed'):
        runtime.prepare(candidate)
    assert not list((candidate.parent / 'store').glob('*/ready.json'))
    assert not list((candidate.parent / 'store').glob('*/instances/build-*'))


def test_builder_environment_has_no_role_credentials_or_ambient_python_configuration(monkeypatch):
    for name in ('OPENAI_API_KEY', 'HG_AGENT_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'PYTHONHOME', 'PYTHONPATH'):
        monkeypatch.setenv(name, 'must-not-enter-builder')
    environment = runtime._environment('/private/neutral')
    assert all('must-not-enter-builder' not in value for value in environment.values())
    assert environment['UV_PYTHON_DOWNLOADS'] == 'never' and environment['UV_NO_CONFIG'] == '1'


@pytest.mark.parametrize('allow_downloads', [False, True])
def test_only_online_builder_receives_proxy_variables(tmp_path, monkeypatch, allow_downloads):
    for key in runtime._PROXY_KEYS:
        monkeypatch.setenv(key, 'http://user:secret@proxy.invalid:1234')
    monkeypatch.setenv('OPENAI_API_KEY', 'private-model-key')
    monkeypatch.setattr(runtime.shutil, 'which', lambda *args, **kwargs: '/bin/bwrap')
    run = Mock(return_value='')
    monkeypatch.setattr(runtime, '_run', run)
    instance = tmp_path / 'instance'
    instance.mkdir()
    runtime._builder(['/mock/uv'], instance=instance, uv=Path('/mock/uv'),
                     base={'base_prefix': '/usr', 'base_exec_prefix': '/usr'},
                     cache=tmp_path / 'cache', allow_downloads=allow_downloads, timeout_s=1)
    env = run.call_args.kwargs['env']
    assert all((key in env) == allow_downloads for key in runtime._PROXY_KEYS)
    assert 'OPENAI_API_KEY' not in env
    assert all(key not in runtime._environment(tmp_path) for key in runtime._PROXY_KEYS)
    assert ('--unshare-net' in run.call_args.args[0]) != allow_downloads


def test_preparation_error_redacts_proxy_credentials(tmp_path):
    proxy = 'http://synthetic-user:synthetic-secret@proxy.invalid:1234'
    env = runtime._environment(tmp_path)
    env['HTTPS_PROXY'] = proxy
    code = "import sys; print(sys.argv[1], file=sys.stderr); print('http://other:password@host/', file=sys.stderr); sys.exit(1)"
    with pytest.raises(runtime.RuntimeUnavailable) as exc:
        runtime._run([sys.executable, '-I', '-S', '-c', code, proxy], cwd=tmp_path, env=env, timeout_s=2)
    assert proxy not in str(exc.value)
    assert 'synthetic-secret' not in str(exc.value)
    assert 'other:password' not in str(exc.value)


def test_preparation_timeout_kills_its_process_group(tmp_path):
    child = tmp_path / 'child.pid'
    code = f"import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path({str(child)!r}).write_text(str(p.pid)); time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired):
        runtime._run([sys.executable, '-I', '-S', '-c', code], cwd=tmp_path,
                     env=runtime._environment(tmp_path), timeout_s=2)
    pid = int(child.read_text())
    for _ in range(20):
        status = Path(f'/proc/{pid}/stat')
        try:
            state = status.read_text().split()[2]
        except (FileNotFoundError, ProcessLookupError):
            break
        if state == 'Z':
            break
        time.sleep(.01)
    else:
        pytest.fail('timed-out preparation left a live child')


def test_configuration_cli_is_offline_and_list_only_admits(candidate):
    command = [sys.executable, '-B', str(ROOT / 'tools/configure_harness.py'), '--harness', str(candidate), '--json']
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    first = subprocess.run([*command, '--list'], cwd=ROOT, env=env, capture_output=True, text=True)
    assert first.returncode == 1 and json.loads(first.stdout)['status'] == 'invalid'
    assert not (candidate.parent / 'store').exists()
    configured = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    assert configured.returncode == 0, configured.stderr
    receipt = json.loads(configured.stdout)
    assert receipt['offline'] and receipt['installed']
    ready = next((candidate.parent / 'store').glob('*/ready.json'))
    original = ready.read_bytes(), ready.stat().st_mtime_ns
    listed = subprocess.run([*command, '--list'], cwd=ROOT, env=env, capture_output=True, text=True)
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)['identity'] == receipt['identity']
    assert (ready.read_bytes(), ready.stat().st_mtime_ns) == original
    refused = subprocess.run([*command, '--dataset', 'demo'], cwd=ROOT, env=env, capture_output=True, text=True)
    assert refused.returncode == 2 and 'native interpreter' in refused.stderr
