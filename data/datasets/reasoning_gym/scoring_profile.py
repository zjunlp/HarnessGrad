"""Source identity for Reasoning Gym's library-owned scoring route."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = ('data/datasets/reasoning_gym.py', 'data/datasets/reasoning_gym/check.py',
           'data/datasets/reasoning_gym/prepare.py', 'data/scaffolds/qa/base.py',
           'data/scaffolds/qa/verify/check.py', 'data/scaffolds/qa/prediction.py')


def profile():
    return {'metric': 'reasoning-gym-0.1.25-score-answer',
            'source_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                              for path in SOURCES}}
