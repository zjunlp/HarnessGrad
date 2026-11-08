"""Synthetic preparation orchestration; never execute upstream tasks or repin data."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from harnessgrad import dataset_runtime
from tools import _tau_prepare as control


def test_worker_admits_before_loading_source_and_checks_exact_python(monkeypatch):
    loaded = []
    request = {'dataset': 'tau2_bench', 'operation': 'prepare', 'options': {},
               'binding': {'dataset': 'tau2_bench', 'python': sys.executable},
               'source_identity': control.source_identity()}
    def denied(*args, **kwargs):
        raise dataset_runtime.DatasetRuntimeUnavailable('changed runtime')
    monkeypatch.setattr(dataset_runtime, 'admit_binding', denied)
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable):
        control.worker(request, loader=lambda name: loaded.append(name))
    assert not loaded
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *a, **k: {'runtime_id': 'a' * 64})
    request['binding']['python'] = '/different/venv/bin/python'
    with pytest.raises(ValueError, match='exact selected interpreter'):
        control.worker(request, loader=lambda name: loaded.append(name))
    assert not loaded


def test_worker_readonly_check_rechecks_after_source_operation(monkeypatch):
    calls = []
    def admit(*args, **kwargs):
        calls.append('admit')
        if len(calls) == 3:
            raise dataset_runtime.DatasetRuntimeUnavailable('package drift')
        return {'runtime_id': 'a' * 64}
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admit)
    def operation(op, options, runtime):
        calls.append('read-only-check')
        return {'tasks': 1}
    request = {'dataset': 'tau3_bench', 'operation': 'check', 'options': {},
               'binding': {'dataset': 'tau3_bench', 'python': sys.executable},
               'source_identity': control.source_identity()}
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='drift'):
        control.worker(request, loader=lambda name: SimpleNamespace(_managed_operation=operation))
    assert calls == ['admit', 'read-only-check', 'admit']


def test_atomic_publish_refuses_concurrent_empty_target(tmp_path):
    staged = tmp_path / 'staged'
    staged.mkdir()
    (staged / 'complete').write_text('synthetic')
    target = tmp_path / 'target'
    target.mkdir()
    identity = target.stat().st_ino
    with pytest.raises(ValueError, match='appeared'):
        control.publish(staged, target)
    assert target.stat().st_ino == identity and not list(target.iterdir())
    assert (staged / 'complete').read_text() == 'synthetic'


def test_environment_has_no_model_or_active_python_configuration(monkeypatch):
    for key in ('HG_ENV_API_KEY', 'HG_ENV_BASE_URL', 'HG_AGENT_API_KEY', 'OPENAI_API_KEY',
                'ANTHROPIC_API_KEY', 'PYTHONPATH', 'VIRTUAL_ENV'):
        monkeypatch.setenv(key, 'PRIVATE-CREDENTIAL-SENTINEL')
    assert 'PRIVATE-CREDENTIAL-SENTINEL' not in json.dumps(control.environment())


@pytest.fixture(params=[('tau2_bench', '3.13'), ('tau3_bench', '3.12')])
def native(request, tmp_path, monkeypatch):
    name, version = request.param
    if os.environ.get('HG_TAU_PREPARATION_NATIVE_CONTROLS') != '1':
        pytest.skip('set HG_TAU_PREPARATION_NATIVE_CONTROLS=1 after explicit artifact preparation')
    store = os.environ.get('HG_' + name.upper() + '_PREPARATION_RUNTIME_ROOT')
    if store:
        monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', store)
    runtime = dataset_runtime.resolve_execution(name)
    stub = tmp_path / 'synthetic-worker.py'
    stub.write_text('''import json,os,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, ROOT)
import types,hashlib
controller_path=Path(ROOT)/'tools/_tau_prepare.py'
raw=controller_path.read_bytes()
controller=types.ModuleType('_source_only_synthetic_controller')
controller.__file__=str(controller_path)
controller.__dict__['__executed_source_sha256__']=hashlib.sha256(raw).hexdigest()
exec(compile(raw,str(controller_path),'exec'),controller.__dict__)
worker=controller.worker
request=json.load(sys.stdin)
def prepare(operation,options,runtime):
    assert Path(options['source']).read_text() == 'synthetic source'
    assert not any(key.startswith(('HG_ENV_','HG_AGENT_','HG_METHOD_','HG_JUDGE_'))
                   or key in {'OPENAI_API_KEY','ANTHROPIC_API_KEY','PYTHONPATH','VIRTUAL_ENV'} for key in os.environ)
    target=Path(options['target'])
    if operation=='prepare':
        target.mkdir()
        (target/'synthetic.txt').write_text('synthetic payload')
    else:
        assert (target/'synthetic.txt').read_text() == 'synthetic payload'
    return {'python':sys.executable,'version':str(sys.version_info.major)+'.'+str(sys.version_info.minor),
            'home':os.environ['HOME'],'cache':os.environ['XDG_CACHE_HOME'],
            'tmp':os.environ['TMPDIR'],'cwd':os.getcwd()}
print(json.dumps(worker(request,loader=lambda name:SimpleNamespace(_managed_operation=prepare))))
'''.replace('ROOT', repr(str(control.ROOT))))
    source = tmp_path / 'source-stub'
    source.write_text('synthetic source')
    return name, version, runtime, stub, source


def test_native_versions_credentials_and_readonly_check(native, tmp_path, monkeypatch):
    name, version, runtime, stub, source = native
    monkeypatch.setenv('HG_ENV_API_KEY', 'PRIVATE-CREDENTIAL-SENTINEL')
    monkeypatch.setenv('OPENAI_API_KEY', 'PRIVATE-CREDENTIAL-SENTINEL')
    target = tmp_path / 'payload'
    options = {'target': str(target), 'source': str(source)}
    result = control.run(name, 'prepare', options, program=stub)
    assert result['python'] == runtime['python'] and result['version'] == version
    assert Path(result['home']) != Path.home()
    assert Path(result['home']).parent == Path(result['cwd'])
    assert Path(result['cache']).parent == Path(result['cwd'])
    assert Path(result['tmp']).parent == Path(result['cwd'])
    assert not Path(result['cwd']).exists()
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (source, target / 'synthetic.txt')}
    assert control.run(name, 'check', options, program=stub)['runtime_id'] == runtime['runtime_id']
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}
    with pytest.raises(ValueError, match='selected managed'):
        control.run(name, 'check', options, runtime_override=tmp_path / 'old-venv', program=stub)


def test_native_parent_post_admission_drift_never_publishes(native, tmp_path, monkeypatch):
    name, _, runtime, stub, source = native
    original = dataset_runtime.admit_binding
    calls = []
    def admit(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise dataset_runtime.DatasetRuntimeUnavailable('post-worker package drift')
        return original(*args, **kwargs)
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admit)
    target = tmp_path / 'payload'
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='post-worker-artifact-admission'):
        control.run(name, 'prepare', {'target': str(target), 'source': str(source)}, program=stub)
    assert not target.exists()
    stage, = tmp_path.glob('.payload-managed-*')
    assert (stage / 'failed.json').is_file()


@pytest.mark.parametrize('name', ['tau2_bench', 'tau3_bench'])
def test_platform_loader_needs_no_optional_packages(name, monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(module, *args, **kwargs):
        if module.split('.')[0] in {'tau2', 'loguru', 'pydantic', 'litellm', 'pandas', 'numpy'}:
            pytest.fail('platform imported optional provider ' + module)
        return original(module, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    assert callable(control.load(name).main)


@pytest.mark.parametrize('name', ['tau2_bench', 'tau3_bench'])
def test_runtime_preparation_is_explicit_and_cannot_be_combined_with_check(name, monkeypatch):
    module = control.load(name)
    calls = []
    monkeypatch.setattr(dataset_runtime, 'prepare_execution', lambda selected: calls.append(selected) or {'prepared': True})
    monkeypatch.setattr(sys, 'argv', ['prepare', '--prepare-runtime'])
    assert module.main() == 0 and calls == [name]
    calls.clear()
    monkeypatch.setattr(sys, 'argv', ['prepare', '--prepare-runtime', '--check-only'])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2 and not calls


def test_supervisor_race_never_replaces_a_new_empty_target(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'path': '/managed/venv'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'python': sys.executable})
    target = tmp_path / 'payload'
    def launch(request, **kwargs):
        stage = Path(request['options']['target'])
        stage.mkdir()
        (stage / 'complete').write_text('synthetic')
        target.mkdir()
        return {'tasks': 1}
    monkeypatch.setattr(control, 'launch', launch)
    with pytest.raises(ValueError, match='staging retained'):
        control.run('tau2_bench', 'prepare', {'target': str(target)})
    assert target.is_dir() and not list(target.iterdir())
    stage, = tmp_path.glob('.payload-managed-*')
    assert (stage / 'failed.json').is_file()


def test_reuse_never_creates_a_missing_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'path': '/managed/venv'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'python': sys.executable})
    monkeypatch.setattr(control, 'launch', lambda *a, **k: pytest.fail('reuse launched a creator'))
    target = tmp_path / 'absent'
    with pytest.raises(ValueError, match='never creates or repairs'):
        control.run('tau2_bench', 'prepare', {'target': str(target), 'reuse': True})
    assert not target.exists()


@pytest.fixture
def owned_sources(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / 'owned-platform'
    for relative in control._OWNED:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(control.ROOT / relative, path)
    monkeypatch.setattr(control, 'ROOT', root)
    return root


def test_source_only_loader_ignores_timestamp_valid_pyc(owned_sources, tmp_path):
    import py_compile
    path = owned_sources / 'data/datasets/tau2_bench/prepare.py'
    expected = path.read_bytes()
    stamp = path.stat().st_mtime
    malicious = b"raise RuntimeError('STALE-PYC-SENTINEL')\n"
    path.write_bytes(malicious + b'#' * (len(expected) - len(malicious)))
    os.utime(path, (stamp, stamp))
    py_compile.compile(str(path), doraise=True)
    path.write_bytes(expected)
    os.utime(path, (stamp, stamp))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_unsafe_cached_prepare', path)
    with pytest.raises(RuntimeError, match='STALE-PYC-SENTINEL'):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    module = control.load('tau2_bench')
    assert callable(module._managed_operation)
    # The actual bootstrap call graph must also bypass a valid cached helper.
    helper = owned_sources / 'data/datasets/tau2_bench/lib/bootstrap.py'
    original = helper.read_bytes()
    stamp = helper.stat().st_mtime
    helper.write_bytes(malicious + b'#' * (len(original) - len(malicious)))
    os.utime(helper, (stamp, stamp))
    py_compile.compile(str(helper), doraise=True)
    helper.write_bytes(original)
    os.utime(helper, (stamp, stamp))
    assert callable(module._bootstrap_module().validate_bundle)


def test_worker_source_drift_refuses_loading_provider(owned_sources, monkeypatch):
    identity = control.source_identity()
    (owned_sources / 'data/datasets/tau3_bench/prepare.py').write_text('PRIVATE-SOURCE-SENTINEL')
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *a, **k: pytest.fail('dependency admitted before source gate'))
    request = {'source_identity': identity, 'binding': {}, 'dataset': 'tau3_bench', 'operation': 'check'}
    with pytest.raises(control.PreparationSourceChanged) as error:
        control.worker(request, loader=lambda name: pytest.fail('provider loaded after source drift'))
    assert 'PRIVATE-SOURCE-SENTINEL' not in str(error.value)


def test_parent_source_drift_retains_stage_without_publishing(owned_sources, tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'path': '/managed/venv'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'python': sys.executable})
    target = tmp_path / 'payload'
    def launch(request, **kwargs):
        staged = Path(request['options']['target'])
        staged.mkdir()
        (staged / 'complete').write_text('synthetic staged payload')
        source = owned_sources / 'data/datasets/tau2_bench/prepare.py'
        source.write_bytes(source.read_bytes() + b'\n# same-path drift\n')
        return {'tasks': 1}
    monkeypatch.setattr(control, 'launch', launch)
    with pytest.raises(control.PreparationSourceChanged, match='staging retained'):
        control.run('tau2_bench', 'prepare', {'target': str(target)})
    assert not target.exists()
    retained, = tmp_path.glob('.payload-managed-*')
    assert (retained / 'payload/complete').read_text() == 'synthetic staged payload'
    assert (retained / 'source-identity.json').is_file()
    original = retained / 'preparation-sources/data/datasets/tau2_bench/prepare.py'
    assert original.is_file()
    assert b'# same-path drift' not in original.read_bytes()


def test_formal_entry_ignores_stale_controller_cache(owned_sources, monkeypatch):
    import py_compile
    path = owned_sources / 'tools/_tau_prepare.py'
    raw = path.read_bytes()
    stamp = path.stat().st_mtime
    attack = b"raise RuntimeError('CACHED-CONTROLLER-SENTINEL')\n"
    path.write_bytes(attack + b'#' * (len(raw) - len(attack)))
    os.utime(path, (stamp, stamp))
    py_compile.compile(str(path), doraise=True)
    path.write_bytes(raw)
    os.utime(path, (stamp, stamp))
    entry = control.owned_module(owned_sources / 'tools/prepare_tau3_bench.py', '_source_only_tau_entry')
    monkeypatch.setattr(sys, 'argv', ['prepare', '--help'])
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 0


def test_private_worker_environment_cannot_read_parent_sdk_homes(tmp_path, monkeypatch):
    parent_home = tmp_path / 'user-home'
    (parent_home / '.config').mkdir(parents=True)
    (parent_home / '.config/credential').write_text('PRIVATE-HOME-CREDENTIAL')
    monkeypatch.setenv('HOME', str(parent_home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(parent_home / '.config'))
    private = tmp_path / 'worker'
    env = control.environment(private)
    assert Path(env['HOME']) != parent_home
    assert Path(env['XDG_CONFIG_HOME']) != parent_home / '.config'
    assert not list(Path(env['HOME']).iterdir())
    assert 'PRIVATE-HOME-CREDENTIAL' not in json.dumps(env)


def test_worker_post_operation_source_drift_is_rejected(owned_sources, monkeypatch):
    identity = control.source_identity()
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *a, **k: {'runtime_id': 'a' * 64})
    def operation(*args):
        path = owned_sources / 'data/datasets/tau3_bench/lib/bootstrap.py'
        path.write_bytes(path.read_bytes() + b'\n# changed during check\n')
        return {'tasks': 1}
    request = {'dataset': 'tau3_bench', 'operation': 'check', 'options': {},
               'binding': {'dataset': 'tau3_bench', 'python': sys.executable}, 'source_identity': identity}
    with pytest.raises(control.PreparationSourceChanged):
        control.worker(request, loader=lambda name: SimpleNamespace(_managed_operation=operation))


def test_source_drift_readonly_check_never_changes_existing_payload(owned_sources, tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'path': '/managed/venv'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'python': sys.executable})
    target = tmp_path / 'existing'
    target.mkdir()
    (target / 'payload').write_text('original legal payload')
    (target / 'provenance.json').write_text('{"original":true}')
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.iterdir()}
    def launch(request, **kwargs):
        path = owned_sources / 'data/datasets/tau3_bench/prepare.py'
        path.write_bytes(path.read_bytes() + b'\n# same-path drift\n')
        control.verify_sources(request['source_identity'])
    monkeypatch.setattr(control, 'launch', launch)
    with pytest.raises(control.PreparationSourceChanged):
        control.run('tau3_bench', 'check', {'target': str(target)})
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.iterdir()}
    assert not list(tmp_path.glob('.existing-managed-*'))


def test_prepare_adapter_constants_do_not_come_from_cached_pyc(owned_sources):
    import py_compile
    path = owned_sources / 'data/datasets/tau2_bench.py'
    raw = path.read_bytes()
    stamp = path.stat().st_mtime
    attack = b"raise RuntimeError('CACHED-ADAPTER-SENTINEL')\n"
    path.write_bytes(attack + b'#' * (len(raw) - len(attack)))
    os.utime(path, (stamp, stamp))
    py_compile.compile(str(path), doraise=True)
    path.write_bytes(raw)
    os.utime(path, (stamp, stamp))
    assert control.load('tau2_bench').dataset.MANIFEST_SHA256 == '887dae934c5e376daecde58169a1c13f550282dc85f1e7d02a5f5813fd5204bd'


def test_explicit_fetch_keeps_controller_auth_and_normalizes_relative_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HOME', str(tmp_path / 'controller-home'))
    monkeypatch.setenv('HTTPS_PROXY', 'https://controller-auth.example')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'path': '/managed/venv'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'python': sys.executable})
    observed = []
    def fetch(url, archive):
        assert os.environ['HTTPS_PROXY'] == 'https://controller-auth.example'
        assert os.environ['HOME'] == str(tmp_path / 'controller-home')
        assert url.endswith('/' + control.load('tau3_bench').REVISION)
        archive.write_bytes(b'synthetic source archive')
    monkeypatch.setattr(control.urllib.request, 'urlretrieve', fetch)
    def launch(request, **kwargs):
        options = request['options']
        assert options['fetch'] is False
        assert Path(options['archive']).is_absolute()
        assert Path(options['archive']).read_bytes() == b'synthetic source archive'
        assert options['source'] == str(tmp_path / 'relative-source')
        target = Path(options['target'])
        assert target.is_absolute()
        target.mkdir()
        (target / 'synthetic').write_text('complete')
        observed.append(options)
        return {'tasks': 1}
    monkeypatch.setattr(control, 'launch', launch)
    assert control.run('tau3_bench', 'prepare', {'fetch': True, 'target': 'relative-target',
                                               'source': 'relative-source'})['tasks'] == 1
    assert (tmp_path / 'relative-target/synthetic').is_file()
    assert not Path(observed[0]['archive']).exists()


def test_disappearing_source_is_a_source_identity_failure(owned_sources):
    identity = control.source_identity()
    (owned_sources / 'data/datasets/tau2_bench/lib/bootstrap.py').unlink()
    with pytest.raises(control.PreparationSourceChanged, match='unavailable or changed'):
        control.verify_sources(identity)


def test_existing_payload_is_rejected_before_explicit_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'path': '/managed/venv'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'python': sys.executable})
    target = tmp_path / 'existing'
    target.mkdir()
    archive = target / 'original-source.tar.gz'
    archive.write_bytes(b'original archive bytes')
    monkeypatch.setattr(control.urllib.request, 'urlretrieve', lambda *a, **k: pytest.fail('fetch touched an existing payload'))
    with pytest.raises(ValueError, match='already exists'):
        control.run('tau3_bench', 'prepare', {'target': str(target), 'archive': str(archive), 'fetch': True})
    assert archive.read_bytes() == b'original archive bytes'


def test_source_reply_mismatch_preserves_attempt_and_payload_stage(owned_sources, tmp_path, monkeypatch):
    binding = {'python': sys.executable, 'runtime_id': 'a' * 64}
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'path': '/managed/venv'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: binding)
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *a, **k: {})
    def subprocess_run(argv, **kwargs):
        request = json.loads(kwargs['input'])
        staged = Path(request['options']['target'])
        staged.mkdir()
        (staged / 'complete').write_text('synthetic staged payload')
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'runtime_id': binding['runtime_id'],
                                                               'source_identity': {}}))
    monkeypatch.setattr(control.subprocess, 'run', subprocess_run)
    target = tmp_path / 'payload'
    with pytest.raises(control.PreparationSourceChanged, match='staging retained'):
        control.run('tau2_bench', 'prepare', {'target': str(target)})
    assert not target.exists()
    stage, = tmp_path.glob('.payload-managed-*')
    assert (stage / 'payload/complete').is_file()
    attempt = json.loads((stage / 'worker-attempt.json').read_text())
    assert attempt['state'] == 'completed' and attempt['exit_code'] == 0
    assert attempt['source_identity'] == json.loads((stage / 'source-identity.json').read_text())


@pytest.mark.parametrize('failure,expected_phase', [
    ('worker', 'worker-execution'),
    ('artifact', 'post-worker-artifact-admission'),
    ('publication', 'publication'),
])
def test_all_failed_attempts_keep_private_diagnostics_and_old_target(owned_sources, tmp_path, monkeypatch,
                                                                   failure, expected_phase):
    binding = {'python': sys.executable, 'runtime_id': 'a' * 64}
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'path': '/managed/venv'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: binding)
    calls = []
    def admit(*args, **kwargs):
        calls.append(1)
        if failure == 'artifact' and len(calls) == 2:
            raise dataset_runtime.DatasetRuntimeUnavailable('PRIVATE-TASK-SENTINEL')
        return {}
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admit)
    target = tmp_path / 'payload'
    old = tmp_path / 'old-payload'
    old.mkdir()
    (old / 'receipt.json').write_text('{"historical":true}')
    before = (old / 'receipt.json').read_bytes(), (old / 'receipt.json').stat().st_mtime_ns
    def subprocess_run(argv, **kwargs):
        request = json.loads(kwargs['input'])
        stage = Path(request['options']['target'])
        stage.mkdir()
        (stage / 'partial').write_text('partial synthetic payload')
        if failure == 'publication':
            target.mkdir()
            (target / 'receipt.json').write_text('{"concurrent":true}')
        return SimpleNamespace(returncode=7 if failure == 'worker' else 0,
                               stdout=('PRIVATE-TASK-SENTINEL' if failure == 'worker' else
                                       json.dumps({'runtime_id': binding['runtime_id'],
                                                   'source_identity': request['source_identity']})),
                               stderr='PRIVATE-TASK-SENTINEL')
    monkeypatch.setattr(control.subprocess, 'run', subprocess_run)
    with pytest.raises((ValueError, dataset_runtime.DatasetRuntimeUnavailable), match='staging retained') as error:
        control.run('tau2_bench', 'prepare', {'target': str(target)})
    assert 'PRIVATE-TASK-SENTINEL' not in str(error.value)
    stage, = tmp_path.glob('.payload-managed-*')
    assert (stage / 'payload/partial').is_file()
    assert (stage / 'preparation-sources/tools/_tau_prepare.py').is_file()
    assert str(stage) in str(error.value)
    receipt = json.loads((stage / 'failed.json').read_text())
    assert set(receipt) == {'error_type', 'phase'} and receipt['phase'] == expected_phase
    assert 'PRIVATE-TASK-SENTINEL' not in json.dumps(receipt)
    assert json.loads((stage / 'worker-attempt.json').read_text())['exit_code'] == (7 if failure == 'worker' else 0)
    assert (stage / 'worker-stderr.txt').read_text() == 'PRIVATE-TASK-SENTINEL'
    assert before == ((old / 'receipt.json').read_bytes(), (old / 'receipt.json').stat().st_mtime_ns)
    if failure == 'publication':
        assert (target / 'receipt.json').read_text() == '{"concurrent":true}'
        assert not (target / 'partial').exists()
    else:
        assert not target.exists()
