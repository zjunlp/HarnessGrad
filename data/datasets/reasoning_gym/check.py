#!/usr/bin/env python3
"""Adapt the trusted QA check's measured-wrong exit to bound verifier transport."""
import sys

if __name__ == '__main__' and not sys.flags.isolated:
    raise SystemExit('Reasoning Gym checks require Python isolated mode (-I)')

import json
import math
from pathlib import Path
import subprocess


def main():
    # The generic QA check compiles its allowlisted modules directly from staged
    # source and keeps actor cwd out of sys.path. Never load it as a package here.
    for name in ('verifier/reward.txt', 'verifier/evidence.json'):
        Path(name).unlink(missing_ok=True)
    result = subprocess.run([sys.executable, '-I', '-B', 'qa/lib/verify/check.py',
                             *sys.argv[1:]], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        return 2
    try:
        evidence = json.loads(Path('verifier/evidence.json').read_text())
        reward = float(Path('verifier/reward.txt').read_text().strip())
        recorded_reward = evidence.get('reward')
        if (evidence.get('measured') is not True or not math.isfinite(reward)
                or not 0 <= reward <= 1 or type(recorded_reward) not in (int, float)
                or not math.isfinite(recorded_reward) or float(f'{recorded_reward:g}') != reward
                or evidence.get('scorer_input', {}).get('verified') is not True):
            return 2
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
