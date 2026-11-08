"""Public scorer identity from current source bytes, independent of Git history."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = ('data/datasets/spreadsheetbench.py',
           'data/datasets/spreadsheetbench/verify/check.py',
           'data/scaffolds/scorer_input.py',
           'data/scaffolds/spreadsheet_runtime/native.py',
           'data/scaffolds/spreadsheet_runtime/actor.py',
           'data/datasets/spreadsheetbench/execution-runtime.json',
           'data/datasets/spreadsheetbench/requirements.lock')


def profile():
    return {'metric': 'spreadsheetbench.verified_400.named_cells.v1',
            'per_task_unit': 'one released workbook variant',
            'platform_aggregation': 'unweighted mean of measured workbook rewards',
            'formula_runtime': 'task-bound verified host installation; private bubblewrap conversion; not hermetic',
            'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in SOURCES}}
