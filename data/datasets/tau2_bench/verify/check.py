#!/usr/bin/env python3
"""Trusted scoring of a sealed τ² episode; private inputs are projected only after actor exit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path('tau2_transport_runtime').resolve()))
from bootstrap import bootstrap                       # noqa: E402
from scoring import score                             # noqa: E402

METRIC = 'tau2.v0.1.0.telecom.ALL.binary'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--case-sha256', required=True)
    args = parser.parse_args()
    output = Path('verifier')
    output.mkdir(exist_ok=True)
    reward_file = output / 'reward.txt'
    reward_file.unlink(missing_ok=True)
    evidence = {'measured': False, 'task_id': args.task_id, 'metric': METRIC}
    code = 3
    try:
        raw = Path('tau2_private.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.case_sha256:
            raise ValueError('private case hash mismatch')
        private = json.loads(raw)
        sealed = json.loads(Path('trusted_episode/episode.json').read_text())
        if sealed.get('schema') != 'harnessgrad.interaction-record.v1':
            raise ValueError('trusted episode schema mismatch')
        payload = sealed['payload']
        if (sealed['binding']['task_id'] != args.task_id
                or payload.get('task_id') != args.task_id
                or payload.get('case_sha256') != args.case_sha256
                or str(payload.get('seed')) != str(sealed['binding'].get('seed'))):
            raise ValueError('trusted episode identity mismatch')
        config = {'task_id': args.task_id, 'domain': payload['domain'],
                  'revision': payload['revision'], 'case_sha256': args.case_sha256,
                  'private': private}
        bootstrap(Path('tau2_bundle'), manifest=Path('tau2_manifest.json'))
        result = score(config, payload)
        evidence.update(measured=True, official=result, revision=config['revision'],
                        scorer_input={'task_id': args.task_id, 'sha256': args.case_sha256,
                                      'verified': True},
                        completion=result.get('termination_substitution'))
        reward_file.write_text(str(result['reward']) + '\n')
        code = 0
    except Exception as exc:                          # noqa: BLE001 - the reason is the evidence
        evidence.update(measured=False, error=type(exc).__name__, detail=str(exc)[:1000])
        print(f'{type(exc).__name__}: {str(exc)[:1000]}', file=sys.stderr)
    (output / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
