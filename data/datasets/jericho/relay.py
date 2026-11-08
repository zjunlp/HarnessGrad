#!/usr/bin/env python3
"""Trusted single-game Jericho relay. No walkthrough is returned to the client."""
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
sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from eval.service.tool_transport import Relay
import jer_env


def _make_public_env(rom, *, seed):
    """Match the existing actor projection's empty bindings and zero seed rule."""
    from jericho import FrotzEnv, defines
    defines.BINDINGS_DICT = {}

    class PublicFrotzEnv(FrotzEnv):
        def seed(self, value=None):
            self._seed = value if value is not None else -1
            return self._seed

    env = PublicFrotzEnv(str(rom), seed=seed)
    env.reset()
    return env


class Game:
    """Replay the actor's action log against a fresh environment per request."""
    def __init__(self, rom, seed, *, make_env=None):
        self.rom = Path(rom)
        self.seed = seed
        self.make_env = make_env or _make_public_env
        self.lock = threading.Lock()

    def handle(self, operation, arguments):
        if operation not in ('look', 'do', 'score', 'inventory') or not isinstance(arguments, dict):
            raise ValueError('invalid request')
        actions = arguments.get('actions')
        if not isinstance(actions, list) or any(not isinstance(a, str) for a in actions):
            raise ValueError('invalid action history')
        if operation == 'do' and (not isinstance(arguments.get('action'), str) or not arguments['action'].strip()):
            raise ValueError('invalid action')
        if set(arguments) != ({'actions', 'action'} if operation == 'do' else {'actions'}):
            raise ValueError('invalid request fields')
        try:
            with self.lock:
                env = self.make_env(self.rom, seed=self.seed)
                try:
                    replay = jer_env.replay(env, actions)
                    if replay.get('failure'):
                        raise RuntimeError('replay failed')
                    last = (replay.get('steps') or [None])[-1]
                    if operation == 'look':
                        try:
                            observation, _, _, _ = env.step('look')
                        except Exception:
                            if last is None:
                                raise
                            observation = last['observation']
                        return {'text': str(observation).strip(), 'moves': (last or {}).get('moves', 0),
                                'score': (last or {}).get('score', 0), 'maximum': env.get_max_score()}
                    if operation == 'do':
                        observation, _, done, info = env.step(arguments['action'].strip())
                        return {'text': str(observation).strip(), 'moves': info.get('moves', 0),
                                'score': info.get('score', 0), 'maximum': env.get_max_score(), 'done': bool(done)}
                    if operation == 'score':
                        return {'moves': (last or {}).get('moves', 0), 'score': (last or {}).get('score', 0),
                                'maximum': env.get_max_score(), 'actions': len(actions)}
                    return {'text': str(env.get_inventory()).strip()}
                finally:
                    # Frotz owns process-global native state. Close while the
                    # request lock is held, including failed replay/inspection.
                    close = getattr(env, 'close', None)
                    if close is not None:
                        close()
        except Exception:
            # Relay formats handler exceptions on the wire: never include private data.
            raise RuntimeError('Jericho environment unavailable') from None


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
    rom = Path(spec['rom_path'])
    if not rom.is_absolute() or rom.is_symlink() or not rom.is_file():
        raise ValueError('invalid Jericho game input')
    if hashlib.sha256(rom.read_bytes()).hexdigest() != spec['rom_sha256']:
        raise ValueError('Jericho game input digest mismatch')
    if type(spec.get('seed')) is not int:
        raise ValueError('invalid Jericho seed')
    game = Game(rom, spec['seed'])
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with Relay(args.socket, game.handle, max_calls=args.max_calls, deadline_s=args.deadline_s,
               max_line_bytes=args.max_line_bytes) as relay:
        clean = stop.wait(args.deadline_s)
    args.receipt.write_text(json.dumps({'schema':'harnessgrad.jericho-relay.v1',
        'stopped_cleanly':clean, 'transport':relay.receipt}) + '\n')
    return 0 if clean and not (relay.receipt['errors'] or relay.receipt['refused']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
