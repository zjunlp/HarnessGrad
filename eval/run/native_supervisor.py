"""Bounded lifecycle supervision for fixed platform-owned native relay workers.

Workers publish their exact Docker names before first create/start. Their parent
owns shutdown independently of worker initialization, active RPC locks and final
artifact collection. The helper never accepts cleanup targets from an actor.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

_NAME = re.compile(r'(?:hg-observed-[0-9a-f]{20}(?:-seed)?|hg-native-public-[0-9a-f]{20})')
_SCHEMAS = {'harnessgrad.swe-cleanup.v1', 'harnessgrad.native-cleanup.v1'}


def publish_runtime(runtime):
    """Publish a trusted runtime's owned names before its first Docker call."""
    value = os.environ.get('HG_NATIVE_CLEANUP_PATH') or os.environ.get('HG_SWE_CLEANUP_PATH')
    if not value:
        return
    names = [runtime.name]
    seed = getattr(runtime, 'seed', None)
    if seed:
        names.append(seed)
    if any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in names):
        raise ValueError('native runtime allocated an unsupported container name')
    path = Path(value)
    with path.open('x', encoding='utf-8') as out:
        json.dump({'schema': 'harnessgrad.native-cleanup.v1', 'containers': names}, out)


def kill_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def cleanup_names(path, *, timeout_s=5.0):
    """Remove exact private startup names, bounded independently of worker code."""
    deadline = time.monotonic() + timeout_s
    if not path.exists():
        return {'complete': True, 'containers': [], 'reason': 'worker did not allocate runtime'}
    try:
        record = json.loads(path.read_text())
        names = record['containers']
        if (record.get('schema') not in _SCHEMAS or not isinstance(names, list)
                or not names or len(names) != len(set(names))
                or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in names)):
            raise ValueError('invalid owned-container declaration')
        def command(argv):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('supervisor cleanup deadline')
            return subprocess.run(argv, capture_output=True, timeout=remaining, start_new_session=True)
        removed = command(['docker', 'rm', '--force', '--volumes', *names])
        errors = []
        for name in names:
            state = command(['docker', 'inspect', '--type', 'container', name])
            if state.returncode == 0 or b'no such' not in state.stderr.lower():
                errors.append('container absence not confirmed: ' + name)
        return {'complete': not errors, 'containers': names, 'errors': errors,
                'remove_returncode': removed.returncode}
    except Exception as exc:
        return {'complete': False, 'error_type': type(exc).__name__, 'error': str(exc)}


def supervise(args, entrypoint, argv, *, worker_flag, ownership_env='HG_NATIVE_CLEANUP_PATH',
              grace_s=5.0, cleanup_s=5.0, label='native'):
    """Stop grace + cleanup stay below the outer ToolRelay 15-second SIGKILL."""
    stopped = threading.Event()
    proc = None
    previous = {}
    def stop(*_):
        stopped.set()
        if proc is not None:
            kill_group(proc, signal.SIGTERM)
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, stop)
    ownership = args.receipt.parent / (label + '-owned-' + uuid.uuid4().hex + '.json')
    env = dict(os.environ, **{worker_flag: '1', ownership_env: str(ownership),
                            'HG_NATIVE_CLEANUP_PATH': str(ownership)})
    code = 3
    forced = False
    try:
        proc = subprocess.Popen([sys.executable, str(entrypoint), *argv], env=env,
                                start_new_session=True)
        grace = None
        while proc.poll() is None:
            if stopped.is_set():
                if grace is None:
                    grace = time.monotonic() + grace_s
                    kill_group(proc, signal.SIGTERM)
                if time.monotonic() >= grace:
                    forced = True
                    kill_group(proc, signal.SIGKILL)
                    break
            time.sleep(0.025)
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            kill_group(proc, signal.SIGKILL)
            proc.wait(timeout=0.5)
        code = proc.returncode
    finally:
        if proc is not None:
            kill_group(proc, signal.SIGKILL)
        cleanup = cleanup_names(ownership, timeout_s=cleanup_s)
        summary = {'schema': 'harnessgrad.native-relay-supervisor.v1', 'worker': label,
                   'cleanup': cleanup, 'worker_exit_code': code, 'forced_stop': forced}
        print(json.dumps(summary), file=sys.stderr, flush=True)
        if not cleanup.get('complete') or forced or code != 0:
            code = 3
        if args.receipt.exists():
            try:
                receipt = json.loads(args.receipt.read_text())
                receipt['supervisor'] = summary
                args.receipt.write_text(json.dumps(receipt))
            except (OSError, ValueError):
                code = 3
        if ownership.exists():
            ownership.unlink()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return code
