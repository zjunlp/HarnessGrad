"""Synthetic entrypoint inspection; no methods, models, Docker or services run."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from harnessgrad import methods, readiness

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / 'methods/lightrsi/run.py'


def arguments(*tail):
    return SimpleNamespace(side='train', rounds=1, sampling='all', improver='none',
        improver_model=None, method_entrypoint=[sys.executable, str(WRAPPER), *tail])


@pytest.mark.parametrize('equals', [True, False])
def test_absolute_base_script_preflight_and_sandbox_mount(tmp_path, equals, monkeypatch):
    base = tmp_path / 'custom method' / 'script.py'
    base.parent.mkdir(); base.write_text('# synthetic\n')
    tail = (f'--base={base}',) if equals else ('--base', str(base))
    args = arguments(*tail)
    original = list(args.method_entrypoint)
    assert methods._check_method_entrypoint(original) is None
    assert all(c.ok for c in readiness.method_checks(args))
    assert all(c.ok for c in readiness.configuration_checks(args))
    work = tmp_path / 'work'; work.mkdir()
    scratch = tmp_path / 'scratch'; scratch.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    plan = methods._method_sandbox_plan(work, original, scratch)
    assert base.parent in plan['visible_paths']
    assert args.method_entrypoint == original


@pytest.mark.parametrize('tail', [('--base=',), ('--base=relative.py',),
    ('--base=/synthetic/missing.py',), (f'--base={WRAPPER}',)])
def test_invalid_or_recursive_option_equals_still_refuses(tail):
    args = arguments(*tail)
    checks = readiness.method_checks(args)
    assert any(not c.ok for c in checks)


def test_unrelated_equals_option_does_not_mount_arbitrary_file(tmp_path):
    secret = tmp_path / 'private.txt'; secret.write_text('synthetic')
    work = tmp_path / 'work'; work.mkdir()
    scratch = tmp_path / 'scratch'; scratch.mkdir()
    plan = methods._method_sandbox_plan(work, [sys.executable, str(WRAPPER),
                                              f'--unrelated={secret}'], scratch)
    assert secret.parent not in plan['visible_paths']


def test_absolute_extensionless_base_option_is_mounted(tmp_path):
    base = tmp_path / 'custom' / 'launcher'
    base.parent.mkdir(); base.write_text('# synthetic\n')
    work = tmp_path / 'work'; work.mkdir()
    scratch = tmp_path / 'scratch'; scratch.mkdir()
    args = arguments(f'--base={base}')
    assert all(c.ok for c in readiness.method_checks(args))
    assert base.parent in methods._method_sandbox_plan(work, args.method_entrypoint, scratch)['visible_paths']
