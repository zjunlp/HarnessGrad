"""Compatibility entry points for the managed WideSearch scorer artifact.

Legacy interpreter environment overrides cannot establish artifact custody and
are rejected instead of silently selecting unverified host dependencies.
"""
import os
from harnessgrad.config.areas import ConfigMissing
from .component_runtime import binding, apply as apply_component


def settings(env=None):
    env = os.environ if env is None else env
    if env.get('HG_JUDGE_WIDESEARCH_PYTHON') or env.get('HG_JUDGE_WIDESEARCH_RUNTIME_PATHS'):
        raise ConfigMissing('widesearch: legacy interpreter overrides are unsupported; prepare the managed execution runtime')
    selected = binding('widesearch', 'verifier')
    return selected['python'], selected['runtime_paths']


def readiness():
    settings()
    return binding('widesearch', 'verifier')


def apply(spec):
    settings()
    return apply_component('widesearch', spec)
