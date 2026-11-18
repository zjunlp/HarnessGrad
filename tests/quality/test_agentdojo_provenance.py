"""Public source declarations and implementation roles; no task or SDK needed."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from harnessgrad.provenance import measurement_components as identities, scoring_identity
from provenance_test_helpers import copy_profile_sources


def copy_sources(root):
    copy_profile_sources(root, 'agentdojo', extra_attributes=('SOURCE_MANIFEST', 'PREPARED_MANIFEST'))


def test_declared_source_loader_and_scorer_are_separate(monkeypatch, tmp_path):
    monkeypatch.setenv('HG_DATA_AGENTDOJO', str(tmp_path / 'no-dataset'))
    value = identities.resolve('agentdojo')
    scoring = scoring_identity.resolve('agentdojo')
    identities.require_scoring_alignment(scoring, value)
    assert value['data_source']['details']['dataset'] == 'agentdojo'
    assert value['data_source']['details']['origin'] == 'AgentDojo source archive and declared prepared case inventory'
    assert scoring['profile']['metric'] == 'AgentDojo utility under fixed injection'
    assert not (tmp_path / 'no-dataset').exists()
    assert identities.bundle_problems(None, value)
    assert scoring_identity.problems(None, scoring)


def test_loader_scorer_and_shared_code_are_distinct(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('agentdojo')
    score = scoring_identity.resolve('agentdojo')
    prepare = tmp_path / 'data/datasets/agentdojo/prepare.py'
    prepare.write_bytes(prepare.read_bytes() + b'\n# authored loader change\n')
    loader = identities.resolve('agentdojo')
    assert first['loader'] != loader['loader'] and first['scorer'] == loader['scorer']
    assert first['data_source'] == loader['data_source'] and scoring_identity.resolve('agentdojo') == score
    check = tmp_path / 'data/datasets/agentdojo/verify/check.py'
    check.write_bytes(check.read_bytes() + b'\n# authored scorer change\n')
    scorer = identities.resolve('agentdojo')
    assert scorer['loader'] == loader['loader'] and scorer['scorer'] != loader['scorer']
    assert scorer['data_source'] == loader['data_source']
    assert scoring_identity.problems(score, scoring_identity.resolve('agentdojo'))
    bridge = tmp_path / 'data/datasets/agentdojo/bridge.py'
    bridge.write_bytes(bridge.read_bytes() + b'\n# authored shared-code change\n')
    shared = identities.resolve('agentdojo')
    assert shared['loader'] != scorer['loader'] and shared['scorer'] != scorer['scorer']
    assert shared['data_source'] == scorer['data_source']


@pytest.mark.parametrize('name', ['source-manifest.json', 'prepared-manifest.json'])
def test_modified_declaration_fails_closed(tmp_path, monkeypatch, name):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    path = tmp_path / 'data/datasets/agentdojo' / name
    path.write_text(json.dumps(json.loads(path.read_text())) + '\n')
    with pytest.raises(ValueError, match='pinned manifest'):
        identities.resolve('agentdojo')


def test_inconsistent_attack_declaration_is_rejected(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    bridge = tmp_path / 'data/datasets/agentdojo/bridge.py'
    bridge.write_text(bridge.read_text().replace('ATTACK_NAME = "ignore_previous"',
                                                  'ATTACK_NAME = "other"'))
    with pytest.raises(ValueError, match='inconsistent'):
        identities.resolve('agentdojo')


def test_bootstrap_pin_drift_fails_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    bootstrap = tmp_path / 'data/datasets/agentdojo/bootstrap.py'
    text = bootstrap.read_text().replace(
        'd9fc402c7cd510ef3c766aa7c28fb70e18352b24b6d941a443bcbe60b1654826', 'f' * 64)
    bootstrap.write_text(text)
    with pytest.raises(ValueError, match='pins disagree'):
        identities.resolve('agentdojo')


def test_shared_source_change_during_capture_fails_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    capture = identities.capture_implementation

    def mutate(kind, root, components, **kwargs):
        if kind == 'scorer':
            path = root / 'data/datasets/agentdojo/bootstrap.py'
            path.write_bytes(path.read_bytes() + b'\n# authored concurrent change\n')
        return capture(kind, root, components, **kwargs)

    monkeypatch.setattr(identities, 'capture_implementation', mutate)
    with pytest.raises(ValueError, match='changed during identity capture'):
        identities.resolve('agentdojo')


def test_loaded_adapter_declaration_mismatch_fails_closed(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setitem(sys.modules, 'data.datasets.agentdojo', SimpleNamespace(
        COMMIT='0' * 40, VERSION='v1.2.2'))
    with pytest.raises(ValueError, match='loaded AgentDojo declaration differs'):
        identities.resolve('agentdojo')


def test_real_loaded_adapter_matches_profile_without_data_or_sdk(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = """
import importlib.abc, sys
class BlockAgentDojo(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'agentdojo' or fullname.startswith('agentdojo.'):
            raise AssertionError('unexpected AgentDojo SDK import')
sys.meta_path.insert(0, BlockAgentDojo())
import data.datasets.agentdojo as adapter
from harnessgrad.provenance import measurement_components, scoring_identity
assert adapter.VERSION == 'v1.2.2'
value = measurement_components.resolve('agentdojo')
measurement_components.require_scoring_alignment(scoring_identity.resolve('agentdojo'), value)
assert 'agentdojo' not in sys.modules
"""
    env = {**os.environ, 'PYTHONPATH': str(root), 'PYTHONDONTWRITEBYTECODE': '1',
           'HG_DATA_AGENTDOJO': str(tmp_path / 'absent'), 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'absent').exists()
