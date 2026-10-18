#!/usr/bin/env python3
"""Run the pinned Harbor merged-pair scorer in a fresh, isolated runtime."""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path.cwd()
    output = root / 'verifier'
    output.mkdir(exist_ok=True)
    evidence = {'metric': 'cooperbench.harbor.merged_pair_success.v1', 'measured': False}
    try:
        case = json.loads((root / 'private/case.json').read_text())
        evidence.update(task_id=case['task_id'], provenance=case['provenance'])
        base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root / 'repo', text=True).strip()
        if base != case['base_commit']:
            raise ValueError('runtime repository base identity mismatch')
        for relative, expected in case['private_sha256'].items():
            if hashlib.sha256((root / 'private' / relative).read_bytes()).hexdigest() != expected:
                raise ValueError(f'private scorer input identity mismatch: {relative}')
        record_path = root / 'worker_record/workers.json'
        record = json.loads(record_path.read_text()) if record_path.exists() else None
        if record is not None and record['task_id'] != case['task_id']:
            raise ValueError('worker record belongs to another task')
        for name in ('agent1', 'agent2'):
            shared = root / 'shared' / name
            shared.mkdir(parents=True)
            patch = root / 'workers' / name / 'agent.patch'
            if patch.is_file():
                raw = patch.read_bytes()
                if record is not None:
                    artifact = record['workers'][name]['artifacts']['agent.patch']
                    if artifact.get('sha256') != hashlib.sha256(raw).hexdigest():
                        raise ValueError('worker artifact identity mismatch')
                shutil.copyfile(patch, shared / 'agent.patch')
            (shared / 'agent.done').touch()
        # Native builds can exceed the small system /tmp mount. Keep their
        # scratch inside the already admitted, bounded workspace instead.
        scratch = root / '.test-tmp'
        scratch.mkdir(exist_ok=True)
        proc = subprocess.run(['bash', 'private/test.sh'], text=True, capture_output=True, timeout=1700,
                              env={**os.environ, 'TMPDIR': str(scratch)})
        evidence['scorer_exit_code'] = proc.returncode
        if proc.returncode:
            raise RuntimeError(f'Harbor scorer failed: {proc.stderr[-4000:]}')
        official = json.loads((root / 'logs/verifier/reward.json').read_text())
        reward = int((root / 'logs/verifier/reward.txt').read_text())
        if reward not in (0, 1) or official['reward'] != reward:
            raise ValueError('inconsistent official score')
        evidence['official'] = official
        test_log = root / 'logs/verifier/test-output.txt'
        evidence['test_output_tail'] = test_log.read_text(errors='replace')[-16000:] if test_log.exists() else ''
        exits = [int(code) for code in re.findall(r'=== Feature [12] tests \(exit=(\d+)\) ===', test_log.read_text(errors='replace') if test_log.exists() else '')]
        if any(code in (124, 125, 126, 127, 137, 143) for code in exits):
            raise RuntimeError(f'feature runner timeout/infrastructure failure: {exits}')
        evidence['measured'] = True
        (output / 'reward.txt').write_text(str(reward))
    except Exception as exc:
        evidence['error'] = f'{type(exc).__name__}: {exc}'
    (output / 'evidence.json').write_text(json.dumps(evidence))
    return 0 if evidence['measured'] else 3


if __name__ == '__main__':
    sys.exit(main())
