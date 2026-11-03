"""Explicit trusted WideSearch scorer interpreter, using existing runtime paths.

The HG_JUDGE prefix keeps this configuration out of the actor environment.
No packages are installed, no socket is opened, and no actor chooses a runtime.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from harnessgrad.config.areas import ConfigMissing

REQUIRED = ('pandas', 'numpy', 'dateparser', 'loguru', 'datasets')
PLATFORM = Path(__file__).resolve().parents[3]


def settings(env=None):
    env = os.environ if env is None else env
    python = env.get('HG_JUDGE_WIDESEARCH_PYTHON')
    declared = env.get('HG_JUDGE_WIDESEARCH_RUNTIME_PATHS')
    if not python and not declared:
        return None
    if not python or not declared:
        raise ConfigMissing('widesearch: set HG_JUDGE_WIDESEARCH_PYTHON and HG_JUDGE_WIDESEARCH_RUNTIME_PATHS together')
    paths = json.loads(declared)
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
        raise ValueError('WideSearch runtime paths must be a nonempty JSON list of absolute directories')
    roots = []
    for value in paths:
        path = Path(value)
        if (not path.is_absolute() or path == Path('/') or '..' in path.parts
                or not path.is_dir() or any(p.is_symlink() for p in (path, *path.parents))
                or path == PLATFORM or PLATFORM in path.parents
                or path == Path('/tmp') or Path('/tmp') in path.parents):
            raise ValueError('WideSearch scorer runtime must be an owned absolute non-symlink directory outside hidden platform/tmp')
        if path not in roots:
            roots.append(path)
    executable = Path(python)
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ConfigMissing('widesearch: selected scorer Python is not an absolute executable')
    # Match existing VERIFY.runtime_paths custody: both link and resolved binary
    # must belong to the declared roots (or the ordinary system /usr runtime).
    for path in (executable, executable.resolve()):
        if not any(path == root or root in path.parents for root in (*roots, Path('/usr'), Path('/bin'))):
            raise ValueError('WideSearch scorer executable/target is outside declared runtimes')
    return str(executable), [str(path) for path in roots]


def readiness():
    selected = settings()
    if selected is None:
        missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
        if missing:
            raise ConfigMissing('widesearch: native dependencies missing: ' + ', '.join(missing)
                + '; provision the host interpreter or select HG_JUDGE_WIDESEARCH_PYTHON/RUNTIME_PATHS')
        return {'source': 'platform-interpreter', 'python': sys.executable}
    python, paths = selected
    script = 'import json,sys,importlib.metadata;' + ';'.join('import ' + name for name in REQUIRED)
    script += ';print(json.dumps({"python":sys.executable,"dependencies":{n:importlib.metadata.version(n) for n in ' + repr(REQUIRED) + '}}))'
    env = {key: value for key, value in os.environ.items()
           if key in ('PATH', 'LANG', 'SYSTEMROOT', 'WINDIR')}
    try:
        probe = subprocess.run([python, '-I', '-B', '-c', script], env=env,
            capture_output=True, text=True, errors='replace', timeout=30)
        identity = json.loads(probe.stdout) if probe.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        identity = None
    if not isinstance(identity, dict):
        raise ConfigMissing('widesearch: selected trusted runtime cannot import all native scorer dependencies')
    return {'source': 'explicit-trusted-interpreter', **identity, 'runtime_paths': paths}


def apply(spec):
    selected = settings()
    if selected is not None:
        python, paths = selected
        spec['argv'][0] = python
        spec['runtime_paths'] = paths
    return spec
