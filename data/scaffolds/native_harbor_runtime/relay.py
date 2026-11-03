#!/usr/bin/env python3
"""Supervised private native actor; commands never reach the Docker host."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.scaffolds.native_harbor_runtime.session import Session
from eval.run.native_supervisor import supervise
from eval.service.tool_transport import Relay


def arguments():
    parser = argparse.ArgumentParser()
    for key in ('case-spec', 'socket', 'receipt'):
        parser.add_argument('--' + key, required=True, type=Path)
    parser.add_argument('--max-calls', type=int)
    parser.add_argument('--max-line-bytes', type=int)
    parser.add_argument('--deadline-s', type=float)
    return parser.parse_args()


def worker(args):
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    case = json.loads(args.case_spec.read_text())
    context = json.loads(Path(os.environ['HG_RELAY_CONTEXT_PATH']).read_text())
    binding = {'adapter_id': case['task_id'], 'run_id': context['run_id'],
               'trial_id': context.get('trial_id') or 'trial-1', 'manifest_sha256': case['source_sha256']}
    session = None
    error = None
    try:
        # Session publishes exact Docker names before entering its runtime. The
        # parent can stop/clean startup even before the tool socket is listening.
        session = Session(case, binding)
        if stopped.is_set():
            raise InterruptedError('native actor interrupted during startup')
        with Relay(args.socket, session.handle, max_calls=args.max_calls,
                   max_line_bytes=args.max_line_bytes, deadline_s=args.deadline_s) as relay:
            stopped.wait(args.deadline_s)
        try:
            session.finish(cleanup=True)
        except Exception as exc:
            error = type(exc).__name__
        Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps(session.record(), allow_nan=False))
        args.receipt.write_text(json.dumps({'schema': 'native-harbor-relay.v1',
            'task_id': case['task_id'], 'stopped_cleanly': error is None,
            'error': error, 'transport': relay.receipt}))
        return 1 if error else 0
    except Exception as exc:
        print(json.dumps({'schema': 'native-harbor-worker-error.v1',
            'error_type': type(exc).__name__, 'detail': str(exc),
            'runtime': session.runtime.receipt if session is not None else None}),
            file=sys.stderr, flush=True)
        return 3
    finally:
        if session is not None:
            session.runtime.close()


def main():
    args = arguments()
    if os.environ.get('HG_NATIVE_HARBOR_RELAY_WORKER') == '1':
        return worker(args)
    return supervise(args, Path(__file__).resolve(), sys.argv[1:],
        worker_flag='HG_NATIVE_HARBOR_RELAY_WORKER', label='harbor')


if __name__ == '__main__':
    raise SystemExit(main())
