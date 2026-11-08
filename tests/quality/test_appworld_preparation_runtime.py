"""Authored encrypted bundles only; never read AppWorld data or start services."""
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import types
import zipfile

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parents[2]


def test_authored_bundle_uses_isolated_worker_and_binds_preparation(tmp_path, monkeypatch):
    from harnessgrad import dataset_runtime
    root = tmp_path / 'dataset'
    bundle = root / '_src/data-0.1.0.bundle'
    bundle.parent.mkdir(parents=True)
    db = b'{"synthetic": 1}\n'
    digest = hashlib.sha256(b'synthetic.jsonl' + db).hexdigest()
    rows = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('data/version.txt', 'synthetic')
        for split in ('train', 'dev', 'test_normal', 'test_challenge'):
            task = 'fixture-' + split
            z.writestr(f'data/datasets/{split}.txt', task + '\n')
            prefix = f'data/tasks/{task}/'
            z.writestr(prefix + 'specs.json', json.dumps({'instruction': 'authored task'}))
            z.writestr(prefix + 'ground_truth/metadata.json', json.dumps(
                {'difficulty': 'easy', 'num_api_calls': 0, 'num_apps': 0}))
            z.writestr(prefix + 'dbs/synthetic.jsonl', db)
            z.writestr(prefix + 'ground_truth/evaluation.py', '# fixture only\n')
            if split == 'train':
                z.writestr(prefix + 'ground_truth/api_calls.json', '[]')
            rows.append({'task_id': task, 'split': split, 'difficulty': 'easy',
                         'num_api_calls': 0, 'num_apps': 0, 'instruction': 'authored task',
                         'dbs': digest, 'released_oracle': split == 'train'})
    iv = b'0123456789abcdef'
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b'Nvx#rYcYQ2%btf',
                     iterations=100000).derive(b'WEquKLy##9M@qu')
    encryptor = Cipher(algorithms.AES(key), modes.CFB(iv)).encryptor()
    original_bundle = iv + encryptor.update(archive.getvalue()) + encryptor.finalize()
    bundle.write_bytes(original_bundle)
    pool_bytes = b''.join((json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n').encode()
                          for r in sorted(rows, key=lambda r: r['task_id']))
    pool = root / 'pool.jsonl'
    monkeypatch.setenv('HG_DATA_APPWORLD', str(root))
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('network forbidden'))
    source = (ROOT / 'data/datasets/appworld.py').read_text()
    source = source.replace('fd9f9608c2ec71ed0ac25c3633a738b9129a318a129e31230425b9188e508250',
                            hashlib.sha256(original_bundle).hexdigest())
    source = source.replace('970c39331bc350ce9d3ed91a75fd32b554c257fc48fc31633dfccdde74164e65',
                            hashlib.sha256(pool_bytes).hexdigest())
    module = types.ModuleType('synthetic_appworld')
    module.__file__ = str(ROOT / 'data/datasets/appworld.py')
    real_import = builtins.__import__
    def no_crypto(name, *args, **kwargs):
        if name.startswith('cryptography'):
            raise AssertionError('loader imported platform cryptography')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_crypto)
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    with pytest.raises(ValueError, match="not prepared"):
        module.load()
    # Keep the import guard active through loading and preparation; the worker
    # is a separate interpreter. Host cryptography above only creates the fixture.
    runtime_root = os.environ.get('HG_TEST_APPWORLD_RUNTIME_ROOT')
    calls = []
    if runtime_root:
        monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', runtime_root)
        selected = dataset_runtime.resolve_execution('appworld')
    else:
        selected = {'dataset': 'appworld', 'profile': 'execution', 'runtime_id': 'a' * 64,
                    'manifest_path': '/synthetic/prepared/artifact.json', 'python': sys.executable,
                    'path': '/synthetic/prepared/venv', 'runtime_paths': ['/synthetic/prepared/venv']}
        def resolve(name):
            calls.append(name)
            return selected
        monkeypatch.setattr(dataset_runtime, 'resolve_execution', resolve)
    assert module.split_ids() == {s: ['fixture-' + s] for s in module.SPLIT_SIZES}
    assert module.task_ids() == sorted(r['task_id'] for r in rows)
    assert module.prepare() == pool
    assert len(module.load()[0]) == 3  # train + default test, not dev
    assert len(module.SETUP) == len(module.VERIFY) == len(module.ENV) == 3
    assert pool.read_bytes() == pool_bytes and bundle.read_bytes() == original_bundle
    receipt, = (root / 'preparation-receipts').glob('*.json')
    value = json.loads(receipt.read_text())
    assert value['bundle_sha256'] == hashlib.sha256(original_bundle).hexdigest()
    assert value['pool_sha256'] == hashlib.sha256(pool_bytes).hexdigest()
    assert value['runtime'] == selected and value['unpacked_tree_verified'] is True
    official = root / '_src/unpacked/data/tasks/fixture-train/ground_truth/api_calls.json'
    original = official.read_bytes()
    extra = root / '_src/unpacked/user-extra.txt'
    extra.write_text('preserve extra')
    assert module.prepare() == pool
    assert extra.read_text() == 'preserve extra'
    receipts = set((root / 'preparation-receipts').glob('*.json'))
    official.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='missing or changed official file'):
        module.prepare()
    assert official.read_bytes() == b'tampered' and extra.read_text() == 'preserve extra'
    assert set((root / 'preparation-receipts').glob('*.json')) == receipts
    official.write_bytes(original)
    official.unlink()
    with pytest.raises(ValueError, match='missing or changed official file'):
        module.prepare()
    official.write_bytes(original)
    saved = official.with_name('saved-user-file')
    official.rename(saved)
    official.symlink_to(saved)
    with pytest.raises(ValueError, match='symlink'):
        module.prepare()
    assert official.is_symlink() and saved.read_bytes() == original
    official.unlink()
    saved.rename(official)
    worker = ROOT / 'data/datasets/appworld/decrypt_bundle.py'
    assert value['worker_sha256'] == hashlib.sha256(worker.read_bytes()).hexdigest()
    pool.write_bytes(b'preserve existing differing synthetic pool')
    with pytest.raises(ValueError, match='existing pool differs'):
        module.prepare()
    assert pool.read_bytes() == b'preserve existing differing synthetic pool'
    assert bundle.read_bytes() == original_bundle
    assert set((root / 'preparation-receipts').glob('*.json')) == receipts
    pool.write_bytes(pool_bytes)
    unpacked = root / '_src/unpacked'
    moved = root / '_src/preserved-unpacked'
    unpacked.rename(moved)
    outside = root / 'outside'
    outside.mkdir()
    unpacked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        module.prepare()
    assert list(outside.iterdir()) == [] and moved.is_dir()
    unpacked.unlink()
    unpacked.mkdir()
    (unpacked / 'partial.txt').write_text('keep')
    with pytest.raises(ValueError, match='missing or changed official file'):
        module.prepare()
    assert (unpacked / 'partial.txt').read_text() == 'keep'
    assert set((root / 'preparation-receipts').glob('*.json')) == receipts
    if not runtime_root:
        assert set(calls) == {'appworld'}

    # An untrusted caller cannot substitute the executable of an admitted artifact.
    captured = []
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda _: selected)
    monkeypatch.setattr(module.subprocess, 'run', lambda argv, **kwargs:
                        captured.append(argv) or types.SimpleNamespace(returncode=0, stdout=b'ok'))
    forged = dict(selected, python='/untrusted/substitute')
    assert module._decrypt(b'synthetic', forged) == b'ok'
    assert captured[0][0] == selected['python']


