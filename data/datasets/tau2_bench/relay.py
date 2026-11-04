#!/usr/bin/env python3
"""Selected-task private τ² runtime; actor has no reset, seed or scoring endpoint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from eval.service.tool_transport import Relay, call
from bootstrap import bootstrap
from runtime import validate_assistant_calls


class Service:
    def __init__(self, config, state):
        self.config = deepcopy(config)
        self.state = state
        self.failure = None
        self.lock = threading.Lock()

    def handle(self, op, args):
        with self.lock:
            if not isinstance(args, dict):
                return {'error': 'arguments_must_be_object'}
            # Invalid actor requests are ordinary protocol errors, never admin access.
            operations = {
                'start': (self.state.start_conversation, set()),
                'say': (self.state.submit_assistant_message, {'message'}),
                'call': (self.state.call_assistant_tool, {'tool_name', 'arguments'}),
                'calls': (lambda calls: self.state.call_assistant_tools(calls), {'calls'}),
                'status': (self.state.get_runtime_status, set()),
            }
            if op not in operations or set(args) != operations[op][1]:
                return {'error': 'unsupported_operation_or_fields'}
            if self.state.termination_reason is not None and op != 'status':
                return {'error': 'conversation_ended', 'termination_reason': self.state.termination_reason}
            if op == 'say' and (not isinstance(args['message'], str) or not args['message'].strip()):
                return {'error': 'message_must_be_text'}
            if op in ('call', 'calls'):
                calls = (args['calls'] if op == 'calls' else
                         [{'id': 'single', 'name': args['tool_name'], 'arguments': args['arguments']}])
                try:
                    validate_assistant_calls(calls)
                except ValueError:
                    return {'error': 'invalid_tool_call'}
            if op in ('say', 'call', 'calls') and not self.state.bootstrap_complete:
                return {'error': 'start_conversation_first'}
            try:
                return {'result': operations[op][0](**args)}
            except Exception as exc:
                if self.failure is None:
                    self.failure = type(exc).__name__
                raise

    def episode(self):
        # `task_id` stays the released id the official scorer binds to; the platform id the
        # verdict is keyed by travels beside it, and `revision` is the release tag itself.
        episode = self.state.episode()
        if (episode.get('task_id') != self.config['task']['id']
                or episode.get('domain') != self.config['domain']
                or episode.get('seed') != self.config['seed']):
            raise ValueError('private runtime episode identity mismatch')
        return {**episode, 'case_task_id': self.config['task_id'],
                'failure': self.failure, 'revision': self.config['release'],
                'case_sha256': self.config['case_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-spec', type=Path, required=True)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--deadline-s', type=float)
    parser.add_argument('--max-calls', type=int)
    parser.add_argument('--max-line-bytes', type=int)
    args = parser.parse_args()
    spec = json.loads(args.case_spec.read_text())
    raw = Path(spec['case']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec['sha256']:
        raise ValueError('selected private case digest mismatch')
    config = json.loads(raw)
    context = json.loads(Path(os.environ['HG_RELAY_CONTEXT_PATH']).read_text())
    if config['task_id'] != context['task_id']:
        raise ValueError('relay task binding mismatch')
    config.update(seed=int(context['seed']), user_model=spec['user_model'], case_sha256=spec['sha256'])
    bootstrap(spec['bundle'])
    from model import install
    from runtime import Tau2Runtime
    install('environment', call)
    private_config = args.case_spec.with_name('runtime-config.json')
    private_config.write_text(json.dumps(config))
    service = Service(config, Tau2Runtime(private_config))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with Relay(args.socket, service.handle, max_calls=args.max_calls,
               deadline_s=args.deadline_s, max_line_bytes=args.max_line_bytes) as relay:
        clean = stop.wait(args.deadline_s)
        # Wait for any current simulator/tool operation before sealing. The platform
        # bounds graceful exit; an unfinished call cannot create an accepted episode.
        with service.lock:
            # Generic shutdown failures must not replace the first known exception.
            if service.failure is None:
                if not clean:
                    service.failure = 'service_deadline'
                elif (relay.receipt['errors'] > relay.receipt['request_errors']
                      or relay.receipt['refused']):
                    service.failure = 'transport_failure'
            Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps(service.episode()))
    args.receipt.write_text(json.dumps({'transport': relay.receipt, 'stopped_cleanly': clean,
                                      'task_id': config['task_id'], 'failure': service.failure}))
    return 0 if clean and service.failure is None else 1

if __name__ == '__main__':
    raise SystemExit(main())
