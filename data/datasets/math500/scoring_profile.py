"""MATH-500 command scorer identity; loader and data declarations are separate."""
import importlib.util
from pathlib import Path


def profile():
    spec = importlib.util.spec_from_file_location('_hg_math500_measurement_profile',
                                                   Path(__file__).with_name('measurement_profile.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scorer = module.profile()['scorer']
    return {'metric': scorer['details']['protocol'], 'scorer_sha256': scorer['sha256']}
