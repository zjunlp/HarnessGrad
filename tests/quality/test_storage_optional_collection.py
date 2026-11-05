"""Subprocess controls for explicitly optional external-storage import scopes."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _probe(tmp_path, source, *, configured=False, collect_only=False, opt_in=True):
    # Exercise the actual helper, with settings independent of operator .env files.
    plugin = (ROOT / 'tests/quality/conftest.py').read_text()
    bootstrap = (f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
                 "import harnessgrad.paths as paths\n"
                 f"paths._settings = lambda: {({'HG_DATA_ROOT': str(tmp_path / 'storage')} if configured else {})!r}\n")
    (tmp_path / 'conftest.py').write_text(plugin + '\n' + bootstrap)
    if opt_in:
        source = ('from conftest import optional_storage_import\n'
                  'with optional_storage_import():\n' + textwrap.indent(source, '    '))
    (tmp_path / 'test_probe.py').write_text(source)
    env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTEST_ADDOPTS', None)
    command = [sys.executable, '-m', 'pytest', '-q', '-rs', '-p', 'no:cacheprovider']
    if collect_only:
        command.append('--collect-only')
    return subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('collect_only', [False, True])
def test_missing_storage_in_explicit_scope_skips(tmp_path, collect_only):
    result = _probe(tmp_path, "from harnessgrad.paths import dataset_root\n"
                    "dataset_root('synthetic').is_dir()\n", collect_only=collect_only)
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, output
    assert 'SKIPPED [1]' in output
    assert 'Dataset storage is not configured. Set HG_DATA_ROOT' in output
    assert 'HG_DATA_SYNTHETIC' in output
    assert 'ERROR collecting' not in output


@pytest.mark.parametrize('source, diagnostic', [
    ("raise ImportError('ordinary import defect')\n", 'ordinary import defect'),
    ("raise RuntimeError('ordinary runtime defect')\n", 'ordinary runtime defect'),
    ("raise ValueError('Dataset storage is not configured')\n", 'ValueError'),
    ('def broken(:\n', 'SyntaxError'),
    ('from harnessgrad.paths import DataRootNotConfigured\n'
     "class Defect(DataRootNotConfigured): pass\nraise Defect('subclass defect')\n", 'subclass defect'),
    ('from harnessgrad.paths import DataRootNotConfigured\n'
     "try:\n    raise DataRootNotConfigured('inner config signal')\n"
     "except DataRootNotConfigured as exc:\n    raise ImportError('wrapped defect') from exc\n", 'wrapped defect'),
    ('from harnessgrad.paths import dataset_root\n'
     'def test_probe(value): pass\n'
     "def pytest_generate_tests(metafunc):\n    dataset_root('synthetic').is_dir()\n", 'DataRootNotConfigured'),
])
def test_import_and_collection_defects_remain_errors(tmp_path, source, diagnostic):
    result = _probe(tmp_path, source)
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.INTERRUPTED, output
    assert 'ERROR collecting test_probe.py' in output
    assert diagnostic in output
    assert 'skipped' not in output


def test_body_configuration_failure_is_not_a_skip(tmp_path):
    result = _probe(tmp_path, 'from harnessgrad.paths import dataset_root\n'
                    "def test_probe():\n    dataset_root('synthetic').is_dir()\n")
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, output
    assert '1 failed' in output and 'DataRootNotConfigured' in output
    assert 'skipped' not in output


def test_fixture_configuration_failure_remains_an_error(tmp_path):
    result = _probe(tmp_path, 'import pytest\nfrom harnessgrad.paths import dataset_root\n'
                    "@pytest.fixture\ndef storage():\n    dataset_root('synthetic').is_dir()\n"
                    'def test_probe(storage): pass\n')
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, output
    assert '1 error' in output and 'DataRootNotConfigured' in output
    assert 'skipped' not in output


def test_configured_storage_missing_file_is_not_swallowed(tmp_path):
    result = _probe(tmp_path, 'from harnessgrad.paths import dataset_root\n'
                    "(dataset_root('synthetic') / 'absent.json').read_text()\n", configured=True)
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.INTERRUPTED, output
    assert 'FileNotFoundError' in output and 'absent.json' in output
    assert 'skipped' not in output


def test_unopted_module_configuration_failure_remains_error(tmp_path):
    result = _probe(tmp_path, 'from harnessgrad.paths import dataset_root\n'
                    "dataset_root('synthetic').is_dir()\n", opt_in=False)
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.INTERRUPTED, output
    assert 'DataRootNotConfigured' in output and 'skipped' not in output


def test_pytest_plugin_configuration_failure_remains_error(tmp_path):
    (tmp_path / 'storage_plugin.py').write_text(
        'from harnessgrad.paths import DataRootNotConfigured\n'
        "raise DataRootNotConfigured('plugin defect')\n")
    result = _probe(tmp_path, "pytest_plugins = ['storage_plugin']\ndef test_probe(): pass\n")
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.INTERRUPTED, output
    assert 'plugin defect' in output and 'skipped' not in output


def test_mixed_module_source_test_survives_optional_fixture_skip(tmp_path):
    result = _probe(tmp_path, 'import pytest\nfrom conftest import optional_storage_import\n'
                    'from harnessgrad.paths import dataset_root\n'
                    '@pytest.fixture\ndef storage():\n    with optional_storage_import():\n'
                    "        dataset_root('synthetic').is_dir()\n"
                    'def test_external(storage): pass\ndef test_source(): assert 2 + 2 == 4\n',
                    opt_in=False)
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.OK, output
    assert '1 passed, 1 skipped' in output
