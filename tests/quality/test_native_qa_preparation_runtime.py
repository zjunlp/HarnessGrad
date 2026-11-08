"""Authored offline source rows; no benchmark downloads or models."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from data.scaffolds.native_qa import prepare as prep
from harnessgrad import dataset_runtime


def source(tmp_path):
    path = tmp_path / 'authored.jsonl'
    path.write_text('{"question":"authored", "answer":"fixture"}\n')
    return path


def test_stdlib_local_custody_and_preservation(tmp_path, monkeypatch):
    path = source(tmp_path)
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda *a, **k: pytest.fail('runtime'))
    root = tmp_path / 'output'
    manifest = prep.prepare('math500', root, ['test=' + str(path)])
    assert manifest['source_files'][0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert manifest['revision'] == prep.CATALOG['math500']['revision']
    assert manifest['preparation']['purpose'] == 'standard-library local native import'
    saved = (root / 'source-manifest.json').read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        prep.prepare('math500', root, ['test=' + str(path)])
    assert (root / 'source-manifest.json').read_bytes() == saved


def test_fake_parquet_worker_uses_admitted_runtime_and_filters_credentials(tmp_path, monkeypatch):
    path = tmp_path / 'authored.parquet'
    path.write_bytes(b'fake transport, not a real Parquet file')
    runtime = {'python': sys.executable, 'runtime_id': 'fixture'}
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: runtime)
    monkeypatch.setenv('OPENAI_API_KEY', 'never-forward')
    monkeypatch.setenv('HF_TOKEN', 'fetch-only')
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        request = json.loads(kwargs['input'])
        root = Path(request['root'])
        (root / 'converted').mkdir()
        (root / 'converted/00.jsonl').write_text('{"synthetic":true}\n')
        (root / 'conversion.json').write_text('[{"split":"test","path":"converted/00.jsonl"}]')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(prep.subprocess, 'run', run)
    manifest = prep.prepare('math500', tmp_path / 'output', ['test=' + str(path)])
    argv, kwargs = calls[0]
    assert argv[0] == sys.executable and argv[1] == '-I'
    assert 'HF_TOKEN' not in kwargs['env'] and 'OPENAI_API_KEY' not in kwargs['env']
    assert manifest['preparation']['runtime'] == runtime


def test_fake_fetch_and_failure_retains_attempt_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'python': sys.executable})
    monkeypatch.setenv('HF_TOKEN', 'sensitive')
    monkeypatch.setenv('OPENAI_API_KEY', 'model-secret')
    def fail(argv, **kwargs):
        assert kwargs['env']['HF_TOKEN'] == 'sensitive'
        assert 'OPENAI_API_KEY' not in kwargs['env']
        return SimpleNamespace(returncode=1, stderr='sensitive model-secret')
    monkeypatch.setattr(prep.subprocess, 'run', fail)
    root = tmp_path / 'failed'
    with pytest.raises(ValueError, match='worker failed') as error:
        prep.prepare('math500', root)
    assert 'sensitive' not in str(error.value)
    assert list(root.glob('preparation-attempt-*'))
    assert not (root / 'source-manifest.json').exists()


def test_profile_is_preparation_only_and_check_readonly(tmp_path):
    spec = dataset_runtime.execution_descriptor('native_qa')
    assert spec['pins']['pyarrow'] == '20.0.0'
    assert not (dataset_runtime.ROOT / 'data/datasets/native_qa.py').exists()
    store = tmp_path / 'absent'
    assert dataset_runtime.check_execution('native_qa', root=store)['present'] is False
    assert not store.exists()


def test_publication_failure_retains_complete_attempt_and_allows_retry(tmp_path, monkeypatch):
    path = source(tmp_path)
    root = tmp_path / 'output'
    original_link = prep.os.link
    def fail(*args, **kwargs):
        raise OSError('authored publication failure')
    monkeypatch.setattr(prep.os, 'link', fail)
    with pytest.raises(OSError, match='publication failure'):
        prep.prepare('math500', root, ['test=' + str(path)])
    assert not (root / 'source-manifest.json').exists()
    assert not (root / 'rows').exists()
    attempt, = root.glob('preparation-attempt-*')
    staged = json.loads((attempt / 'source-manifest.json').read_text())
    assert (root / staged['files'][0]['path']).is_file()
    saved = (attempt / 'source-manifest.json').read_bytes()
    monkeypatch.setattr(prep.os, 'link', original_link)
    result = prep.prepare('math500', root, ['test=' + str(path)])
    assert result['attempt'] != attempt.name
    assert (attempt / 'source-manifest.json').read_bytes() == saved


def test_manifest_appearing_at_publication_is_never_overwritten(tmp_path, monkeypatch):
    path = source(tmp_path)
    root = tmp_path / 'output'
    original_link = prep.os.link
    def competing_link(staged, destination):
        destination.write_bytes(b'preserve competing manifest')
        original_link(staged, destination)
    monkeypatch.setattr(prep.os, 'link', competing_link)
    with pytest.raises(ValueError, match='competing publication'):
        prep.prepare('math500', root, ['test=' + str(path)])
    assert (root / 'source-manifest.json').read_bytes() == b'preserve competing manifest'
    assert not (root / 'rows').exists()


def _concurrent_writer(root, path, barrier, results):
    original = prep._publish_manifest
    def publish(*args):
        barrier.wait(timeout=15)
        return original(*args)
    prep._publish_manifest = publish
    try:
        result = prep.prepare('math500', root, ['test=' + str(path)])
        results.put(('published', result['attempt']))
    except ValueError as error:
        results.put(('refused', str(error)))


def test_two_process_writers_publish_exactly_one_complete_attempt(tmp_path):
    import multiprocessing
    path = source(tmp_path)
    root = tmp_path / 'output'
    context = multiprocessing.get_context('fork')
    barrier = context.Barrier(2)
    results = context.Queue()
    writers = [context.Process(target=_concurrent_writer,
               args=(root, path, barrier, results)) for _ in range(2)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=20)
        assert not writer.is_alive()
        assert writer.exitcode == 0
    outcomes = [results.get(timeout=2) for _ in writers]
    assert sorted(item[0] for item in outcomes) == ['published', 'refused']
    manifest = json.loads((root / 'source-manifest.json').read_text())
    assert manifest['attempt'] == next(item[1] for item in outcomes if item[0] == 'published')
    raw = (root / manifest['files'][0]['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest['files'][0]['sha256']
    assert len(list(root.glob('preparation-attempt-*'))) == 2
    assert not (root / 'rows').exists()


def test_legacy_rows_remain_readable_and_existing_rows_are_preserved(tmp_path):
    from data.scaffolds.native_qa import Dataset
    path = tmp_path / 'math.jsonl'
    path.write_text(json.dumps({'unique_id': 'authored-id', 'problem': 'Find two.',
                               'solution': 'The answer is 2.'}) + '\n')
    root = tmp_path / 'output'
    existing = root / 'rows'
    existing.mkdir(parents=True)
    marker = existing / 'user-file'
    marker.write_bytes(b'preserve user content')
    manifest = prep.prepare('math500', root, ['test=' + str(path)])
    assert marker.read_bytes() == b'preserve user content'
    original_records = Dataset('math500', root).records()
    entry = manifest['files'][0]
    legacy = existing / '00-test.jsonl'
    legacy.write_bytes((root / entry['path']).read_bytes())
    entry['path'] = 'rows/00-test.jsonl'
    (root / 'source-manifest.json').write_text(json.dumps(manifest))
    assert Dataset('math500', root).records() == original_records
