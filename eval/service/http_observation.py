"""Passive HTTP/1 response metadata and optional bounded request image capture.

Framing follows Content-Length, chunked encoding and EOF, so body text cannot be
mistaken for a later status line on a keep-alive connection. Unsupported/malformed
traffic makes observation incomplete; it does not interrupt the model transport.
"""
from collections import deque
import http.client
import io
import threading
from urllib.parse import urlsplit

from eval.service.visual_evidence import MAX_REQUEST_BYTES


class HTTPObservation:
    def __init__(self, visual=None):
        self.lock = threading.RLock()
        self.calls = []
        self.errors = []
        self.connections = []
        self.visual = visual

    def connection(self):
        with self.lock:
            pair = _Connection(self)
            self.connections.append(pair)
            return pair

    def report(self):
        with self.lock:
            return {'version': 1, 'calls': [dict(c) for c in self.calls],
                    'complete': not self.errors and all(c.complete for c in self.connections),
                    'errors': list(self.errors)}


class _Connection:
    def __init__(self, owner):
        self.owner = owner
        self.pending = deque()
        self.request = _Messages(self, False)
        self.response = _Messages(self, True)

    @property
    def complete(self):
        return (not self.pending and self.request.idle and self.response.idle)

    def feed(self, side, data):
        with self.owner.lock:
            parser = self.request if side == 'client' else self.response
            try:
                parser.feed(data)
            except (ValueError, http.client.HTTPException, UnicodeError) as exc:
                # No raw request/header values: those may contain credentials.
                if parser.state != 'broken':
                    self.owner.errors.append({'side': side, 'type': type(exc).__name__})
                parser.state = 'broken'
                parser.buffer.clear()
                parser.image_body = None


class _Messages:
    def __init__(self, pair, response):
        self.pair, self.response = pair, response
        self.buffer = bytearray()
        self.state = 'headers'
        self.remaining = 0
        self.call = None
        self.image_call = None
        self.image_body = None

    @property
    def idle(self):
        return self.state == 'headers' and not self.buffer

    def finish(self):
        if self.call is not None:
            self.call['complete'] = True
        if self.image_call is not None:
            if self.response:
                self.image_call['response_complete'] = True
            elif self.image_body is not None:
                self.pair.owner.visual.capture(self.image_call, self.image_body)
        self.call = None
        self.image_call = self.image_body = None
        self.state = 'headers'

    def feed(self, data):
        if self.state == 'broken':
            return
        if not data:
            if self.state == 'eof':
                self.finish()
            return
        self.buffer.extend(data)
        while self.buffer:
            if self.state == 'headers':
                end = self.buffer.find(b'\r\n\r\n')
                if end < 0:
                    # CPython's HTTP header limits bound observer memory only;
                    # exceeding them never rejects or truncates a model request.
                    if (len(self.buffer) > http.client._MAXLINE * http.client._MAXHEADERS
                            or any(len(line) > http.client._MAXLINE for line in self.buffer.split(b'\n'))):
                        raise ValueError('HTTP observation header limit')
                    return
                raw = bytes(self.buffer[:end + 4])
                del self.buffer[:end + 4]
                self.headers(raw)
            elif self.state in ('body', 'chunk'):
                size = min(len(self.buffer), self.remaining)
                if self.image_body is not None:
                    if len(self.image_body) + size <= MAX_REQUEST_BYTES:
                        self.image_body.extend(self.buffer[:size])
                    else:
                        self.image_call['capture_status'] = 'request_too_large'
                        self.image_body = None
                del self.buffer[:size]
                self.remaining -= size
                if self.remaining:
                    return
                if self.state == 'body':
                    self.finish()
                else:
                    self.state = 'chunk_end'
            elif self.state == 'chunk_end':
                if len(self.buffer) < 2:
                    return
                if self.buffer[:2] != b'\r\n':
                    raise ValueError('invalid chunk terminator')
                del self.buffer[:2]
                self.state = 'chunk_size'
            elif self.state in ('chunk_size', 'trailers'):
                end = self.buffer.find(b'\r\n')
                if end < 0:
                    if len(self.buffer) > http.client._MAXLINE:
                        raise ValueError('HTTP observation line limit')
                    return
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 2]
                if self.state == 'trailers':
                    if not line:
                        self.finish()
                else:
                    size = line.split(b';', 1)[0]
                    if not size or any(c not in b'0123456789abcdefABCDEF' for c in size):
                        raise ValueError('invalid chunk size')
                    self.remaining = int(size, 16)
                    self.state = 'chunk' if self.remaining else 'trailers'
            elif self.state == 'eof':
                self.buffer.clear()

    def headers(self, raw):
        first, rest = raw.split(b'\r\n', 1)
        headers = http.client.parse_headers(io.BytesIO(rest))
        parts = first.decode('ascii').split(' ')
        if len(parts) < 3:
            raise ValueError('invalid HTTP start line')
        if self.response:
            if parts[0] not in ('HTTP/1.0', 'HTTP/1.1') or not self.pair.pending:
                raise ValueError('unmatched HTTP response')
            status = int(parts[1])
            if not 100 <= status <= 599 or status == 101:
                raise ValueError('unsupported HTTP response')
            if status < 200:
                return  # Interim responses do not consume a request.
            method, operation, self.image_call = self.pair.pending.popleft()
            self.call = {'operation': operation, 'status': status, 'complete': False}
            self.pair.owner.calls.append(self.call)
            if self.image_call is not None:
                self.image_call['http_status'] = status
            if method == 'HEAD' or status in (204, 304):
                self.finish()
                return
        else:
            method, target, version = parts
            if version not in ('HTTP/1.0', 'HTTP/1.1'):
                raise ValueError('unsupported HTTP request')
            path = urlsplit(target).path.rstrip('/')
            operation = next((name for suffix, name in (
                ('/chat/completions', 'chat.completions'), ('/responses', 'responses'),
                ('/completions', 'completions')) if method == 'POST' and path.endswith(suffix)), 'other')
            if self.pair.owner.visual is not None and operation in ('chat.completions', 'responses'):
                self.image_call = self.pair.owner.visual.start_request(operation, headers)
                if headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    self.image_call['capture_status'] = 'unsupported_content_encoding'
                else:
                    self.image_body = bytearray()
            self.pair.pending.append((method, operation, self.image_call))
        transfer = headers.get('Transfer-Encoding', '').lower().strip()
        if transfer:
            if transfer != 'chunked':
                raise ValueError('unsupported transfer encoding')
            self.state = 'chunk_size'
        elif headers.get('Content-Length') is not None:
            lengths = headers.get_all('Content-Length')
            if len(set(lengths)) != 1 or not lengths[0].isdigit():
                raise ValueError('invalid content length')
            self.remaining = int(lengths[0])
            self.state = 'body'
            if not self.remaining:
                self.finish()
        elif self.response:
            self.state = 'eof'
        else:
            self.finish()


class ObservedReader:
    """The existing socket pump still forwards exactly what recv returned."""
    def __init__(self, socket, observation, side):
        self.socket, self.observation, self.side = socket, observation, side

    def recv(self, size):
        data = self.socket.recv(size)
        self.observation.feed(self.side, data)
        return data
