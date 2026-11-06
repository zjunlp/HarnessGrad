"""Portable scorer package controls, using authored bytes and local captured Python.

No released tasks, model calls, dependency downloads or shared installations.
"""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import venv
from types import SimpleNamespace

import pytest

from harnessgrad import native_runtime_store as store
from eval.verification import native_runtime, verifier

ROOT = Path(__file__).resolve().parents[2]


def bind(path, manifest):
    raw = (json.dumps(manifest, sort_keys=True) + '\n').encode()
    path.write_bytes(raw)
    return {'dataset': manifest['dataset'], 'manifest': str(path),
            'sha256': hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def authored(tmp_path):
    sources = tmp_path / 'sources'
    sources.mkdir()
    files = {}
    for target, name, raw in (('/opt/python', 'python', b'authored Python'),
                              ('/lib/loader', 'loader', b'authored loader'),
                              ('/scorer/check.py', 'check.py', b'authored scorer')):
        path = sources / name
        path.write_bytes(raw)
        files[target] = {'source': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                         'size': len(raw), 'entry': 'authored package control'}
    launcher = sources / 'launcher'
    launcher.write_bytes(b'authored launcher')
    legacy = {'schema': 1, 'dataset': 'bird_minidev', 'files': files,
              'launcher': {'path': str(launcher), 'sha256': hashlib.sha256(launcher.read_bytes()).hexdigest()},
              'interpreter': '/opt/python', 'loader': '/lib/loader', 'library_path': ['/lib'],
              'python_flags': ['-I', '-S', '-B'], 'environment': {'PATH': '/opt'},
              'identity': {'machine': platform.machine(), 'kernel': 'authored-build-kernel'}}
    ledger = tmp_path / 'sources.json'
    ledger.write_text(json.dumps(legacy))
    manifest = store.portable_manifest(legacy, launcher.stat().st_size)
    binding = bind(tmp_path / 'manifest.json', manifest)
    source_map = {store.blob_name(entry['sha256']): Path(entry['source']) for entry in files.values()}
    source_map[store.blob_name(legacy['launcher']['sha256'])] = launcher
    archive = tmp_path / 'package.tar.gz'
    store.write_archive(Path(binding['manifest']).read_bytes(), source_map, archive)
    return SimpleNamespace(legacy=legacy, manifest=manifest, binding=binding, ledger=ledger,
                           sources=sources, source_map=source_map, archive=archive,
                           root=tmp_path / 'store')


def test_migration_preserves_execution_and_payload_identity(authored):
    legacy, portable = authored.legacy, authored.manifest
    for name in legacy.keys() - {'schema', 'files', 'launcher'}:
        assert portable[name] == legacy[name]
    assert portable['files'].keys() == legacy['files'].keys()
    for target, entry in legacy['files'].items():
        assert portable['files'][target] == {**entry, 'source': store.blob_name(entry['sha256'])}
    assert portable['launcher']['sha256'] == legacy['launcher']['sha256']
    assert 'path' not in portable['launcher']


def test_all_five_reviewed_manifests_have_independent_portable_pins():
    from data.runtimes import NATIVE_PINS
    assert set(NATIVE_PINS) == store.DATASETS
    for binding in NATIVE_PINS.values():
        _, manifest = store.load_manifest(binding)
        blobs = store.entries(manifest)
        assert manifest['launcher']['source'] in blobs
        assert all(not Path(entry['source']).is_absolute() for entry in manifest['files'].values())
        assert manifest['interpreter'].startswith('/opt/hg-python/bin/')
        assert manifest['identity']['executable'] == manifest['interpreter']
        assert not any(prefix in json.dumps(manifest) for prefix in ('/mnt/', '/home/', '/Users/'))


def test_store_uses_shared_storage_rules(monkeypatch, tmp_path):
    from harnessgrad import paths
    monkeypatch.setenv('HG_DATA_ROOT', str(tmp_path / 'data'))
    monkeypatch.delenv('HG_DATA_NATIVE_RUNTIMES', raising=False)
    assert store.store_root() == tmp_path / 'data/native-runtimes'
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(tmp_path / 'independent'))
    assert store.store_root() == tmp_path / 'independent'
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', '../native-location')
    monkeypatch.chdir(tmp_path)
    assert store.store_root() == paths.main_checkout().parent / 'native-location'


