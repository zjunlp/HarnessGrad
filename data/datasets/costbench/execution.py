"""One explicitly prepared CostBench runtime shared by both trusted roles."""
from functools import lru_cache
import importlib.util
import os
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def runtime():
    """Freeze the selected artifact; launchers still admit its exact binding."""
    from harnessgrad.dataset_runtime import resolve_execution
    return resolve_execution('costbench')


def binding(role):
    from harnessgrad.dataset_runtime import bind
    return bind(runtime(), role)


def read_paths(assets, transport, folder, root):
    """Selected service inputs, without exposing the whole dataset or checkout."""
    assets, transport, folder, root = map(Path, (assets, transport, folder, root))
    source = assets.parents[2]
    paths = [assets, transport, folder, root / 'bundle', root / 'search-spaces']
    for relative in ('eval/__init__.py', 'eval/service/__init__.py'):
        path = source / relative
        if path.is_file():
            paths.append(path)
    return list(dict.fromkeys(str(path.resolve()) for path in paths))


def provision_status(root, prepared, roster, detail):
    """Report data and execution dependencies without installing or launching."""
    from harnessgrad.dataset_runtime import check_execution
    from harnessgrad.paths import DataRootNotConfigured
    try:
        root = str(root)
        rows = roster()
        data_present = all((prepared / row['path'] / name).is_file()
                           for row in rows.values() for name in ('public.json', 'private.json'))
    except DataRootNotConfigured as exc:
        return {'present': False, 'status': 'config_missing', 'scope': 'costbench-data',
                'detail': str(exc)}
    except (OSError, ValueError, KeyError) as exc:
        status = 'data_missing' if isinstance(exc, FileNotFoundError) else 'invalid'
        return {'root': str(root), 'present': False, 'status': status,
                'scope': 'costbench-data', 'detail': str(exc)}
    execution = check_execution('costbench')
    present = data_present and execution['present']
    status = execution['status'] if not execution['present'] else ('ready' if present else 'data_missing')
    failure = execution.get('detail', 'execution runtime unavailable') if not execution['present'] else 'data files missing'
    return {'root': root, 'present': present, 'status': status,
            'scope': 'costbench-data-and-execution', 'tasks': len(rows),
            'detail': detail if present else detail + '; ' + failure,
            'execution_runtime': execution}


def observation():
    """Facts from the running service/scorer, independent of platform Python."""
    origins = {}
    for name in ('yaml', 'numpy', 'pydantic', 'pydantic_core'):
        try:
            spec = importlib.util.find_spec(name)
            origins[name] = spec.origin if spec is not None else None
        except (ImportError, ValueError):
            origins[name] = getattr(sys.modules.get(name), '__file__', None)
    return {'python': sys.executable, 'prefix': sys.prefix,
            'version': list(sys.version_info[:3]), 'isolated': bool(sys.flags.isolated),
            'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY),
            'site_paths': [path for path in sys.path if path.endswith('site-packages')],
            'package_origins': origins}
