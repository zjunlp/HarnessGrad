"""Offline HTTP/provenance controls; no Hub calls or model weights in unit tests."""
import hashlib
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'data/datasets/cooperbench/hf_mirror'


def module(name):
    spec = importlib.util.spec_from_file_location('cooper_hf_' + name, SOURCE / (name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def fixture_assets(root):
    (root / 'blobs').mkdir()
    def blob(raw):
        sha = hashlib.sha256(raw).hexdigest(); (root / 'blobs' / sha).write_bytes(raw); return sha
    content = b'public-file\x00binary'
    sha = blob(content)
    info = {'id': 'owner/model', 'sha': 'a' * 40}
    tree = [{'type': 'file', 'path': 'model.bin', 'size': len(content), 'oid': 'b' * 40}]
    info_sha = blob(json.dumps(info).encode()); tree_sha = blob(json.dumps(tree).encode())
    manifest = {'version': 1, 'repos': {'owner/model': {
        'revision': 'a' * 40, 'info': info, 'tree': tree,
        'info_blob': info_sha, 'info_sha256': info_sha, 'tree_blob': tree_sha, 'tree_sha256': tree_sha,
        'files': {'model.bin': {'sha256': sha, 'size': len(content), 'etag': 'b' * 40, 'commit': 'a' * 40}}}}}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return content, manifest


@pytest.fixture
def mirror(tmp_path):
    mod = module('server'); content, _ = fixture_assets(tmp_path)
    manifest, sha = mod.load(tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), mod.handler(tmp_path, manifest, {'manifest_sha256': sha}))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}', content
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_exact_metadata_and_file_bytes(mirror):
    base, content = mirror
    for rev in ('main', 'a' * 40):
        with urllib.request.urlopen(base + '/api/models/owner/model/revision/' + rev) as response:
            assert json.load(response)['sha'] == 'a' * 40
        with urllib.request.urlopen(base + '/api/models/owner/model/tree/' + rev + '?recursive=True'.lower()) as response:
            assert json.load(response)[0]['path'] == 'model.bin'
        url = base + '/owner/model/resolve/' + rev + '/model.bin'
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD')) as response:
            assert response.read() == b''
            assert response.headers['X-Repo-Commit'] == 'a' * 40
            assert response.headers['ETag'] == '"' + 'b' * 40 + '"'
            assert int(response.headers['Content-Length']) == len(content)
        with urllib.request.urlopen(url) as response: assert response.read() == content


@pytest.mark.parametrize('path', [
    '/api/models/unknown/repo', '/owner/model/resolve/main/missing.bin',
    '/owner/model/resolve/other/model.bin', '/owner/model/resolve/main/../../manifest.json',
    '/owner/model/resolve/main/%2e%2e/manifest.json',
    '/api/models/owner/model/tree/main?recursive=true&unknown=yes',
    '/api/models/owner/model/tree/main?recursive=true&recursive=false',
    '/owner/model/resolve/main/model.bin?download=true', '/health?anything=1',
])
def test_unknown_routes_do_not_return_assets(mirror, path):
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(mirror[0] + path)
    assert error.value.code == 404


def test_altered_weights_and_metadata_refuse_startup(tmp_path):
    mod = module('server'); _, manifest = fixture_assets(tmp_path)
    item = manifest['repos']['owner/model']['files']['model.bin']
    (tmp_path / 'blobs' / item['sha256']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='weight identity'): mod.load(tmp_path)
    metadata = manifest['repos']['owner/model']['info_blob']
    (tmp_path / 'blobs' / metadata).write_text('{}')
    with pytest.raises(ValueError, match='metadata identity'): mod.load(tmp_path)


def test_server_has_no_upstream_transport():
    import ast
    tree = ast.parse((SOURCE / 'server.py').read_text())
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not set(imports) & {'requests', 'urllib.request', 'http.client', 'huggingface_hub'}