def test_import_export_relocation_and_sealing_keep_the_same_receipt(authored, monkeypatch, tmp_path):
    store.import_package(authored.binding, authored.archive, authored.root)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(authored.root))
    monkeypatch.setattr(native_runtime, '_check_host', lambda runtime: {'policy': 'authored admission only'})
    with native_runtime.admit(authored.binding) as runtime:
        receipt = deepcopy(runtime.receipt)
        assert os.pread(runtime.fds['/opt/python'], 100, 0) == b'authored Python'
    # Runtime admission no longer depends on any original host source or launcher.
    shutil.rmtree(authored.sources)
    moved = tmp_path / 'moved-store'
    authored.root.rename(moved)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(moved))
    with native_runtime.admit(authored.binding) as runtime:
        assert runtime.receipt == receipt
        assert os.pread(runtime.launcher, 100, 0) == b'authored launcher'
    archive = store.export_package(authored.binding, tmp_path / 'export.tar.gz', moved)
    imported = store.import_package(authored.binding, archive, tmp_path / 'imported')
    assert imported['verified'] and imported['sha256'] == receipt['sha256']
    assert store.import_package(authored.binding, archive, tmp_path / 'imported') == imported


@pytest.mark.parametrize('failure', ['missing-blob', 'missing-launcher', 'modified-blob',
                                      'linked-blob', 'linked-blobs-directory', 'manifest'])
def test_incomplete_or_changed_installation_never_uses_host_sources(authored, monkeypatch, failure):
    store.import_package(authored.binding, authored.archive, authored.root)
    package = store.package_path(authored.manifest, authored.binding['sha256'], authored.root)
    entry = (authored.manifest['launcher'] if failure == 'missing-launcher'
             else authored.manifest['files']['/opt/python'])
    path = package / entry['source']
    if failure.startswith('missing'):
        path.unlink()
    elif failure == 'modified-blob':
        path.chmod(0o600)
        path.write_bytes(b'changed runtime')
    elif failure == 'linked-blob':
        path.unlink()
        path.symlink_to(authored.source_map[entry['source']])
    elif failure == 'linked-blobs-directory':
        (package / 'blobs').rename(package / 'original-blobs')
        (package / 'blobs').symlink_to(package / 'original-blobs', target_is_directory=True)
    else:
        marker = package / 'manifest.json'
        marker.chmod(0o600)
        marker.write_text('{}')
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(authored.root))

    def no_execution(*args):
        pytest.fail('invalid package reached execution')

    monkeypatch.setattr(native_runtime, '_check_host', no_execution)
    with pytest.raises(native_runtime.NativeRuntimeUnavailable):
        with native_runtime.admit(authored.binding):
            pytest.fail('invalid package was admitted')


@pytest.mark.parametrize('failure', ['duplicate', 'traversal', 'absolute', 'symlink', 'hardlink',
                                      'device', 'size', 'bytes', 'manifest', 'missing-launcher'])
