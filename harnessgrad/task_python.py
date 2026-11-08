"""Dataset-owned Python for task shell tools, separate from the harness SDK."""
from __future__ import annotations

import os
from pathlib import Path

from harnessgrad import dataset_runtime


def validate(env, *, sandbox=True, setup=None, worker=None):
    binding = (env or {}).get('task_python')
    if 'task_python' not in (env or {}):
        return None
    if (not sandbox or (env or {}).get('kind', 'files') != 'files'
            or (env or {}).get('backend') is not None or worker is not None
            or (setup or {}).get('workers') is not None):
        raise ValueError('task_python requires sandboxed files without workers or observed backends')
    return dataset_runtime.validate_binding(binding, 'actor')


def admit(env, *, sandbox=True, setup=None, worker=None):
    binding = validate(env, sandbox=sandbox, setup=setup, worker=worker)
    return None if binding is None else dataset_runtime.admit_binding(binding, 'actor')


def environment(environment, artifact):
    if artifact is None:
        return environment
    value = {key: val for key, val in environment.items()
             if not key.startswith('PYTHON') and key not in ('VIRTUAL_ENV', 'CONDA_PREFIX')}
    # Harness startup uses its absolute SDK interpreter. Only task shell lookup
    # changes here; do not inject task packages into the harness's sys.path.
    value.update(PATH=str(Path(artifact['python']).parent) + os.pathsep + value.get('PATH', os.defpath),
                 VIRTUAL_ENV=artifact['path'], PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    return value


def receipt(artifact):
    return {'dataset': artifact['dataset'], 'runtime_id': artifact['runtime_id'],
            'role': 'actor', 'readonly': True}
