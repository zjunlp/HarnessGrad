"""Fetch the fixed W3C resource and issue a dedicated, narrowly scoped local CA."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import importlib.util
import subprocess
import tempfile
import urllib.request

_spec = importlib.util.spec_from_file_location('fixed_https_asset_server', Path(__file__).with_name('server.py'))
_server = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_server)
URL, PDF_SHA256, PDF_SIZE = _server.URL, _server.PDF_SHA256, _server.PDF_SIZE


def prepare(root, *, source=None, metadata=None):
    root.mkdir(parents=True, exist_ok=False)
    if source is None:
        with urllib.request.urlopen(URL, timeout=30) as response:
            data = response.read(PDF_SIZE + 1)
            provenance = {'url': URL, 'final_url': response.url, 'status': response.status,
                          'headers': dict(response.headers),
                          'retrieved_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    else:
        data = source.read_bytes(); provenance = json.loads(metadata.read_text())
    if (len(data) != PDF_SIZE or hashlib.sha256(data).hexdigest() != PDF_SHA256
            or provenance['url'] != URL or provenance['final_url'] != URL or provenance['status'] != 200
            or not provenance.get('headers') or not provenance.get('retrieved_at')):
        raise ValueError('public resource bytes or recorded download identity differ')
    (root / 'dummy.pdf').write_bytes(data)
    # The signing key exists only in this temporary preparation directory; it is
    # destroyed before publication and never enters the immutable runtime layer.
    with tempfile.TemporaryDirectory(prefix='hg-w3c-ca-') as temporary:
        temp = Path(temporary)
        def openssl(*args):
            subprocess.run(['openssl', *args], check=True, capture_output=True, timeout=30)
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
                '-subj', '/CN=HarnessGrad fixed W3C resource local CA',
                '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                '-addext', 'nameConstraints=critical,permitted;DNS:www.w3.org',
                '-keyout', str(temp / 'ca.key'), '-out', str(root / 'ca.pem'))
        openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=www.w3.org',
                '-keyout', str(root / 'server.key'), '-out', str(temp / 'server.csr'))
        (temp / 'server.ext').write_text('basicConstraints=critical,CA:FALSE\n'
            'keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n'
            'subjectAltName=DNS:www.w3.org\n')
        openssl('x509', '-req', '-in', str(temp / 'server.csr'), '-CA', str(root / 'ca.pem'),
                '-CAkey', str(temp / 'ca.key'), '-set_serial', '1', '-days', '3650',
                '-extfile', str(temp / 'server.ext'), '-out', str(root / 'server.pem'))
        openssl('verify', '-CAfile', str(root / 'ca.pem'), '-verify_hostname', 'www.w3.org', str(root / 'server.pem'))
    (root / 'server.key').chmod(0o400)
    manifest = {'version': 1, 'url': URL, 'source': provenance,
                'prepared_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'tls_identity': 'local CA mirror, not the W3C public certificate chain',
                'files': {name: {'sha256': hashlib.sha256((root / name).read_bytes()).hexdigest(),
                                 'size': (root / name).stat().st_size}
                          for name in ('dummy.pdf', 'ca.pem', 'server.pem', 'server.key')}}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--metadata', type=Path)
    args = parser.parse_args()
    if bool(args.source) != bool(args.metadata): parser.error('--source and --metadata are required together')
    prepare(args.root, source=args.source, metadata=args.metadata)
