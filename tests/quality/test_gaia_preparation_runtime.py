"""Authored GAIA preparation controls; no gated source acquisition."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harnessgrad import dataset_runtime

SPEC = importlib.util.spec_from_file_location('gaia_prepare', Path(__file__).resolve().parents[2] / 'data/datasets/gaia/prepare.py')
prep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prep)


def test_conversion_admits_binding_and_drops_credentials(tmp_path, monkeypatch):
    for name in (prep.METADATA, prep.METADATA_TEST):
        (tmp_path / prep.LOCAL[name]).write_bytes(b'authored transport')
    monkeypatch.setenv('OPENAI_API_KEY', 'secret')
    monkeypatch.setenv('HF_TOKEN', 'fetch-only')
    calls = []
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'selected': name})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'role': role})
    def admit(binding, role, **kwargs):
        calls.append(('admit', role))
        return {'python': '/isolated/python'}
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admit)
    def run(argv, **kwargs):
        assert calls == [('admit', 'service')]
        assert argv[:3] == ['/isolated/python', '-I', '-B']
        assert 'HF_TOKEN' not in kwargs['env'] and 'OPENAI_API_KEY' not in kwargs['env']
        request = json.loads(kwargs['input'])
        assert request['binding'] == {'role': 'service'}
        inputs = request['inputs']
        assert all(item['sha256'] == prep._sha256(Path(item['path']).read_bytes()) for item in inputs)
        return SimpleNamespace(returncode=0, stdout='[[], []]')
    monkeypatch.setattr(prep.subprocess, 'run', run)
    assert prep._tables(tmp_path) == ([], [])
    assert calls == [("admit", "service"), ("admit", "service")]


def test_missing_runtime_precedes_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(prep.adapter, 'DATASET_ROOT', tmp_path)
    def unavailable(*args, **kwargs):
        raise ValueError('runtime unavailable')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', unavailable)
    monkeypatch.setattr(prep, 'fetch', lambda *args: pytest.fail('download'))
    with pytest.raises(ValueError, match='runtime unavailable'):
        prep.main([])
    assert list(tmp_path.iterdir()) == []


def test_check_never_resolves_or_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(prep.adapter, 'DATASET_ROOT', tmp_path)
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda *args: pytest.fail('runtime'))
    monkeypatch.setattr(prep, 'fetch', lambda *args: pytest.fail('download'))
    assert prep.main(['--check']) == 1
    assert list(tmp_path.iterdir()) == []


def test_existing_workspace_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(prep.adapter, 'DATASET_ROOT', tmp_path)
    (tmp_path / 'pool.jsonl').write_bytes(b'user receipt')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda *args: {})
    with pytest.raises(ValueError, match='already exists'):
        prep.main([])
    assert (tmp_path / 'pool.jsonl').read_bytes() == b'user receipt'


def test_competing_pool_publication_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(prep.adapter, 'DATASET_ROOT', tmp_path)
    monkeypatch.setattr(prep.adapter, 'ROWS', 1)
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda *args: {})
    monkeypatch.setattr(prep, 'fetch', lambda *args: {'rows': [{}], 'provenance': {}})
    original_link = prep.os.link
    def competing_link(source, destination):
        destination.write_bytes(b'competing user receipt')
        original_link(source, destination)
    monkeypatch.setattr(prep.os, 'link', competing_link)
    with pytest.raises(FileExistsError):
        prep.main([])
    assert (tmp_path / 'pool.jsonl').read_bytes() == b'competing user receipt'
    assert not (tmp_path / 'attachments').exists()
    assert len(list(tmp_path.glob('gaia-preparation-attempt-*/pool.jsonl'))) == 1


def test_post_conversion_artifact_drift_rejected_before_output_consumption(tmp_path, monkeypatch):
    for name in (prep.METADATA, prep.METADATA_TEST):
        (tmp_path / prep.LOCAL[name]).write_bytes(b'authored transport')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'role': role})
    calls = []
    def admit(binding, role, **kwargs):
        calls.append(binding)
        if len(calls) == 2:
            raise dataset_runtime.DatasetRuntimeUnavailable('authored artifact drift')
        return {'python': '/isolated/python'}
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admit)
    # Invalid JSON would fail if output was consumed before the second admission.
    monkeypatch.setattr(prep.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout='must not consume'))
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='artifact drift'):
        prep._tables(tmp_path)
    assert calls == [{'role': 'service'}, {'role': 'service'}]
    assert (tmp_path / 'preparation-runtime.json').is_file()
    assert not (tmp_path / 'pool.jsonl').exists()


def test_worker_source_drift_rejected_before_output_consumption(tmp_path, monkeypatch):
    for name in (prep.METADATA, prep.METADATA_TEST):
        (tmp_path / prep.LOCAL[name]).write_bytes(b'authored transport')
    worker = tmp_path / 'prepare_worker.py'
    worker.write_bytes(b'original reviewed worker')
    monkeypatch.setattr(prep, '__file__', str(tmp_path / 'prepare.py'))
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: {'role': role})
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *a, **k:
                        {'python': '/isolated/python'})
    def mutate(*args, **kwargs):
        worker.write_bytes(b'changed during conversion')
        return SimpleNamespace(returncode=0, stdout='must not consume')
    monkeypatch.setattr(prep.subprocess, 'run', mutate)
    with pytest.raises(ValueError, match='worker source changed'):
        prep._tables(tmp_path)
    assert not (tmp_path / 'pool.jsonl').exists()


def test_installed_entrypoint_help_and_absent_runtime_check_are_readonly(tmp_path):
    import os
    import subprocess
    import sys
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('HG_DATA_ROOT', 'HG_DATA_GAIA', 'HF_TOKEN',
                                  'HUGGING_FACE_HUB_TOKEN', 'OPENAI_API_KEY')}
    store = tmp_path / 'absent-runtime'
    data = tmp_path / 'absent-data'
    environment['HG_DATA_GAIA'] = str(data)
    for arguments, expected in ((['--help'], 0),
                               (['--check-runtime', '--runtime-root', str(store)], 1)):
        proc = subprocess.run([sys.executable, '-m', 'tools.prepare_gaia', *arguments],
                              cwd=Path(SPEC.origin).parents[3],
                              env=environment, capture_output=True, text=True, timeout=30)
        assert proc.returncode == expected, proc.stderr
        if arguments == ['--help']:
            assert '--prepare-runtime' in proc.stdout and '--check-runtime' in proc.stdout
        else:
            result = json.loads(proc.stdout)
            assert result['present'] is False and result['status'] == 'unavailable'
        assert not store.exists()
        assert not data.exists()


def test_source_and_module_missing_runtime_diagnostics_match_in_empty_env(tmp_path):
    import subprocess
    import sys
    store = tmp_path / 'absent-runtime'
    data = tmp_path / 'absent-data'
    environment = {'HG_DATA_GAIA': str(data), 'HOME': str(tmp_path),
                   'PATH': '/usr/bin:/bin'}
    commands = ([sys.executable, '-m', 'tools.prepare_gaia'],
                [sys.executable, str(SPEC.origin)])
    diagnostics = []
    for command in commands:
        proc = subprocess.run([*command, '--runtime-root', str(store)],
                              cwd=Path(SPEC.origin).parents[3], env=environment,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 1
        assert 'Prepare explicitly' in proc.stderr
        assert 'Traceback' not in proc.stderr
        assert proc.stdout == ''
        assert not store.exists()
        assert not data.exists()
        diagnostics.append(proc.stderr)
    assert diagnostics[0] == diagnostics[1]
