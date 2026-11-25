#!/usr/bin/env python3
"""Run one private episode behind the platform's tool socket."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import sys
import threading

ASSETS = Path(__file__).resolve().parent
sys.path[:0] = [str(ASSETS), str(ASSETS.parents[2])]

from eval.service.tool_transport import Relay
from protocol import EPISODE_SCHEMA, REVISION, SCENE_SPLIT, encode_case, validate_case
from simulator import Episode, open_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('socket', 'receipt', 'case-spec'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--max-line-bytes', type=int)
    args = parser.parse_args()

    raw = args.case_spec.read_bytes()
    spec = json.loads(raw)
    context = json.loads(Path(os.environ['HG_RELAY_CONTEXT_PATH']).read_text())
    case = validate_case(spec['case'])
    if context['task_id'] != case['task_id'] or context['input_sha256'] != sha256(raw).hexdigest():
        raise ValueError('RoboCasa365 relay context mismatch')
    env, observation = open_env(case, spec['prepared'], spec['osmesa'])
    episode = Episode(env, observation, case['horizon'])
    lock = threading.Lock()
    stop = threading.Event()

    def handle(op, args):
        with lock:
            return episode.handle(op, args)

    relay = Relay(args.socket, handle, max_calls=None, max_line_bytes=args.max_line_bytes)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())

    try:
        with relay:
            stop.wait()
    finally:
        with lock:
            env.close()

    clean = relay.receipt['errors'] == relay.receipt['request_errors'] and not relay.receipt['refused']
    args.receipt.write_text(json.dumps({
        'transport': relay.receipt, 'stopped_cleanly': clean, 'task_id': case['task_id'],
        'python': sys.executable, 'python_version': list(sys.version_info[:3]),
    }))
    if not clean:
        return 1

    Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps({
        'schema': EPISODE_SCHEMA, 'task_id': case['task_id'], 'revision': REVISION,
        'scene_split': SCENE_SPLIT, 'bundle': spec['bundle'],
        'input_sha256': context['input_sha256'],
        'case_sha256': sha256(encode_case(case).encode()).hexdigest(),
        'steps': episode.steps, 'first_success_step': episode.first_success_step,
        'terminal': episode.ended, 'python_version': list(sys.version_info[:3]),
    }))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
