"""One HTTP attempt in a cancellable trusted broker child; no model fallback."""
import json
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('redirect refused')


try:
    request = json.load(sys.stdin)
    limit = request.get('response_max_bytes', 8 * 1024 * 1024)
    with build_opener(ProxyHandler({}), NoRedirect()).open(
            Request(request['url'], data=request['body'].encode(), headers=request['headers']),
            timeout=request.get('timeout')) as response:
        raw = response.read() if limit is None else response.read(limit + 1)
    if limit is not None and len(raw) > limit:
        raise ValueError('response too large')
    sys.stdout.buffer.write(raw)
except Exception as exc:
    # Bodies, exception messages, URLs and headers can contain credentials.
    # Only classified failure metadata may leave the trusted child.
    failure = {'error_type': type(exc).__name__, 'retryable': False}
    if isinstance(exc, HTTPError):
        failure.update(http_status=exc.code, retryable=exc.code == 429 or 500 <= exc.code < 600)
    elif isinstance(exc, (TimeoutError, ConnectionError, socket.gaierror)):
        failure['retryable'] = True
    elif isinstance(exc, URLError):
        failure['retryable'] = isinstance(exc.reason, (TimeoutError, ConnectionError, socket.gaierror))
    sys.stderr.write(json.dumps(failure))
    sys.exit(1)
