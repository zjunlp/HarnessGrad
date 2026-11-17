"""ChessBench managed-provider declaration and measurement identities."""
import importlib.util
from pathlib import Path
import shutil

from harnessgrad.datasets import runtime as dataset_runtime
from harnessgrad.datasets import runtime_identity as dataset_runtime_identity
from harnessgrad.provenance import scoring_identity
from harnessgrad.provenance import measurement_components

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'data/datasets/chessbench_puzzles'


def test_provider_uses_existing_chess_distribution_pin():
    declaration = dataset_runtime.descriptor('chessbench_puzzles')
    assert declaration['provider'] == 'qa'
    assert declaration['python'] == '3.12'
    assert declaration['pins'] == {'chess': '1.11.2'}


def test_source_mutation_changes_scoring_identity_and_refuses_reuse(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('chess_profile_for_test', ASSETS / 'measurement_profile.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative in (set(module.LOADER.values()) | set(module.SCORER.values()) |
                     {'data/datasets/chessbench_puzzles/scoring_profile.py',
                      'data/datasets/chessbench_puzzles/measurement_profile.py'}):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    before = scoring_identity.resolve('chessbench_puzzles')
    provider = tmp_path / 'harnessgrad/datasets/provider.py'
    provider.write_bytes(provider.read_bytes() + b'\n# authored loader change\n')
    assert scoring_identity.resolve('chessbench_puzzles') == before
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


def test_loader_profile_covers_provider_implementation_not_only_a_compatibility_shim():
    """The explicitly hashed provider path must own the implementation actually used."""
    import inspect
    from harnessgrad.datasets.provider import DatasetProvider

    spec = importlib.util.spec_from_file_location('chess_profile_source_contract', ASSETS / 'measurement_profile.py')
    profile = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profile)
    implementation = Path(inspect.getsourcefile(DatasetProvider)).resolve()
    assert implementation.is_relative_to(ROOT)
    assert implementation.relative_to(ROOT).as_posix() in profile.LOADER.values()
    assert implementation.relative_to(ROOT).as_posix() not in profile.SCORER.values()


def test_old_scoring_identity_is_retained_but_not_reused_after_provider_move():
    """A source-path migration must not silently relabel historical measurements."""
    import json

    old = json.loads((ROOT / 'tests/fixtures/chessbench_provider_identity/before.json').read_text())
    current = scoring_identity.resolve('chessbench_puzzles')
    assert scoring_identity.validate(old) == old
    assert old['profile']['metric'] == current['profile']['metric']
    assert 'harnessgrad/dataset_provider.py' in old['profile']['source_sha256']
    assert current['profile']['scorer_sha256'] == measurement_components.resolve('chessbench_puzzles')['scorer']['sha256']
    assert old['sha256'] != current['sha256']
    assert scoring_identity.problems(old, current)


def test_legacy_provider_import_fails_instead_of_loading_another_checkout():
    import importlib
    import pytest

    with pytest.raises(ImportError, match=r'harnessgrad\.datasets\.provider'):
        importlib.import_module('harnessgrad.dataset_provider')
