#!/usr/bin/env python3
"""Trusted fixed-task ScienceWorld replay service."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
# Isolated (-I) Python omits the script directory; runtime.py is an explicit
# read-only sibling admitted by the setup declaration.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from eval.service.tool_transport import Relay
import scw_env


class Game:
    """Fixed selected task; no gold/reference operation is accepted."""
    def __init__(self, spec, *, make_env=None):
        self.spec = spec
        self.make_env = make_env
        self.lock = threading.Lock()
    def handle(self, operation, arguments):
        if operation not in ('look','inventory','task') or not isinstance(arguments,dict) or set(arguments) != {'actions'}:
            raise ValueError('invalid request')
        actions=arguments['actions']
        if not isinstance(actions,list) or len(actions)>100 or any(not isinstance(a,str) for a in actions):
            raise ValueError('invalid action history')
        with self.lock:
            env=None
            try:
                if self.make_env:
                    env=self.make_env()
                else:
                    import os
                    os.environ['HG_SEALED_JAVA_HOME']=self.spec['java_home']
                    env=scw_env.make_env(Path('.'),step_budget=100,server_path=Path(self.spec['jar']))
                    task=self.spec['task']
                    env.load(task['task_name'],task['variation'],task['simplifications'],generateGoldPath=False)
                last=None
                for action in actions:
                    last=env.step(action)
                    if last[2]: break
                if operation=='look':
                    # The CLI needs feedback/status only. Do not expose wrapper
                    # info fields (e.g. valid actions) through the raw socket.
                    public_last = ([str(last[0]), last[1], bool(last[2]),
                                    {'score': last[3].get('score', 0)}] if last is not None else None)
                    return {'text':str(env.look()).strip(),'last':public_last}
                if operation=='inventory': return {'text':str(env.inventory()).strip()}
                return {'text':str(env.get_task_description()).strip()}
            except Exception:
                raise RuntimeError('ScienceWorld environment unavailable') from None
            finally:
                if env is not None:
                    try:
                        env.close()
                    except Exception:
                        raise RuntimeError('ScienceWorld environment unavailable') from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-spec', type=Path, required=True)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--budget', type=int, default=None)
    parser.add_argument('--max-calls', type=int, default=None)
    parser.add_argument('--deadline-s', type=float, default=None)
    parser.add_argument('--max-line-bytes', type=int, default=None)
    args = parser.parse_args()
    spec = json.loads(args.case_spec.read_text())
    from runtime import JAR_SHA256
    jar=Path(spec['jar'])
    if not jar.is_absolute() or jar.is_symlink() or hashlib.sha256(jar.read_bytes()).hexdigest()!=JAR_SHA256:
        raise ValueError('ScienceWorld JAR identity mismatch')
    java=Path(spec['java_home']) / 'bin/java'
    if hashlib.sha256(java.read_bytes()).hexdigest()!=spec['java_sha256']:
        raise ValueError('ScienceWorld Java identity mismatch')
    game = Game(spec)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with Relay(args.socket, game.handle, max_calls=args.max_calls, deadline_s=args.deadline_s,
               max_line_bytes=args.max_line_bytes) as relay:
        clean = stop.wait(args.deadline_s)
    args.receipt.write_text(json.dumps({'schema':'harnessgrad.scienceworld-relay.v1',
        'stopped_cleanly':clean, 'transport':relay.receipt}) + '\n')
    return 0 if clean and not (relay.receipt['errors'] or relay.receipt['refused']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
