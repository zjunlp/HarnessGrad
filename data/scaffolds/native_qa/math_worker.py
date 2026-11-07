"""Isolated Math-Verify worker; the pinned grading source remains unchanged."""
import contextlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import sys


def observation():
    import math_verify
    import latex2sympy2_extended
    import sympy
    modules = {'math_verify': math_verify, 'latex2sympy2_extended': latex2sympy2_extended,
               'sympy': sympy}
    return {'python': sys.executable, 'prefix': sys.prefix,
            'version': list(sys.version_info[:3]), 'isolated': bool(sys.flags.isolated),
            'no_bytecode': sys.dont_write_bytecode,
            'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY),
            'site_paths': [path for path in sys.path if path.endswith('site-packages')],
            'package_versions': {name: importlib.metadata.version(name) for name in modules},
            'package_origins': {name: module.__file__ for name, module in modules.items()}}


def main():
    if not sys.flags.isolated:
        raise ValueError('Math worker requires isolated Python startup')
    request = json.load(sys.stdin)
    if sys.argv[1:] == ['grade']:
        if importlib.metadata.version('math-verify') != '0.9.0':
            raise ValueError('math-verify==0.9.0 is required')
        grade = runpy.run_path(str(Path(__file__).with_name('math_grade.py')))['grade']
        with contextlib.redirect_stdout(sys.stderr):
            correct = grade(request)
        result = {'correct': correct, 'python_runtime': observation()}
    elif sys.argv[1:] == ['score'] and request['dataset'] in ('math', 'math500'):
        # Reuse the exact-source loader; neither a local package nor cached
        # bytecode in the scorer's directory is an import authority.
        load = runpy.run_path(str(Path(__file__).with_name('check.py')))['load_scorer']
        result = load()(request['dataset'], request['prediction'], request['gold'],
                        _math_runtime=True)
    else:
        raise ValueError('invalid Math worker request')
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit(1) from None
