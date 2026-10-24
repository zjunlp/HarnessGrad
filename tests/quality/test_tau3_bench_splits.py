"""Native task membership, hash-partitioned banking and full-base reporting."""
import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest

from data.datasets import tau3_bench as dataset
from data.scaffolds import registry

#: **A fresh checkout has no payload.** `prepare.py` writes it outside the repository (see the
#: adapter's README), so a machine that has not provisioned τ³ must skip these tests rather than
#: fail them -- the convention every other dataset test here follows (`test_alfworld_dataset.py`
#: does the same for its extracted data). With the payload present the tests are strict.
if not (dataset.DATASET_ROOT / "manifest.json").is_file():
    pytest.skip(f"no prepared tau3 payload under {dataset.DATASET_ROOT}; run "
                "data/datasets/tau3_bench/prepare.py (its README has the commands)",
                allow_module_level=True)


@pytest.fixture
def upstream():
    path = os.environ.get('TAU3_UPSTREAM')
    if not path:
        pytest.skip('set TAU3_UPSTREAM to the pinned extracted source')
    return Path(path)


def preparation():
    spec = importlib.util.spec_from_file_location('tau3_split_prepare', dataset.ASSETS / 'prepare.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('domain', ['airline', 'retail', 'telecom'])
def test_declared_membership_matches_original_release(upstream, domain):
    source = json.loads((upstream / f'data/tau2/domains/{domain}/split_tasks.json').read_text())
    _, _, split = registry.load_split('tau3_bench')
    prefix = f'tau3_bench/{domain}/'
    for side, native in [('train', 'train'), ('eval', 'test')]:
        assert [tid[len(prefix):] for tid in split[side] if tid.startswith(prefix)] == source[native]


def test_all_tasks_are_partitioned_and_banking_uses_platform_default():
    tasks, _, split = registry.load_split('tau3_bench')
    assert len(split['train']) == 242
    assert len(split['eval']) == 133
    assert not set(split['train']) & set(split['eval'])
    assert set(split['train']) | set(split['eval']) == {t['task_id'] for t in tasks}
    banking = {tid for tid, row in dataset.roster().items() if row['domain'] == 'banking_knowledge'}
    assert len(banking) == 97
    expected = registry.default_split([{'task_id': tid} for tid in sorted(banking)])
    assert len(expected['train']) == 64
    assert len(expected['eval']) == 33
    for side in ('train', 'eval'):
        assert banking & set(split[side]) == set(expected[side])
    assert dataset.SPLIT_SOURCE == 'declared'


def test_full_base_reporting_is_separate_from_held_out_evaluation():
    tasks, _, split = registry.load_split('tau3_bench')
    base = registry.official_split('tau3_bench', tasks)
    assert base['train'] == []
    assert set(base['eval']) == {t['task_id'] for t in tasks}
    assert set(split['train']) < set(base['eval'])


def test_preparation_checks_upstream_split_provenance(upstream, tmp_path):
    prepare = preparation()
    prepare.validate_native_splits(upstream)
    source = tmp_path / 'source'
    for domain in ('airline', 'retail', 'telecom'):
        relative = f'data/tau2/domains/{domain}/split_tasks.json'
        target = source / relative
        target.parent.mkdir(parents=True)
        shutil.copyfile(upstream / relative, target)
    changed = source / 'data/tau2/domains/airline/split_tasks.json'
    value = json.loads(changed.read_text())
    value['train'][0], value['test'][0] = value['test'][0], value['train'][0]
    changed.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='official train/test split mismatch'):
        prepare.prepare(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_modified_split_metadata_is_rejected(monkeypatch, tmp_path):
    (tmp_path / 'specs').mkdir()
    metadata = json.loads((dataset.ASSETS / 'specs/task-splits.json').read_text())
    airline = metadata['domains']['airline']
    airline['train'][0], airline['test'][0] = airline['test'][0], airline['train'][0]
    (tmp_path / 'specs/task-splits.json').write_text(json.dumps(metadata))
    monkeypatch.setattr(dataset, 'ASSETS', tmp_path)
    with pytest.raises(ValueError, match='native split digest mismatch'):
        _ = dataset.SPLIT
