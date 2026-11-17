"""Opt-in HTTP admission at the actual model transport boundary.

The tokenizer is a driver-owned callback, never resolved from actor input. Its
contract must count the provider's complete model-specific input serialization.
Only HTTP/1 Content-Length JSON generation requests are supported initially.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import io
import json
import socket
import threading
import time
from urllib.parse import urlsplit

from harnessgrad.execution.budget_dispatch import BudgetExceeded, StaleRequest


class AdmissionRefused(ValueError):
    pass


@dataclass(frozen=True)
class TaskHTTPBudget:
    binding: object
    split: str
    tokenizer: object = None
    tokenizer_contract: str | None = None

    def __post_init__(self):
        policy = self.binding.profile.data.get('task_http', {})
        if policy.get('mode') != 'strict':
            raise AdmissionRefused('strict_task_http_not_configured')
        if self.split not in ('train', 'audit'):
            raise AdmissionRefused('invalid_budget_split')
        # Split isolation is part of the durable ledger identity, not a request field.
        if (not self.binding.ledger_path.stem.endswith('.' + self.split)
                or not self.binding.run_id.endswith('::task:' + self.split)):
            raise AdmissionRefused('budget_split_identity_mismatch')

    def admission_stop(self):
        """Read-only terminal check; never buys a request or invents token counts."""
        d = self.binding.dispatcher
        state = d.snapshot()
        if state.get('unknown_requests'):
            return {'reason': 'unknown_usage', 'counter': 'unknown_usage',
                    'current': len(state['unknown_requests']), 'limit': 0}
        deadline = state.get('deadline')
        if deadline is not None and d.clock() >= deadline:
            return {'reason': 'wall_exhausted', 'counter': 'wall',
                    'current': d.clock() - state['started_at'],
                    'limit': deadline - state['started_at']}
        limits = d.profile.limits_for('task')
        role = state.get('roles', {}).get('task', {})
        for counter in ('requests', 'calls'):
            cap = limits.get(counter)
            current = state['counts'].get(counter, 0)
            if cap is not None and current >= cap:
                return {'reason': 'budget_exhausted', 'counter': counter,
                        'current': current, 'limit': cap}
        for name, counter in (('prompt', 'prompt_tokens'), ('output', 'output_tokens'),
                              ('total', 'total_tokens')):
            for scope, cap, current in (
                ('global', d.profile.global_limits.get(name),
                 state['spent'][counter] + state['reserved'][counter]),
                ('role.task', limits.get(name),
                 role.get('spent_' + counter, 0) + role.get(counter, 0))):
                if cap is not None and current >= cap:
                    return {'reason': 'budget_exhausted', 'counter': scope + '.' + counter,
                            'current': current, 'limit': cap}
        return None

    def admit(self, method, target, headers, body):
        policy = self.binding.profile.data['task_http']
        if (not callable(self.tokenizer) or not self.tokenizer_contract
                or policy.get('tokenizer_contract') != self.tokenizer_contract):
            raise AdmissionRefused('exact_tokenization_unavailable')
        path = urlsplit(target).path.rstrip('/')
        operation = next((v for k, v in (
            ('/chat/completions', 'chat.completions'),
            ('/completions', 'completions'), ('/responses', 'responses'))
            if path.endswith(k)), None)
        if method != 'POST' or operation is None or headers.get('Content-Encoding'):
            raise AdmissionRefused('unsupported_budget_http_request')
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise AdmissionRefused('invalid_budget_json') from exc
        if not isinstance(payload, dict) or not isinstance(payload.get('model'), str):
            raise AdmissionRefused('missing_budget_model_identity')
        fields = ('max_output_tokens',) if operation == 'responses' else ('max_tokens', 'max_completion_tokens')
        values = [payload[k] for k in fields if k in payload]
        if len(values) != 1 or type(values[0]) is not int or values[0] <= 0:
            raise AdmissionRefused('explicit_output_bound_required')
        # Multiple completions would multiply the output reservation; initially refuse.
        if payload.get('n', 1) != 1:
            raise AdmissionRefused('unsupported_multiple_completions')
        count = self.tokenizer(model=payload['model'], operation=operation, payload=payload)
        if type(count) is not int or count < 0:
            raise AdmissionRefused('invalid_exact_token_count')
        return self.binding.dispatcher.reserve('task', count, values[0])


class _TeeFile:
    def __init__(self, source, downstream, observation=None, stats=None):
        self.source, self.downstream = source, downstream
        self.observation = observation
        self.stats = stats
    def read(self, n=-1):
        data = self.source.read(n)
        if self.observation is not None:
            self.observation.feed("upstream", data)
        if self.stats is not None:
            self.stats.add_bytes("upstream", len(data))
        self.downstream.sendall(data)
        return data
    def read1(self, n=-1):
        data = self.source.read1(n)
        if self.observation is not None:
            self.observation.feed("upstream", data)
        if self.stats is not None:
            self.stats.add_bytes("upstream", len(data))
        self.downstream.sendall(data)
        return data
    def readline(self, n=-1):
        data = self.source.readline(n)
        if self.observation is not None:
            self.observation.feed("upstream", data)
        if self.stats is not None:
            self.stats.add_bytes("upstream", len(data))
        self.downstream.sendall(data)
        return data
    def close(self):
        self.source.close()


class _TeeSocket:
    def __init__(self, upstream, downstream, observation=None, stats=None):
        self.upstream, self.downstream = upstream, downstream
        self.observation = observation
        self.stats = stats
    def makefile(self, mode):
        return _TeeFile(self.upstream.makefile(mode), self.downstream, self.observation, self.stats)


class UsageObserver:
    """Bounded metadata observation; overflows keep wire bytes but lose certainty."""
    LIMIT = 1024 * 1024
    def __init__(self, content_type):
        self.sse = 'text/event-stream' in content_type
        self.buffer = bytearray()
        self.usage = None
        self.terminal = False
        self.broken = False
    def feed(self, data):
        if self.broken:
            return
        self.buffer.extend(data)
        if self.sse:
            while b'\n' in self.buffer:
                line, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                if len(line) > self.LIMIT:
                    self.broken = True
                    break
                if line.startswith(b'data:'):
                    raw = line[5:].strip()
                    if raw == b'[DONE]':
                        self.terminal = True
                        continue
                    if self.terminal:
                        self.broken = True
                        break
                    try:
                        value = json.loads(raw)
                        candidate = value.get('usage')
                        response = value.get('response')
                        if value.get('type') == 'response.completed':
                            if (not isinstance(response, dict) or response.get('status') != 'completed'
                                    or 'usage' not in response):
                                self.broken = True
                                break
                            self.usage = response['usage']
                            self.terminal = True
                        else:
                            # A later data event invalidates an earlier interim usage.
                            # Chat final usage must directly precede the terminal marker.
                            self.usage = candidate
                    except (ValueError, AttributeError):
                        self.broken = True
                        break
        if len(self.buffer) > self.LIMIT or self.broken:
            self.broken = True
            self.buffer.clear()
            self.usage = None
    def finish(self):
        if self.broken:
            return None
        if self.sse:
            return self.usage if self.terminal and not self.buffer.strip() else None
        try:
            value = json.loads(self.buffer)
            return value.get('usage') if isinstance(value, dict) else None
        except (ValueError, UnicodeError):
            return None


class Settlement:
    """Per-route reservation barrier, independent of persistent connection life."""
    def __init__(self):
        self.condition = threading.Condition()
        self.pending = {}
    def begin(self, context, ticket, stop):
        with self.condition:
            self.pending[ticket.request_id] = (context, stop, None)
    def attach(self, ticket, upstream):
        with self.condition:
            if ticket.request_id in self.pending:
                context, stop, _ = self.pending[ticket.request_id]
                self.pending[ticket.request_id] = (context, stop, upstream)
    def cancel(self):
        with self.condition:
            sockets = [entry[2] for entry in self.pending.values() if entry[2] is not None]
        for upstream in sockets:
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            upstream.close()
    def end(self, ticket):
        with self.condition:
            self.pending.pop(ticket.request_id, None)
            self.condition.notify_all()
    def wait(self, timeout=5):
        with self.condition:
            complete = self.condition.wait_for(lambda: not self.pending, timeout)
            if not complete:
                for request_id, (context, stop, upstream) in list(self.pending.items()):
                    state = context.binding.dispatcher.snapshot()
                    if state["requests"][request_id]["status"] in ("settled", "abandoned"):
                        continue
                    if upstream is not None:
                        try:
                            upstream.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    try:
                        context.binding.dispatcher.reconcile(request_id, None)
                    except StaleRequest:
                        continue  # The handler settled between the snapshot and lock.
                    stop('settlement_pending', request_id)
            return complete


def serve_budgeted(client, target, context, record_stop, tracker=None, observation=None, stats=None, settlement=None):
    """Forward admitted raw requests and response bytes, with per-request tickets."""
    reader = client.makefile('rb')
    try:
        while True:
            ticket = None
            response_started = False
            deadline_timer = None
            deadline_expired = threading.Event()
            try:
                first = reader.readline(65537)
                if len(first) > 65536:
                    raise AdmissionRefused("request_headers_too_large")
                if not first:
                    return
                lines = [first]
                while True:
                    line = reader.readline(65537)
                    if len(line) > 65536 or sum(map(len, lines)) + len(line) > 262144:
                        raise AdmissionRefused("request_headers_too_large")
                    if not line:
                        raise AdmissionRefused('incomplete_request_headers')
                    lines.append(line)
                    if line == b'\r\n':
                        break
                method, request_target, version = first.decode('ascii').strip().split(' ')
                if version not in ('HTTP/1.0', 'HTTP/1.1'):
                    raise AdmissionRefused('unsupported_http_version')
                headers = http.client.parse_headers(io.BytesIO(b''.join(lines[1:])))
                lengths = headers.get_all('Content-Length', [])
                if (headers.get('Transfer-Encoding') or headers.get('Expect') or len(lengths) != 1
                        or not lengths[0].isdigit()):
                    raise AdmissionRefused('unsupported_request_framing')
                size = int(lengths[0])
                if size > 16 * 1024 * 1024:
                    raise AdmissionRefused("request_body_too_large")
                body = reader.read(size)
                if len(body) != size:
                    raise AdmissionRefused('incomplete_request_body')
                ticket = context.admit(method, request_target, headers, body)
                if settlement is not None:
                    settlement.begin(context, ticket, record_stop)
                # No socket is constructed or connected before the durable reservation.
                request_deadline = (context.binding.dispatcher.clock() + ticket.timeout
                                    if ticket.timeout is not None else None)
                connect_timeout = min(15, ticket.timeout) if ticket.timeout is not None else 15
                with socket.create_connection(target, timeout=connect_timeout) as upstream:
                    if request_deadline is not None:
                        remaining = request_deadline - context.binding.dispatcher.clock()
                        if remaining <= 0:
                            raise TimeoutError('budget request deadline elapsed')
                        def expire(connection=upstream):
                            deadline_expired.set()
                            try:
                                connection.shutdown(socket.SHUT_RDWR)
                            except OSError:
                                pass
                        deadline_timer = threading.Timer(remaining, expire)
                        deadline_timer.daemon = True
                        deadline_timer.start()
                    if settlement is not None:
                        settlement.attach(ticket, upstream)
                    if tracker is not None:
                        with tracker.lock:
                            if tracker.stopped:
                                raise AdmissionRefused('route_stopped')
                            tracker.connections.add(upstream)
                    upstream.settimeout(ticket.timeout)
                    raw_request = b''.join(lines) + body
                    if observation is not None:
                        observation.feed('client', raw_request)
                    upstream.sendall(raw_request)
                    if stats is not None:
                        stats.add_bytes("client", len(raw_request))
                    response = http.client.HTTPResponse(_TeeSocket(upstream, client, observation, stats))
                    response_started = True
                    response.begin()
                    usage_observer = UsageObserver(response.getheader("Content-Type", ""))
                    while True:
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        usage_observer.feed(chunk)
                    if deadline_expired.is_set():
                        raise TimeoutError("budget request deadline elapsed")
                    if response.length is not None and response.length > 0:
                        raise http.client.IncompleteRead(b"", response.length)
                    usage = usage_observer.finish()
                    context.binding.dispatcher.reconcile(ticket.request_id, usage)
                    response.close()
                    if tracker is not None:
                        with tracker.lock:
                            tracker.connections.discard(upstream)
                    if usage is None:
                        record_stop('unknown_usage', ticket.request_id)
                        return
                    if headers.get('Connection', '').lower() == 'close' or response.will_close:
                        return
            except Exception as exc:
                if tracker is not None and 'upstream' in locals():
                    with tracker.lock:
                        tracker.connections.discard(upstream)
                if ticket is not None:
                    context.binding.dispatcher.reconcile(ticket.request_id, None)
                reason = (str(exc) if isinstance(exc, AdmissionRefused) else
                          ('budget_exceeded:' + exc.counter if isinstance(exc, BudgetExceeded)
                           else type(exc).__name__))
                record_stop(reason, ticket.request_id if ticket else None)
                if not response_started:
                    payload = json.dumps({'error': {'type': 'budget_stop', 'reason': reason}}).encode()
                    client.sendall(b'HTTP/1.1 429 Budget Stop\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload)
                return
            finally:
                if deadline_timer is not None:
                    deadline_timer.cancel()
                if ticket is not None and settlement is not None:
                    settlement.end(ticket)
    finally:
        if observation is not None:
            observation.feed("client", b"")
            observation.feed("upstream", b"")
        reader.close()


def task_http_context(binding, split):
    """Build the operator's pinned tokenizer contract, or retain legacy routing.

    The configured command owns correctness of the model's full provider template;
    its hash and exact/model/contract assertions establish identity, not accuracy.
    """
    policy = binding.profile.data.get('task_http') if binding is not None else None
    if policy is None:
        return None
    if not isinstance(policy, dict) or policy.get('mode') != 'strict':
        raise AdmissionRefused('invalid_task_http_policy')
    spec = policy.get('tokenizer')
    tokenizer = None
    contract = policy.get('tokenizer_contract')
    if spec is not None:
        import hashlib
        from pathlib import Path
        import subprocess
        if (not isinstance(spec, dict) or not isinstance(spec.get('argv'), list)
                or not spec['argv'] or any(not isinstance(v, str) for v in spec['argv'])
                or not isinstance(spec.get('sha256'), dict)
                or not isinstance(spec.get('models'), list) or not spec['models']
                or not isinstance(contract, str) or not contract):
            raise AdmissionRefused('invalid_tokenizer_contract')
        argv = tuple(spec['argv'])
        hashes = dict(spec['sha256'])
        executable = Path(argv[0])
        if not executable.is_absolute() or str(executable) not in hashes:
            raise AdmissionRefused('unpinned_tokenizer_executable')
        # Script/file arguments must be pinned too; flags/literal parameters are
        # part of the immutable profile hash. No shell is ever invoked.
        for argument in argv[1:]:
            if Path(argument).is_file() and str(Path(argument).absolute()) not in hashes:
                raise AdmissionRefused('unpinned_tokenizer_file')
        def tokenize(*, model, operation, payload):
            if model not in spec['models']:
                raise AdmissionRefused('tokenizer_model_mismatch')
            for name, digest in hashes.items():
                if not Path(name).is_absolute() or hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
                    raise AdmissionRefused('tokenizer_identity_mismatch')
            try:
                result = subprocess.run(argv, input=json.dumps({
                    'contract_id': contract, 'model': model, 'operation': operation,
                    'payload': payload}).encode(), stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                    cwd='/', timeout=spec.get('timeout_s', 30), check=True)
                value = json.loads(result.stdout)
            except Exception as exc:
                raise AdmissionRefused('tokenizer_execution_failed') from exc
            if (not isinstance(value, dict) or value.get('exact') is not True
                    or value.get('contract_id') != contract or value.get('model') != model):
                raise AdmissionRefused('tokenizer_contract_mismatch')
            return value.get('prompt_tokens')
        tokenizer = tokenize
    return TaskHTTPBudget(binding, split, tokenizer, contract)
