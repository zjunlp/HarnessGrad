"""Prepare only the pinned public baseline HF dependencies; never run tests/models."""
from __future__ import annotations
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import urllib.parse
import urllib.request

SOURCES = {
    'erwanf/gpt2-mini': ('f12cc7ee54aa4d3e6366597f72ff7acb39b0ab3a', None),
    'M4-ai/TinyMistral-248M-v2-Instruct-GGUF': (
        '114104e33976dc4e499866d2b5951c8558fa8d14',
        {'TinyMistral-248M-v2-Instruct.Q4_K_M.gguf'}),
}
ORIGIN = 'https://huggingface.co'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def request(path, *, method='GET', headers=None):
    # No tokens, ambient HF login, retries, or model API. urllib follows the
    # public Hub's download redirects only during this explicit preparation.
    return urllib.request.urlopen(urllib.request.Request(ORIGIN + path, method=method, headers=headers or {}), timeout=60)


def metadata(path):
    with request(path) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024 or response.headers.get('Link'):
            raise ValueError('unexpected oversized/paginated fixed source metadata')
    return raw, json.loads(raw)


def prepare(root, max_total_bytes=None):
    if max_total_bytes is not None and (type(max_total_bytes) is not int or max_total_bytes <= 0):
        raise ValueError('max_total_bytes must be a positive integer or None')
    root.mkdir(parents=True, exist_ok=False)
    blobs = root / 'blobs'; blobs.mkdir()
    manifest = {'version': 1, 'origin': ORIGIN, 'repos': {}, 'total_bytes': 0,
                'preparation_limits': {'max_total_bytes': max_total_bytes}}
    for repo, (revision, wanted) in SOURCES.items():
        info_raw, info = metadata(f'/api/models/{repo}/revision/{revision}')
        tree_raw, tree = metadata(f'/api/models/{repo}/tree/{revision}?recursive=true&expand=false')
        if info['sha'] != revision:
            raise ValueError('public source revision mismatch')
        row = {'revision': revision, 'info': info, 'tree': tree,
               'info_sha256': digest(info_raw), 'tree_sha256': digest(tree_raw), 'files': {}}
        # Keep original response bytes as provenance, not a synthesized listing.
        for kind, raw in [('info', info_raw), ('tree', tree_raw)]:
            key = digest(raw); (blobs / key).write_bytes(raw); row[kind + '_blob'] = key
        for entry in tree:
            name = entry['path']
            if entry['type'] != 'file' or (wanted is not None and name not in wanted):
                continue
            if name.startswith('/') or '..' in Path(name).parts:
                raise ValueError('unsafe public path')
            size = entry['size']
            if type(size) is not int or size < 0:
                raise ValueError('invalid public dependency size')
            if max_total_bytes is not None and manifest['total_bytes'] + size > max_total_bytes:
                raise ValueError('explicit dependency byte budget exceeded')
            path = f'/{repo}/resolve/{revision}/{urllib.parse.quote(name)}'
            # Capture the Hub HEAD fact at the initial endpoint: the linked ETag
            # is the LFS object identity, not the CDN's multipart ETag.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            try:
                response = urllib.request.build_opener(NoRedirect).open(
                    urllib.request.Request(ORIGIN + path, method='HEAD'), timeout=60)
            except urllib.error.HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308): raise
                response = exc
            with response:
                headers = response.headers
                etag = (headers.get('X-Linked-Etag') or headers.get('ETag') or '').strip('"')
                commit = headers.get('X-Repo-Commit')
                linked_size = headers.get('X-Linked-Size') or (headers.get('Content-Length') if response.status == 200 else None)
            if commit != revision or not etag or (linked_size is not None and int(linked_size) != size):
                raise ValueError(f'public HEAD identity/size mismatch for {repo}/{name}')
            expected = entry.get('lfs', {}).get('oid')
            if etag != (expected or entry['oid']):
                raise ValueError('HEAD ETag differs from public tree identity')
            # Bounded transport recovery can resume an interrupted public file,
            # but only when the origin confirms the exact byte range. Final
            # Git/LFS identity always covers the entire assembled file.
            temporary = blobs / ('download-' + hashlib.sha256(name.encode()).hexdigest() + '.partial')
            sha = hashlib.sha256()
            git_sha = hashlib.sha1(f'blob {size}\0'.encode())
            count = 0
            for attempt in range(3):
                try:
                    headers = {'Range': f'bytes={count}-'} if count else {}
                    with request(path, headers=headers) as response, temporary.open('ab') as output:
                        if response.status != (206 if count else 200):
                            raise ValueError('unexpected public download status')
                        if count and response.headers.get('Content-Range') != f'bytes {count}-{size - 1}/{size}':
                            raise ValueError('origin refused exact resume range')
                        while chunk := response.read(1024 * 1024):
                            count += len(chunk)
                            if count > size: raise ValueError('download exceeds public size')
                            output.write(chunk); sha.update(chunk); git_sha.update(chunk)
                except urllib.error.HTTPError:
                    raise
                except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead):
                    if attempt == 2: raise
                    continue
                if count != size:
                    if attempt == 2: raise ValueError('public download truncated after three attempts')
                    continue
                actual = sha.hexdigest()
                if (actual if expected else git_sha.hexdigest()) != (expected or entry['oid']):
                    raise ValueError('download does not match public Git/LFS identity')
                temporary.rename(blobs / actual)
                break
            row['files'][name] = {'sha256': actual, 'size': size, 'etag': etag, 'commit': commit}
            manifest['total_bytes'] += size
        if wanted and set(row['files']) != wanted:
            raise ValueError('required baseline dependency missing from source')
        manifest['repos'][repo] = row
    (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--max-total-bytes', type=int, help='Optional total public asset download budget; no adapter default cap')
    args = parser.parse_args()
    result = prepare(args.root, args.max_total_bytes)
    print(json.dumps({'repos': list(result['repos']), 'bytes': result['total_bytes'],
                      'manifest_sha256': digest((args.root / 'manifest.json').read_bytes())}))