def test_untrusted_archive_cannot_publish_partial_or_unsafe_packages(authored, tmp_path, failure):
    manifest_raw = Path(authored.binding['manifest']).read_bytes()
    members = [('manifest.json', manifest_raw, tarfile.REGTYPE, '')]
    members += [(name, path.read_bytes(), tarfile.REGTYPE, '') for name, path in authored.source_map.items()]
    if failure == 'duplicate':
        members.append(members[0])
    elif failure in ('traversal', 'absolute'):
        members.append(('../escaped' if failure == 'traversal' else str(tmp_path / 'escaped'), b'', tarfile.REGTYPE, ''))
    elif failure in ('symlink', 'hardlink', 'device'):
        kind = {'symlink': tarfile.SYMTYPE, 'hardlink': tarfile.LNKTYPE, 'device': tarfile.CHRTYPE}[failure]
        members[1] = (members[1][0], b'', kind, '../escaped')
    elif failure in ('size', 'bytes'):
        name, value, kind, link = members[1]
        members[1] = (name, value + b'X' if failure == 'size' else b'X' * len(value), kind, link)
    elif failure == 'manifest':
        members[0] = ('manifest.json', b'X' * len(manifest_raw), tarfile.REGTYPE, '')
    else:
        name = authored.manifest['launcher']['source']
        members = [member for member in members if member[0] != name]
    archive = tmp_path / 'unsafe.tar'
    with tarfile.open(archive, 'w') as tar:
        for name, value, kind, link in members:
            member = tarfile.TarInfo(name)
            member.type, member.linkname = kind, link
            member.size = len(value)
            tar.addfile(member, io.BytesIO(value) if kind == tarfile.REGTYPE else None)
    with pytest.raises(store.StoreError):
        store.import_package(authored.binding, archive, authored.root)
    assert not store.package_path(authored.manifest, authored.binding['sha256'], authored.root).exists()
    assert not (tmp_path / 'escaped').exists()


@pytest.mark.parametrize('locator', ['/host/python', '../escape', 'blobs/' + '0' * 64])
def test_manifest_cannot_select_external_or_unbound_sources(authored, tmp_path, locator):
    authored.manifest['files']['/opt/python']['source'] = locator
    binding = bind(tmp_path / 'bad-manifest.json', authored.manifest)
    with pytest.raises(store.StoreError, match='digest-bound'):
        store.import_package(binding, authored.archive, authored.root)
    assert not authored.root.exists()


@pytest.mark.parametrize('field,value', [('files', []), ('launcher', []),
                                        ('files', {'/opt/python': []}), ('schema', True)])
def test_malformed_inventory_is_refused_before_preparation(authored, tmp_path, field, value):
    authored.manifest[field] = value
    binding = bind(tmp_path / 'malformed.json', authored.manifest)
    with pytest.raises(store.StoreError):
        store.import_package(binding, authored.archive, authored.root)
    assert not authored.root.exists()


def test_export_does_not_overwrite_evidence_or_publish_drifted_payload(authored, tmp_path):
    store.import_package(authored.binding, authored.archive, authored.root)
    output = tmp_path / 'existing.tar'
    output.write_bytes(b'preserve evidence')
    with pytest.raises(FileExistsError):
        store.export_package(authored.binding, output, authored.root)
    assert output.read_bytes() == b'preserve evidence'
    source = authored.source_map[authored.manifest['files']['/opt/python']['source']]
    source.write_bytes(b'changed source')
    output = tmp_path / 'drifted.tar'
    with pytest.raises((store.StoreError, OSError)):
        store.write_archive(Path(authored.binding['manifest']).read_bytes(), authored.source_map, output)
    assert not output.exists()


@pytest.mark.parametrize('method', ['source-map', 'legacy-archive'])
def test_assembly_recovers_exact_pinned_bytes_from_explicit_sources(authored, tmp_path, method):
    moved = tmp_path / 'relocated-sources'
    authored.sources.rename(moved)
    if method == 'source-map':
        result = store.assemble_package(authored.binding, source_manifests=[authored.ledger],
            remaps=[(authored.sources, moved)], root=authored.root)
    else:
        archive = tmp_path / 'legacy.tar'
        with tarfile.open(archive, 'w') as tar:
            for index, path in enumerate(moved.iterdir()):
                tar.add(path, arcname='unrelated-layout/' + str(index), recursive=False)
        result = store.assemble_package(authored.binding, archives=[archive], root=authored.root)
    assert result['verified'] and store.verify_package(authored.binding, authored.root) == result


