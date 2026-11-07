"""Host selection and direct diagnostics for the shared MATH scoring artifact.

The sandboxed scorer never imports this platform-side helper. Direct score and
control callers use the same exact dependency binding, without a sandbox claim.
"""
from functools import lru_cache
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

from harnessgrad import dataset_runtime


@lru_cache(maxsize=1)
def runtime():
    return dataset_runtime.resolve_execution('math')


def binding():
    return dataset_runtime.bind(runtime(), 'verifier')


def score(name, prediction, gold):
    if name not in ('math', 'math500'):
        raise ValueError('not a MATH scoring target')
    selected = binding()
    dataset_runtime.admit_binding(selected, 'verifier', python=selected['python'])
    argv = [selected['python'], '-I', '-B', '-X', 'utf8',
            str(Path(__file__).with_name('math_worker.py')), 'score']
    with tempfile.TemporaryDirectory(prefix='hg-math-score-') as directory:
        env = {'PATH': os.defpath, 'HOME': directory, 'TMPDIR': directory,
               'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': directory,
               'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                env=env, cwd=directory, start_new_session=True)
        try:
            stdout, _ = proc.communicate(json.dumps({'dataset': name, 'prediction': prediction,
                                                     'gold': gold}), timeout=30)
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise
    if proc.returncode:
        raise ValueError('Math-Verify trusted scorer unavailable or invalid input')
    return json.loads(stdout)
