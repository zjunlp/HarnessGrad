"""ChessBench managed-provider declaration and measurement identities."""
import importlib.util
from pathlib import Path
import shutil

from harnessgrad import dataset_runtime, dataset_runtime_identity, scoring_identity

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'data/datasets/chessbench_puzzles'


def test_provider_uses_existing_chess_distribution_pin():
    declaration = dataset_runtime.descriptor('chessbench_puzzles')
    assert declaration['provider'] == 'qa'
    assert declaration['python'] == '3.12'
    assert declaration['pins'] == {'chess': '1.11.2'}


def test_source_mutation_changes_scoring_identity_and_refuses_reuse(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('chess_profile_for_test', ASSETS / 'scoring_profile.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative in (*module.SOURCES, 'data/datasets/chessbench_puzzles/scoring_profile.py'):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    before = scoring_identity.resolve('chessbench_puzzles')
    target = tmp_path / 'data/datasets/chessbench_puzzles.py'
    target.write_bytes(target.read_bytes() + b'\n# authored source change\n')
    after = scoring_identity.resolve('chessbench_puzzles')
    assert before['sha256'] != after['sha256']
    assert scoring_identity.problems(before, after)


def test_dependency_artifact_change_refuses_reuse():
    def provider(revision):
        root = '/synthetic/' + revision
        return dataset_runtime_identity.create(provider={'dataset': 'chessbench_puzzles',
            'runtime_id': revision * 64, 'manifest_path': root + '/artifact.json',
            'path': root + '/venv', 'python': root + '/venv/bin/python',
            'descriptor_sha256': 'd' * 64, 'requirements_sha256': 'e' * 64})
    assert dataset_runtime_identity.problems(provider('a'), provider('b'))