def test_failed_assembly_never_publishes_or_accepts_another_version(authored):
    next(authored.sources.iterdir()).write_bytes(b'other version')
    with pytest.raises(store.MissingBlobs) as captured:
        store.assemble_package(authored.binding, source_manifests=[authored.ledger], root=authored.root)
    assert captured.value.missing
    assert not store.package_path(authored.manifest, authored.binding['sha256'], authored.root).exists()


def test_explicit_store_inside_checkout_is_refused(authored):
    with pytest.raises(store.StoreError, match='outside'):
        store.import_package(authored.binding, authored.archive, ROOT / 'forbidden-runtime')
    assert not (ROOT / 'forbidden-runtime').exists()


def test_cli_requires_an_independent_pin_for_custom_manifests(authored, capsys):
    from tools.prepare_native_runtime import main
    with pytest.raises(SystemExit) as error:
        main(['verify', '--dataset', 'bird_minidev', '--manifest', authored.binding['manifest']])
    assert error.value.code == 2 and '--sha256' in capsys.readouterr().err
    status = main(['import', '--dataset', 'bird_minidev', '--manifest', authored.binding['manifest'],
                   '--sha256', authored.binding['sha256'], '--archive', str(authored.archive),
                   '--root', str(authored.root)])
    assert status == 0 and json.loads(capsys.readouterr().out)['verified']


@pytest.fixture(scope='module')
def captured_portable(tmp_path_factory):
    work = tmp_path_factory.mktemp('portable-native-capture')
    path = work / 'manifest.json'
    proc = subprocess.run([sys.executable, str(ROOT / 'tools/build_bird_native_runtime.py'),
        '--dataset', 'bird_minidev', '--portable', '--out', str(work / 'capture'),
        '--manifest', str(path), '--store', str(work / 'archives')], cwd=ROOT,
        env={'PATH': os.defpath, 'LANG': 'C', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout)
    binding = {'dataset': 'bird_minidev', 'manifest': str(path), 'sha256': summary['manifest_sha256']}
    assert summary['schema'] == 2
    return binding, Path(summary['payload'])


def test_portable_python_has_no_build_host_prefix(captured_portable, tmp_path, monkeypatch):
    binding, archive = captured_portable
    installed = tmp_path / 'installed'
    store.import_package(binding, archive, installed)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(installed))
    reward = tmp_path / 'verifier'
    reward.mkdir()
    original = str(Path(sys.executable).resolve())
    control = '''import json, pathlib, subprocess, sys, sysconfig
assert sys.prefix == sys.base_prefix == '/opt/hg-python'
assert sys.executable.startswith('/opt/hg-python/bin/')
assert sysconfig.get_path('stdlib').startswith('/opt/hg-python/lib/')
assert not pathlib.Path(sys.argv[-1]).exists()
child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
    "import sys; assert sys.prefix == '/opt/hg-python'; import sqlite3"],
    capture_output=True, text=True, timeout=3)
assert child.returncode == 0, child.stderr
print(json.dumps({'python': sys.executable, 'prefix': sys.prefix, 'child': True}))
'''
    with native_runtime.admit(binding) as runtime:
        proc = runtime.run(['python3', '-c', control, original], workdir=tmp_path,
                           reward_dir=reward, timeout_s=10)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    observed = json.loads(proc.stdout)
    assert observed['prefix'] == '/opt/hg-python' and observed['child']


