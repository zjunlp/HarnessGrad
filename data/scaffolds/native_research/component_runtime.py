"""Native component dependencies belong to published execution artifacts.

Resolution and admission are read-only. A missing artifact is an explicit
provisioning error, never permission to import packages from the platform.
"""
import os
from harnessgrad.config.areas import ConfigMissing
from harnessgrad.dataset_runtime import bind, resolve_execution


def binding(name, role):
    if name == 'widesearch' and (os.environ.get('HG_JUDGE_WIDESEARCH_PYTHON') or os.environ.get('HG_JUDGE_WIDESEARCH_RUNTIME_PATHS')):
        raise ConfigMissing('widesearch: legacy interpreter overrides are unsupported; prepare the managed execution runtime')
    return bind(resolve_execution(name), role)


def apply(name, spec):
    selected = binding(name, 'verifier')
    spec['argv'][0] = selected['python']
    spec['python_runtime'] = selected
    spec['runtime_paths'] = selected['runtime_paths']
    return spec
