"""Submission wire adapter; the canonical backend owns queue/quota/archive work."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

@dataclass(frozen=True)
class SubmissionEndpoints:
    base_environment: object
    _submission_backend: Callable
    _submission_reject: Callable
    load_settings: Callable
    submission_env: Callable
    submission_form: Callable
    submission_length: Callable
    submission_origin: Callable
    submission_query: Callable
    submission_service: Callable


def error(self, exc, *, ports) -> None:
    if isinstance(exc, ports._submission_backend().SubmissionRejected):
        source = exc.problems[0]
    else:
        source = {'code':'internal-error','detail':f'submission operation failed: {exc}'}
    problem = {k:source[k] for k in ('code','detail','field','existing_submission_id') if k in source}
    code = problem['code']
    status = {'bad-host':400, 'origin-forbidden':403, 'submission-not-found':404,
              'bad-query':400, 'payload-too-large':413, 'archive-too-large':413,
              'service-not-ready':503, 'internal-error':500}.get(code,
              409 if code in ('scenario-not-available','duplicate-submission','queue-full','running','not-started') else 400)
    self._send({'api_version':'1','error':problem}, status)


def get(self, parts, query, *, ports) -> None:
    try:
        service = ports.submission_service(self.server)
        if parts == ['api','scenarios']:
            if query:
                ports._submission_reject('bad-query','scenarios does not accept query fields',next(iter(query)))
            settings, base_env = ports.load_settings(), dict(ports.base_environment)
            payload = service.scenarios(driver_env=base_env, env_for_scenario=lambda name:
                ports.submission_env(name, settings=settings, base_env=base_env))
            return self._send(payload)
        if parts == ['api','submissions']:
            return self._send(service.list(**ports.submission_query(query)))
        if len(parts) == 4 and parts[3] == 'log':
            raw = service.log(parts[2], **ports.submission_query(query,for_log=True))
            return self._send(raw, ctype='text/plain; charset=utf-8')
        if len(parts) == 3:
            if query:
                ports._submission_reject('bad-query','detail does not accept query fields',next(iter(query)))
            return self._send(service.get(parts[2]))
        ports._submission_reject('submission-not-found','submission route was not found')
    except (ValueError,OSError,TypeError) as exc:
        self._submission_error(exc)


def post(self, *, ports) -> None:
    token = None
    service = None
    try:
        ports.submission_origin(self.headers,port=self.server.server_port)
        service = ports.submission_service(self.server)
        service._guard()  # Reject resource/config failures before reading an upload.
        length = ports.submission_length(self.headers)
        token = service.reserve_upload(length)
        self.connection.settimeout(30)
        raw, fields = ports.submission_form(self.headers,self.rfile.read(length))
        service._quota_reserve(token,4096+len(raw)-length)
        path = token['directory']/'archive.bin'
        path.write_bytes(raw)  # Client filename is never used.
        code, payload = service.submit(path,fields['scenario'],label=fields.get('label',''),
                                       contact=fields.get('contact',''),upload_token=token,
                                       driver_env=ports.submission_env(fields['scenario']))
        return self._send(payload,code)
    except TimeoutError:
        self.close_connection = True
        self._submission_error(ports._submission_backend().SubmissionRejected([
            {'code':'bad-multipart','detail':'upload read timed out'}]))
    except (ValueError,OSError,TypeError) as exc:
        self.close_connection = True
        self._submission_error(exc)
    finally:
        if service is not None:
            service.release_upload(token)


def delete(self, *, ports) -> None:  # noqa: N802
    url = urlparse(self.path)
    parts = [unquote(p) for p in url.path.strip('/').split('/') if p]
    if not self._api_guard():
        return
    try:
        ports.submission_origin(self.headers,port=self.server.server_port)
        if parts[:2] != ['api','submissions'] or len(parts) != 3:
            ports._submission_reject('submission-not-found','submission route was not found')
        if url.query:
            ports._submission_reject('bad-query','DELETE does not accept query fields')
        service = ports.submission_service(self.server)
        service.start()
        code,payload = service.delete(parts[2])
        return self._send(payload,code)
    except (ValueError,OSError,TypeError) as exc:
        self._submission_error(exc)
