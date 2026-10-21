"""Opt-in, verifier-only model capability; no credentials enter the scorer sandbox.

The scorer uses tool_transport.call('/run/harnessgrad-tool/tool.sock', 'complete',
{'messages': [...]}); model, sampling parameters and limits belong to VERIFY.judge.
No streaming, model fallback, redirects or caller-selected models/tools.
Optional retries apply only to transport failures, never to invalid model output.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from eval.service.tool_transport import Relay
from harnessgrad.config import current


class JudgeUnavailable(RuntimeError):
    pass


from data.scaffolds.runtime_extensions import validate_judge as validate


class Judge:
    def __init__(self, spec, *, role="judge", trial_seed=None):
        validate(spec, allow_trial_seed=role == "environment")
        self.spec = dict(spec)
        if role not in ("judge", "environment"):
            raise JudgeUnavailable("unsupported model role")
        self.role = role
        if spec.get('seed_source') == 'trial':
            try:
                if type(trial_seed) not in (str, int):
                    raise ValueError('missing or noninteger trial seed')
                seed = int(trial_seed)
            except ValueError as exc:
                raise JudgeUnavailable('environment trial seed must be an explicit integer') from exc
            self.spec['parameters'] = {**spec.get('parameters', {}), 'seed': seed}
        cfg = getattr(current(), role)
        if not cfg.base_url:
            raise JudgeUnavailable(f'HG_{"JUDGE" if role == "judge" else "ENV"}_BASE_URL must be configured')
        # Judge model precedence is resolved by the trusted dataset adapter.
        # The frozen declaration controls both calls and evidence; the global
        # model is a default, not a second equality constraint on overrides.
        #
        # **But a mismatch is recorded, not ignored.** For `environment` it is still fatal --
        # an environment model that differs from the declaration is a different world. For
        # `judge` the override is a supported choice (strongreject's metric identity is
        # `strongreject.rubric.defense.<model>.v1`, memoryagentbench's profile carries the
        # judge model plus its reasoning policy), so refusing it would remove a capability.
        # What was missing is that nothing *said* which model actually answered: a judge run
        # with an override looked exactly like one without, and two profiles that must be
        # reported separately could be pooled in silence. `report()` now carries the pair, so
        # a receipt states the declared model and the model that answered, or nothing when
        # they agree.
        self.model_mismatch = None
        declared_model = spec.get('model')
        if declared_model and cfg.model and cfg.model != declared_model:
            self.model_mismatch = {'declared': declared_model, 'actual': cfg.model}
        if role == 'environment' and self.model_mismatch:
            raise JudgeUnavailable('HG_ENV_MODEL must match the declared model')
        parsed = urlsplit(cfg.base_url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise JudgeUnavailable('judge base URL must be HTTP(S) without credentials/query/fragment')
        self.url = cfg.base_url.rstrip('/') + '/chat/completions'
        self.key = cfg.api_key
        self.api_options = None
        if spec.get('reasoning') == 'disabled':
            from data.scaffolds.model_api import request_options
            self.api_options = request_options(spec['model'], self.spec.get('parameters', {}))
        self.calls = []
        self.attempts = []
        self.faults = []
        self.directory = None
        self.relay = None
        self.proc = None
        self.lock = threading.Lock()
        self.closed = False

    def __enter__(self):
        self.directory = Path(tempfile.mkdtemp(prefix='hg-judge-'))
        self.path = self.directory / 'judge.sock'
        try:
            # The transport ceiling is deliberately separate: requests rejected by the
            # handler still have an attributable, persistent failure record.
            self.relay = Relay(self.path, self._call, max_calls=self.spec.get('max_calls'),
                               max_line_bytes=self.spec.get('max_line_bytes', 8 * 1024 * 1024)).start()
            return self
        except BaseException:
            self.close()
            raise

    def _call(self, op, args):
        record = None
        try:
            if op != 'complete' or set(args) != {'messages'}:
                raise JudgeUnavailable('judge accepts only complete(messages)')
            messages = args['messages']
            if not isinstance(messages, list) or not messages:
                raise JudgeUnavailable('model messages must be a nonempty list')
            for message in messages:
                if (not isinstance(message, dict) or set(message) - {'role', 'content', 'name', 'tool_call_id', 'tool_calls'}
                        or message.get('role') not in ('system', 'user', 'assistant', 'tool')):
                    raise JudgeUnavailable('unsupported model message fields')
                content = message.get('content')
                if not isinstance(content, str) and not (content is None and message.get('tool_calls')):
                    raise JudgeUnavailable('model messages require text or assistant tool calls')
                if (message.get('role') == 'tool' or message.get('tool_calls')) and not self.spec.get('tools'):
                    raise JudgeUnavailable('tool messages require declared model tools')
            body = {**self.spec.get('parameters', {}), 'model': self.spec['model'],
                    'messages': messages, 'stream': False}
            token_parameter = 'max_tokens'
            if self.api_options is not None:
                body = {**self.api_options['parameters'], **self.api_options['fields'],
                        'model': self.spec['model'], 'messages': messages, 'stream': False}
                token_parameter = self.api_options['token_parameter']
            if self.spec.get('max_output_tokens') is not None:
                body[token_parameter] = self.spec['max_output_tokens']
            if self.spec.get('tools'):
                body['tools'] = self.spec['tools']
                body['tool_choice'] = self.spec.get('tool_choice', 'auto')
            data = json.dumps(body, allow_nan=False).encode()
            record = {'request_sha256': hashlib.sha256(data).hexdigest(), 'status': 'pending'}
            self.calls.append(record)
            raw, attempt = self._request(data, record)
            result = json.loads(raw)
            usage = result.get('usage')
            if isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                              for k in ('prompt_tokens', 'completion_tokens')):
                attempt.update(usage_known=True, usage={k: usage[k] for k in
                                                       ('prompt_tokens', 'completion_tokens')})
            message = result['choices'][0]['message']
            content = message.get('content')
            tool_calls = message.get('tool_calls')
            if not isinstance(content, str) and not (self.spec.get('tools') and isinstance(tool_calls, list) and tool_calls):
                raise JudgeUnavailable('model response has no text or declared tool calls')
            usage = result.get('usage')
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                                  for k in ('prompt_tokens', 'completion_tokens')):
                raise JudgeUnavailable('judge response has no valid token usage')
            if self.api_options is not None:
                from data.scaffolds.model_api import check_non_reasoning_response
                try:
                    check_non_reasoning_response(message, usage)
                except ValueError as exc:
                    raise JudgeUnavailable(str(exc)) from None
            record.update(status='ok', response_sha256=hashlib.sha256(raw).hexdigest(),
                          usage={k: usage[k] for k in ('prompt_tokens', 'completion_tokens')},
                          response_model=result.get('model'))
            return {'content': content, 'message': message, 'usage': record['usage']}
        except Exception as exc:
            # Do not expose provider response bodies, URLs or credentials in errors.
            reason = str(exc) if isinstance(exc, JudgeUnavailable) else type(exc).__name__
            self.faults.append(reason)
            if record is not None and record['status'] == 'pending':
                record.update(status='error', error=reason)
            raise JudgeUnavailable(reason) from None

    def _request(self, data, record):
        headers = {'Content-Type': 'application/json'}
        if self.key:
            headers['Authorization'] = 'Bearer ' + self.key
        request = json.dumps({'url': self.url, 'body': data.decode(), 'headers': headers,
                              'timeout': self.spec.get('timeout_s'),
                              'response_max_bytes': self.spec.get('response_max_bytes', 8 * 1024 * 1024)}).encode()
        record['attempts'] = []
        for retry in range(self.spec.get('max_retries', 0) + 1):
            with self.lock:
                if self.closed:
                    raise JudgeUnavailable('model broker is closing')
                cap = self.spec.get('max_attempts')
                if cap is not None and len(self.attempts) >= cap:
                    raise JudgeUnavailable('model request attempt budget exceeded')
                attempt = {'index': len(self.attempts) + 1, 'status': 'pending',
                           'usage_known': False, 'usage': None}
                self.attempts.append(attempt)
                record['attempts'].append(attempt)
                try:
                    proc = self.proc = subprocess.Popen(
                        [sys.executable, '-I', str(Path(__file__).with_name('judge_request.py'))],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                except OSError as exc:
                    attempt.update(status='error', error_type=type(exc).__name__, retryable=False)
                    raise JudgeUnavailable('model request process could not start') from None
            failure = None
            try:
                raw, error = proc.communicate(request, timeout=self.spec.get('timeout_s'))
                if proc.returncode:
                    try:
                        failure = json.loads(error)
                    except (ValueError, UnicodeDecodeError):
                        failure = {'error_type': 'RequestProcessError', 'retryable': False}
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                failure = {'error_type': 'TimeoutError', 'retryable': True}
            finally:
                with self.lock:
                    self.proc = None
            if failure is None:
                attempt['status'] = 'ok'
                return raw, attempt
            attempt.update(status='error', **failure)
            if self.closed:
                raise JudgeUnavailable('model broker is closing')
            if not failure.get('retryable') or retry == self.spec.get('max_retries', 0):
                raise JudgeUnavailable('model request failed: ' + failure['error_type'])
        raise AssertionError('unreachable request retry state')

    def report(self):
        transport = dict(self.relay.receipt) if self.relay else {}
        failures = list(self.faults)
        if transport.get('refused') or transport.get('errors'):
            failures.append('judge transport rejected or failed a request')
        return {**({'api_options': self.api_options} if self.api_options is not None else {}),
                **({'model_mismatch': self.model_mismatch} if self.model_mismatch else {}),
                'schema': 'harnessgrad.judge.v1', 'role': self.role,
                'configuration': self.spec, 'endpoint_sha256': hashlib.sha256(self.url.encode()).hexdigest(), 'calls': self.calls, 'attempts': self.attempts, 'faults': failures,
                'transport': transport,
                'usage_complete': all(a['usage_known'] for a in self.attempts),
                'usage': {k: sum((a.get('usage') or {}).get(k, 0) for a in self.attempts)
                          for k in ('prompt_tokens', 'completion_tokens')}}

    def close(self):
        with self.lock:
            self.closed = True
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
        if self.relay:
            self.relay.stop()
        if self.directory:
            shutil.rmtree(self.directory)
            self.directory = None

    def __exit__(self, *args):
        self.close()
