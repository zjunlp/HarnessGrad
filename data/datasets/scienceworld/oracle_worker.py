#!/usr/bin/env python3
"""Trusted operator-only reference generation; never an actor relay endpoint."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import sys

ASSETS = Path(__file__).resolve().parent
sys.path.insert(0, str(ASSETS))
sys.path.insert(0, str(ASSETS / 'lib'))
import scw_env
from runtime import JAR_SHA256


def generate(spec):
    jar = Path(spec['jar'])
    java = Path(spec['java_home']) / 'bin/java'
    if (not jar.is_absolute() or jar.is_symlink()
            or hashlib.sha256(jar.read_bytes()).hexdigest() != JAR_SHA256):
        raise ValueError('ScienceWorld JAR identity mismatch')
    if hashlib.sha256(java.read_bytes()).hexdigest() != spec['java_sha256']:
        raise ValueError('ScienceWorld Java identity mismatch')
    import os
    os.environ['HG_SEALED_JAVA_HOME'] = spec['java_home']
    env = scw_env.make_env(Path('.'), step_budget=100, server_path=jar)
    try:
        task = spec['task']
        env.load(task['task_name'], task['variation'], task['simplifications'], generateGoldPath=True)
        actions = list(env.get_gold_action_sequence())
        if not actions or any(not isinstance(a, str) for a in actions) or actions[0].startswith('ERROR'):
            raise RuntimeError('selected task has no reference action sequence')
        return actions
    finally:
        env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-spec', type=Path, required=True)
    args = parser.parse_args()
    # Only the trusted parent consumes stdout. Failures never become empty paths.
    with contextlib.redirect_stdout(sys.stderr):
        actions = generate(json.loads(args.case_spec.read_text()))
    print(json.dumps(actions))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
