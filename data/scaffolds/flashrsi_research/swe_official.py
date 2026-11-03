#!/usr/bin/env python3
"""Run the unchanged FlashRSI SWE helper with authenticated source custody.

An installed VCS distribution uses its original revision check. An explicit
HG_SWE_SOURCE is an alternative for offline archive installs: every upstream
package file must match the platform's pinned inventory, including the complete
file set. This changes acquisition authentication, never scoring or parsing.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SOURCE_COPY = None


def activate():
    global SOURCE_COPY
    source = os.environ.get('HG_SWE_SOURCE')
    if source is None:
        return None
    root = Path(source)
    if not root.is_absolute() or root.is_symlink() or any(p.is_symlink() for p in root.parents):
        raise ValueError('official source must be an absolute unlinked directory')
    inventory = json.loads((HERE / 'SWE_EVALUATOR_SOURCE.json').read_text())
    actual = {}
    snapshot = {}
    for path in sorted((root / 'swebench').rglob('*')):
        if '__pycache__' in path.parts:
            continue
        if path.is_symlink():
            raise ValueError('linked official scorer dependency')
        if path.is_file():
            relative = str(path.relative_to(root))
            raw = path.read_bytes()
            actual[relative] = hashlib.sha256(raw).hexdigest()
            snapshot[relative] = raw
    if not actual or actual != inventory['files']:
        raise ValueError('official scorer source inventory mismatch')
    # Execute the same bytes we hashed, with no inherited __pycache__. Python's
    # dont_write_bytecode alone would still accept a cached mutable .pyc file.
    SOURCE_COPY = tempfile.TemporaryDirectory(prefix='hg-swe-official-source-')
    for relative, raw in snapshot.items():
        target = Path(SOURCE_COPY.name) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    sys.path.insert(0, SOURCE_COPY.name)
    sys.dont_write_bytecode = True
    return inventory


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('check', 'prepare', 'grade'))
    p.add_argument('root', nargs='?', type=Path)
    args = p.parse_args()
    try:
        inventory = activate()
        helper = HERE / 'vendor/flashrsi/integrations/_swe_grade.py'
        spec = importlib.util.spec_from_file_location('hg_swe_official_helper', helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if inventory is not None:
            if inventory['revision'] != module.REVISION:
                raise ValueError('official evaluator revision mismatch')
            module.check = lambda: None  # Exact source inventory was checked above.
        if args.action == 'check':
            module.check()
            from swebench.harness.grading import get_eval_report
            from swebench.harness.utils import make_test_spec
            from swebench.harness.run_evaluation import GIT_APPLY_CMDS
            return 0
        return module.main(args.action, args.root)
    except Exception as exc:
        print('official SWE evaluator unavailable (' + type(exc).__name__ + ')', file=sys.stderr)
        return 3
    finally:
        if SOURCE_COPY is not None:
            SOURCE_COPY.cleanup()


if __name__ == '__main__':
    raise SystemExit(main())
