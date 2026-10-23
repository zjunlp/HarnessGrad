"""Pinned source, released split integrity, and executable gold audit contracts."""
import importlib.util
import json
from pathlib import Path
import os

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'data/datasets/costbench'
DATA = Path(os.environ.get('HG_COSTBENCH_DATA', ROOT.parent / 'datasets/costbench'))


def preparation():
    spec = importlib.util.spec_from_file_location('costbench_prepare', ASSETS / 'prepare.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_protocol_is_explicit():
    mod = preparation()
    assert mod.PROTOCOL == {'refinement_level':2,'ban_longest_tool':True,
        'min_atomic_cost':15,'max_atomic_cost':25,'noise_std':0.1,
        'max_tool_steps':20,'tool_creation_seed':42,'use_blocker':False}
    pins = json.loads((ASSETS / 'source-files.json').read_text())
    for relative in ('env/utils/eval.py','env/utils/solver.py','env/core/base_types.py',
                     'env/domains/travel/enums.py','env/config/travel_config.yaml'):
        assert len(pins[relative]) == 64


def test_bootstrap_rejects_modified_source(tmp_path):
    mod = preparation()
    bundle = tmp_path / 'bundle'
    pins = json.loads((ASSETS / 'source-files.json').read_text())
    first = next(iter(pins))
    target = bundle / first
    target.parent.mkdir(parents=True)
    target.write_text('modified source')
    with pytest.raises(ValueError, match='source digest mismatch'):
        mod.bootstrap(bundle)


def test_released_split_identity_and_raw_train_defect():
    source = DATA / ('CostBench-' + preparation().REVISION)
    if not source.exists():
        pytest.skip('run CostBench preparation to download the pinned source')
    test = json.loads((source / 'env/data/runtime/queries/queries.json').read_text())
    train = json.loads((source / 'env/data/runtime/train_queries/train/queries.json').read_text())
    assert len(test) == len({r['query_id'] for r in test}) == 381
    assert len(train) == len({r['query_id'] for r in train}) == 1912
    # IDs overlap across split, so the adapter must namespace them by split.
    assert {r['query_id'] for r in train} & {r['query_id'] for r in test}
    import re
    assert {len(re.search(r'\d+',r['groundtruth'])[0]) for r in train} == {7}
    assert {len(re.search(r'\d+',r['groundtruth'])[0]) for r in test} == {5}


def test_whole_selected_suite_reaches_official_path_oracle():
    audit_path = DATA / 'gold-audit.json'
    if not audit_path.exists():
        pytest.skip('run the whole-suite CostBench preparation audit')
    audit = json.loads(audit_path.read_text())
    assert audit['revision'] == preparation().REVISION
    assert audit['passed'] == audit['total'] == sum(audit['counts'].values())
    assert audit['counts']['test'] == 381
    assert all(r['passed'] and r['normalized_edit_distance'] == 0 for r in audit['results'])
    assert all(abs(r['agent_cost']-r['gt_cost']) < 1e-8 for r in audit['results'])
    test_results = [r for r in audit['results'] if '/test/' in r['task_id']]
    assert len(test_results) == 381 and all(r['tcr'] for r in test_results)
    train_results = [r for r in audit['results'] if '/train/' in r['task_id']]
    assert all(r['train_compat_tcr'] for r in train_results)


def test_shards_preserve_every_lookup_and_reject_tampering(tmp_path):
    bundle = DATA / 'bundle'
    if not bundle.exists():
        pytest.skip('requires pinned source preparation')
    mod = preparation()
    preference = str(('city','architectural_marvel','historic_and_traditional','major_metropolis'))
    meal = str(('restaurant','unique_themed_environment','culinary_quality_priority','popular_local_mainstay'))
    full = {'locations':{preference:'<LocationPreference00001>'},
            'location_information':{'<LocationPreference00001>':{'dining':{meal:'<DiningPreference00001>'}}}}
    database = tmp_path/'search_space.json'
    database.write_text(json.dumps(full))
    index = mod.shard_database(database)
    mod.bootstrap(bundle,DATA/'runtime',search_space=index,config_path=tmp_path/'runtime.yaml')
    from env.core import base_types
    lookup = base_types.get_id_from_search_space
    original = lookup._costbench_original
    cases = [
        ('location','city','major_metropolis','historic_and_traditional','architectural_marvel',None),
        ('dining','restaurant','popular_local_mainstay','culinary_quality_priority','unique_themed_environment','<LocationPreference00001>'),
        ('dining','restaurant','popular_local_mainstay','culinary_quality_priority','unique_themed_environment','<LocationPreference99999>'),
        ('location','invalid','major_metropolis','historic_and_traditional','architectural_marvel',None),
    ]
    for args in cases:
        assert lookup(str(index),*args) == original(str(database),*args)
    (index.parent/'locations.json').write_text('{}')
    # Infrastructure corruption must escape runtime's ordinary tool ValueErrors.
    with pytest.raises(RuntimeError,match='shard digest mismatch'):
        lookup(str(index),*cases[0])


def test_training_compatibility_is_split_specific(tmp_path):
    bundle = DATA/'bundle'
    if not bundle.exists():
        pytest.skip('requires pinned source preparation')
    mod = preparation()
    mod.bootstrap(bundle,DATA/'runtime',split='train')
    from env.domains.travel import enums
    assert 'forest' in {v.value for v in enums.LocationCategory}
    assert 'city' not in {v.value for v in enums.LocationCategory}
    mod.bootstrap(bundle,DATA/'runtime',split='test')
    assert 'city' in {v.value for v in enums.LocationCategory}
    assert 'forest' not in {v.value for v in enums.LocationCategory}
