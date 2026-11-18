"""Bind Jericho's scoring profile to its separately captured scorer code."""
import importlib.util
from pathlib import Path


def profile():
    spec = importlib.util.spec_from_file_location('_hg_jericho_measurement',
                                                  Path(__file__).with_name('measurement_profile.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scorer = module.profile()['scorer']
    return {'metric': scorer['details']['protocol'], 'scorer_sha256': scorer['sha256']}
