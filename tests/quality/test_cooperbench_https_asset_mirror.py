"""Fixed HTTPS dependencies use real TLS and ordinary requests/httpx clients."""
import hashlib
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import ssl
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'data/datasets/cooperbench/https_asset_mirror'


def module(name):
    spec = importlib.util.spec_from_file_location('fixed_https_' + name, SOURCE / (name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def assets(tmp_path, monkeypatch):
    # Small local bytes exercise transport independently of the public-download
    # control. Real preparations only accept the pinned 13264-byte W3C file.
    prepare = module('prepare')
    data = b'%PDF-1.4\nlocal transport control\n'
    monkeypatch.setattr(prepare, 'PDF_SIZE', len(data))
    monkeypatch.setattr(prepare, 'PDF_SHA256', hashlib.sha256(data).hexdigest())
    source = tmp_path / 'source.pdf'; source.write_bytes(data)
    metadata = tmp_path / 'source.json'; metadata.write_text(json.dumps({
        'url': prepare.URL, 'final_url': prepare.URL, 'status': 200,
        'headers': {'Content-Type': 'application/pdf'}, 'retrieved_at': 'fixture'}))
    root = tmp_path / 'assets'
    prepare.prepare(root, source=source, metadata=metadata)
    return root, data


@pytest.fixture
def mirror(assets):
    root, data = assets
    server = module('server')
    http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler(root, {'manifest_sha256': 'fixture'}))
    http.daemon_threads = True
    thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
    try: yield root, data, 'http://127.0.0.1:' + str(http.server_port)
    finally: http.shutdown(); http.server_close(); thread.join(timeout=2)


def test_real_clients_preserve_tls_and_fixed_bytes(mirror, monkeypatch):
    requests = pytest.importorskip('requests')
    httpx = pytest.importorskip('httpx')
    root, data, proxy = mirror
    monkeypatch.setenv('HTTPS_PROXY', proxy); monkeypatch.setenv('https_proxy', proxy)
    monkeypatch.setenv('NO_PROXY', ''); monkeypatch.setenv('no_proxy', '')
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', str(root / 'ca.pem'))
    monkeypatch.setenv('SSL_CERT_FILE', str(root / 'ca.pem'))
    url = module('server').URL
    assert requests.get(url, timeout=3).content == data
    assert httpx.get(url, timeout=3).content == data
    assert requests.head(url, timeout=3).headers['Content-Length'] == str(len(data))
    assert not list(root.glob('*ca.key*'))
    assert (root / 'server.key').stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize('suffix,headers', [('/extra', {}), ('?q=1', {}), ('?q=', {}),
    ('', {'Host': 'wrong.example'}), ('', {'Host': 'www.w3.org:444'}),
    ('', {'Content-Length': '1'})])
def test_unknown_http_requests_are_rejected(mirror, suffix, headers):
    requests = pytest.importorskip('requests')
    root, _, proxy = mirror
    session = requests.Session(); session.trust_env = False
    response = session.get(module('server').URL + suffix, headers=headers,
                           proxies={'https': proxy}, verify=str(root / 'ca.pem'), timeout=3)
    assert response.status_code == 404


def test_unknown_connect_and_default_ca_fail(mirror):
    requests = pytest.importorskip('requests')
    root, _, proxy = mirror
    session = requests.Session(); session.trust_env = False
    with pytest.raises(requests.exceptions.ProxyError):
        session.get('https://wrong.example/resource', proxies={'https': proxy}, verify=str(root / 'ca.pem'), timeout=3)
    with pytest.raises(requests.exceptions.SSLError):
        session.get(module('server').URL, proxies={'https': proxy}, timeout=3)


def test_wrong_sni_fails(mirror):
    root, _, proxy = mirror
    port = int(proxy.rsplit(':', 1)[1])
    with socket.create_connection(('127.0.0.1', port), timeout=3) as connection:
        connection.sendall(b'CONNECT www.w3.org:443 HTTP/1.0\r\n\r\n')
        response = b''
        while not response.endswith(b'\r\n\r\n'): response += connection.recv(1)
        assert b' 200 ' in response
        context = ssl.create_default_context(cafile=str(root / 'ca.pem'))
        with pytest.raises(ssl.SSLError):
            context.wrap_socket(connection, server_hostname='wrong.example')


@pytest.mark.parametrize('host', ['wrong.example:443', 'www.w3.org:444'])
def test_wrong_connect_host_header_fails(mirror, host):
    _, _, proxy = mirror
    with socket.create_connection(('127.0.0.1', int(proxy.rsplit(':', 1)[1])), timeout=3) as connection:
        connection.sendall(('CONNECT www.w3.org:443 HTTP/1.0\r\nHost: ' + host + '\r\n\r\n').encode())
        assert b' 403 ' in connection.recv(4096)


def test_request_header_and_time_limits(mirror):
    import time
    _, _, proxy = mirror
    address = ('127.0.0.1', int(proxy.rsplit(':', 1)[1]))
    with socket.create_connection(address, timeout=3) as connection:
        connection.sendall(b'GET /health HTTP/1.0\r\nX-Large: ' + b'x' * 65536 + b'\r\n\r\n')
        assert b' 431 ' in connection.recv(4096)
    with socket.create_connection(address, timeout=7) as connection:
        start = time.monotonic()
        connection.sendall(b'GET /health HTTP/1.0\r\nX-Slow: ')
        assert connection.recv(4096) == b''
        assert time.monotonic() - start < 6.5


def test_asset_tampering_is_rejected(assets, monkeypatch):
    root, data = assets
    mod = module('server')
    monkeypatch.setattr(mod, 'PDF_SIZE', len(data))
    monkeypatch.setattr(mod, 'PDF_SHA256', hashlib.sha256(data).hexdigest())
    mod.load(root)
    (root / 'dummy.pdf').write_bytes(b'changed')
    with pytest.raises(ValueError, match='identity mismatch'): mod.load(root)


def test_preparation_refuses_substituted_public_bytes(tmp_path):
    prepare = module('prepare')
    source = tmp_path / 'bad.pdf'; source.write_bytes(b'%PDF-bad')
    meta = tmp_path / 'meta.json'; meta.write_text(json.dumps({
        'url': prepare.URL, 'final_url': prepare.URL, 'status': 200,
        'headers': {'Content-Type': 'application/pdf'}, 'retrieved_at': 'fixture'}))
    with pytest.raises(ValueError, match='identity differ'):
        prepare.prepare(tmp_path / 'prepared', source=source, metadata=meta)
    assert not (tmp_path / 'prepared/manifest.json').exists()


def test_real_prepared_asset_build_excludes_ca_signing_key(tmp_path):
    path = os.environ.get('HG_COOPERBENCH_HTTPS_TEST_ASSETS')
    if not path: pytest.skip('requires explicitly prepared public asset directory')
    assets = Path(path)
    target = tmp_path / 'build'
    identity = module('build').stage(assets, target, 'sha256:' + '1' * 64, '/usr/local/bin/python3')
    assert identity['resource_sha256'] == module('server').PDF_SHA256
    assert set(p.name for p in (target / 'assets').iterdir()) == {
        'dummy.pdf', 'ca.pem', 'server.pem', 'server.key', 'manifest.json'}
    assert 'chmod 0400 /opt/hg-hf/assets/server.key' in (target / 'Dockerfile').read_text()
    assert (target / 'assets/server.key').stat().st_mode & 0o777 == 0o400


def test_real_observed_clients_private_key_and_failed_scorer(tmp_path, monkeypatch):
    """Opt-in real image control: no replacement loader, network or PDF bytes."""
    import concurrent.futures
    import queue
    import sys
    import uuid
    image = os.environ.get('HG_COOPERBENCH_HTTPS_TEST_IMAGE')
    assets_path = os.environ.get('HG_COOPERBENCH_HTTPS_TEST_ASSETS')
    if not image or not assets_path: pytest.skip('requires explicit local image and prepared assets')
    sys.path.insert(0, str(ROOT))
    from eval.containers.observed import Runtime, checked
    from eval.verification import observed, verifier
    context = tmp_path / 'build'
    identity = module('build').stage(Path(assets_path), context, image, '/usr/local/bin/python3')
    tag = 'hg-https-asset-control-' + uuid.uuid4().hex[:12]
    monkeypatch.setenv('DOCKER_BUILDKIT', '0')
    checked('build', '--network=none', '--pull=false', '-t', tag, str(context), timeout=120)
    built = json.loads(checked('image', 'inspect', tag))[0]['Id']
    spec = {'kind': 'exec', 'backend': 'observed-v1', 'image': built,
            'python': '/opt/hg-hf/python', 'workdir': '/workspace', 'network': 'none',
            'cpus': 1, 'memory_mb': 6488, 'pids': 128,
            'filesystems': {'/workspace': {'bytes': 4656685056, 'inodes': 200000}}}
    receipts = {'identity': identity, 'image': built, 'source_image': image}
    try:
        runtime = Runtime(spec, binding={'adapter_id': 'w3c-asset', 'run_id': 'clients',
            'trial_id': '1', 'manifest_sha256': identity['manifest_sha256']}, timeout_s=60)
        try:
            with runtime:
                proc = runtime.run(['/opt/hg-hf/python', '-c', '''
import hashlib,json,os
from pathlib import Path
import httpx,requests,certifi
assert os.getuid()==65534
key=Path('/opt/hg-hf/assets/server.key')
assert key.stat().st_uid==0 and key.stat().st_mode&0o777==0o400
try:key.read_bytes()
except PermissionError:pass
else:raise AssertionError('actor read private TLS key')
assert not Path('/opt/hg-hf/assets/ca.key').exists()
url='https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf'
expected='3df79d34abbca99308e79cb94461c1893582604d68329a41fd4bec1885e6adb4'
assert hashlib.sha256(requests.get(url,timeout=5).content).hexdigest()==expected
assert hashlib.sha256(httpx.get(url,timeout=5).content).hexdigest()==expected
s=requests.Session();s.trust_env=False
try:s.get(url,proxies={'https':os.environ['HTTPS_PROXY']},verify=certifi.where(),timeout=5)
except requests.exceptions.SSLError:pass
else:raise AssertionError('public CA unexpectedly trusted local mirror')
from llama_index.core.base.llms.types import DocumentBlock
doc=DocumentBlock(url=url,title='dummy_pdf')
assert hashlib.sha256(doc.resolve_document().read()).hexdigest()==expected
doc._guess_mimetype();assert doc.document_mimetype=='application/pdf'
print(json.dumps({'requests':True,'httpx':True,'official_document_block':True,
                  'actor_key_denied':True,'default_ca_rejected':True}))
'''])
                receipts['clients'] = {'returncode': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr[-4000:]}
                assert proc.returncode == 0, proc.stderr
        finally: receipts['clients_runtime'] = runtime.receipt
        assert runtime.receipt['cleanup']['complete']
        ready = queue.Queue()
        class Capture(Runtime):
            def __enter__(self):
                result = super().__enter__(); ready.put(self); return result
        monkeypatch.setattr(observed, 'Runtime', Capture)
        work = tmp_path / 'verify'; work.mkdir()
        verify_spec = {'kind': 'command', 'argv': ['/opt/hg-hf/python', 'check.py'],
            'inputs': [{'path': 'check.py', 'content': '''
from pathlib import Path
import time
root=Path('/workspace');(root/'verifier').mkdir(exist_ok=True)
(root/'verifier/reward.txt').write_text('1');(root/'verifier').chmod(0o555)
(root/'started').write_text('1')
for _ in range(500):
    if (root/'release').exists():break
    time.sleep(.02)
else:raise RuntimeError('host release timeout')
'''}], 'reward_file': '/verifier/reward.txt', 'container_runtime': spec,
            'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(verifier._verify, work, verify_spec, '', sandbox=True,
                timeout_s=60, task_id='w3c-asset/fault', run_id='scorer-fault', trial_id='1')
            scorer = ready.get(timeout=90)
            checked('exec', scorer.name, '/usr/local/bin/python3', '-I', '-S', '-c', '''
import json,os,time
from pathlib import Path
for _ in range(500):
    if Path('/workspace/started').exists():break
    time.sleep(.02)
else:raise RuntimeError('scorer did not start')
os.kill(json.loads(Path('/tmp/hg-hf-identity.json').read_text())['pid'],9)
Path('/workspace/release').write_text('1')
''')
            scored = future.result(timeout=90)
        receipts['fault'] = scored
        assert scored['exit_code'] == 78 and scored['score'] is None, scored
        assert scored['outcome'] == 'unmeasured' and 'requires exit 0' in scored['unmeasured']
        assert (work / 'verifier/reward.txt').read_text() == '1'
        assert scored['container_runtime']['cleanup']['complete']
    finally:
        checked('image', 'rm', tag, timeout=60)
        output = os.environ.get('HG_COOPERBENCH_HTTPS_TEST_RECEIPT')
        if output:
            target = Path(output).resolve()
            if target.is_relative_to(ROOT): raise ValueError('control evidence must stay outside repository')
            target.write_text(json.dumps(receipts, indent=2) + '\n')
