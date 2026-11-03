#!/usr/bin/env python3
"""Trusted after-actor check for FlashRSI QA adapters; gold is not actor-visible."""
from __future__ import annotations
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flashrsi_qa.scoring import score


def main():
    gold_path, expected_digest, task_id, name = sys.argv[1:]
    raw = Path(gold_path).read_bytes()
    evidence = {'task_id': task_id, 'measured': False,
                'scorer_input': {'task_id': task_id, 'path': gold_path,
                                 'sha256': hashlib.sha256(raw).hexdigest(),
                                 'expected_sha256': expected_digest,
                                 'verified': hashlib.sha256(raw).hexdigest() == expected_digest}}
    out = Path('verifier'); out.mkdir(exist_ok=True)
    try:
        if evidence['scorer_input']['sha256'] != expected_digest:
            raise ValueError('trusted gold digest mismatch')
        answer_path = Path('answer.txt')
        prediction = answer_path.read_text(encoding='utf-8', errors='replace') if answer_path.is_file() else ''
        result = score(name, prediction, json.loads(raw))
        reward = result['reward']
        if type(reward) not in (int, float) or not math.isfinite(reward) or not 0 <= reward <= 1:
            raise ValueError('invalid score range')
        evidence.update(measured=True, reward=reward, correct=result['correct'],
                        official=result['official'], why=result['why'])
        (out/'reward.txt').write_text(str(reward), encoding='utf-8')
    except Exception as exc:
        # Do not echo reference text, source paths, or scorer exception payloads.
        evidence['error'] = f'{type(exc).__name__}: trusted scorer unavailable or invalid input'
        (out/'evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
        return 2
    (out/'evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
    return 0


if __name__ == '__main__': raise SystemExit(main())
