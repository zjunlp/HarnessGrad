"""Code provenance for the ChessBench loader and its pinned native scorer."""
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCES = (
    'data/datasets/chessbench_puzzles.py',
    'data/scaffolds/qa/base.py',
    'data/scaffolds/qa/read.py',
    'data/scaffolds/qa/verify/check.py',
    'data/scaffolds/provider_worker.py',
    'harnessgrad/dataset_provider.py',
    'eval/run/chess_stepwise.py',
    'eval/run/chess_stepwise_worker.py',
    'data/runtimes/chessbench_puzzles-native.json',
)


def profile():
    return {'metric': 'ChessBench strict legal puzzle replay v1',
            'upstream_diagnostic': 'ChessBench upstream puzzle replay (unchecked push)',
            'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in SOURCES}}
