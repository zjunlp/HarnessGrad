"""HTTP transport primitives: headers, origin/framing, JSON and static bytes.

Business endpoints are capabilities supplied by the composition root. No server
or application service is imported here.
"""
from __future__ import annotations
import ipaddress
import json
import pathlib
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import unquote, urlparse
if __package__:
    from .serialization import json_safe
    from .path_policy import within
else:
    from serialization import json_safe
    from path_policy import within

class HttpTransport(BaseHTTPRequestHandler):
    server_version = 'hg-console/0.2'

    def log_message(self, fmt: str, *args) -> None:  # 安静一点，别刷屏
        pass


    def _send(self, payload, status: int = 200,
              ctype: str = "application/json; charset=utf-8",
              cache_control: str | None = None, disposition: str | None = None) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(
            json_safe(payload), ensure_ascii=False, allow_nan=False,
            default=str).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control or "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


    def _error(self, message: str, status: int = 400) -> None:
        self._send({"error": message}, status)


    def _guard_error(self, code: str, status: int) -> None:
        parts = [unquote(p) for p in urlparse(self.path).path.strip('/').split('/') if p]
        if parts[:2] == ['api', 'submissions'] or parts == ['api', 'scenarios']:
            self._send({'api_version': '1', 'error': {'code': code, 'detail': code}}, status)
        else:
            self._error(code, status)


    def _api_guard(self) -> bool:
        """Reject browser cross-site requests and DNS-rebinding Host values."""
        hosts = self.headers.get_all('Host', [])
        address, port = self.server.server_address[:2]
        try:
            # Compare the literal authority, not a DNS resolution or URL parser's
            # normalized hostname. This also rejects userinfo and ambiguous ports.
            ip = ipaddress.ip_address(address)
            if not ip.is_loopback:
                raise ValueError('listener is not loopback')
            numeric = f'[{ip}]' if ip.version == 6 else str(ip)
            allowed = {f'{numeric}:{port}', f'localhost:{port}'}
            if len(hosts) != 1 or hosts[0] not in allowed:
                raise ValueError('Host must name this loopback listener and port')
        except ValueError:
            self.close_connection = True
            self._guard_error('bad-host', 400)
            return False
        origins = self.headers.get_all('Origin', [])
        sites = self.headers.get_all('Sec-Fetch-Site', [])
        if (len(origins) > 1 or (origins and origins[0] != f'http://{hosts[0]}')
                or len(sites) > 1 or (sites and sites[0] not in ('same-origin', 'none'))):
            self.close_connection = True
            self._guard_error('origin-forbidden', 403)
            return False
        return True


    def _json_body(self, *, strict: bool = False) -> dict | None:
        """The request body as an object, or None when it is not one."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            if length and len(raw) != length:
                return None
            if strict:
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError('duplicate_json_key')
                        result[key] = value
                    return result
                body = json.loads(raw.decode("utf-8") or "{}", object_pairs_hook=unique)
            else:
                body = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError, RecursionError):
            return None
        return body if isinstance(body, dict) else None


def post_framing(self, *, archive: bool = False, archive_limit) -> bool:
    lengths = self.headers.get_all('Content-Length', [])
    types = self.headers.get_all('Content-Type', [])
    if (self.headers.get_all('Transfer-Encoding') or len(lengths) != 1
            or not re.fullmatch(r'[0-9]{1,10}', lengths[0])):
        self.close_connection = True
        self._error('bad-length', 400)
        return False
    limit = archive_limit if archive else 1024 * 1024
    if int(lengths[0]) > limit:
        self.close_connection = True
        self._error('payload-too-large', 413)
        return False
    if not archive and (len(types) != 1 or
            not re.fullmatch(r'application/json(?:\s*;\s*charset\s*=\s*utf-8)?',
                             types[0], re.IGNORECASE)):
        self.close_connection = True
        self._error('unsupported-content-type', 415)
        return False
    return True


def static(self, path: str, *, root) -> None:
    rel = unquote(path.lstrip("/")) or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    target = (root / rel).resolve()
    if not within(root, target) or not target.is_file():
        return self._error("not found", 404)
    ctype = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
    }.get(target.suffix, "application/octet-stream")
    try:
        body = target.read_bytes()
    except OSError:
        return self._error("not found", 404)
    return self._send(body, ctype=ctype)
