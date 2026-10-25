#!/usr/bin/env python3
"""Trusted episode scoring: private inputs are projected only after actor exit."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path('tau3_transport_runtime').resolve()))
from bootstrap import bootstrap
from scoring import score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--case-sha256', required=True)
    args = parser.parse_args()
    output = Path('verifier')
    output.mkdir(exist_ok=True)
    reward_file = output / 'reward.txt'
    reward_file.unlink(missing_ok=True)
    evidence = {'measured': False, 'task_id': args.task_id, 'metric': 'tau3.v1.0.1.ALL.binary'}
    code = 3
    try:
        raw = Path('tau3_private.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.case_sha256:
            raise ValueError('private case hash mismatch')
        config = json.loads(raw)
        sealed = json.loads(Path('trusted_episode/episode.json').read_text())
        if (sealed['schema'] != 'harnessgrad.interaction-record.v1'
                or sealed['binding']['task_id'] != args.task_id or config['task_id'] != args.task_id
                or sealed['payload']['case_sha256'] != args.case_sha256
                or str(sealed['payload']['seed']) != sealed['binding']['seed']):
            raise ValueError('trusted episode identity mismatch')
        bootstrap('tau3_bundle', 'tau3_runtime')
        from model import install
        from tool_transport import call
        install('judge', call)
        result = score(config, sealed['payload'])
        evidence.update(measured=True, official=result, revision=config['revision'],
                        scorer_input={'task_id': args.task_id, 'sha256': args.case_sha256, 'verified': True})
        if sealed['payload']['termination_reason'] is None:
            evidence['completion'] = {
                'reason': 'incomplete_conversation_without_stop_signal',
                'recorded_termination_reason': None,
                'scorer_termination_reason': 'agent_error',
            }
        reward_file.write_text(str(result['reward']) + '\n')
        code = 0
    except Exception as exc:
        evidence.update(measured=False, error=type(exc).__name__, detail=str(exc)[:1000])
        print(f"{type(exc).__name__}: {str(exc)[:1000]}", file=sys.stderr)
    (output / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False))
    return code

if __name__ == '__main__':
    raise SystemExit(main())
