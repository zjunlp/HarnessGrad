"""Offline controls for native component boundaries and private byte custody."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from data.scaffolds.native_research.adapter import Adapter
from data.scaffolds.native_research import component_runtime, preparation
from harnessgrad.dataset_runtime import DatasetRuntimeUnavailable


def test_case_checks_and_parses_same_bytes(tmp_path, monkeypatch):
    raw = b'{"task_id":"hle/control","question":"original"}'
    case = tmp_path / 'case.json'
    case.write_bytes(raw)
    original = Path.read_bytes
    def read_once(path):
        value = original(path)
        if path == case:
            case.write_bytes(b'{"task_id":"hle/control","question":"replacement"}')
        return value
    monkeypatch.setattr(Path, 'read_bytes', read_once)
    value = Adapter('hle', tmp_path).case({'case':'case.json','task_id':'hle/control'},
        {'files':{'case.json':hashlib.sha256(raw).hexdigest()}})
    assert value['question'] == 'original'


@pytest.mark.parametrize('name', ['travelplanner', 'workbench', 'widesearch'])
def test_no_platform_dependency_fallback(name, monkeypatch):
    def missing(*args, **kwargs):
        raise DatasetRuntimeUnavailable('not provisioned')
    monkeypatch.setattr(component_runtime, 'resolve_execution', missing)
    with pytest.raises(DatasetRuntimeUnavailable, match='not provisioned'):
        component_runtime.apply(name, {'argv':['python3','-I','verify.py']})


def test_verifier_carries_exact_runtime(monkeypatch):
    selected = {'python':'/owned/bin/python','runtime_paths':['/owned'], 'runtime_id':'exact'}
    monkeypatch.setattr(component_runtime, 'binding', lambda name, role:selected)
    spec = component_runtime.apply('travelplanner', {'argv':['python3','-I','verify.py']})
    assert spec['argv'][0] == selected['python']
    assert spec['python_runtime'] is selected
    assert spec['runtime_paths'] is selected['runtime_paths']


def test_preparation_uses_component_and_records_only_worker_manifest(tmp_path, monkeypatch):
    from harnessgrad import dataset_runtime
    selected = {'python':'/owned/bin/python','runtime_paths':['/owned']}
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: selected)
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: runtime)
    def run(argv, **kwargs):
        assert argv[:3] == ['/owned/bin/python','-I','-B']
        request = json.loads(kwargs['input'])
        assert request['runtime'] == selected
        assert request['name'] == 'travelplanner'
        assert 'PYTHONPATH' not in kwargs['env']
        (tmp_path/'manifest.json').write_text('{"prepared":true}')
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(preparation.subprocess, 'run', run)
    assert preparation.prepare_component('travelplanner', tmp_path, {}) == {'prepared':True}


def test_manifest_publication_never_replaces_an_existing_result(tmp_path):
    from tools.prepare_native_research import _publish_manifest
    path = tmp_path / 'manifest.json'
    original = b'{"preserved":"original evidence"}\n'
    path.write_bytes(original)
    with pytest.raises(FileExistsError):
        _publish_manifest(path, {'replacement': True})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_manifest_publication_has_one_complete_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from tools.prepare_native_research import _publish_manifest
    barrier = Barrier(2)
    path = tmp_path / 'manifest.json'
    values = [{'preparation_runtime': {'runtime_id': str(i)}, 'payload':'x' * 20000}
              for i in range(2)]
    def publish(value):
        barrier.wait()
        try:
            _publish_manifest(path, value)
            return value
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, values))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert json.loads(path.read_bytes()) == winners[0]
    assert list(tmp_path.iterdir()) == [path]


def test_preparation_runtime_is_in_the_first_published_manifest(tmp_path, monkeypatch):
    import tools.prepare_native_research as conversion
    source = tmp_path / 'test.jsonl'
    source.write_text(json.dumps({'id':'control','question':'Return a marker.',
        'answer':'private','image':'','category':'test','answer_type':'exactMatch'}) + '\n')
    selected = {'runtime_id':'selected-before-conversion'}
    published = []
    real_publish = conversion._publish_manifest
    def publish(path, manifest):
        assert not path.exists()
        assert manifest['preparation_runtime'] is selected
        real_publish(path, manifest)
        published.append(path.read_bytes())
    monkeypatch.setattr(conversion, '_publish_manifest', publish)
    output = tmp_path / 'prepared'
    conversion._prepare('hle', output, {'data_path':str(source)},
                        preparation_runtime=selected)
    assert len(published) == 1
    assert (output / 'manifest.json').read_bytes() == published[0]
    with pytest.raises(ValueError, match='fresh output directory'):
        conversion._prepare('hle', output, {'data_path':str(source)},
                            preparation_runtime={'runtime_id':'replacement'})
    assert (output / 'manifest.json').read_bytes() == published[0]


def test_failed_conversion_keeps_exclusive_claim_and_no_published_manifest(tmp_path, monkeypatch):
    import tools.prepare_native_research as conversion
    calls = []
    def failure(*args):
        calls.append(True)
        raise ValueError('authored conversion failure')
    monkeypatch.setattr(conversion, 'benchmark', failure)
    output = tmp_path / 'failed'
    with pytest.raises(ValueError, match='authored conversion failure'):
        conversion._prepare('hle', output, {'data_path':'authored-input'})
    assert (output / '.prepare.claim').is_file()
    assert not (output / 'manifest.json').exists()
    with pytest.raises(ValueError, match='fresh output directory'):
        conversion._prepare('hle', output, {'data_path':'authored-input'})
    assert calls == [True]


def test_late_preparation_contender_cannot_reuse_completed_reservation(tmp_path, monkeypatch):
    """Pause B after its empty check; A completes before B tries exclusive claim."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    import tools.prepare_native_research as conversion
    source = tmp_path / 'test.jsonl'
    source.write_text(json.dumps({'id':'control','question':'Return a marker.',
        'answer':'private','image':'','category':'test','answer_type':'exactMatch'}) + '\n')
    output = tmp_path / 'prepared'
    output.mkdir()
    checked, resume = Event(), Event()
    real_mkdir = Path.mkdir
    def paused_mkdir(path, *args, **kwargs):
        # _prepare calls mkdir immediately after its empty-directory admission.
        if path == output and current_thread().name.startswith('late-contender'):
            checked.set()
            assert resume.wait(15), 'completed preparation did not release the test barrier'
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', paused_mkdir)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='late-contender') as pool:
        contender = pool.submit(conversion._prepare, 'hle', output, {'data_path':str(source)})
        try:
            assert checked.wait(15), 'contender did not pass the empty-directory check'
            manifest = conversion._prepare('hle', output, {'data_path':str(source)})
            assert (output / '.prepare.claim').is_file()
            before = {path.relative_to(output):path.read_bytes()
                      for path in output.rglob('*') if path.is_file()}
        finally:
            resume.set()
        with pytest.raises(FileExistsError):
            contender.result(timeout=15)
    after = {path.relative_to(output):path.read_bytes()
             for path in output.rglob('*') if path.is_file()}
    assert before == after
    assert json.loads(after[Path('manifest.json')]) == manifest
    assert Path(manifest['records'][0]['case']) in after