def test_health_rejects_actor_written_identity(tmp_path, monkeypatch):
    mod = module('launcher')
    public = tmp_path / 'identity.json'; public.write_text('{}')
    monkeypatch.setattr(mod, 'PUBLIC', public)
    with pytest.raises(RuntimeError, match='root-owned/read-only'):
        mod.health({'manifest_sha256': 'x'})


def test_observed_container_mirror_lifecycle(tmp_path):
    """Opt-in real isolation check; uses tiny HTTP assets, never model inference."""
    import os
    import shutil
    import sys
    image = os.environ.get('HG_COOPERBENCH_HF_TEST_IMAGE')
    if not image: pytest.skip('requires explicit already-local container image')
    sys.path.insert(0, str(ROOT))
    from eval.containers.observed import Runtime, checked
    code = tmp_path / 'mirror'; code.mkdir()
    for name in ('server.py', 'launcher.py', 'python'):
        shutil.copyfile(SOURCE / name, code / name)
    (code / 'python').write_text((code / 'python').read_text().replace('@REAL_PYTHON@', '/usr/local/bin/python3'))
    (code / 'python').chmod(0o755)
    assets = code / 'assets'; assets.mkdir(); fixture_assets(assets)
    sha = hashlib.sha256((assets / 'manifest.json').read_bytes()).hexdigest()
    (code / 'config.json').write_text(json.dumps({'python': '/usr/local/bin/python3', 'manifest_sha256': sha}))
    spec = {'kind': 'exec', 'backend': 'observed-v1', 'image': image,
            'python': '/opt/hg-hf/python', 'workdir': '/workspace', 'network': 'none',
            'cpus': 1, 'memory_mb': 512, 'pids': 64,
            'filesystems': {'/workspace': {'bytes': 2 << 30, 'inodes': 200000}}}
    runtime = Runtime(spec, binding={'adapter_id': 'hf-component', 'run_id': 'unit',
                                    'trial_id': '1', 'manifest_sha256': sha},
                      timeout_s=30, mounts=[(code, '/opt/hg-hf')])
    with runtime:
        probe = runtime.run(['/opt/hg-hf/python', '--hf-mirror-health'])
        assert probe.returncode == 0, probe.stderr
        first = json.loads(probe.stdout)
        assert runtime.run(['/opt/hg-hf/python', '-c',
            'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:18765/owner/model/resolve/main/model.bin").read()==b"public-file\\x00binary"']).returncode == 0
        repeat = runtime.run(['/opt/hg-hf/python', '--hf-mirror-health'])
        assert json.loads(repeat.stdout)['pid'] == first['pid']
        attack = runtime.run(['/opt/hg-hf/python', '-c',
                              'open("/tmp/hg-hf-identity.json","w").write("{}")'])
        assert attack.returncode != 0 and 'PermissionError' in attack.stderr
        checked('exec', runtime.name, '/usr/local/bin/python3', '-c', f'import os; os.kill({first["pid"]}, 9)')
        failed = runtime.run(['/opt/hg-hf/python', '--hf-mirror-health'])
        assert failed.returncode == 78 and 'mirror unavailable' in failed.stderr
    assert runtime.receipt['cleanup']['complete']
    # A fresh verifier-like container cannot reuse the prior service or cache.
    second = Runtime(spec, binding={'adapter_id': 'hf-component', 'run_id': 'fresh',
                                   'trial_id': '1', 'manifest_sha256': sha},
                     timeout_s=30, mounts=[(code, '/opt/hg-hf')])
    with second:
        fresh = second.run(['/opt/hg-hf/python', '--hf-mirror-health'])
        assert fresh.returncode == 0
        assert json.loads(fresh.stdout)['nonce_sha256'] != first['nonce_sha256']
    assert second.receipt['cleanup']['complete']
    # A changed asset is rejected before any candidate process starts, and the
    # partially started root service/container is cleaned up as well.
    item = json.loads((assets / 'manifest.json').read_text())['repos']['owner/model']['files']['model.bin']
    (assets / 'blobs' / item['sha256']).write_bytes(b'corrupt')
    broken = Runtime(spec, binding={'adapter_id': 'hf-component', 'run_id': 'broken',
                                   'trial_id': '1', 'manifest_sha256': sha},
                     timeout_s=30, mounts=[(code, '/opt/hg-hf')])
    with pytest.raises((RuntimeError, OSError)):
        with broken: pytest.fail('corrupt assets unexpectedly started')
    assert broken.receipt['cleanup']['complete']