def test_real_scorer_and_cache_survive_store_move_and_refuse_missing_blob(
        captured_portable, tmp_path, monkeypatch):
    from data.scaffolds.scorer_input import bind as bind_inputs
    from eval.run import runner

    binding, archive = captured_portable
    root = tmp_path / 'installed'
    store.import_package(binding, archive, root)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(root))
    work_root = tmp_path / 'work'
    work_root.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work_root))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    database = tmp_path / 'authored.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('create table numbers (n int)')
        db.executemany('insert into numbers values (?)', [(1,), (2,)])
    spec = {'kind': 'command', 'native_runtime': binding,
        'argv': ['python3', 'check.py', '--db', 'database.sqlite', '--gold', 'gold.sql',
                 '--pred', 'answer.sql', '--reward-file', 'verifier/reward.txt',
                 '--evidence-file', 'verifier/evidence.json'],
        'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
        'inputs': [{'path': 'check.py', 'from': str(ROOT / 'data/datasets/bird_minidev/verify/check.py')},
                   {'path': 'database.sqlite', 'from': str(database)},
                   {'path': 'gold.sql', 'content': 'select n from numbers'}]}
    spec = bind_inputs(spec, 'authored-portable-cache', ['database.sqlite', 'gold.sql'])
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo / 'harness.json').write_text(json.dumps({'name': 'authored-portable-cache', 'entrypoint': 'agent.py'}))
    (repo / 'agent.py').write_text("import argparse\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir')\n"
        "a=p.parse_args();Path(a.workdir,'answer.sql').write_text('select n from numbers')\n"
        "Path(a.workdir,'trace.jsonl').write_text('{}\\n')\n")
    task = {'task_id': 'authored-portable-cache', 'goal': 'authored SQL control'}

    class Cache(dict):
        reads = 0

        def get(self, *args):
            self.reads += 1
            return super().get(*args)

    cache = Cache()

    def evaluate():
        return runner.evaluate(repo, [task], {}, cache=cache, harness_sha='same-source',
                               verifiers={task['task_id']: spec}, persist_trial_report=False)

    first = evaluate()
    if first['per_task'] != {task['task_id']: 1.0}:
        print(json.dumps({key: first.get(key) for key in (
            'per_task', 'invalid', 'verdicts', 'harness_failed', 'harness_output')}, indent=2))
    assert first['per_task'] == {task['task_id']: 1.0} and not first['cached_tasks'], first
    receipt = first['verdicts'][task['task_id']]['native_runtime']
    moved = tmp_path / 'moved'
    root.rename(moved)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(moved))

    def no_actor(*args, **kwargs):
        pytest.fail('cache reuse or missing runtime launched another actor')

    monkeypatch.setattr(runner, 'run_one', no_actor)
    reused = evaluate()
    assert reused['cached_tasks'] == [task['task_id']] and len(cache) == 1
    assert reused['verdicts'][task['task_id']]['native_runtime'] == receipt
    _, manifest = store.load_manifest(binding)
    package = store.package_path(manifest, binding['sha256'], moved)
    (package / manifest['files']['/scorer/check.py']['source']).unlink()
    reads = cache.reads
    refused = evaluate()
    assert not refused['per_task'] and not refused['cached_tasks'] and cache.reads == reads
    assert refused['invalid'][task['task_id']]['stage'] == 'native runtime preparation'


def test_capture_venv_dependencies_survives_removing_the_original_environment(tmp_path, monkeypatch):
    import chess
    from data.datasets import chessbench_puzzles as dataset

    environment = tmp_path / 'selected-venv'
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / 'bin/python'
    env = {'PATH': os.defpath, 'LANG': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}
    package_root = subprocess.run([str(python), '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], env=env,
        capture_output=True, text=True, check=True).stdout.strip()
    shutil.copytree(Path(chess.__file__).resolve().parent, Path(package_root) / 'chess',
                    ignore=shutil.ignore_patterns('__pycache__'))
    manifest = tmp_path / 'venv-manifest.json'
    capture = subprocess.run([str(python), str(ROOT / 'tools/build_bird_native_runtime.py'),
        '--dataset', 'chessbench_puzzles', '--portable', '--out', str(tmp_path / 'capture'),
        '--manifest', str(manifest), '--store', str(tmp_path / 'archives')], cwd=ROOT,
        env=env, capture_output=True, text=True, timeout=120)
    assert capture.returncode == 0, capture.stdout + capture.stderr
    summary = json.loads(capture.stdout)
    binding = {'dataset': 'chessbench_puzzles', 'manifest': str(manifest),
               'sha256': summary['manifest_sha256']}
    installed = tmp_path / 'installed'
    store.import_package(binding, Path(summary['payload']), installed)
    shutil.rmtree(environment)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(installed))
    spec = dataset.verify_for({'task_id': 'authored-portable-chess', 'gold': {
        'fen': chess.STARTING_FEN, 'moves': ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'],
        'answer': 'e7e5 b8c6', 'puzzle_id': 'authored'}})
    spec['native_runtime'] = binding
    work = tmp_path / 'task'
    work.mkdir()
    for answer, score in (('e7e5 b8c6', 1.0), ('e7e5', 0.0)):
        (work / 'answer.txt').write_text(answer)
        result = verifier._verify(work, spec, answer, sandbox=True, timeout_s=30,
                                  task_id='authored-portable-chess')
        assert result.get('score') == score and not result.get('unmeasured'), result
        assert result['native_runtime']['verified']


