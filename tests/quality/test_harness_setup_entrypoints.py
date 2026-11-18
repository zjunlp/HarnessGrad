"""Setup-tool moves preserve invocation and module identity without installing anything."""
import importlib
import os
import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('configure_harness', 'configure_codex_harness', 'configure_dsh_harness', 'configure_opencode_harness',
         'configure_openclaw_harness', 'configure_bun_harness', 'configure_responses_overlay')


@pytest.mark.parametrize('name', NAMES)
def test_old_and_new_imports_share_globals_and_root(name, monkeypatch):
    old = importlib.import_module('tools.' + name)
    new = importlib.import_module('tools.harness_setup.' + name)
    assert old is new and Path(new.__file__).resolve().parents[2] == ROOT
    assert getattr(new, "ROOT", ROOT) == ROOT
    monkeypatch.setattr(old, '_compat_probe', object(), raising=False)
    assert old._compat_probe is new._compat_probe


@pytest.mark.parametrize('name', NAMES)
def test_direct_help_is_identical_from_outside_checkout(name, tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    outputs = []
    for script in (ROOT / 'tools' / (name + '.py'), ROOT / 'tools/harness_setup' / (name + '.py')):
        proc = subprocess.run([sys.executable, str(script), '--help'], cwd=tmp_path,
                              env=env, capture_output=True, text=True, timeout=20)
        outputs.append((proc.returncode, proc.stdout, proc.stderr))
    assert outputs[0] == outputs[1] and outputs[0][0] == 0


@pytest.mark.parametrize('name', NAMES)
def test_runpy_and_registered_path_loader_expose_functions(name):
    path = ROOT / 'tools' / (name + '.py')
    values = runpy.run_path(str(path))
    assert callable(values['main'])
    spec = importlib.util.spec_from_file_location('_compat_custom_' + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert callable(module.main) and module.main.__globals__ is module.__dict__
    finally:
        del sys.modules[spec.name]