def test_build_context_contains_only_verified_assets(tmp_path):
    build = module('build')
    assets = tmp_path / 'assets'; assets.mkdir(); fixture_assets(assets)
    (assets / 'blobs/failed-download.partial').write_bytes(b'incomplete')
    output = tmp_path / 'context'
    result = build.stage(assets, output, 'sha256:' + 'a' * 64, '/usr/local/bin/python3')
    assert result['runtime_python'] == '/opt/hg-hf/python'
    assert 'exec /usr/local/bin/python3 -I -S ' in (output / 'python').read_text()
    assert '@REAL_PYTHON@' not in (output / 'python').read_text()
    assert not (output / 'assets/blobs/failed-download.partial').exists()
    assert 'ENTRYPOINT' not in (output / 'Dockerfile').read_text()
    with pytest.raises(ValueError, match='immutable'):
        build.stage(assets, tmp_path / 'bad', 'image:latest', '/usr/local/bin/python3')


def test_real_hub_clients_and_released_llama_resolver(tmp_path):
    """Opt-in real dependency resolution; the model constructor is not executed."""
    import os
    import shutil
    import sys
    image = os.environ.get('HG_COOPERBENCH_HF_TEST_IMAGE')
    location = os.environ.get('HG_COOPERBENCH_HF_TEST_ASSETS')
    if not image or not location: pytest.skip('requires explicit pinned local image and prepared assets')
    assets = Path(location)
    manifest, sha = module('server').load(assets)
    code = tmp_path / 'mirror'; code.mkdir()
    (code / 'assets').mkdir()  # Nested read-only bind target must already exist.
    for name in ('server.py', 'launcher.py', 'python'): shutil.copyfile(SOURCE / name, code / name)
    (code / 'python').write_text((code / 'python').read_text().replace('@REAL_PYTHON@', '/usr/local/bin/python3'))
    (code / 'python').chmod(0o755)
    (code / 'config.json').write_text(json.dumps({'python': '/usr/local/bin/python3', 'manifest_sha256': sha}))
    sys.path.insert(0, str(ROOT))
    from eval.containers.observed import Runtime
    spec = {'kind': 'exec', 'backend': 'observed-v1', 'image': image,
            'python': '/opt/hg-hf/python', 'workdir': '/workspace', 'network': 'none',
            'cpus': 1, 'memory_mb': 1536, 'pids': 128,
            'filesystems': {'/workspace': {'bytes': 2 << 30, 'inodes': 200000}}}
    runtime = Runtime(spec, binding={'adapter_id': 'hf-resolution-control', 'run_id': 'component',
                                    'trial_id': '1', 'manifest_sha256': sha},
                      timeout_s=120, mounts=[(code, '/opt/hg-hf'), (assets, '/opt/hg-hf/assets')])
    program = '''
import hashlib,json
from pathlib import Path
from huggingface_hub import HfFileSystem,hf_hub_download
from llama_cpp import Llama
manifest=json.loads(Path('/opt/hg-hf/assets/manifest.json').read_text())
repo='M4-ai/TinyMistral-248M-v2-Instruct-GGUF'
name='TinyMistral-248M-v2-Instruct.Q4_K_M.gguf'
assert any(x['name'].endswith(name) for x in HfFileSystem().ls(repo,recursive=True))
class RecordConstruction:
    def __init__(self,**kwargs): self.path=kwargs['model_path']
# The released resolver is unchanged. Only its final model construction is a
# recording control, so this test loads no model and performs no inference.
resolved=Llama.from_pretrained.__func__(RecordConstruction,repo_id=repo,filename=name)
assert resolved.path.startswith('/workspace/.hf-cache/')
assert hashlib.sha256(Path(resolved.path).read_bytes()).hexdigest()==manifest['repos'][repo]['files'][name]['sha256']
repo='erwanf/gpt2-mini'
for name,item in manifest['repos'][repo]['files'].items():
    path=hf_hub_download(repo,filename=name)
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==item['sha256']
from transformers import AutoConfig,AutoTokenizer
assert AutoConfig.from_pretrained(repo).model_type=='gpt2'
assert AutoTokenizer.from_pretrained(repo).encode('offline dependency check')
print('released resolver and all pinned dependency bytes verified; no model loaded')
'''
    with runtime:
        result = runtime.run(['/opt/hg-hf/python', '-c', program])
        assert result.returncode == 0, result.stderr[-4000:]
        assert 'no model loaded' in result.stdout
        assert runtime.run(['/opt/hg-hf/python', '--hf-mirror-health']).returncode == 0
    assert runtime.receipt['cleanup']['complete']


