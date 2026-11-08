"""Synthetic preparation boundary controls; never execute CostBench episodes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]


def controller():
    path = ROOT / 'data/datasets/costbench/prepare_controller.py'
    spec = importlib.util.spec_from_file_location('prepare_boundary', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name', ['costbench', 'costbench_dynamic'])
def test_help_in_empty_platform(name, tmp_path):
    # -S removes site-packages. No yaml/numpy/pydantic dependency is needed.
    code = "import sys;sys.path.insert(0,sys.argv[1]);import runpy;runpy.run_module(sys.argv[2],run_name='__main__')"
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(ROOT),
                             'tools.prepare_' + name, '--help'], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--install' in result.stdout


def test_publish_never_replaces_existing_or_late_claimant(tmp_path):
    module = controller()
    stage = tmp_path / 'stage'
    stage.mkdir()
    (stage / 'manifest.json').write_text('new')
    final = tmp_path / 'final'
    final.mkdir()
    (final / 'evidence').write_text('old')
    with pytest.raises(FileExistsError):
        module.publish(stage, final)
    assert (final / 'evidence').read_text() == 'old'
    assert (stage / 'manifest.json').read_text() == 'new'
    target = tmp_path / 'fresh'
    module.publish(stage, target)
    assert (target / 'manifest.json').read_text() == 'new'


def test_source_guard_rejects_unlisted_and_modified_native_source(tmp_path):
    module = controller()
    guard = module.SourceGuard(tmp_path)
    with pytest.raises(ImportError, match='unlisted'):
        guard.find_spec('env.actor_injected')
    env = tmp_path / 'env'
    env.mkdir()
    (env / '__init__.py').write_text('raise RuntimeError("actor")')
    with pytest.raises(ImportError, match='digest'):
        guard.find_spec('env')


def test_worker_admits_before_importing_benchmark_libraries(tmp_path, monkeypatch):
    module = controller()
    import harnessgrad.dataset_runtime as runtime
    events = []
    def reject(binding, role, python):
        events.append((role, python))
        raise ValueError('rejected binding')
    monkeypatch.setattr(runtime, 'admit_binding', reject)
    path = tmp_path / 'request.json'
    path.write_text(json.dumps({'binding': {}}))
    with pytest.raises(ValueError, match='rejected binding'):
        module.worker(path)
    assert events == [('service', sys.executable)]
    assert 'env.settings' not in sys.modules


@pytest.mark.parametrize('dynamic', [False, True])
def test_controller_publication_and_clean_environment(tmp_path, monkeypatch, dynamic):
    module = controller()
    import harnessgrad.dataset_runtime as runtime
    monkeypatch.setattr(module, 'validate_output', lambda *args, **kwargs: None)
    selected = {'python': '/synthetic/artifact/bin/python'}
    admitted = []
    monkeypatch.setattr(runtime, 'resolve_execution', lambda name: selected)
    monkeypatch.setattr(runtime, 'bind', lambda artifact, role: artifact)
    monkeypatch.setattr(runtime, 'admit_binding', lambda binding, role: admitted.append(binding))
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-cross')
    monkeypatch.setenv('HTTPS_PROXY', 'http://local-proxy.invalid:8080')
    monkeypatch.setenv('no_proxy', 'localhost,127.0.0.1')
    monkeypatch.setenv('PYTHONPATH', '/actor/shadow')
    monkeypatch.setenv('COSTBENCH_TRAVEL_CONFIG', '/actor/config')
    root = tmp_path / 'catalog'
    root.mkdir()
    (root / 'static-evidence').write_text('preserve')
    (root / 'source.tar.gz').write_text('raw source remains')
    def child(command, **kwargs):
        assert command[:3] == [selected['python'], '-I', '-B']
        assert kwargs['env']['HTTPS_PROXY'] == 'http://local-proxy.invalid:8080'
        assert kwargs['env']['no_proxy'] == 'localhost,127.0.0.1'
        assert not any(key in kwargs['env'] for key in ('OPENAI_API_KEY', 'PYTHONPATH', 'COSTBENCH_TRAVEL_CONFIG'))
        request = json.loads(Path(command[-1]).read_text())
        pending = Path(request['output']) if dynamic else Path(request['root']) / 'prepared-v1'
        pending.mkdir(parents=True)
        (pending / 'manifest.json').write_text('{"synthetic":true}')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(module.subprocess, 'run', child)
    assert module.main(['--root', str(root)], dynamic=dynamic) == 0
    final = root / ('dynamic-prepared-v1' if dynamic else 'prepared-v1')
    assert json.loads((final / 'manifest.json').read_text()) == {'synthetic': True}
    assert len(admitted) == 2
    with pytest.raises(FileExistsError):
        module.main(['--root', str(root)], dynamic=dynamic)
    assert (root / 'static-evidence').read_text() == 'preserve'
    assert (root / 'source.tar.gz').read_text() == 'raw source remains'


def test_dangling_prepared_target_refuses_before_worker_or_reservation(tmp_path, monkeypatch):
    module = controller()
    from harnessgrad import dataset_runtime
    selected = {'python': '/synthetic/python'}
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: selected)
    monkeypatch.setattr(dataset_runtime, 'bind', lambda artifact, role: artifact)
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *args, **kwargs: selected)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must not start worker'))
    root = tmp_path / 'catalog'
    root.mkdir()
    target = root / 'prepared-v1'
    target.symlink_to(root / 'retained-missing-payload')
    with pytest.raises(FileExistsError):
        module.main(['--root', str(root)])
    assert target.is_symlink()
    assert list(root.iterdir()) == [target]


def test_failed_attempt_is_reserved_and_preserved(tmp_path, monkeypatch):
    module = controller()
    import harnessgrad.dataset_runtime as runtime
    monkeypatch.setattr(runtime, 'resolve_execution', lambda name: {'python': '/synthetic/python'})
    monkeypatch.setattr(runtime, 'bind', lambda artifact, role: artifact)
    monkeypatch.setattr(runtime, 'admit_binding', lambda binding, role: None)
    monkeypatch.setattr(module.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 7))
    root = tmp_path / 'catalog'
    assert module.main(['--root', str(root)]) == 7
    claim = root / '.prepared-v1.prepare-reservation'
    requests = list(claim.glob('attempt-*/request.json'))
    assert len(requests) == 1
    assert not (root / "prepared-v1").exists()
    with pytest.raises(FileExistsError):
        module.main(['--root', str(root)])
    assert requests[0].is_file()


def test_actual_artifact_synthetic_worker_control(tmp_path):
    """Opt-in real artifact, synthetic engine only; never a native audit result."""
    import os
    store = os.environ.get('HG_COSTBENCH_PREPARE_CONTROL_RUNTIME_ROOT')
    if not store:
        pytest.skip('explicit task-owned actual artifact cache required')
    from harnessgrad.dataset_runtime import resolve_execution, bind, admit_binding
    binding = bind(resolve_execution('costbench', root=store), 'service')
    admit_binding(binding, 'service')
    assets = tmp_path / 'fixture/data/datasets/costbench'
    assets.mkdir(parents=True)
    (assets.parent / 'costbench_dynamic').mkdir()
    original = ROOT / 'data/datasets/costbench'
    (assets / 'prepare_controller.py').write_bytes((original / 'prepare_controller.py').read_bytes())
    (assets / 'source-files.json').write_bytes((original / 'source-files.json').read_bytes())
    for name in ('costbench.py', 'costbench_dynamic.py'):
        (assets.parent / name).write_bytes((original.parent / name).read_bytes())
    (assets / 'prepare_worker.py').write_text('''import json, sys
from pathlib import Path
def prepare(root, splits, source_root=None):
    import yaml, numpy, pydantic
    Path(root, 'synthetic-control.json').write_text(json.dumps({'synthetic': True, 'python':sys.executable, 'isolated':sys.flags.isolated, 'yaml':yaml.__file__, 'numpy':numpy.__file__, 'pydantic':pydantic.__file__}))
''')
    spec = importlib.util.spec_from_file_location('synthetic_controller', assets / 'prepare_controller.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    request = {'binding': binding, 'source_sha256': module.source_hash(), 'root': str(tmp_path),
               'source_root': str(tmp_path), 'dynamic': False, 'splits': ['test']}
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request))
    launch = "import sys;sys.path.insert(0,sys.argv[1]);p=sys.argv[2];d={'__file__':p,'__name__':'fixture'};exec(compile(open(p,'rb').read(),p,'exec'),d);d['worker'](sys.argv[3])"
    command = [binding['python'], '-I', '-B', '-c', launch, str(ROOT), str(assets / 'prepare_controller.py'), str(path)]
    result = subprocess.run(command, cwd=tmp_path, env={'PYTHON_DOTENV_DISABLED': '1'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    admit_binding(binding, 'service')
    assert module.source_hash() == request['source_sha256']
    evidence = json.loads((tmp_path / 'synthetic-control.json').read_text())
    assert evidence['synthetic'] and evidence['isolated']
    assert evidence['python'] == binding['python']
    assert all(evidence[name].startswith(binding['path']) for name in ('yaml', 'numpy', 'pydantic'))
    request['source_sha256'] = '0' * 64
    path.write_text(json.dumps(request))
    rejected = subprocess.run(command, cwd=tmp_path, env={}, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert 'source changed before launch' in rejected.stderr


def test_post_worker_admission_failure_prevents_publication(tmp_path, monkeypatch):
    module = controller()
    import harnessgrad.dataset_runtime as runtime
    monkeypatch.setattr(runtime, 'resolve_execution', lambda name: {'python': '/synthetic/python'})
    monkeypatch.setattr(runtime, 'bind', lambda artifact, role: artifact)
    calls = []
    def admit(binding, role):
        calls.append(role)
        if len(calls) == 2:
            raise ValueError('artifact changed after worker')
    monkeypatch.setattr(runtime, 'admit_binding', admit)
    def child(command, **kwargs):
        request = json.loads(Path(command[-1]).read_text())
        prepared = Path(request['root']) / 'prepared-v1'
        prepared.mkdir()
        (prepared / 'manifest.json').write_text('{}')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(module.subprocess, 'run', child)
    root = tmp_path / 'catalog'
    with pytest.raises(ValueError, match='artifact changed'):
        module.main(['--root', str(root)])
    assert not (root / 'prepared-v1').exists()
    assert len(list(root.glob('.prepared-v1.prepare-reservation/attempt-*/data/prepared-v1/manifest.json'))) == 1


def test_source_guard_ignores_stale_native_bytecode(tmp_path):
    import py_compile
    import types
    module = controller()
    env = tmp_path / 'env'
    env.mkdir()
    path = env / '__init__.py'
    path.write_text('raise RuntimeError("stale injected bytecode")')
    py_compile.compile(str(path), doraise=True)
    # The original pinned package initializer is empty. Source-only execution
    # must consume those exact bytes despite a competing cached executable.
    path.write_bytes(b'')
    guard = module.SourceGuard(tmp_path)
    spec = guard.find_spec('env')
    native = types.ModuleType('env')
    native.__spec__ = spec
    guard.exec_module(native)
    assert '__builtins__' in native.__dict__


def synthetic_catalog(module, pending, source_root, monkeypatch):
    """Author-owned tiny fixture; its expected digest is never a release pin."""
    import hashlib
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value, sort_keys=True).encode()
        path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()
    fixture_assets = source_root.parent / 'synthetic-assets'
    fixture_assets.mkdir(exist_ok=True)
    write(fixture_assets / 'source-files.json', {})
    monkeypatch.setattr(module, 'ASSETS', fixture_assets)
    monkeypatch.setattr(module, 'expected_counts', lambda **kwargs: {'train': 1, 'test': 1})
    manifest = {'revision': 'synthetic-revision', 'archive_sha256': 'synthetic-archive',
                'counts': {'train': 1, 'test': 1}, 'sources': {}, 'tasks': []}
    results = []
    for split in ('train', 'test'):
        relative = 'released/' + split + '/queries.json'
        raw_root = source_root / 'CostBench-synthetic-revision'
        query_digest = write(raw_root / relative, [{'synthetic': split}])
        filtered_digest = write(raw_root / relative.replace('queries.json', 'filtered_queries_with_requirements.json'), [])
        search = pending / 'search-spaces' / split
        shard_digest = write(search / 'tiny.json', {'synthetic': True})
        entry = {'path': 'tiny.json', 'sha256': shard_digest}
        index_digest = write(search / 'index.json', {'schema': 'costbench.search-index.v1',
                             'locations': entry, 'empty': entry, 'location_information': {}})
        manifest['sources'][split] = {'path': relative, 'sha256': query_digest,
                                    'filtered_sha256': filtered_digest, 'search_space_sha256': index_digest}
        row = {'task_id': 'synthetic/' + split, 'split': split, 'path': 'tasks/' + split}
        for name in ('public', 'private'):
            row[name + '_sha256'] = write(pending / 'prepared-v1' / row['path'] / (name + '.json'), {'synthetic': name})
        receipt = {'task_id': row['task_id'], 'passed': True}
        write(pending / 'prepared-v1' / row['path'] / 'oracle.json', receipt)
        manifest['tasks'].append(row)
        results.append(receipt)
    manifest['gold_audit_sha256'] = write(pending / 'gold-audit.json', {
        'revision': 'synthetic-revision', 'total': 2, 'passed': 2, 'results': results})
    expected = write(pending / 'prepared-v1/manifest.json', manifest)
    monkeypatch.setattr(module, 'adapter_constants', lambda dynamic=False: {
        'REVISION': 'synthetic-revision', 'ARCHIVE_SHA256': 'synthetic-archive', 'MANIFEST_SHA256': expected})
    return manifest


@pytest.mark.parametrize('damage', [None, 'wrongdigest', 'missing-private', 'missing-shard'])
def test_independent_manifest_and_payload_gate_before_publication(tmp_path, monkeypatch, damage):
    module = controller()
    import harnessgrad.dataset_runtime as runtime
    monkeypatch.setattr(runtime, 'resolve_execution', lambda name: {'python': '/synthetic/python'})
    monkeypatch.setattr(runtime, 'bind', lambda artifact, role: artifact)
    monkeypatch.setattr(runtime, 'admit_binding', lambda binding, role: None)
    monkeypatch.setattr(module, 'source_hash', lambda: 'synthetic-source-control')
    root = tmp_path / 'catalog'
    root.mkdir()
    (root / 'source.tar.gz').write_text('original raw archive')
    def child(command, **kwargs):
        request = json.loads(Path(command[-1]).read_text())
        pending = Path(request['root'])
        synthetic_catalog(module, pending, root, monkeypatch)
        if damage == 'wrongdigest':
            with (pending / 'prepared-v1/manifest.json').open('ab') as stream:
                stream.write(b'\n')
        elif damage == 'missing-private':
            (pending / 'prepared-v1/tasks/train/private.json').unlink()
        elif damage == 'missing-shard':
            (pending / 'search-spaces/train/tiny.json').unlink()
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(module.subprocess, 'run', child)
    if damage:
        with pytest.raises(ValueError):
            module.main(['--root', str(root)])
        assert not (root / 'prepared-v1').exists()
        assert not (root / 'search-spaces').exists()
    else:
        assert module.main(['--root', str(root)]) == 0
        assert (root / 'prepared-v1/manifest.json').is_file()
        assert (root / 'search-spaces/train/tiny.json').is_file()
    assert (root / 'source.tar.gz').read_text() == 'original raw archive'
    assert (root / 'CostBench-synthetic-revision/released/train/queries.json').is_file()


def test_partial_static_is_retained_diagnostic_without_normal_readiness(tmp_path, monkeypatch, capsys):
    module = controller()
    import harnessgrad.dataset_runtime as runtime
    monkeypatch.setattr(runtime, 'resolve_execution', lambda name: {'python': '/synthetic/python'})
    monkeypatch.setattr(runtime, 'bind', lambda artifact, role: artifact)
    monkeypatch.setattr(runtime, 'admit_binding', lambda binding, role: None)
    monkeypatch.setattr(module, 'validate_output', lambda *args, **kwargs: None)
    root = tmp_path / 'catalog'
    root.mkdir()
    (root / 'source.tar.gz').write_text('preserve archive')
    (root / 'prepared-v1').mkdir()
    (root / 'prepared-v1/old-evidence').write_text('preserve catalog')
    def child(command, **kwargs):
        request = json.loads(Path(command[-1]).read_text())
        pending = Path(request['root']) / 'prepared-v1'
        pending.mkdir()
        (pending / 'manifest.json').write_text('{"synthetic":true}')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(module.subprocess, 'run', child)
    assert module.main(['--root', str(root), '--splits', 'test']) == 0
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic['diagnostic'] and not diagnostic['published']
    assert (Path(diagnostic['attempt']) / 'data/prepared-v1/manifest.json').is_file()
    assert (root / 'prepared-v1/old-evidence').read_text() == 'preserve catalog'
    assert not (root / 'prepared-v1/manifest.json').exists()
    assert (root / 'source.tar.gz').read_text() == 'preserve archive'


@pytest.mark.parametrize('damage', [None, 'failed-audit', 'wrong-source', 'wrong-count'])
def test_dynamic_smoke_has_its_own_source_inventory_and_audit_gate(tmp_path, monkeypatch, damage):
    import hashlib
    module = controller()
    root = tmp_path / 'catalog'
    root.mkdir()
    static = synthetic_catalog(module, root, root, monkeypatch)
    static_constants = module.adapter_constants()
    modes = ('cost_change', 'steplen_change', 'ban_tool', 'preference_change')
    monkeypatch.setattr(module, 'adapter_constants', lambda dynamic=False: {
        **static_constants, **({'MODES': modes, 'MANIFEST_SHA256': 'not-a-smoke-pin'} if dynamic else {})})
    monkeypatch.setattr(module, 'expected_counts', lambda **kwargs: {'train': 6, 'test': 6})
    pending = tmp_path / 'smoke-attempt'
    def write(relative, value):
        path = pending / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value, sort_keys=True).encode()
        path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()
    tasks, results = [], []
    for split in ('train', 'test'):
        for mode in modes:
            for domain in ('location', 'transportation', 'accommodation', 'attraction', 'dining', 'shopping'):
                relative = '/'.join(('tasks', split, mode, domain))
                row = {'task_id': 'synthetic/' + relative, 'split': split, 'mode': mode, 'domain': domain, 'path': relative}
                for name in ('public', 'private'):
                    row[name + '_sha256'] = write(relative + '/' + name + '.json', {'synthetic': True})
                receipt = {'task_id': row['task_id'], 'passed': True}
                write(relative + '/oracle.json', receipt)
                tasks.append(row)
                results.append(receipt)
    if damage == 'failed-audit':
        results[0]['passed'] = False
    audit_digest = write('gold-audit.json', {'revision': static['revision'], 'smoke': True,
                                           'total': 48, 'passed': 48, 'results': results})
    manifest = {'revision': static['revision'], 'archive_sha256': static['archive_sha256'],
                'counts': {'train': 6, 'test': 6}, 'sources': static['sources'], 'tasks': tasks,
                'modes': list(modes), 'static_manifest_sha256': static_constants['MANIFEST_SHA256'],
                'gold_audit_sha256': audit_digest}
    if damage == 'wrong-source':
        manifest['sources']['train']['sha256'] = '0' * 64
    if damage == 'wrong-count':
        manifest['counts']['test'] = 5
    write('manifest.json', manifest)
    if damage:
        with pytest.raises(ValueError):
            module.validate_output(pending, root, dynamic=True, smoke=True)
    else:
        module.validate_output(pending, root, dynamic=True, smoke=True)
        with pytest.raises(ValueError, match='independent released'):
            module.validate_output(pending, root, dynamic=True, smoke=False)


def test_release_manifest_pins_are_read_from_original_adapters():
    module = controller()
    assert module.adapter_constants()['MANIFEST_SHA256'] == '7bcc4ba0bc653229b5536f625d797217b3b48f31764314d9d2a397e2e8332587'
    assert module.adapter_constants(True)['MANIFEST_SHA256'] == 'a424801c3d156bfc9527534e715e40ed02cd812889ae0d1c711c9d8c792e4c14'


def test_local_synthetic_proxy_survives_clean_environment_without_api_keys(tmp_path, monkeypatch):
    """A localhost proxy serves a tiny fake URL; no upstream request is made."""
    import http.server
    import threading
    module = controller()
    observations = []
    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            observations.append((self.path, bool(self.headers.get('Proxy-Authorization'))))
            body = b'synthetic proxy response'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proxy = 'http://synthetic-user:synthetic-password@127.0.0.1:' + str(server.server_port)
        for key in module.PROXY_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv('HTTP_PROXY', proxy)
        monkeypatch.setenv('NO_PROXY', '')
        monkeypatch.setenv('HF_TOKEN', 'must-stay-in-parent')
        monkeypatch.setenv('OPENAI_API_KEY', 'must-stay-in-parent')
        env = module.child_environment(tmp_path)
        assert 'HF_TOKEN' not in env and 'OPENAI_API_KEY' not in env
        code = "import urllib.request;print(urllib.request.urlopen('http://synthetic.invalid/tiny-control',timeout=5).read().decode())"
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code], env=env,
                                cwd=tmp_path, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, module.redact_network_credentials(result.stderr, env)
        assert result.stdout.strip() == 'synthetic proxy response'
        assert observations == [('http://synthetic.invalid/tiny-control', True)]
        diagnostic = module.redact_network_credentials('proxy failed ' + proxy + ' synthetic-user synthetic-password', env)
        assert 'synthetic-user' not in diagnostic and 'synthetic-password' not in diagnostic
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_failed_worker_proxy_diagnostics_are_redacted(tmp_path, monkeypatch, capsys):
    module = controller()
    import harnessgrad.dataset_runtime as runtime
    monkeypatch.setattr(runtime, 'resolve_execution', lambda name: {'python': '/synthetic/python'})
    monkeypatch.setattr(runtime, 'bind', lambda artifact, role: artifact)
    monkeypatch.setattr(runtime, 'admit_binding', lambda binding, role: None)
    proxy = 'http://private-network-user:private-network-password@localhost:1'
    monkeypatch.setenv('HTTPS_PROXY', proxy)
    monkeypatch.setattr(module.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(
        command, 4, stdout='failed: ' + proxy, stderr='private-network-password'))
    assert module.main(['--root', str(tmp_path / 'catalog')]) == 4
    captured = capsys.readouterr()
    assert 'private-network-user' not in captured.out + captured.err
    assert 'private-network-password' not in captured.out + captured.err
    assert '[redacted proxy credential]' in captured.out


@pytest.mark.parametrize('username,password', [('u', '4'), ('4', 'u'), ('1', '4')])
def test_short_proxy_credentials_do_not_rewrite_public_json_progress(username, password):
    module = controller()
    proxy = 'http://' + username + ':' + password + '@proxy.invalid:8080'
    env = {'HTTPS_PROXY': proxy}
    records = [
        {'completed': 4, 'total': 4000, 'manifest_sha256': 'a4b'},
        {'split': 'train', 'completed': 4, 'total': 4000, 'passed': True},
        {'total': 4, 'passed': 4, 'manifest_sha256': 'a4b' * 21 + '4'},
        {'split': 'test', 'mode': 'ban_tool', 'completed': 4, 'total': 4000},
        {'revision': module.adapter_constants()['REVISION'], 'total': 4, 'passed': 4,
         'tcr_passed': 4, 'counts': {'train': 4, 'test': 0},
         'protocol': {'refinement_level': 2, 'ban_longest_tool': True, 'min_atomic_cost': 15,
                      'max_atomic_cost': 25, 'noise_std': 0.1, 'max_tool_steps': 20,
                      'tool_creation_seed': 42, 'use_blocker': False}},
    ]
    original = ''.join(json.dumps(record) + '\n' for record in records)
    redacted = module.redact_network_credentials(original, env)
    assert redacted == original
    assert [json.loads(line) for line in redacted.splitlines()] == records
    errors = ['proxy failed ' + proxy, 'auth username=' + username + ' password=' + password,
              json.dumps({'error': 'proxy ' + proxy, 'auth': username + ':' + password})]
    for error in errors:
        safe = module.redact_network_credentials(error, env)
        assert proxy not in safe
        assert 'username=' + username not in safe
        assert 'password=' + password not in safe
        assert username + ':' + password not in safe
        assert '[redacted proxy credential]' in safe
