#!/usr/bin/env python3
"""Trusted, selected-task CostBench state. No actor reset, solver or scoring API."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from eval.service.tool_transport import Relay
from bootstrap import bootstrap
from runtime import CostBenchRuntime


class Service:
    def __init__(self, config, public):
        self.state = CostBenchRuntime(config, public)
        self.failure = None
        self.lock = threading.Lock()

    def handle(self, op, args):
        with self.lock:
            if not isinstance(args, dict):
                return {'error': 'arguments_must_be_object'}
            if op == 'status' and not args:
                return self.state.status()
            if op != 'turn' or set(args) != {'content', 'calls'}:
                return {'error': 'unsupported_operation_or_fields'}
            try:
                return self.state.turn(**args)
            except Exception as exc:
                self.failure = type(exc).__name__
                raise

    def episode(self):
        return {**self.state.episode(), 'failure': self.failure}


def checked_json(path, expected):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('selected case digest mismatch')
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('case-spec', 'socket', 'receipt'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--deadline-s', type=float)
    parser.add_argument('--max-calls', type=int)
    parser.add_argument('--max-line-bytes', type=int)
    args = parser.parse_args()
    spec = json.loads(args.case_spec.read_text())
    config = checked_json(spec['case'], spec['sha256'])
    public = checked_json(spec['public'], spec['public_sha256'])
    context = json.loads(Path(os.environ['HG_RELAY_CONTEXT_PATH']).read_text())
    if config['task_id'] != context['task_id']:
        raise ValueError('relay task binding mismatch')
    config.update(seed=str(context['seed']), case_sha256=spec['sha256'])
    # Paths in prepared cases are relative so source/manifest pins are portable.
    search_space = Path(spec['dataset_root']) / config['search_space']
    with search_space.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != config['search_space_sha256']:
        raise ValueError('search database digest mismatch')
    bootstrap(spec['bundle'], split=config['split'], search_space=search_space,
              config_path=args.case_spec.with_name('runtime-config.yaml'))
    service = Service(config, public)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with Relay(args.socket, service.handle, max_calls=args.max_calls,
               deadline_s=args.deadline_s, max_line_bytes=args.max_line_bytes) as relay:
        clean = stop.wait(args.deadline_s)
        with service.lock:
            if not clean:
                service.failure = 'service_deadline'
            elif relay.receipt['errors'] or relay.receipt['refused']:
                service.failure = 'transport_failure'
            Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps(service.episode()))
    args.receipt.write_text(json.dumps({'transport': relay.receipt, 'stopped_cleanly': clean,
                                      'task_id': config['task_id']}))
    return 0 if clean and service.failure is None else 1


if __name__ == '__main__':
    raise SystemExit(main())