@pytest.mark.parametrize('bad_range', [False, True])
def test_download_resume_requires_origin_range_and_final_hash(tmp_path, monkeypatch, bad_range):
    import io
    mod = module('prepare')
    content = b'fixed public bytes'
    revision = 'a' * 40
    oid = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
    tree = [{'path': 'config.json', 'type': 'file', 'size': len(content), 'oid': oid}]
    monkeypatch.setattr(mod, 'SOURCES', {'owner/model': (revision, None)})
    def metadata(path):
        value = tree if '/tree/' in path else {'sha': revision}
        return json.dumps(value).encode(), value
    monkeypatch.setattr(mod, 'metadata', metadata)
    class Response(io.BytesIO):
        def __init__(self, raw, headers):
            super().__init__(raw); self.headers = headers; self.status = 206 if 'Content-Range' in headers else 200
    class Head:
        def open(self, *args, **kwargs):
            return Response(b'', {'ETag': oid, 'X-Repo-Commit': revision, 'Content-Length': str(len(content))})
    monkeypatch.setattr(mod.urllib.request, 'build_opener', lambda *args: Head())
    requests = []
    def request(path, **kwargs):
        headers = kwargs.get('headers', {}); requests.append(headers)
        if len(requests) == 1: return Response(content[:5], {})
        assert headers == {'Range': 'bytes=5-'}
        return Response(content[5:], {'Content-Range': 'bytes 0-1/2' if bad_range else f'bytes 5-{len(content)-1}/{len(content)}'})
    monkeypatch.setattr(mod, 'request', request)
    target = tmp_path / 'prepared'
    if bad_range:
        with pytest.raises(ValueError, match='exact resume range'): mod.prepare(target)
        assert not (target / 'manifest.json').exists()
    else:
        result = mod.prepare(target)
        item = result['repos']['owner/model']['files']['config.json']
        assert (target / 'blobs' / item['sha256']).read_bytes() == content
        assert len(requests) == 2


