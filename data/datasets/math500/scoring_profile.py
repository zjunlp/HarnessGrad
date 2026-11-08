"""Source identity for the pinned MATH-500 equivalence scorer and loader."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = ('data/datasets/math500.py', 'data/scaffolds/native_qa/__init__.py',
           'data/scaffolds/native_qa/check.py', 'data/scaffolds/native_qa/scoring.py',
           'data/scaffolds/native_qa/math_grade.py')


def profile():
    return {'metric': 'math-verify-0.9.0-last-boxed',
            'source_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                              for path in SOURCES}}
