"""Static LightRSI preflight: no task, model or method subprocess.

methods/lightrsi/run.py:178 requires --base; its saturation branch may skip the
wrapped method only after inspecting the incumbent. These tests check that the
entrypoint requirement and potential inner requirements are reported beforehand.
"""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from harnessgrad import readiness

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / 'methods/lightrsi/run.py'


def args(*tail, rounds=2):
    return SimpleNamespace(rounds=rounds, side='train', sampling='all', trials=1,
                           harness_dir=str(ROOT / 'base_harness/loop'),
                           method_entrypoint=[sys.executable, str(WRAPPER), *tail],
                           improver='none', improver_model=None)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(readiness, 'current', lambda: SimpleNamespace(
        agent=SimpleNamespace(backend='mock'),
        method=SimpleNamespace(base_url='', model='', api_key='')))
    monkeypatch.setattr(readiness, '_editor_plan', lambda *a: {
        'kind':'openai-compatible', 'base_url':'', 'model':'', 'key':''})
    monkeypatch.delenv('HG_POLICY_CONFIG', raising=False)


@pytest.mark.parametrize('tail', [(), ('--base',), ('--base=',), ('--base', '../relative.py'),
                                  ('--base', 'not-present'), ('--base', 'lightrsi')])
def test_invalid_base_refused(tail):
    rows = readiness.configuration_checks(args(*tail))
    assert any(row.name == 'method' and not row.ok for row in rows)


def test_noop_base_does_not_require_editor():
    rows = readiness.configuration_checks(args('--base', 'noop'))
    assert not any(row.name == 'editor' for row in rows)
    assert any(row.name == 'method' and row.ok and 'saturated' in row.detail for row in rows)


def test_declared_editor_requirement_is_checked_before_run():
    rows = readiness.configuration_checks(args('--base=ahe'))
    assert any(row.name == 'editor' and not row.ok for row in rows)


def test_inner_trials_and_instruction_requirements_are_checked():
    rows = readiness.configuration_checks(args('--base', 'modular_rsi'))
    assert any(row.name == 'method' and not row.ok and 'ModularRSI' in row.detail for row in rows)
    rows = readiness.configuration_checks(args('--base', 'ace'))
    assert any(row.name == 'method' and not row.ok and 'instruction' in row.detail for row in rows)


def test_custom_absolute_method_remains_eligible_without_declared_editor(tmp_path):
    script = tmp_path / 'custom.py'; script.write_text('# synthetic\n')
    rows = readiness.configuration_checks(args('--base', str(script)))
    assert not any(row.name == 'editor' for row in rows)
    assert any(row.name == 'method' and row.ok for row in rows)


def test_no_improvement_rounds_do_not_require_base():
    assert readiness.configuration_checks(args(rounds=0)) == []


def test_early_method_check_rejects_missing_base():
    rows = readiness.method_checks(args())
    assert any(row.name == 'method' and not row.ok and '--base' in row.detail for row in rows)


def test_early_method_check_noop_does_not_require_editor():
    rows = readiness.method_checks(args('--base', 'noop'))
    assert rows and all(row.ok for row in rows)
    assert not any(row.name == 'editor' for row in rows)