def test_runtime_manifest_has_complete_exact_pins():
    from harnessgrad.dataset_runtime import execution_descriptor
    spec = execution_descriptor('appworld')
    assert spec['pins'] == {'cryptography': '42.0.5', 'cffi': '2.1.1', 'pycparser': '2.21'}


def test_unprepared_catalog_load_is_read_only_and_requires_explicit_preparation(tmp_path, monkeypatch):
    from harnessgrad import dataset_runtime
    root = tmp_path / 'unprepared'
    root.mkdir()
    monkeypatch.setenv('HG_DATA_APPWORLD', str(root))
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution',
                        lambda *a, **k: pytest.fail('loading must not select a preparation runtime'))
    source = (ROOT / 'data/datasets/appworld.py').read_text()
    module = types.ModuleType('synthetic_appworld_unprepared')
    module.__file__ = str(ROOT / 'data/datasets/appworld.py')
    real_import = builtins.__import__
    def no_crypto(name, *args, **kwargs):
        if name.startswith('cryptography'):
            raise AssertionError('loader imported cryptography')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_crypto)
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    assert module.ORACLE_COVERAGE == 'unavailable until prepared'
    with pytest.raises(ValueError, match=r'run python -m tools\.prepare_appworld'):
        module.load()
    assert list(root.iterdir()) == []
