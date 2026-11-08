"""Submission HTTP admission and one canonical backend per listener.

Queue/archive/quota/retention/driver mechanics stay in console.submissions.
This controller owns only the initialization/close lock and explicit HTTP/env
admission; the backend remains attached to its owning httpd.
"""
from __future__ import annotations
import ipaddress
import os
import pathlib
import re
import sys
import threading
from email import policy as email_policy
from email.parser import BytesParser
from urllib.parse import urlparse
from dataclasses import dataclass

@dataclass(frozen=True)
class SubmissionPaths:
    platform: pathlib.Path
    runs: pathlib.Path
    budget_dir: pathlib.Path | None

def origin(headers, *, port: int, reject) -> None:
    hosts = headers.get_all('Host', [])
    try:
        if len(hosts) != 1 or any(c in hosts[0] for c in '/@?#\\\r\n '):
            raise ValueError('one numeric loopback Host is required')
        url = urlparse('//' + hosts[0])
        if not ipaddress.ip_address(url.hostname).is_loopback or url.port != port:
            raise ValueError('Host must use a loopback address and this server port')
    except (ValueError, TypeError) as exc:
        reject('bad-host', str(exc), 'Host')
    origins = headers.get_all('Origin', [])
    if origins and origins != [f'http://127.0.0.1:{port}']:
        reject('origin-forbidden', 'Origin must match this loopback service', 'Origin')


def length(headers, *, reject) -> int:
    values = headers.get_all('Content-Length', [])
    if headers.get_all('Transfer-Encoding') or len(values) != 1 or not re.fullmatch(r'[0-9]+', values[0]):
        reject('bad-multipart', 'one Content-Length and no Transfer-Encoding are required')
    # Bound integer parsing as well as allocation.
    if len(values[0]) > 10:
        reject('payload-too-large', 'multipart envelope exceeds 20 MiB + 64 KiB')
    length = int(values[0])
    if length > 20*1024*1024 + 65536:
        reject('payload-too-large', 'multipart envelope exceeds 20 MiB + 64 KiB')
    return length


def form(headers, body: bytes, *, reject, length) -> tuple[bytes, dict]:
    if len(body) != length(headers):
        reject('bad-multipart', 'multipart body is incomplete')
    types = headers.get_all('Content-Type', [])
    if len(types) != 1 or len(types[0]) > 1024 or '\r' in types[0] or '\n' in types[0]:
        reject('bad-multipart', 'one bounded Content-Type is required')
    prefix = b'Content-Type: ' + types[0].encode('ascii', errors='replace') + b'\r\nMIME-Version: 1.0\r\n\r\n'
    header = BytesParser(policy=email_policy.default).parsebytes(prefix, headersonly=True)
    boundary = header.get_boundary()
    if not boundary or not boundary.isascii() or not 1 <= len(boundary) <= 70:
        reject('bad-multipart', 'a bounded ASCII boundary is required')
    delimiter = re.compile(rb'(?:^|\r\n)--' + re.escape(boundary.encode()) + rb'(?:--)?[ \t]*(?:\r\n|$)')
    if sum(1 for _ in delimiter.finditer(body)) > 5:
        reject('bad-multipart', 'multipart accepts at most four parts')
    try:
        message = BytesParser(policy=email_policy.default).parsebytes(prefix + body)
    except (RecursionError, ValueError) as exc:
        reject('bad-multipart', f'multipart nesting or encoding is invalid: {exc}')
    if message.get_content_type() != 'multipart/form-data' or not message.is_multipart() or message.defects:
        reject('bad-multipart', 'valid multipart/form-data with a closing boundary is required')
    fields = {}
    archive = None
    for part in message.iter_parts():
        if part.is_multipart() or part.defects or part.get_content_disposition() != 'form-data':
            reject('bad-multipart', 'nested or malformed multipart part')
        if (len(part.get_all('Content-Disposition', [])) != 1
                or sum(len(k)+len(str(v)) for k, v in part.raw_items()) > 8192
                or part.get('Content-Transfer-Encoding', 'binary').lower() not in ('binary', '8bit')):
            reject('bad-multipart', 'invalid multipart part headers or encoding')
        names = [v for k, v in part.get_params(header='content-disposition', failobj=[]) if k == 'name']
        name = names[0] if len(names) == 1 else None
        if name not in ('archive', 'scenario', 'label', 'contact'):
            reject('bad-multipart', 'unknown or missing multipart field', name or 'part')
        if name in fields or (name == 'archive' and archive is not None):
            reject('bad-multipart', f'duplicate {name} part', name)
        raw = part.get_payload(decode=True)
        if raw is None:
            reject('bad-multipart', 'part must contain bytes', name)
        if name == 'archive':
            if len(raw) > 20*1024*1024:
                reject('payload-too-large', 'archive exceeds 20 MiB', 'archive')
            archive = raw
        else:
            if len(raw) > 4096:
                reject('bad-multipart', f'{name} is too long', name)
            try:
                fields[name] = raw.decode('utf-8')
            except UnicodeError:
                reject('bad-multipart', f'{name} must be UTF-8', name)
    if archive is None:
        reject('bad-multipart', 'archive part is required', 'archive')
    if not fields.get('scenario'):
        reject('bad-multipart', 'scenario part is required', 'scenario')
    label, contact = fields.get('label', ''), fields.get('contact', '')
    if label and not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', label):
        reject('bad-multipart', 'label must match [A-Za-z0-9._-] and be <=64 characters', 'label')
    if len(contact) > 200:
        reject('bad-multipart', 'contact must be <=200 characters', 'contact')
    return archive, fields


