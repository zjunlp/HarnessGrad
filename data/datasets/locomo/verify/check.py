#!/usr/bin/env python3
"""Trusted rematerialized LoCoMo verifier. Score answers.json with Harbor rules."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import sys

# The runner replaces the two exact files from trusted VERIFY.inputs after the
# actor exits. Never put their directory on sys.path: other files there can be
# actor-controlled. -I leaves package imports inside the selected environment.


def _score_answers():
    path = Path(__file__).resolve().with_name('scoring.py')
    # A source file passed to run_path does not add its parent to sys.path or
    # load an actor's leftover __pycache__ entry for this module.
    return runpy.run_path(str(path))['score_answers']


def _runtime_observation():
    import nltk
    from nltk.stem import porter
    return {'python': sys.executable, 'prefix': sys.prefix,
            'version': list(sys.version_info[:3]), 'isolated': bool(sys.flags.isolated),
            'no_bytecode': sys.dont_write_bytecode,
            'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY),
            'site_paths': [path for path in sys.path if path.endswith('site-packages')],
            'package_versions': {'nltk': importlib.metadata.version('nltk')},
            'package_origins': {'nltk': nltk.__file__, 'nltk.stem.porter': porter.__file__}}


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
        score_answers = _score_answers()
        evidence['python_runtime'] = _runtime_observation()
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
