"""Trusted verifier: replay action.json, not subject-supplied state or observations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reward-file', default='verifier/reward.txt')
    ap.add_argument('--evidence-file', default='verifier/evidence.json')
    args = ap.parse_args()
    work = Path.cwd()
    reward_file = work / args.reward_file
    evidence_file = work / args.evidence_file
    reward_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_file.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(work / 'trusted_runtime/bin/python'), str(work / 'verify/bridge.py'),
            'evaluate', '--case', str(work / 'verify/case.json')]
    if (work / 'action.json').is_file():
        argv += ['--action', str(work / 'action.json')]
    proc = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=90)
    if proc.returncode:
        evidence_file.write_text(json.dumps({'measured': False, 'error': proc.stderr[-2000:]}))
        reward_file.unlink(missing_ok=True)
        return 3
    result = json.loads(proc.stdout)
    reward_file.write_text(str(result['reward']) + '\n')
    result.update(measured=True, why=f"utility={result['utility']}, attack_success={result['attack_success']}",
                  official={'metric': 'AgentDojo utility under fixed injection',
                            'attack_success': result['attack_success'], 'version': result['suite_version']})
    evidence_file.write_text(json.dumps(result))
    print(f"reward {result['reward']:g}: {result['why']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
