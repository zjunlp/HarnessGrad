"""ChessBench scoring protocol and scorer implementation identity.

Data source and loader provenance are recorded separately by the measurement
profile. Historical combined scoring profiles remain valid v1 records, but do
not compare equal to this narrower profile.
"""
import importlib.util
from pathlib import Path


def profile():
    path = Path(__file__).with_name('measurement_profile.py')
    spec = importlib.util.spec_from_file_location('_hg_chess_measurement_profile', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scorer = module.profile()['scorer']
    return {'metric': scorer['details']['protocol'],
            'upstream_diagnostic': 'ChessBench upstream puzzle replay (unchecked push)',
            'scorer_sha256': scorer['sha256']}
