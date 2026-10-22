#!/usr/bin/env python3
"""Replay missing B5 bodies only; frozen receipts and SHA256SUMS stay untouched.

The output is a new, exclusive JSONL evidence file outside the frozen packet.
Only an HTTP 200 with exactly the pinned digest and length is materialised.
HEAD bodies with the empty-string pin are materialised without a request.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / 'tests/fixtures/external_provenance_b5'
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
MAX_BYTES = 999_999


def now():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def missing_rows():
    rows = []
    for line in (PACKET / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        path = PACKET / name
        if path.exists():
            continue
        if not name.startswith('responses/') or not name.endswith('.body'):
            raise ValueError('unexpected missing entry: ' + name)
        receipt_path = path.with_suffix('.receipt.json')
        receipt = json.loads(receipt_path.read_bytes())
        body = receipt['response_body']
        if body['path'] != name or body['sha256'] != digest:
            raise ValueError('receipt disagrees with SHA256SUMS: ' + name)
        rows.append((path, receipt_path, receipt, digest))
    return rows


def replay(output, delay, timeout):
    rows = missing_rows()  # Validate every receipt before any network or write.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    # Exclusive creation prevents overwriting or resuming earlier evidence.
    with output.open('x') as evidence:
        evidence.write(json.dumps({
            'type': 'policy', 'started_at_utc': now(),
            'initial_missing': len(rows), 'delay_seconds': delay,
            'timeout_seconds': timeout, 'max_body_bytes': MAX_BYTES,
            'redirects': False, 'proxy': None, 'credentials_added': False,
            'sha256sums_sha256': hashlib.sha256(
                (PACKET / 'SHA256SUMS').read_bytes()).hexdigest(),
        }, sort_keys=True) + '\n')
        evidence.flush()
        request_count = 0
        for path, receipt_path, receipt, digest in rows:
            row = {
                'type': 'outcome', 'path': str(path.relative_to(PACKET)),
                'receipt': str(receipt_path.relative_to(ROOT)),
                'receipt_sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                'method': receipt['method'], 'url': receipt['url'],
                'request_headers': receipt['request_headers'],
                'recorded_status': receipt['status'],
                'recorded_bytes': receipt['response_body']['bytes'],
                'recorded_sha256': digest, 'started_at_utc': now(),
                'status': None, 'observed_sha256': None, 'observed_bytes': None,
                'request_sent': False, 'written': False,
            }
            body = None
            if row['method'] == 'HEAD':
                if digest == EMPTY_SHA256 and row['recorded_bytes'] == 0:
                    body = b''
                    row.update(outcome='restored_deliberately_empty',
                               observed_sha256=EMPTY_SHA256, observed_bytes=0)
                else:
                    row.update(outcome='fetch_failure', error='HEAD has nonempty recorded body')
            elif row['method'] != 'GET':
                row.update(outcome='fetch_failure', error='unsupported method')
            elif row['recorded_bytes'] > MAX_BYTES:
                row.update(outcome='fetch_failure', error='recorded body exceeds size limit')
            else:
                if request_count:
                    time.sleep(delay)
                request_count += 1
                row['request_sent'] = True
                row['request_started_at_utc'] = now()
                request = urllib.request.Request(row['url'], method=row['method'],
                                                 headers=row['request_headers'])
                try:
                    try:
                        response = opener.open(request, timeout=timeout)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        row.update(status=response.code, reason=response.reason,
                                   response_headers=list(response.headers.items()))
                        content_length = response.headers.get('Content-Length')
                        if content_length and int(content_length) > MAX_BYTES:
                            row.update(outcome='fetch_failure',
                                       error='advertised body exceeds size limit; not read')
                        else:
                            received = response.read(MAX_BYTES)
                            row.update(observed_bytes=len(received),
                                       observed_sha256=hashlib.sha256(received).hexdigest())
                            if len(received) == MAX_BYTES:
                                row.update(outcome='fetch_failure',
                                           error='body reached size limit; completeness unknown')
                            elif response.code != 200:
                                row.update(outcome='fetch_failure', error='HTTP status is not 200')
                            elif row['observed_sha256'] != digest:
                                row['outcome'] = 'mismatch'
                            elif len(received) != row['recorded_bytes']:
                                row.update(outcome='fetch_failure', error='pinned length mismatch')
                            else:
                                body = received
                                row['outcome'] = 'restored_verified'
                except (OSError, urllib.error.URLError, ValueError) as error:
                    row.update(outcome='fetch_failure', error_type=type(error).__name__,
                               error=str(error))
            if body is not None:
                # Never replace an existing body, even if one appeared mid-replay.
                with path.open('xb') as restored:
                    restored.write(body)
                row['written'] = True
            row['completed_at_utc'] = now()
            evidence.write(json.dumps(row, sort_keys=True) + '\n')
            evidence.flush()
            print(json.dumps({key: row[key] for key in (
                'path', 'outcome', 'status', 'observed_bytes', 'observed_sha256', 'written')}
            ), flush=True)


def verify(evidence):
    rows = [json.loads(line) for line in evidence.read_text().splitlines()]
    restored = [row for row in rows if row.get('written')]
    failures = []
    for row in restored:
        path = PACKET / row['path']
        if not path.is_file():
            failures.append({'path': row['path'], 'error': 'missing'})
            continue
        body = path.read_bytes()
        if (hashlib.sha256(body).hexdigest() != row['recorded_sha256']
                or len(body) != row['recorded_bytes']):
            failures.append({'path': row['path'], 'error': 'digest or length differs'})
        if hashlib.sha256((ROOT / row['receipt']).read_bytes()).hexdigest() != row['receipt_sha256']:
            failures.append({'path': row['receipt'], 'error': 'receipt changed'})
    pinned = dict(line.split('  ', 1)[::-1]
                  for line in (PACKET / 'SHA256SUMS').read_text().splitlines())
    mismatched_present = [name for name, digest in pinned.items()
                          if (PACKET / name).is_file()
                          and hashlib.sha256((PACKET / name).read_bytes()).hexdigest() != digest]
    missing = sorted(name for name in pinned if not (PACKET / name).is_file())
    sums_unchanged = hashlib.sha256((PACKET / 'SHA256SUMS').read_bytes()).hexdigest() == rows[0]['sha256sums_sha256']
    print(json.dumps({
        'restored_verified': len(restored) - len(failures),
        'restored_verification_failures': failures,
        'manifest_entries': len(pinned), 'present': len(pinned) - len(missing),
        'missing': len(missing), 'present_digest_mismatches': mismatched_present,
        'sha256sums_unchanged': sums_unchanged,
    }, sort_keys=True))
    return 0 if not failures and not mismatched_present and sums_unchanged else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['replay', 'verify'])
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--delay', type=float, default=2.0)
    parser.add_argument('--timeout', type=float, default=15.0)
    args = parser.parse_args()
    if args.action == 'replay':
        if args.delay < 1 or args.timeout <= 0:
            parser.error('delay must be >=1 second; timeout must be positive')
        replay(args.evidence, args.delay, args.timeout)
    else:
        raise SystemExit(verify(args.evidence))
