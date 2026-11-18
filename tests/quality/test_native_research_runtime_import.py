"""Authored source identity controls for native research runtime imports."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from data.scaffolds.native_research import special_runtime
from harnessgrad.provenance import verification_context

ROOT = Path(__file__).resolve().parents[2]
BEFORE = json.loads((ROOT / 'tests/fixtures/native_research_runtime_import/before.json').read_text())


def clean_source(tmp_path):
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--', 'data/scaffolds/native_research'], cwd=ROOT)
    for raw in paths.split(b'\0'):
        if not raw:
            continue
        relative = Path(raw.decode('utf-8'))
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path / 'data/scaffolds/native_research'


def test_verifier_context_refuses_previous_native_source_tree(tmp_path, monkeypatch):
    clean_source(tmp_path)
    monkeypatch.setattr(verification_context, 'ROOT', tmp_path)
    old = BEFORE['verification_context']
    assert verification_context.validate(old) == old
    current = verification_context.capture(
        [{'task_id': 'authored/native', 'goal': 'Authored task'}], {},
        verifiers={'authored/native': {'kind': 'command', 'inputs': [
            {'path': 'native_research', 'from': 'data/scaffolds/native_research', 'immutable': True}]}})
    assert verification_context.problems(old, current) == ['verification_context changed']


def test_special_worker_fingerprint_changes_only_bootstrap_source(tmp_path, monkeypatch):
    root = clean_source(tmp_path)
    monkeypatch.setattr(special_runtime, '__file__', str(root / 'special_runtime.py'))
    identity = special_runtime.fingerprint({'runtime_id': 'a' * 64}, {'bootstrap': root / 'special_runtime.py'})
    modules = dict(identity['modules'])
    bootstrap = modules.pop('bootstrap')
    others = hashlib.sha256(json.dumps(modules, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert bootstrap != BEFORE['bootstrap_sha256']
    assert others == BEFORE['other_modules_sha256']
