"""Opt-in, verifier-only model capability; no credentials enter the scorer sandbox.

The scorer uses tool_transport.call('/run/harnessgrad-tool/tool.sock', 'complete',
{'messages': [...]}); model, sampling parameters and limits belong to VERIFY.judge.
No streaming, fallback, retries, redirects or caller-selected models/tools.
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
        if cfg.model != spec['model'] or not cfg.base_url:
            raise JudgeUnavailable(f'HG_{"JUDGE" if role == "judge" else "ENV"}_MODEL/BASE_URL must match the declared model')
        parsed = urlsplit(cfg.base_url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise JudgeUnavailable('judge base URL must be HTTP(S) without credentials/query/fragment')
        self.url = cfg.base_url.rstrip('/') + '/chat/completions'
        self.key = cfg.api_key
        self.calls = []
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
            self.relay = Relay(self.path, self._call, max_calls=self.spec['max_calls']).start()
            return self
        except BaseException:
            self.close()
            raise

    def _call(self, op, args):
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
                    'messages': messages, 'max_tokens': self.spec['max_output_tokens'], 'stream': False}
            if self.spec.get('tools'):
                body['tools'] = self.spec['tools']
                body['tool_choice'] = self.spec.get('tool_choice', 'auto')
            data = json.dumps(body, allow_nan=False).encode()
            record = {'request_sha256': hashlib.sha256(data).hexdigest(), 'status': 'pending'}
            self.calls.append(record)
            headers = {'Content-Type': 'application/json'}
            if self.key:
                headers['Authorization'] = 'Bearer ' + self.key
            request = json.dumps({'url': self.url, 'body': data.decode(), 'headers': headers,
                                  'timeout': self.spec['timeout_s']}).encode()
            with self.lock:
                if self.closed:
                    raise JudgeUnavailable('model broker is closing')
                proc = self.proc = subprocess.Popen(
                    [sys.executable, '-I', str(Path(__file__).with_name('judge_request.py'))],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                raw, error = proc.communicate(request, timeout=self.spec['timeout_s'])
            except subprocess.TimeoutExpired:
                proc.kill(); proc.communicate()
                raise JudgeUnavailable('model request deadline exceeded') from None
            finally:
                with self.lock:
                    self.proc = None
            if proc.returncode:
                raise JudgeUnavailable('model request failed: ' + error.decode(errors='replace')[:80])
            result = json.loads(raw)
            message = result['choices'][0]['message']
            content = message.get('content')
            tool_calls = message.get('tool_calls')
            if not isinstance(content, str) and not (self.spec.get('tools') and isinstance(tool_calls, list) and tool_calls):
                raise JudgeUnavailable('model response has no text or declared tool calls')
            usage = result.get('usage')
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                                  for k in ('prompt_tokens', 'completion_tokens')):
                raise JudgeUnavailable('judge response has no valid token usage')
            record.update(status='ok', response_sha256=hashlib.sha256(raw).hexdigest(),
                          usage={k: usage[k] for k in ('prompt_tokens', 'completion_tokens')},
                          response_model=result.get('model'))
            return {'content': content, 'message': message, 'usage': record['usage']}
        except Exception as exc:
            # Do not expose provider response bodies, URLs or credentials in errors.
            reason = str(exc) if isinstance(exc, JudgeUnavailable) else type(exc).__name__
            self.faults.append(reason)
            if self.calls and self.calls[-1]['status'] == 'pending':
                self.calls[-1].update(status='error', error=reason)
            raise JudgeUnavailable(reason) from None

    def report(self):
        transport = dict(self.relay.receipt) if self.relay else {}
        failures = list(self.faults)
        if transport.get('refused') or transport.get('errors'):
            failures.append('judge transport rejected or failed a request')
        return {'schema': 'harnessgrad.judge.v1', 'role': self.role,
                'configuration': self.spec, 'endpoint_sha256': hashlib.sha256(self.url.encode()).hexdigest(), 'calls': self.calls, 'faults': failures,
                'transport': transport,
                'usage': {k: sum(c.get('usage', {}).get(k, 0) for c in self.calls)
                          for k in ('prompt_tokens', 'completion_tokens')}}

    def close(self):
        with self.lock:
            self.closed = True
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
        if self.relay:
            self.relay.stop()
        if self.directory:
            shutil.rmtree(self.directory)
            self.directory = None

    def __exit__(self, *args):
        self.close()