@pytest.fixture(scope='module')
def captured_py4j_runtime(tmp_path_factory):
    """Real Python/Py4J/JVM; ScienceWorld is an inventory-only placeholder."""
    py4j = pytest.importorskip('py4j.java_gateway', reason='Py4J is required for the Gateway control')
    jar = Path(py4j.find_jar_path())
    if not jar.is_file():
        pytest.skip('the installed Py4J JAR is required for the Gateway control')
    candidates = []
    if os.environ.get('JAVA_HOME'):
        candidates.append(Path(os.environ['JAVA_HOME']))
    if java := shutil.which('java'):
        candidates.append(Path(java).resolve().parents[1])
    if Path('/usr/lib/jvm').is_dir():
        candidates.extend(sorted(Path('/usr/lib/jvm').iterdir()))
    java_home = next((path.resolve() for path in candidates
                      if (path / 'bin/java').is_file() and (path / 'release').is_file()
                      and (path / 'lib/server/libjvm.so').is_file()), None)
    if java_home is None:
        pytest.skip('a JRE with lib/server/libjvm.so is required for the Gateway control')
    work = tmp_path_factory.mktemp('captured-py4j-runtime')
    spec = importlib.util.spec_from_file_location('native_py4j_scw_paths',
        ROOT / 'data/datasets/scienceworld/lib/scw_env.py')
    scw_paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scw_paths)
    # Use an owned staging tree; neither the shared Python nor JVM is modified.
    with tempfile.TemporaryDirectory(prefix='hg-py4j-capture-', dir='/var/tmp') as temporary:
        stage = Path(temporary)
        selected = stage / 'venv'
        venv.EnvBuilder(with_pip=False).create(selected)
        python = selected / 'bin/python'
        env = {'PATH': os.defpath, 'HOME': str(stage), 'LANG': 'C.UTF-8',
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'HG_REVIEWED_ENVIRONMENT_ONLY': '1', 'HG_DATA_SCIENCEWORLD': str(stage / 'data')}
        packages = Path(subprocess.run([str(python), '-c',
            'import sysconfig;print(sysconfig.get_path("purelib"))'], env=env,
            capture_output=True, text=True, check=True).stdout.strip())
        shutil.copytree(Path(py4j.__file__).resolve().parent, packages / 'py4j',
                        ignore=shutil.ignore_patterns('__pycache__'))
        original_jar = selected / 'share/py4j' / jar.name
        original_jar.parent.mkdir(parents=True)
        shutil.copyfile(jar, original_jar)
        (packages / 'scienceworld').mkdir()
        (packages / 'scienceworld/__init__.py').write_text(
            '# Authored capture inventory only; no ScienceWorld game engine.\n')
        jre = stage / 'data/jre' / scw_paths.JRE_DIRNAME
        jre.parent.mkdir(parents=True)
        jre.symlink_to(java_home, target_is_directory=True)
        manifest_path = work / 'manifest.json'
        capture = subprocess.run([str(python), str(ROOT / 'tools/build_bird_native_runtime.py'),
            '--dataset', 'scienceworld', '--portable', '--out', str(work / 'capture'),
            '--manifest', str(manifest_path), '--store', str(work / 'archives')], cwd=ROOT,
            env=env, capture_output=True, text=True, timeout=180)
        assert capture.returncode == 0, capture.stdout + capture.stderr
        summary = json.loads(capture.stdout)
        binding = {'dataset': 'scienceworld', 'manifest': str(manifest_path),
                   'sha256': summary['manifest_sha256']}
        archive = Path(summary['payload'])
        installed = work / 'installed'
        store.import_package(binding, archive, installed)
    assert not selected.exists() and not original_jar.exists()
    installed.rename(work / 'moved-store')
    manifest = json.loads(manifest_path.read_text())
    captured_jar = next(target for target in manifest['files']
                        if target.endswith('/share/py4j/' + jar.name))
    return SimpleNamespace(binding=binding, archive=archive, manifest=manifest,
        root=work / 'moved-store', captured_jar=captured_jar, original_jar=str(original_jar),
        expected_jar=str(Path('/opt/hg-python/share/py4j') / jar.name),
        jar_sha256=hashlib.sha256(jar.read_bytes()).hexdigest())


