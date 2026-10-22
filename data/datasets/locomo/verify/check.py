#!/usr/bin/env python3
"""Trusted rematerialized LoCoMo verifier. Score answers.json with Harbor rules."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

# The runner replaces this directory from trusted VERIFY.inputs after the actor exits.
# -I disables cwd/user/PYTHONPATH imports; only this explicit scorer directory is added.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--gold', type=Path, required=True)
    parser.add_argument('--gold-sha256', required=True)
    parser.add_argument('--answers', type=Path, default=Path('answers.json'))
    parser.add_argument('--reward-file', type=Path, default=Path('verifier/reward.txt'))
    parser.add_argument('--evidence-file', type=Path, default=Path('verifier/evidence.json'))
    args = parser.parse_args(argv)
    args.reward_file.parent.mkdir(parents=True, exist_ok=True)
    args.reward_file.unlink(missing_ok=True)
    evidence = {'schema': 'hg-locomo-evidence-v1', 'task_id': args.task_id, 'measured': False}
    code = 3
    try:
        raw = args.gold.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != args.gold_sha256:
            raise ValueError('gold_digest_mismatch')
        gold = json.loads(raw)
        if (gold['schema'] != 'hg-locomo-gold-v1' or gold['task_id'] != args.task_id
                or gold['metric'] != 'locomo.harbor.question_micro_mean.v1'):
            raise ValueError('gold_identity_mismatch')
        from scoring import score_answers
        prediction = args.answers.read_text(encoding='utf-8') if args.answers.exists() else None
        result = score_answers(prediction, gold['questions'])
        evidence.update(measured=True, metric=gold['metric'], source_sha256=gold['source_sha256'],
                        conversation_sha256=gold['conversation_sha256'], **result)
        evidence['scorer_input'] = {'task_id': args.task_id, 'path': str(args.gold), 'sha256': digest,
                                    'expected_sha256': args.gold_sha256, 'verified': True}
        # Score details contain predictions and numeric results only, never gold.
        args.reward_file.write_text(str(result['reward']) + '\n')
        code = 0
    except Exception as error:
        evidence.update(measured=False, error=type(error).__name__)
    args.evidence_file.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_file.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'measured': evidence['measured'], 'task_id': args.task_id}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
