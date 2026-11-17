"""Retired imports explain migration while retained commands keep their CLI behavior."""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
MOVED_CLI_MODULES = {
    "eval.gaia_preflight": "eval.policy.gaia_preflight",
    "eval.harness_runtime": "eval.containers.harness_runtime",
    "eval.scorer_bundle": "eval.admission.scorer_bundle",
    "eval.separated_stdio": "eval.service.separated_stdio",
}


@pytest.mark.parametrize("old,new", MOVED_CLI_MODULES.items())
def test_retired_cli_module_import_explains_canonical_path(old, new):
    with pytest.raises(ImportError, match=re.escape(new)):
        importlib.import_module(old)
    assert importlib.import_module(new).__name__ == new


def test_repository_root_survives_the_move():
    from eval.run.integrity import PLATFORM_ROOT

    assert PLATFORM_ROOT == ROOT


LEGACY_ADAPTER = ROOT / "legacy/adapters/harnessgrad_domain/adapter.py"


def test_legacy_critic_recognizes_both_scorer_path_locations():
    """历史提交的适配器里那份 `critic_patterns`，必须同时认旧路径与新路径。

    那份适配器**不在本仓库里**：它是当年提交上来的候选 harness（`legacy/` 从未进过
    git，只存在于留有它的机器上）。所以文件不在时这条跳过，而不是让每个 checkout 都红
    ——它证明的是那份历史产物的属性，不是平台自己的属性。
    """
    if not LEGACY_ADAPTER.is_file():
        pytest.skip(f"historical adapter is not in this checkout: {LEGACY_ADAPTER}")
    # Inspect declarations without importing or executing the historical adapter.
    tree = ast.parse(LEGACY_ADAPTER.read_text())
    patterns = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "critic_patterns"
                            for target in node.targets))
    for name in ("runner", "metrics", "integrity"):
        old = f"eval/{name}.py"
        new = f"eval/run/{name}.py"
        old_reasons = [reason for pattern, reason in patterns if re.search(pattern, old)]
        new_reasons = [reason for pattern, reason in patterns if re.search(pattern, new)]
        assert old_reasons and old_reasons == new_reasons


@pytest.mark.parametrize("module", [
    "gaia_preflight", "harness_runtime", "scorer_bundle", "separated_stdio",
])
def test_legacy_module_cli_delegates(module):
    old = "eval." + module
    new = MOVED_CLI_MODULES[old]
    results = [subprocess.run(
        [sys.executable, "-m", name, "--help"], cwd=ROOT,
        capture_output=True, text=True, timeout=30,
    ) for name in (old, new)]
    assert results[0].returncode == results[1].returncode == 0
    assert results[0].stdout.replace(old, new) == results[1].stdout
    assert results[0].stderr == results[1].stderr == ""


