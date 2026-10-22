#!/usr/bin/env python3
"""Test this worker's patch atop the cache worker in a disposable sparse file overlay.

No merge, branch mutation, dataset workbook access, or full repository copy.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
BASE = 'e0c1cd3254e0168c9e9e1a7dabee750772ddc868'
CACHE = 'e5aa274acc5c947d1d31a5d2737a4f831530c411'
CHECK = 'data/datasets/spreadsheetbench/verify/check.py'
CLASSIFICATION = 'tests/quality/test_spreadsheetbench_classification_w_spread2.py'
CONTROL = 'tests/quality/test_spreadsheetbench_control_submit.py'


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def main():
    patch = git('diff', BASE, '--', CHECK, CLASSIFICATION)
    for mode in ('before', 'after'):
        # Engine control bind-mounts its pytest task over /tmp, so source must live
        # outside /tmp; only the handful of source files goes on persistent HDD.
        with tempfile.TemporaryDirectory(prefix=f'cache-{mode}-',
                dir='/mnt/20t/xubuqiang/hg-work/spreadvacuous') as directory, \
                tempfile.TemporaryDirectory(prefix='spreadvacuous-integration-pytest-') as pytestdir:
            overlay = Path(directory)
            for name in (CHECK, CLASSIFICATION, CONTROL, 'data/datasets/spreadsheetbench.py',
                         'data/datasets/spreadsheetbench/control.py'):
                target = overlay / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(git('show', f'{CACHE}:{name}'))
            if mode == 'after':
                subprocess.run(['git', 'apply', '-'], input=patch, cwd=overlay, check=True)
            digest = hashlib.sha256((overlay / CHECK).read_bytes()).hexdigest()
            print(json.dumps(dict(mode=mode, cache_commit=CACHE, patch_base=BASE,
                                  checker_sha256=digest)), flush=True)
            # Eval/helper imports resolve only to the assigned worktree, never main.
            env = dict(os.environ, PYTHONPATH=str(ROOT), HG_SPREADCACHE_ENGINE_CONTROL='1',
                       PYTHONDONTWRITEBYTECODE='1', HG_SPREADSHEETBENCH_DATA=str(overlay / 'empty-data'))
            proc = subprocess.run([sys.executable, '-m', 'pytest', '-q', CLASSIFICATION, CONTROL,
                                   f'--basetemp={pytestdir}'],
                                  cwd=overlay, env=env, capture_output=True, text=True, timeout=300)
            print(proc.stdout, end='', flush=True)
            print(proc.stderr, end='', flush=True)
            print(f'{mode}_exit={proc.returncode}', flush=True)
            if proc.returncode:
                return proc.returncode
    print('sparse_overlays_and_pytest_workbooks_removed=True')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
