#!/usr/bin/env python3
"""Selected WorkBench world: native tools only; no actor-accessible grade/reset."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]
from eval.service.tool_transport import Relay
from workbench_process import Session


def main():
    parser = argparse.ArgumentParser()
    for name in ('case-spec', 'socket', 'receipt'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('deadline-s', 'max-calls', 'max-line-bytes'):
        parser.add_argument('--' + name, type=float if name == 'deadline-s' else int)
    args = parser.parse_args()
    spec = json.loads(args.case_spec.read_text())
    world = Path(spec['world'])
    for name, expected in spec['files'].items():
        path = world / name
        if path.is_symlink() or not path.resolve().is_relative_to(world.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('native WorkBench world changed after preparation')
    session = Session(world)
    actions = []
    failure = None
    lock = threading.Lock()
    def handle(op, value):
        nonlocal failure
        with lock:
            if op == 'describe' and value == {}:
                return {'tools': session.description}
            if op != 'call' or set(value) != {'name', 'arguments'} or not isinstance(value['arguments'], dict):
                return {'error': 'only describe or native call(name,arguments) is allowed'}
            try:
                response = session.request({'operation': 'call', **value})
                actions.append({'request': value, 'response': response})
                return response
            except Exception as exc:
                failure = type(exc).__name__
                raise
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with Relay(args.socket, handle, max_calls=args.max_calls,
               deadline_s=args.deadline_s, max_line_bytes=args.max_line_bytes) as relay:
        clean = stop.wait(args.deadline_s)
        with lock:
            Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps({
                'task_id': spec['task_id'], 'world_sha256': spec['world_sha256'],
                'actions': actions, 'failure': failure}))
        args.receipt.write_text(json.dumps({'transport': relay.receipt, 'stopped_cleanly': clean,
                                           'task_id': spec['task_id'], 'failure': failure}))
    session.close()
    return 0 if clean and not failure else 3


if __name__ == '__main__':
    raise SystemExit(main())