def test_stdio_scorer_keeps_its_standalone_script_entrypoint(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "eval" / "separated_stdio.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize('name', ['console', 'progress', 'trace', 'tracing', 'phases'])
def test_retired_observation_import_explains_canonical_path(name):
    old = 'eval.' + name
    new = 'eval.observe.' + name
    with pytest.raises(ImportError, match=re.escape(new)):
        importlib.import_module(old)
    assert importlib.import_module(new).__name__ == new


def test_observation_refusal_works_in_fresh_python_process():
    code = """import importlib
for name in ('console', 'progress', 'trace', 'tracing', 'phases'):
 try:
  importlib.import_module('eval.' + name)
 except ImportError as exc:
  assert 'eval.observe.' + name in str(exc)
 else:
  raise AssertionError(name)
 assert importlib.import_module('eval.observe.' + name).__name__ == 'eval.observe.' + name
"""
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('name', ['diagnostic_policy', 'runtime_policy', 'appworld_reporting',
                                  'gaia_snapshot_attestation'])
def test_retired_policy_import_explains_canonical_path(name):
    with pytest.raises(ImportError, match=re.escape('eval.policy.' + name)):
        importlib.import_module('eval.' + name)
    assert importlib.import_module('eval.policy.' + name).__name__ == 'eval.policy.' + name


@pytest.mark.parametrize('group,name', [
    ('admission', 'pilot_admission'), ('admission', 'artifact_admission'),
    ('admission', 'runner_admission'), ('admission', 'scorer_admission'),
    ('admission', 'scorer_backend'), ('admission', 'lifecycle_receipt'),
    ('service', 'services'), ('service', 'modelgate'),
    ('service', 'unix_model_relay'), ('service', 'stepwise_transport'),
])
def test_retired_admission_and_service_imports_explain_canonical_path(group, name):
    target = 'eval.' + group + '.' + name
    with pytest.raises(ImportError, match=re.escape(target)):
        importlib.import_module('eval.' + name)
    assert importlib.import_module(target).__name__ == target


@pytest.mark.parametrize('name', ['candidate', 'metrics', 'integrity', 'task_contract', 'runner'])
def test_retired_run_import_explains_canonical_path(name):
    target = 'eval.run.' + name
    with pytest.raises(ImportError, match=re.escape(target)):
        importlib.import_module('eval.' + name)
    assert importlib.import_module(target).__name__ == target


@pytest.mark.parametrize("name", ["container", "container_state", "sandbox"])
def test_retired_container_import_explains_canonical_path(name):
    target = "eval.containers." + name
    with pytest.raises(ImportError, match=re.escape(target)):
        importlib.import_module("eval." + name)
    assert importlib.import_module(target).__name__ == target


def test_retired_verifier_import_explains_canonical_path():
    with pytest.raises(ImportError, match=re.escape("eval.verification.verifier")):
        importlib.import_module("eval.verifier")
    assert importlib.import_module("eval.verification.verifier").__name__ == "eval.verification.verifier"


def test_scorer_bundle_cli_delegation_verifies_real_synthetic_files(tmp_path):
    import hashlib
    import json
    from eval.admission.scorer_bundle import ISOLATION_SCHEMA, seal_bundle

    root = tmp_path / 'bundle'
    root.mkdir()
    files = []
    for name, body, role, requires in (
        ('python', b'authored runtime\n', 'runtime', []),
        ('check.py', b'authored scorer\n', 'scorer', ['python']),
    ):
        (root / name).write_bytes(body)
        files.append({'path': name, 'sha256': hashlib.sha256(body).hexdigest(),
                      'bytes': len(body), 'role': role, 'requires': requires})
    raw, pin = seal_bundle({'schema': ISOLATION_SCHEMA, 'isolation_class': 'pristine-offline',
        'source_sha256': 'a' * 64, 'runtime_image_id': 'sha256:' + 'b' * 64,
        'network': 'none', 'scorer': {'entrypoint': 'check.py', 'files': files,
        'argv': ['/control-scorer/python', '/control-scorer/check.py']}})
    manifest = tmp_path / 'manifest.json'
    manifest.write_bytes(raw)
    args = ['--root', str(root), '--manifest', str(manifest), '--sha256', pin]

    def invoke(module):
        return subprocess.run([sys.executable, '-m', module, *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=30)

    old, new = (invoke(name) for name in ('eval.scorer_bundle', 'eval.admission.scorer_bundle'))
    assert old.returncode == new.returncode == 0
    assert json.loads(old.stdout) == json.loads(new.stdout)
    assert json.loads(old.stdout)['files'] == 2
    (root / 'check.py').write_bytes(b'tampered')
    old, new = (invoke(name) for name in ('eval.scorer_bundle', 'eval.admission.scorer_bundle'))
    assert old.returncode == new.returncode == 2
    assert json.loads(old.stdout) == json.loads(new.stdout) == {
        'valid': False, 'scope': 'local_declared_closure_integrity'}


def test_harness_runtime_cli_delegation_inspects_manifest(tmp_path):
    import json
    (tmp_path / 'harness.json').write_text(json.dumps({'name': 'synthetic'}))
    results = [subprocess.run([sys.executable, '-m', module, str(tmp_path)], cwd=ROOT,
                              capture_output=True, text=True, timeout=30)
               for module in ('eval.harness_runtime', 'eval.containers.harness_runtime')]
    assert results[0].returncode == results[1].returncode == 0
    assert results[0].stdout == results[1].stdout == 'install: (none declared)\n'
    assert results[0].stderr == results[1].stderr == ''


def test_stdio_command_delegation_preserves_invalid_input_refusal():
    """Run both entrypoints with an authored invalid input and captured verdict path."""
    import json
    source = r'''
import json, runpy, sys
from pathlib import Path
name = sys.argv[1]
sys.argv = [name, '--score-in-namespace']
read_bytes, write_text = Path.read_bytes, Path.write_text

def read(self):
    return b'{}' if str(self) == '/tests.json' else read_bytes(self)

def write(self, value, *args, **kwargs):
    if str(self) == '/verdict/result.json':
        print(value, end='')
        return len(value)
    return write_text(self, value, *args, **kwargs)

Path.read_bytes, Path.write_text = read, write
runpy.run_module(name, run_name='__main__')
'''
    results = [subprocess.run([sys.executable, '-B', '-c', source, module], cwd=ROOT,
                              capture_output=True, text=True, timeout=30)
               for module in ('eval.separated_stdio', 'eval.service.separated_stdio')]
    assert results[0].returncode == results[1].returncode == 0
    assert results[0].stderr == results[1].stderr == ''
    assert json.loads(results[0].stdout) == json.loads(results[1].stdout)
    assert json.loads(results[0].stdout)['measured'] is False
    assert json.loads(results[0].stdout)['error'] == 'verifier input or namespace failure'