def test_real_worker_cannot_hide_mid_episode_mirror_loss(tmp_path, monkeypatch):
    """The actual worker launcher must reject an actor that swallows service loss."""
    import concurrent.futures
    import os
    import queue
    import socket
    import sys
    import uuid
    image = os.environ.get('HG_COOPERBENCH_HF_TEST_IMAGE')
    if not image: pytest.skip('requires explicit already-local container image')
    sys.path.insert(0, str(ROOT))
    from eval.containers.observed import Runtime, checked
    from eval.run import container_workers
    assets = tmp_path / 'assets'; assets.mkdir(); fixture_assets(assets)
    context = tmp_path / 'context'
    module('build').stage(assets, context, image, '/usr/local/bin/python3')
    with (context / 'Dockerfile').open('a') as dockerfile:
        dockerfile.write('RUN /usr/local/bin/python3 -I -S -c "import pathlib,sysconfig; pathlib.Path(sysconfig.get_path(\'purelib\'), \'hf_fixture.pth\').write_text(\'/workspace/repo\\n\')"\n')
    tag = 'hg-hf-component-' + uuid.uuid4().hex[:12]
    monkeypatch.setenv('DOCKER_BUILDKIT', '0')
    checked('build', '--network=none', '--pull=false', '-t', tag, str(context), timeout=120)
    built = json.loads(checked('image', 'inspect', tag))[0]['Id']
    ready = queue.Queue()
    class CaptureRuntime(Runtime):
        def __enter__(self):
            result = super().__enter__(); ready.put(self); return result
    monkeypatch.setattr(container_workers, 'Runtime', CaptureRuntime)
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    candidate = tmp_path / 'candidate'; candidate.mkdir()
    (candidate / 'harness.json').write_text(json.dumps({'entrypoint': 'agent.py'}))
    (candidate / 'agent.py').write_text('''
import argparse,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
work=Path(a.workdir)
(work/'repo').mkdir(exist_ok=True)
(work/'repo/sitecustomize.py').write_text("import os; from pathlib import Path; Path('/tmp/hf-root-startup-executed').write_text('bad') if os.getuid()==0 else None")
(work/'started').write_text('1')
for _ in range(500):
    if (work/'release').exists(): break
    time.sleep(.02)
else: raise RuntimeError('host did not release actor')
(work/'answer.txt').write_text('candidate returned zero')
''')
    spec = {'kind': 'exec', 'backend': 'observed-v1', 'image': built,
            'python': '/opt/hg-hf/python', 'workdir': '/workspace', 'network': 'none',
            'cpus': 1, 'memory_mb': 512, 'pids': 64,
            'filesystems': {'/workspace': {'bytes': 2 << 30, 'inodes': 200000}}}
    private = tmp_path / 'private'; private.mkdir()
    channel = tmp_path / 'tool.sock'
    listener = socket.socket(socket.AF_UNIX); listener.bind(str(channel)); listener.listen()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(container_workers.run, candidate,
                {'task_id': 'hf-component/worker', 'goal': 'control', 'inputs': []},
                worker={'id': 'agent1', 'env': spec, 'files': [], 'artifacts': ['agent.patch']},
                private=private, channel=channel, timeout_s=30,
                run_id='component', trial_id='trial-1', run_seed=1)
            runtime = ready.get(timeout=60)
            checked('exec', runtime.name, '/usr/local/bin/python3', '-I', '-S', '-c', '''
import json,os,time
from pathlib import Path
for _ in range(500):
    if Path('/workspace/started').exists(): break
    time.sleep(.02)
else: raise RuntimeError('actor never started')
import subprocess
probe=subprocess.run(['/opt/hg-hf/python','-I','-S','/run/harnessgrad-runtime/helper.py','identity'],
                     env={**os.environ,'PYTHONPATH':'/workspace/repo'},capture_output=True,text=True)
assert probe.returncode==0 and json.loads(probe.stdout)['uid']==0,probe.stderr
assert not Path('/tmp/hf-root-startup-executed').exists(),'actor code ran during root helper startup'
identity=json.loads(Path('/tmp/hg-hf-identity.json').read_text())
os.kill(identity['pid'],9)
Path('/workspace/release').write_text('1')
''')
            result = future.result(timeout=60)
        assert result['answer'] == 'candidate returned zero'
        assert result['exit_code'] == 78, result
        assert 'mirror unavailable' in result['stderr']
        assert result['runtime_services']['container']['cleanup']['complete']
        scoring = tmp_path / 'scoring'; scoring.mkdir()
        (scoring / 'check.py').write_text('''
from pathlib import Path
import time
root=Path('/workspace');(root/'verifier').mkdir(exist_ok=True);(root/'logs').mkdir(exist_ok=True)
(root/'verifier/reward.txt').write_text('1')
(root/'verifier').chmod(0o555)
(root/'logs/official-reward.json').write_text('{"reward":1}')
(root/'started').write_text('1')
for _ in range(500):
    if (root/'release').exists():break
    time.sleep(.02)
else:raise RuntimeError('scorer release timeout')
''')
        from eval.verification import observed, verifier
        monkeypatch.setattr(observed, 'Runtime', CaptureRuntime)
        verify_spec = {
            'kind': 'command', 'argv': ['/opt/hg-hf/python', 'check.py'],
            'inputs': [{'path': 'check.py', 'content': (scoring / 'check.py').read_text()}],
            'reward_file': '/verifier/reward.txt',
            'evidence_file': '/logs/official-reward.json',
            'container_runtime': spec,
            'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True},
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(verifier._verify, scoring, verify_spec, '', sandbox=True,
                                 timeout_s=30, task_id='hf-component/scorer',
                                 run_id='component-scorer', trial_id='trial-1')
            scorer = ready.get(timeout=60)
            checked('exec', scorer.name, '/usr/local/bin/python3', '-I', '-S', '-c', '''
import json,os,time
from pathlib import Path
for _ in range(500):
    if Path('/workspace/started').exists():break
    time.sleep(.02)
else:raise RuntimeError('scorer never started')
os.kill(json.loads(Path('/tmp/hg-hf-identity.json').read_text())['pid'],9)
Path('/workspace/release').write_text('1')
''')
            scored = future.result(timeout=60)
        assert scored['score'] is None and scored['outcome'] == 'unmeasured', scored
        assert scored['exit_code'] == 78 and 'requires exit 0' in scored['unmeasured']
        # Neither the failed scorer nor its wrapper must delete reward files:
        # the platform rejects their contents using the actual process exit.
        assert (scoring / 'verifier/reward.txt').read_text() == '1'
        assert (scoring / 'logs/official-reward.json').read_text() == '{"reward":1}'
        assert scored['container_runtime']['cleanup']['complete']
    finally:
        listener.close()
        checked('image', 'rm', tag, timeout=60)


@pytest.mark.parametrize('changed', [False, True])
def test_verifier_mirror_identity_gate_preserves_valid_score_or_withholds_reward(tmp_path, monkeypatch, changed):
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('cooper_mirror_check', SOURCE.parent / 'verify/check.py')
    check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'private').mkdir()
    (tmp_path / 'private/case.json').write_text(json.dumps({'task_id': 'fixture', 'base_commit': 'base',
        'private_sha256': {}, 'provenance': {'runtime': {'python': '/mirror/python',
        'hf_mirror': {'manifest_sha256': 'expected'}}}}))
    monkeypatch.setattr(check.subprocess, 'check_output', lambda *a, **kw: 'base')
    calls = []
    def execute(argv, **kwargs):
        if argv == ['/mirror/python', '--hf-mirror-health']:
            calls.append('health')
            identity = 'changed' if changed and len(calls) > 1 else 'expected'
            return SimpleNamespace(returncode=0, stdout=json.dumps({'manifest_sha256': identity}), stderr='')
        assert argv == ['bash', 'private/test.sh']
        official = tmp_path / 'logs/verifier'; official.mkdir(parents=True)
        (official / 'reward.txt').write_text('1')
        (official / 'reward.json').write_text('{"reward":1}')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(check.subprocess, 'run', execute)
    assert check.main() == (3 if changed else 0)
    reward = tmp_path / 'verifier/reward.txt'
    assert reward.exists() is not changed
    assert len(calls) == 2
    assert (tmp_path / 'logs/verifier/reward.txt').read_text() == '1'
