"""CostBench source-only identity checks; no prepared data or native runtime."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

from harnessgrad.provenance import verification_context

ROOT = Path(__file__).resolve().parents[2]
BEFORE = json.loads((ROOT / 'tests/fixtures/costbench_runtime_import/before.json').read_text())


def clean_source(tmp_path):
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--', 'data/datasets/costbench',
        'data/datasets/costbench_dynamic', 'data/datasets/costbench.py',
        'data/datasets/costbench_dynamic.py'], cwd=ROOT)
    for raw in paths.split(b'\0'):
        if not raw:
            continue
        relative = Path(raw.decode('utf-8'))
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path / 'data/datasets/costbench'


def test_preparation_and_verifier_source_commitments_change(tmp_path, monkeypatch):
    root = clean_source(tmp_path)
    spec = importlib.util.spec_from_file_location('_authored_costbench_controller', root / 'prepare_controller.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.source_hash() != BEFORE['preparation_source_sha256']
    for name, old in BEFORE['files'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() != old
    for name, old in BEFORE['unchanged'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == old
    monkeypatch.setattr(verification_context, 'ROOT', tmp_path)
    previous = BEFORE['verification_context']
    assert verification_context.validate(previous) == previous
    current = verification_context.capture(
        [{'task_id': 'authored/cost', 'goal': 'Authored task'}], {},
        verifiers={'authored/cost': {'kind': 'command', 'inputs': [
            {'path': 'costbench_code', 'from': 'data/datasets/costbench', 'immutable': True}]}})
    assert verification_context.problems(previous, current) == ['verification_context changed']
