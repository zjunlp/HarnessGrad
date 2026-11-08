"""Bounded, credential-safe Chat Completions transport using only the stdlib.

Supports the editor's one request shape; this is not a general OpenAI SDK.
The system's standard proxy and CA configuration is honored. Redirects are
refused so an Authorization header cannot be forwarded to another destination.
"""
from __future__ import annotations

import http.client
import json
import math
import socket
import time
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RETRIES = 2
MAX_RETRY_DELAY = 5.0


class EndpointError(RuntimeError):
    """A transport or response failure without endpoint secrets or server body."""
    def __init__(self, message, *, response_received=False, attempts=0):
        super().__init__(message)
        self.response_received = response_received
        self.attempts = attempts


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(base_url):
    try:
        parts = urlsplit(base_url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment
                or any(ord(c) < 32 or ord(c) == 127 for c in base_url)):
            raise ValueError
        parts.port  # validate even when the port is not otherwise needed
        return urlunsplit((parts.scheme, parts.netloc,
                           parts.path.rstrip('/') + '/chat/completions', '', ''))
    except (ValueError, TypeError):
        raise EndpointError('invalid method endpoint URL') from None


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def _decode(raw, attempts):
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get('choices'), list) or not value['choices']:
            raise ValueError
        choice = value['choices'][0]
        if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
            raise ValueError
        content = choice['message'].get('content')
        if content is None:
            content = ''
        if not isinstance(content, str):
            raise ValueError
        usage = value.get('usage')
        if usage is not None and not isinstance(usage, dict):
            raise ValueError
        if usage is not None:
            for name in ('prompt_tokens', 'completion_tokens'):
                if type(usage.get(name)) is not int or usage[name] < 0:
                    raise ValueError
            details = usage.get('prompt_tokens_details')
            if details is not None and not isinstance(details, dict):
                raise ValueError
            cached = []
            if isinstance(details, dict) and 'cached_tokens' in details:
                cached.append(details['cached_tokens'])
            cached.extend(usage[name] for name in
                          ('prompt_cache_hit_tokens', 'cache_read_tokens', 'cached_tokens')
                          if name in usage)
            cached = [count for count in cached if count is not None]
            if any(type(count) is not int or count < 0 or count > usage['prompt_tokens']
                   for count in cached) or len(set(cached)) > 1:
                raise ValueError
        model = value.get('model')
        if model is not None and not isinstance(model, str):
            raise ValueError
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=_namespace(usage), model=model, transport_attempts=attempts,
                               unknown_transport_attempts=attempts - 1)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise EndpointError(f'invalid method endpoint response after {attempts} attempt(s)', response_received=True, attempts=attempts) from None


def _retry_delay(headers, attempt):
    """Honor server delays when bounded; never retry earlier than requested."""
    milliseconds = headers.get('retry-after-ms')
    raw = headers.get('retry-after')
    delay = None
    try:
        if milliseconds is not None:
            delay = float(milliseconds) / 1000
        elif raw is not None:
            try:
                delay = float(raw)
            except ValueError:
                delay = parsedate_to_datetime(raw).timestamp() - time.time()
    except (TypeError, ValueError, OverflowError):
        delay = None
    if delay is not None and (not math.isfinite(delay) or delay < 0):
        delay = None
    return delay if delay is not None else min(0.25 * (2 ** (attempt - 1)), MAX_RETRY_DELAY)


class Client:
    def __init__(self, *, base_url, api_key, timeout=180):
        self.url = _endpoint(base_url)
        if not isinstance(api_key, str) or not api_key or not api_key.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in api_key):
            raise EndpointError('invalid method endpoint credential')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout:
            raise EndpointError('method endpoint timeout must be finite and positive')
        self.api_key = api_key
        self.timeout = timeout
        self.opener = build_opener(_NoRedirect())

    def complete(self, *, model, temperature, messages):
        payload = json.dumps({'model': model, 'temperature': temperature, 'messages': messages},
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
        sent = 0
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                request = Request(self.url, data=payload, method='POST', headers={
                    'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json',
                    'Accept': 'application/json'})
                deadline = time.monotonic() + self.timeout
                sent += 1
                with self.opener.open(request, timeout=self.timeout) as response:
                    chunks, total = [], 0
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError
                        # CPython's HTTPResponse carries the socket here; bound each read by
                        # the remaining wall-clock budget, including slow streamed bodies.
                        raw_socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                        if raw_socket is not None:
                            raw_socket.settimeout(remaining)
                        chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - total))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise EndpointError(f'method endpoint response exceeds limit after {attempt} attempt(s)', response_received=True, attempts=sent)
                return _decode(b''.join(chunks), attempt)
            except HTTPError as exc:
                status = exc.code
                exc.close()
                retry = status in (408, 409, 429) or 500 <= status <= 599
                if str(exc.headers.get('x-should-retry', '')).strip().lower() == 'false':
                    retry = False
                if retry and attempt <= MAX_RETRIES:
                    delay = _retry_delay(exc.headers, attempt)
                    if delay > MAX_RETRY_DELAY:
                        raise EndpointError(f'method endpoint HTTP {status}: retry delay exceeds limit after {attempt} attempt(s)', attempts=sent) from None
                    time.sleep(delay)
                    continue
                raise EndpointError(f'method endpoint HTTP {status} after {attempt} attempt(s)', attempts=sent) from None
            except (ValueError, UnicodeError, OverflowError):
                raise EndpointError(f'invalid method endpoint request after {attempt} attempt(s)', attempts=sent) from None
            except (URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException):
                if attempt <= MAX_RETRIES:
                    time.sleep(min(0.25 * (2 ** (attempt - 1)), MAX_RETRY_DELAY))
                    continue
                raise EndpointError(f'method endpoint transport failed after {attempt} attempt(s)', attempts=sent) from None
        raise AssertionError('unreachable')