def query(query: dict, *, for_log: bool = False, reject) -> dict:
    allowed = {'tail'} if for_log else {'limit', 'cursor'}
    for key, values in query.items():
        if key not in allowed or len(values) != 1:
            reject('bad-query', 'unknown or repeated query field', key)
    key, default, maximum = ('tail', 20000, 200000) if for_log else ('limit', 50, 200)
    value = query.get(key, [str(default)])[0]
    if not re.fullmatch(r'[0-9]{1,6}', value) or not 1 <= int(value) <= maximum:
        reject('bad-query', f'{key} must be an integer in 1..{maximum}', key)
    result = {key: int(value)}
    if not for_log:
        cursor = query.get('cursor', [None])[0]
        if cursor is not None and not re.fullmatch(r'sub-\d{8}T\d{6}Z-[0-9a-f]{8}', cursor):
            reject('bad-query', 'cursor must be a submission ID', 'cursor')
        result['cursor'] = cursor
    return result


def resolve_environment(scenario_name, *, platform, backend, load_settings, environment, settings=None, base_env=None):
    spec, _ = backend._scenario(scenario_name, platform)
    env = dict(environment if base_env is None else base_env)
    # Native files tasks launch "python". Starting this server by absolute venv
    # interpreter must work just as activating that venv before starting it.
    env['PATH'] = str(pathlib.Path(sys.executable).parent) + os.pathsep + env.get('PATH','/usr/bin:/bin')
    if spec['model']['backend'] == 'mock':
        env['HG_AGENT_BACKEND'] = 'mock'
        for name in ('HG_AGENT_MODEL','HG_AGENT_BASE_URL','HG_AGENT_API_KEY'):
            env[name] = ''  # Keep driver.load_env's setdefault from repopulating them.
    else:
        task = (load_settings() if settings is None else settings)['task']
        env['HG_AGENT_BACKEND'] = env.get('HG_AGENT_BACKEND') or 'openai'
        if env['HG_AGENT_BACKEND'] == 'mock':
            env['HG_AGENT_BACKEND'] = 'openai'
        for key, variable in (('model','HG_AGENT_MODEL'),('base_url','HG_AGENT_BASE_URL'),('api_key','HG_AGENT_API_KEY')):
            if task.get(key):
                env[variable] = task[key]
    return backend.scenario_environment(spec, env)


class SubmissionController:
    def __init__(self):
        self.lock = threading.Lock()

    def close(self, httpd):
        with self.lock:
            if hasattr(httpd, '_submission_service'):
                httpd._submission_service.close()

    def service(self, httpd, *, backend, environment, paths, budget_capacity, liveness, env_resolver):
        root = environment.get('HG_CONSOLE_SUBMISSIONS_ROOT')
        if not root:
            backend._reject('service-not-ready', 'configure HG_CONSOLE_SUBMISSIONS_ROOT outside the platform')
        if httpd.server_address[0] != '127.0.0.1':
            backend._reject('service-not-ready', 'submission service requires a 127.0.0.1 listener')
        if paths.budget_dir is None or not paths.budget_dir.is_absolute():
            backend._reject('service-not-ready', 'configure HG_CONSOLE_BUDGET_DIR as an absolute shared directory outside the platform')
        try:
            capacity = budget_capacity()
        except ValueError as exc:
            backend._reject('service-not-ready', str(exc))
        with self.lock:
            if getattr(httpd, '_console_closed', False):
                backend._reject('service-not-ready', 'submission listener is closed')
            if not hasattr(httpd, '_submission_service'):
                try:
                    config = {
                        'max_queue':int(environment.get('HG_CONSOLE_SUBMISSIONS_MAX_QUEUE','16')),
                        'quota_bytes':int(environment.get('HG_CONSOLE_SUBMISSIONS_QUOTA_BYTES',str(5*1024**3))),
                        'retention_seconds':float(environment.get('HG_CONSOLE_SUBMISSIONS_RETENTION_SECONDS',str(30*86400))),
                        'sweep_seconds':float(environment.get('HG_CONSOLE_SUBMISSIONS_SWEEP_SECONDS','1'))}
                except (ValueError,OverflowError):
                    backend._reject('service-not-ready','invalid submission queue/quota/retention configuration')
                httpd._submission_service = backend.SubmissionService(
                    platform_root=paths.platform, submissions_root=pathlib.Path(root), runs_root=paths.runs,
                    work_root=pathlib.Path(environment.get('HG_CONSOLE_SUBMISSIONS_WORK','/tmp/hg-submissions-work')),
                    budget_dir=paths.budget_dir, budget_capacity=capacity,
                    liveness=liveness,
                    env_resolver=env_resolver,**config)
            return httpd._submission_service
