"""Bounded, unauthenticated public metadata acquisition; never fetch dataset bytes.

Run only from /tmp/hg-e2. Every response hop and failure is retained, including
HEAD's empty HTTP entity (whose digest is NOT the archive digest).
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
LIMIT = 2 * 1024 * 1024
PROXY = 'http://127.0.0.1:17891'  # Existing egress route documented by B6.
REQUESTS = {
    'twowiki-later-download-followup-head': ('HEAD', 'https://uca47197cb573ff9881874c910b1.dl.dropboxusercontent.com/cd/0/get/DJ-vT3i2vbrOlt-lfrMN9wUpbF8K7rhwv4gbofApBWgeQPKJxqca-3cR_toxOFKxjnOelfycnC8olSsK7AAGg59rRm6_mr1zuPnLCqUc9YQ7bjlZ3HHEdJuSO4xYtmErAhVH7hgBOiqyB18mNXA2S1UV/file?dl=1'),
    'lcb-pinned-card': ('GET', 'https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/0fe84c3912ea0c4d4a78037083943e8f0c4dd505/README.md'),
    'lcb-refs': ('GET', 'https://huggingface.co/api/datasets/livecodebench/code_generation_lite/refs'),
    'lcb-pinned-history': ('GET', 'https://huggingface.co/api/datasets/livecodebench/code_generation_lite/commits/0fe84c3912ea0c4d4a78037083943e8f0c4dd505'),
    'twowiki-license-issues': ('GET', 'https://api.github.com/search/issues?q=repo%3AAlab-NII%2F2wikimultihop%20license&per_page=100'),
    'twowiki-original-download-head': ('HEAD', 'https://www.dropbox.com/s/9fwhnzektzv4wh5/data_ids.zip?dl=1'),
    'twowiki-later-download-head': ('HEAD', 'https://www.dropbox.com/s/7ep3h8unu2njfxv/data_ids.zip?dl=1'),
    'twowiki-pinned-history': ('GET', 'https://huggingface.co/api/datasets/kamelliao/2wikimultihopqa/commits/f4f0d7e4ae275d9281e90c46c93e66d6cdda3674'),
    'twowiki-releases': ('GET', 'https://api.github.com/repos/Alab-NII/2wikimultihop/releases?per_page=100'),
    'twowiki-tags': ('GET', 'https://api.github.com/repos/Alab-NII/2wikimultihop/tags?per_page=100'),
    'twowiki-original-archive-head': ('HEAD', 'https://www.dropbox.com/s/9fwhnzektzv4wh5/data_ids.zip?dl=0'),
    'twowiki-later-archive-head': ('HEAD', 'https://www.dropbox.com/s/7ep3h8unu2njfxv/data_ids.zip?dl=0'),
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def allowed(method, url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        return False
    if method == 'HEAD':
        return parsed.hostname in {'www.dropbox.com', 'dl.dropboxusercontent.com', 'dropbox.com'} or parsed.hostname.endswith('.dl.dropboxusercontent.com')
    if parsed.hostname == 'api.github.com':
        return parsed.path in {'/repos/Alab-NII/2wikimultihop/releases', '/repos/Alab-NII/2wikimultihop/tags', '/search/issues'}
    if any(token in parsed.path for token in ('/rows', '/first-rows', '.parquet', '.jsonl', '/dev.json', '/train.json', '/id_aliases.json')):
        return False
    if parsed.hostname == 'huggingface.co':
        return parsed.path.startswith('/api/datasets/') or (
            '/resolve/' in parsed.path or '/api/resolve-cache/datasets/' in parsed.path
        ) and parsed.path.endswith('/README.md')
    return False


def acquire(name):
    method, url = REQUESTS[name]
    directory = ROOT / name
    directory.mkdir()  # Never overwrite any previous attempt.
    receipts = []
    opener = build_opener(ProxyHandler({'https': PROXY}), NoRedirect)
    for hop in range(6):
        receipt = dict(method=method, url=url, started_at_utc=datetime.now(timezone.utc).isoformat(),
                       transport_proxy=PROXY, tls_certificate_validation=True,
                       publisher_signature_verified=False)
        if not allowed(method, url):
            receipt['error'] = 'Refused URL outside metadata/HEAD allowlist'
            receipts.append(receipt)
            break
        try:
            request = Request(url, method=method, headers={
                'User-Agent': 'HarnessGrad-metadata-provenance-review',
                'Accept-Encoding': 'identity',
            })
            try:
                response = opener.open(request, timeout=18)
            except HTTPError as error:
                response = error
            with response:
                # HEAD has no response entity; never change it to a GET.
                body = response.read(LIMIT + 1)
                headers = response.headers.as_bytes()
                (directory / f'hop-{hop}.body').write_bytes(body)
                (directory / f'hop-{hop}.headers').write_bytes(headers)
                receipt.update(status=response.code, bytes=len(body),
                               sha256=hashlib.sha256(body).hexdigest(),
                               headers_sha256=hashlib.sha256(headers).hexdigest(),
                               complete=len(body) <= LIMIT,
                               headers_representation='HTTPMessage.as_bytes; parsed response headers')
                location = response.headers.get('Location')
        except Exception as error:
            receipt['error'] = f'{type(error).__name__}: {error}'
            receipts.append(receipt)
            break
        receipts.append(receipt)
        if receipt['complete'] and receipt['status'] in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
        else:
            break
    (directory / 'receipt.json').write_text(json.dumps(receipts, indent=2) + '\n')
    return {'name': name, 'hops': [{k: r[k] for k in ('status', 'bytes', 'sha256', 'error') if k in r} for r in receipts]}


if __name__ == '__main__':
    if Path.cwd() != Path('/tmp/hg-e2'):
        raise SystemExit('Run only in /tmp/hg-e2')
    names = sys.argv[1:] or list(REQUESTS)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for result in executor.map(acquire, names):
            print(json.dumps(result), flush=True)
