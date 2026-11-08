"""Exact Harbor dependency artifacts; no path/version-only adoption or installation."""

PROFILE = 'native_harbor'
REPREPARE = ('Prepare explicitly with `python -m data.scaffolds.native_harbor_runtime.prepare_python prepare`, '
             'then rerun native_harbor_runtime/prepare_runtime.py for the selected tasks')


def resolve(*, root=None):
    from harnessgrad import dataset_runtime
    return dataset_runtime.bind(dataset_runtime.resolve_execution(PROFILE, root=root), 'verifier')


def admit(entry, *, python=None):
    from harnessgrad import dataset_runtime
    binding = entry.get('harbor_runtime') if isinstance(entry, dict) else None
    if not isinstance(binding, dict):
        raise ValueError('legacy Harbor path-only runtime is unbound; ' + REPREPARE)
    if binding.get('dataset') != PROFILE:
        raise ValueError('Harbor requires its canonical native_harbor execution component; ' + REPREPARE)
    selected = entry.get('harbor_python')
    if not isinstance(selected, str) or selected != binding.get('python'):
        raise ValueError('Harbor executable differs from selected dependency artifact; ' + REPREPARE)
    if python is not None and python != selected:
        raise ValueError('Harbor child is not the exact selected interpreter')
    return dataset_runtime.admit_binding(binding, 'verifier', python=selected)
