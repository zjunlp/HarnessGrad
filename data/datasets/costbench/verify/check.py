#!/usr/bin/env python3
"""Score only a sealed platform-owned episode with pinned private inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from bootstrap import bootstrap
from scoring import METRIC, score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--case-sha256', required=True)
    args = parser.parse_args()
    output = Path('verifier')
    output.mkdir(exist_ok=True)
    reward = output / 'reward.txt'
    reward.unlink(missing_ok=True)
    evidence = {'measured': False, 'task_id': args.task_id, 'metric': METRIC}
    code = 3
    try:
        raw = Path('costbench_private.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.case_sha256:
            raise ValueError('private CostBench case digest mismatch')
        config = json.loads(raw)
        sealed = json.loads(Path('trusted_episode/episode.json').read_text())
        binding = sealed['binding']
        payload = sealed['payload']
        if (sealed['schema'] != 'harnessgrad.interaction-record.v1'
                or binding['task_id'] != args.task_id or config['task_id'] != args.task_id
                or payload['case_sha256'] != args.case_sha256
                or str(payload['seed']) != str(binding['seed'])):
            raise ValueError('trusted CostBench episode identity mismatch')
        bootstrap('costbench_bundle', 'costbench_runtime', split=config['split'])
        result = score(config, payload)
        evidence.update(measured=True, official=result, revision=config['revision'], binding=binding,
                        scorer_input={'task_id': args.task_id, 'sha256': args.case_sha256, 'verified': True})
        reward.write_text(str(result['reward']) + '\n')
        code = 0
    except Exception as exc:
        evidence.update(error=type(exc).__name__, detail=str(exc)[:1000])
        print(f'{type(exc).__name__}: {str(exc)[:1000]}', file=sys.stderr)
    (output / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
