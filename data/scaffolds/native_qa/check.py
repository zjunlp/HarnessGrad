#!/usr/bin/env python3
"""Trusted after-actor check for upstream QA adapters; gold is not actor-visible."""
from __future__ import annotations
import sys

if __name__ == '__main__' and not sys.flags.isolated:
    raise SystemExit('Native QA checks require Python isolated mode (-I)')

import hashlib
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_file_location
import json
import math
from pathlib import Path
from types import ModuleType

# Only declared, restored sources may supply this scorer's package. In
# particular, a candidate-created __init__.py or .pyc must never be executed.
class _ScorerSourceLoader(SourceFileLoader):
    def get_code(self, fullname):
        return self.source_to_code(self.get_data(self.path), self.path)


class _ScorerModules:
    modules = frozenset({'scoring', 'metrics', 'bbeh_official', 'office_reward', 'math_grade'})

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith('native_qa.'):
            return None
        name = fullname.removeprefix('native_qa.')
        if name not in self.modules:
            raise ModuleNotFoundError(f'undeclared native QA scorer module: {fullname}')
        source = Path(__file__).resolve().with_name(name + '.py')
        return spec_from_file_location(fullname, source,
            loader=_ScorerSourceLoader(fullname, str(source)))


def load_scorer():
    package = ModuleType('native_qa')
    package.__path__ = []
    sys.modules['native_qa'] = package
    sys.meta_path.insert(0, _ScorerModules())
    from native_qa.scoring import score
    return score


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
        result = load_scorer()(name, prediction, json.loads(raw),
                               _math_runtime=name in ('math', 'math500'))
        reward = result['reward']
        if type(reward) not in (int, float) or not math.isfinite(reward) or not 0 <= reward <= 1:
            raise ValueError('invalid score range')
        evidence.update(measured=True, reward=reward, correct=result['correct'],
                        official=result['official'], why=result['why'])
        if name in ('math', 'math500'):
            evidence['python_runtime'] = result['python_runtime']
        (out/'reward.txt').write_text(str(reward), encoding='utf-8')
    except Exception as exc:
        # Do not echo reference text, source paths, or scorer exception payloads.
        evidence['error'] = f'{type(exc).__name__}: trusted scorer unavailable or invalid input'
        (out/'evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
        return 2
    (out/'evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
    return 0


if __name__ == '__main__': raise SystemExit(main())
