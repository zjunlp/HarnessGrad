"""Current-source scorer identity, independent of checkout Git history."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = ('data/datasets/spreadsheetbench2.py',
           'data/datasets/spreadsheetbench2/verify/check.py',
           'data/datasets/spreadsheetbench2/verify/evaluation.py',
           'data/datasets/spreadsheetbench2/verify/open_spreadsheet.py',
           'data/datasets/spreadsheetbench2/PROVENANCE.json',
           'data/scaffolds/scorer_input.py',
           'data/scaffolds/spreadsheet_runtime/native.py',
           'data/scaffolds/spreadsheet_runtime/actor.py',
           'data/datasets/spreadsheetbench2/execution-runtime.json',
           'data/datasets/spreadsheetbench2/requirements.lock')


def profile():
    return {'metric': 'spreadsheetbench2.official.accuracy',
            'per_task_unit': 'one official v2 workbook',
            'platform_aggregation': 'unweighted mean of measured workbook accuracies',
            'formula_runtime': 'task-bound verified host installation; private bubblewrap UNO; not hermetic',
            'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in SOURCES}}
