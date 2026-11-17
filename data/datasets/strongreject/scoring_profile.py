"""Scoring route and resolved judge identity, without loading task data."""
import hashlib
import importlib.util
from pathlib import Path

from data.datasets import strongreject
from harnessgrad.config import current


def profile():
    spec = importlib.util.spec_from_file_location('_hg_strongreject_measurement',
                                                   Path(__file__).with_name('measurement_profile.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scorer = module.profile()['scorer']
    selected = strongreject.judge_profile()
    if selected['metric'] != scorer['details']['protocol']:
        raise ValueError('StrongREJECT judge changed during identity capture')
    endpoint = current().judge.base_url
    endpoint = endpoint.rstrip('/') + '/chat/completions' if endpoint else None
    return {'metric': selected['metric'], 'judge': selected['judge'],
            'endpoint_sha256': hashlib.sha256(endpoint.encode()).hexdigest() if endpoint else None,
            'scorer_sha256': scorer['sha256']}
