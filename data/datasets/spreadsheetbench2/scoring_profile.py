"""Scoring protocol identity; workbook origin and loader are separate."""
import importlib.util
from pathlib import Path


def profile():
    spec = importlib.util.spec_from_file_location('_hg_spreadsheetbench2_measurement',
                                                   Path(__file__).with_name('measurement_profile.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scorer = module.profile()['scorer']
    return {'metric': 'spreadsheetbench2.official.accuracy',
            'per_task_unit': 'one official v2 workbook',
            'platform_aggregation': 'unweighted mean of measured workbook accuracies',
            'formula_runtime': 'task-bound verified host installation; private bubblewrap UNO; not hermetic',
            'scorer_sha256': scorer['sha256']}
