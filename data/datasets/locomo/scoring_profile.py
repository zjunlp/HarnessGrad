"""Deterministic scorer and aggregation identity; no conversations or answers."""
import importlib.util
from pathlib import Path


def profile():
    spec = importlib.util.spec_from_file_location('_hg_locomo_measurement_profile',
                                                   Path(__file__).with_name('measurement_profile.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scorer = module.profile()['scorer']
    return {'metric': scorer['details']['protocol'],
            'per_task_unit': 'one conversation',
            'platform_aggregation': 'unweighted mean of conversation rewards',
            'report_aggregation': 'question-weighted mean over the selected complete set',
            'scorer_sha256': scorer['sha256']}
