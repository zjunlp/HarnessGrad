"""Actual public profiles through synthetic planning, records and H0 checks."""
from types import SimpleNamespace

import pytest

from harnessgrad.experiment.records import _curve_point
from harnessgrad.orchestration import selected_dataset
from harnessgrad.provenance import measurement_components as identities, scoring_identity, verification_context
from test_agentdojo_provenance import copy_sources as copy_agentdojo_sources
from test_alfworld_provenance import copy_sources as copy_alfworld_sources
from test_jericho_provenance import copy_sources as copy_jericho_sources
from test_scienceworld_provenance import copy_sources as copy_scienceworld_sources
from tools import prepare_h0


@pytest.mark.parametrize('dataset,copy_sources,loader_path', [
    ('agentdojo', copy_agentdojo_sources, 'data/datasets/agentdojo/prepare.py'),
    ('alfworld', copy_alfworld_sources, 'harnessgrad/paths.py'),
    ('jericho', copy_jericho_sources, 'harnessgrad/paths.py'),
    ('scienceworld', copy_scienceworld_sources, 'harnessgrad/paths.py'),
])
def test_profile_flows_through_planning_and_h0_reuse(tmp_path, monkeypatch, dataset, copy_sources, loader_path):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(selected_dataset.dataset_runtime_identity, 'capture', lambda *a, **k: None)
    task_id = dataset + '/synthetic'
    selection = SimpleNamespace(ids=[task_id])
    scoring, runtime, components = selected_dataset._selected_identities(
        SimpleNamespace(dataset=dataset), selection, {}, {}, {})
    assert runtime is None
    assert components == identities.resolve(dataset)
    assert scoring == scoring_identity.resolve(dataset)

    point = _curve_point(run_id='synthetic', round_index=0, identity={},
        sampling={'dataset': dataset, 'task_ids': selection.ids}, scores=[0.5],
        task_ids=selection.ids, cost={}, cumulative={}, scoring_identity=scoring,
        measurement_components=components)
    assert point['measurement_components'] == components and point['scoring_identity'] == scoring

    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setattr(prepare_h0.dataset_runtime_identity, 'resolve_h0', lambda *a, **k: None)
    monkeypatch.setattr(prepare_h0, '_verification_preflight',
                        lambda *a, **k: verification_context.create({task_id: 'a' * 64}))
    monkeypatch.setattr(prepare_h0, 'harness_context', lambda *a, **k: {'harness_sha': 'b' * 40})
    args = SimpleNamespace(dataset=dataset, side='train', study_ids=None,
                           model='mock', endpoint='synthetic', trials=1)
    h0 = prepare_h0.configuration(args)
    assert h0['measurement_components'] == components and h0['scoring_identity'] == scoring
    copies = {'measurement_components': components, 'base': {'measurement_components': components}}
    assert prepare_h0.component_copy_problems(copies, {'measurement_components': components}, [point], h0) == []
    assert any('measurement_components missing' in msg
               for msg in prepare_h0.component_copy_problems({'base': {}}, {}, [{}], h0))

    loader = tmp_path / loader_path
    loader.write_bytes(loader.read_bytes() + b'\n# authored loader change\n')
    changed = prepare_h0.configuration(args)
    assert changed['measurement_components']['data_source'] == components['data_source']
    assert changed['measurement_components']['scorer'] == components['scorer']
    assert changed['measurement_components']['loader'] != components['loader']
    assert any('loader identity changed' in msg for msg in prepare_h0.component_copy_problems(
        copies, {'measurement_components': components}, [point], changed))
