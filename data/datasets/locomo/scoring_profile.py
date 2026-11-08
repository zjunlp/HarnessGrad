"""Public code and aggregation identity; never loads conversations or answers."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = ('data/datasets/locomo.py', 'data/datasets/locomo/lib/inputs.py',
           'data/datasets/locomo/lib/scoring.py', 'data/datasets/locomo/verify/check.py',
           'data/datasets/locomo/report.py', 'data/datasets/locomo/specs/source.json')


def profile():
    return {'metric': 'locomo.harbor.question_micro_mean.v1',
            'per_task_unit': 'one conversation',
            'platform_aggregation': 'unweighted mean of conversation rewards',
            'report_aggregation': 'question-weighted mean over the selected complete set',
            'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in SOURCES}}