_PY4J_GATEWAY_CONTROL = r'''
import hashlib, json, subprocess, sys
from pathlib import Path
from py4j.java_gateway import JavaGateway, GatewayParameters, find_jar_path, launch_gateway

jar = find_jar_path()
print(json.dumps({'found_jar': jar, 'prefix': sys.prefix,
                  'captured_jar_exists': Path(sys.argv[-1]).is_file()}), flush=True)
port, process = launch_gateway(die_on_exit=True, return_proc=True)
gateway = None
try:
    gateway = JavaGateway(gateway_parameters=GatewayParameters(port=port, read_timeout=3))
    assert gateway.jvm.java.lang.Math.abs(-7) == 7
    java_home = gateway.jvm.java.lang.System.getProperty('java.home')
finally:
    try:
        if gateway is not None:
            gateway.shutdown()
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
assert process.returncode == 0, process.returncode
print(json.dumps({'jar_sha256': hashlib.sha256(Path(jar).read_bytes()).hexdigest(),
                  'java_home': java_home, 'gateway_exited': process.poll() is not None}))
'''


def test_py4j_gateway_starts_after_removing_the_original_venv(captured_py4j_runtime, tmp_path, monkeypatch):
    control = captured_py4j_runtime
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(control.root))
    reward = tmp_path / 'verifier'
    reward.mkdir()
    with native_runtime.admit(control.binding) as runtime:
        result = runtime.run(['python3', '-c', _PY4J_GATEWAY_CONTROL, control.captured_jar],
                             workdir=tmp_path, reward_dir=reward, timeout_s=20)
    assert result.returncode == 0, result.stdout + result.stderr
    observed, completed = map(json.loads, result.stdout.splitlines())
    assert observed['prefix'] == '/opt/hg-python'
    assert observed['found_jar'] == control.expected_jar and observed['captured_jar_exists']
    assert completed == {'jar_sha256': control.jar_sha256, 'java_home': '/opt/hg-jre',
                         'gateway_exited': True}


def test_py4j_legacy_jar_location_is_not_a_gateway_ready_package(captured_py4j_runtime, tmp_path, monkeypatch):
    control = captured_py4j_runtime
    manifest = deepcopy(control.manifest)
    manifest['files'][control.original_jar] = manifest['files'].pop(control.captured_jar)
    binding = bind(tmp_path / 'legacy-placement.json', manifest)
    installed = tmp_path / 'legacy-store'
    store.assemble_package(binding, archives=[control.archive], root=installed)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', str(installed))
    reward = tmp_path / 'verifier'
    reward.mkdir()
    with native_runtime.admit(binding) as runtime:
        assert runtime.receipt['host']['capabilities']['child_python']
        result = runtime.run(['python3', '-c', _PY4J_GATEWAY_CONTROL, control.original_jar],
                             workdir=tmp_path, reward_dir=reward, timeout_s=20)
    assert result.returncode != 0 and 'Could not find py4j jar' in result.stderr
    observed = json.loads(result.stdout)
    assert observed['captured_jar_exists'] and observed['found_jar'] == ''
