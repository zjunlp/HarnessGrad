"""Actual pinned renderer + real Hub SDK, authored loopback HF data only."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tarfile
import threading
import venv

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / 'data/datasets/jobbench/prepare.py'
SOURCE = json.loads((ENTRY.parent / 'SOURCE.json').read_text())


def launch(python, args, environment):
    code = '''import importlib.util,runpy,sys
assert importlib.util.find_spec('huggingface_hub') is None
sys.argv=sys.argv[1:]
try: runpy.run_path(sys.argv[0],run_name='__main__')
finally: assert importlib.util.find_spec('huggingface_hub') is None
'''
    return subprocess.run([str(python), '-I', '-B', '-c', code, str(ENTRY), *args],
        env=environment, text=True, capture_output=True, timeout=90)


@pytest.fixture
def cold_platform(tmp_path):
    prefix = tmp_path / 'platform'
    venv.EnvBuilder(with_pip=False).create(prefix)
    return prefix / 'bin/python'


def test_runtime_check_is_readonly_and_missing_render_fails_before_fetch(tmp_path, cold_platform):
    missing = tmp_path / 'absent-store'
    env = {**os.environ, 'HG_DATASET_RUNTIME_ROOT': str(missing)}
    result = launch(cold_platform, ['--check-runtime'], env)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)['present'] is False
    assert not missing.exists()
    output = tmp_path / 'not-created'
    result = launch(cold_platform, ['--root', str(output), '--render'], env)
    assert result.returncode == 1
    assert 'jobbench_renderer' in result.stderr
    assert not output.exists() and not missing.exists()


@pytest.mark.parametrize('mode', ['correct', 'wrong-blob', 'wrong-revision', 'http-failure', 'source-drift', 'package-extra', 'verifier-extra', 'late-source-drift'])
def test_original_generate_runs_in_bound_genuine_runtime(tmp_path, cold_platform, mode):
    source = os.environ.get('HG_JOBBENCH_RENDERER_TEST_SOURCE')
    store = os.environ.get('HG_JOBBENCH_RENDERER_TEST_STORE')
    if not source or not store:
        pytest.skip('select the pinned source-only fixture and genuine managed renderer store')
    source = Path(source)
    pin = json.loads((ENTRY.parent / 'RENDERER_SOURCE.json').read_text())
    archive = tmp_path / 'source-only.tar.gz'
    with tarfile.open(archive, 'w:gz') as handle:
        for relative, digest in pin['files'].items():
            path = source / relative
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            handle.add(path, arcname='fixed-source/' + relative)
    files = {
        'dataset/authored/task1/task_folder/TASK_INSTRUCTIONS.txt': b'Write an authored greeting.',
        'dataset/authored/task1/task_card.md': b'# Authored synthetic control',
        'dataset/authored/task1/RUBRICS.json': json.dumps({'rubrics': [
            {'rubric': 'Authored greeting', 'criterion': ['A greeting exists'], 'weight': 1}]}).encode(),
        'unrelated-private-results.json': b'MUST NOT DOWNLOAD',
    }
    seen = []
    resolved_revision = 'a' * 40 if mode == 'wrong-revision' else SOURCE['revision']
    def blob(data): return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, head=False):
            seen.append(self.path)
            if mode == 'late-source-drift' and len(seen) == 1:
                changed = output / 'harbor/jobbench_harbor/render.py'
                changed.write_bytes(changed.read_bytes() + b'\n# change while actual Hub fetch runs\n')
            if mode == 'http-failure':
                self.send_response(403); self.end_headers(); return
            if '/tree/' in self.path:
                raw = json.dumps([{'type': 'file', 'path': p, 'size': len(b), 'oid': blob(b)}
                    for p,b in files.items()]).encode()
            elif self.path.startswith('/api/datasets/'):
                raw = json.dumps({'id': 'JobBench/job-bench', 'sha': resolved_revision,
                    'siblings': [{'rfilename': p, 'size': len(b), 'blobId': '0' * 40 if mode == 'wrong-blob' else blob(b)} for p,b in files.items()]}).encode()
            elif '/resolve/' in self.path:
                name = self.path.split('/resolve/', 1)[1].split('/', 1)[1]
                assert name != 'unrelated-private-results.json'
                raw = files[name]
                self.send_response(200)
                self.send_header('X-Repo-Commit', resolved_revision)
                self.send_header('ETag', '0' * 40 if mode == 'wrong-blob' else blob(raw))
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                if not head: self.wfile.write(raw)
                return
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            if not head: self.wfile.write(raw)
        def do_HEAD(self): self.respond(True)
        def do_GET(self): self.respond()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {k:v for k,v in os.environ.items() if k.lower() not in
        ('http_proxy','https_proxy','all_proxy','no_proxy')}
    environment.update(HG_DATASET_RUNTIME_ROOT=store,
        HF_ENDPOINT=f'http://127.0.0.1:{server.server_port}', HF_HUB_DISABLE_XET='1',
        HF_TOKEN='synthetic-jobbench-control-token')
    output = tmp_path / 'prepared'
    try:
        result = launch(cold_platform, ['--root', str(output), '--archive', str(archive)], environment)
        assert result.returncode == 0, result.stderr
        if mode in ('source-drift', 'package-extra', 'verifier-extra'):
            path = output / 'harbor/jobbench_harbor/render.py'
            if mode == 'source-drift':
                path.write_bytes(path.read_bytes() + b'\n# authored content drift\n')
            elif mode == 'verifier-extra':
                (output / 'eval/jobbench_eval/unlisted.txt').write_text('unexpected verifier input')
            else:
                (path.parent / 'unlisted.py').write_text('raise RuntimeError("unlisted")')
            # Call the render admission directly: extraction must not repair drift.
            code = '''import sys,runpy
from pathlib import Path
entry=Path(sys.argv[1])/'data/datasets/jobbench/prepare.py'
module=runpy.run_path(str(entry))
module['render'](Path(sys.argv[2]))
'''
            result = subprocess.run([str(cold_platform), '-I', '-B', '-c', code, str(ROOT), str(output)],
                env=environment, capture_output=True, text=True, timeout=30)
            assert result.returncode != 0 and 'renderer' in result.stderr and 'changed' in result.stderr
            assert seen == []
        else:
            result = launch(cold_platform, ['--root', str(output), '--archive', str(archive), '--render'], environment)
    finally:
        server.shutdown(); server.server_close(); thread.join()
    receipts = list(output.glob('renderer-attempt-*/receipt.json'))
    if mode == 'correct':
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(receipts) == 1
        receipt = json.loads(receipts[0].read_text())
        assert 'synthetic-jobbench-control-token' not in receipts[0].read_text()
        assert receipt['huggingface_hub_version'] == '1.33.0'
        assert receipt['binding']['dataset'] == 'jobbench_renderer'
        assert receipt['binding']['python'] != str(cold_platform)
        assert receipt['renderer']['files'] == pin['files']
        manifest = json.loads((output / 'harbor/current/manifest.json').read_text())
        assert manifest['task_count'] == 1 and manifest['revision'] == SOURCE['revision']
        assert manifest['tasks'][0]['id'] == 'main--authored--task1'
        assert any('/resolve/' in p for p in seen)
        assert not any('unrelated-private-results' in p for p in seen)
    else:
        assert result.returncode != 0
        assert receipts == []


def test_explicit_prepare_and_check_reuse_verified_runtime_without_platform_sdk(tmp_path, cold_platform):
    store = os.environ.get('HG_JOBBENCH_RENDERER_TEST_STORE')
    if not store:
        pytest.skip('select a genuine managed renderer store')
    environment = {**os.environ, 'HG_DATASET_RUNTIME_ROOT': store, 'UV_OFFLINE': '1'}
    prepared = launch(cold_platform, ['--prepare-runtime'], environment)
    assert prepared.returncode == 0, prepared.stderr
    assert json.loads(prepared.stdout)['present'] is True
    root = Path(store)
    before = {str(p): (p.stat().st_mtime_ns, p.read_bytes())
              for p in root.rglob('*') if p.is_file() and p.name in ('artifact.json', 'ready.json')}
    checked = launch(cold_platform, ['--check-runtime'], environment)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)['present'] is True
    after = {str(p): (p.stat().st_mtime_ns, p.read_bytes())
              for p in root.rglob('*') if p.is_file() and p.name in ('artifact.json', 'ready.json')}
    assert before == after
