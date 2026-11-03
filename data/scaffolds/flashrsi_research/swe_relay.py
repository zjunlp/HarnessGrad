#!/usr/bin/env python3
"""Private SWE repository capability with bounded, startup-safe supervision.

The supervisor owns exact Docker names published before worker startup. On stop,
it gives the worker a short patch-export/cleanup grace, then removes only those
containers and confirms absence. A killed export is an unmeasured episode.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.scaffolds.flashrsi_research.swe_native import Session
from eval.service.tool_transport import Relay
from eval.run.native_supervisor import cleanup_names as _cleanup_names, kill_group, supervise

GRACE_S = 5.0
CLEANUP_S = 5.0



def arguments():
    parser = argparse.ArgumentParser()
    for field in ('socket', 'receipt', 'case-spec'):
        parser.add_argument('--' + field, required=True, type=Path)
    for field in ('deadline-s', 'max-calls', 'max-line-bytes'):
        parser.add_argument('--' + field, type=float if field == 'deadline-s' else int)
    return parser.parse_args()


def worker(args):
    # Install handlers before Session: Docker image copying/init may be slow.
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    spec = json.loads(args.case_spec.read_text())
    context = json.loads(Path(os.environ['HG_RELAY_CONTEXT_PATH']).read_text())
    session = None
    lock = threading.Lock()
    failure = None
    relay = None
    cleanup_error = None
    try:
        session = Session(spec['case'], spec['case_sha256'], context)
        if stop.is_set():
            raise InterruptedError('native SWE interrupted during startup')
        def handle(op, value):
            nonlocal failure
            if op != 'exec' or set(value) != {'command'} or not isinstance(value['command'], str):
                return {'error': 'only exec(command) in the selected repository is supported'}
            with lock:
                try:
                    return session.execute(value['command'])
                except Exception as exc:
                    failure = type(exc).__name__
                    raise
        with Relay(args.socket, handle, max_calls=args.max_calls,
                   deadline_s=args.deadline_s, max_line_bytes=args.max_line_bytes) as relay:
            clean = stop.wait(args.deadline_s)
            relay.stop()
            # A command still in flight may outlive actor exit. The supervisor
            # will reap it; do not wait on this lock beyond the stop grace.
            if not lock.acquire(timeout=1.0):
                raise TimeoutError('native SWE command still active at seal')
            try:
                payload = session.finish()
                payload['failure'] = failure
            finally:
                lock.release()
        session.close()
        Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps(payload))
        args.receipt.write_text(json.dumps({'transport': relay.receipt,
            'task_id': spec['case']['task_id'], 'stopped_cleanly': clean,
            'runtime': session.runtime.receipt, 'failure': failure}))
        return 0 if clean and not failure else 3
    except Exception as exc:
        failure = failure or type(exc).__name__
        # Full original diagnostics stay in the private relay log, never a
        # model-facing response or a substituted zero score.
        print(json.dumps({'schema': 'harnessgrad.swe-relay-worker-error.v1',
            'error_type': type(exc).__name__, 'detail': str(exc),
            'runtime': session.runtime.receipt if session is not None else None}),
            file=sys.stderr, flush=True)
        return 3
    finally:
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                cleanup_error = type(exc).__name__
                print(json.dumps({'schema': 'harnessgrad.swe-relay-cleanup.v1',
                    'cleanup_error': cleanup_error,
                    'runtime': session.runtime.receipt}), file=sys.stderr, flush=True)


def cleanup_names(path, *, timeout_s=CLEANUP_S):
    return _cleanup_names(path, timeout_s=timeout_s)


def main():
    args = arguments()
    if os.environ.get('HG_SWE_RELAY_WORKER') == '1':
        return worker(args)
    return supervise(args, Path(__file__).resolve(), sys.argv[1:],
        worker_flag='HG_SWE_RELAY_WORKER', ownership_env='HG_SWE_CLEANUP_PATH',
        grace_s=GRACE_S, cleanup_s=CLEANUP_S, label='swe')


if __name__ == '__main__':
    raise SystemExit(main())
