#!/usr/bin/env python3
"""Explicit public code/metadata acquisition; retains every HTTP body and its hash.

No dataset payload endpoint, archive GET, execution of upstream code, or approval.
Call with a unique evidence label and URL; HEAD is supported for archive metadata.
"""
import argparse
import datetime
import hashlib
import http.client
import json
from pathlib import Path
import socket
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'tests/fixtures/external_provenance_b5'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def acquire(label, url, method='GET', connect_ip=None, accept='application/json'):
    directory = OUT / 'responses'
    directory.mkdir(parents=True, exist_ok=True)
    receipt_path = directory / (label + '.receipt.json')
    if receipt_path.exists():
        raise ValueError('refuse to overwrite prior acquisition: ' + label)
    headers = {'User-Agent': 'HarnessGrad-public-provenance/1', 'Accept': accept}
    receipt = {'url': url, 'method': method, 'request_headers': headers,
               'requested_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'production_authorized': False}
    handlers = [NoRedirect]
    if connect_ip:
        # Change only TCP routing; HTTPS still validates the URL hostname and SNI.
        class RoutedHTTPS(urllib.request.HTTPSHandler):
            def https_open(self, request):
                def connection(host, **kwargs):
                    conn = http.client.HTTPSConnection(host, **kwargs)
                    conn._create_connection = lambda address, *args, **kw: socket.create_connection(
                        (connect_ip, address[1]), *args, **kw)
                    return conn
                return self.do_open(connection, request, context=self._context)
        handlers.append(RoutedHTTPS())
        receipt['connect_ip'] = connect_ip
    try:
        request = urllib.request.Request(url, headers=headers, method=method)
        opener = urllib.request.build_opener(*handlers)
        try:
            response = opener.open(request, timeout=35)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            body_path = directory / (label + '.body')
            body_path.write_bytes(body)
            receipt.update(status=response.code, reason=response.reason,
                           response_headers=list(response.headers.items()),
                           response_body={'path': str(body_path.relative_to(OUT)),
                                          'bytes': len(body),
                                          'sha256': hashlib.sha256(body).hexdigest()})
    except (OSError, urllib.error.URLError) as error:
        receipt.update(status=None, error_type=type(error).__name__, error=str(error),
                       response_body=None)
    receipt['completed_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    parser.add_argument('url')
    parser.add_argument('--method', choices=['GET', 'HEAD'], default='GET')
    parser.add_argument('--connect-ip', help='TCP IP override; retains hostname TLS verification')
    parser.add_argument('--accept', default='application/json')
    args = parser.parse_args()
    print(json.dumps(acquire(args.label, args.url, args.method, args.connect_ip, args.accept), indent=2))
