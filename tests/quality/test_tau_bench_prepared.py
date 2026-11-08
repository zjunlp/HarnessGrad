"""Authored offline controls. No released cases are read or replayed."""
import hashlib
import importlib.util
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

SPEC = importlib.util.spec_from_file_location('tau_prepared_control', Path(__file__).resolve().parents[2] / 'data/datasets/tau_bench/prepared.py')
prepared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepared)


def fixture(tmp_path):
    source = tmp_path / 'source'
    (source / 'gt').mkdir(parents=True)
    payload = json.dumps({'task_id': 'authored_001', 'instruction': 'Authored synthetic case.'}).encode()
    (source / 'gt/authored_001.json').write_bytes(payload)
    manifest = source / 'PROVENANCE.json'
    manifest.write_text(json.dumps({'checkout_commit': 'authored-control-only', 'files': {'gt/authored_001.json': hashlib.sha256(payload).hexdigest()}}))
    return source, manifest


def test_import_retry_and_concurrent_publish(tmp_path):
    source, manifest = fixture(tmp_path)
    destination = tmp_path / 'external'
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: prepared.import_assets(source, destination, manifest), range(2)))
    assert results == [destination, destination]
    assert prepared.verify(destination, manifest)['gt/authored_001.json'] == (source / 'gt/authored_001.json').read_bytes()
    original = (destination / 'gt/authored_001.json').read_bytes()
    (source / 'gt/authored_001.json').write_text('damaged')
    with pytest.raises(ValueError, match='checksum'):
        prepared.import_assets(source, destination, manifest)
    assert (destination / 'gt/authored_001.json').read_bytes() == original


def test_damaged_destination_is_preserved(tmp_path):
    source, manifest = fixture(tmp_path)
    destination = tmp_path / 'external'
    prepared.import_assets(source, destination, manifest)
    target = destination / 'gt/authored_001.json'
    target.write_text('historical damaged bytes')
    with pytest.raises(ValueError, match='checksum'):
        prepared.import_assets(source, destination, manifest)
    assert target.read_text() == 'historical damaged bytes'


def test_extra_case_and_symlink_are_refused(tmp_path):
    source, manifest = fixture(tmp_path)
    (source / 'gt/extra.json').write_text('{}')
    with pytest.raises(ValueError, match='inventory'):
        prepared.verify(source, manifest)
    (source / 'gt/extra.json').unlink()
    target = source / 'gt/authored_001.json'
    target.rename(source / 'saved')
    target.symlink_to(source / 'saved')
    with pytest.raises(ValueError, match='unsafe'):
        prepared.verify(source, manifest)


def test_adapter_imports_without_private_data_and_load_explains_preparation(tmp_path, monkeypatch):
    import sys
    import harnessgrad.paths
    monkeypatch.setattr(harnessgrad.paths, 'dataset_root', lambda _: tmp_path / 'unprepared')
    monkeypatch.delenv('HG_TAU_BENCH_PREPARED', raising=False)
    name = 'data.datasets.tau_bench'
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / 'data/datasets/tau_bench.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert module._ALL_GT == []
    assert module.VERIFY == {}
    with pytest.raises(ValueError, match='tools.prepare_tau_bench'):
        module.load()
    assert module.downloads()[0]['version'] == '59a200c6d575d595120f1cb70fea53cef0632f6b'


def test_authored_adapter_split_and_verified_byte_reader(tmp_path, monkeypatch):
    """Use manifest identities with wholly authored contents; never open released cases."""
    import importlib
    import sys
    import harnessgrad.paths
    monkeypatch.setattr(harnessgrad.paths, 'dataset_root', lambda _: tmp_path / 'external')
    monkeypatch.delenv('HG_TAU_BENCH_PREPARED', raising=False)
    name = 'data.datasets.tau_bench'
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / 'data/datasets/tau_bench.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    payloads = {}
    for path in prepared.inventory()['files']:
        if path.startswith('gt/'):
            tid = Path(path).stem
            domain, split, index = tid.split('_')
            payloads[path] = json.dumps(dict(task_id=tid, domain=domain, split=split,
                index=int(index), user_id='authored-user', instruction='Authored fixture only.')).encode()
    monkeypatch.setattr(importlib.import_module(name + '.prepared'), 'verify', lambda _: payloads)
    for pool, counts in [('test', (495, 139)), ('dev', (495, 19))]:
        monkeypatch.setenv('HG_TAU_BENCH_POOL', pool)
        module = importlib.reload(module)
        assert (len(module.SPLIT['train']), len(module.SPLIT['eval'])) == counts
        tasks, scorable = module.load()
        assert len(tasks) == len(scorable) == sum(counts)
        assert all('Authored fixture only.' in task['goal'] for task in tasks)
        assert not (set(module.SPLIT['train']) & set(module.SPLIT['eval']))
        assert not (set(scorable) & set(module.EXCLUDED_TASKS))
    monkeypatch.setenv('HG_TAU_BENCH_POOL', 'train')
    module = importlib.reload(module)
    with pytest.raises(ValueError, match='not an official split'):
        module.load()


def test_uncooperative_existing_directory_is_never_replaced(tmp_path, monkeypatch):
    source, manifest = fixture(tmp_path)
    destination = tmp_path / 'external'
    original_mkdtemp = prepared.tempfile.mkdtemp
    def competing_publish(*args, **kwargs):
        temporary = original_mkdtemp(*args, **kwargs)
        destination.mkdir()
        return temporary
    monkeypatch.setattr(prepared.tempfile, 'mkdtemp', competing_publish)
    with pytest.raises(FileNotFoundError):
        prepared.import_assets(source, destination, manifest)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not list(tmp_path.glob('.external-*'))
