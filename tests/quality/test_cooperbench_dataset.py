"""Pair privacy, grouped splits, provenance and fixed-worker declarations."""
import json
from types import SimpleNamespace

import pytest

from data.datasets import cooperbench as dataset
from data.scaffolds import registry
from tools import dataset_classes


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    row = {'task_id': 'cooperbench/repo/task1/f1-2', 'group': 'repo/task1', 'path': 'pairs/one',
           'base_commit': 'a' * 40}
    monkeypatch.setattr(dataset, 'roster', lambda: {row['task_id']: row})
    monkeypatch.setattr(dataset, 'runtime_spec', lambda row: {'kind': 'exec', 'backend': 'observed-v1',
        'image': 'sha256:' + '1' * 64, 'workdir': '/workspace', 'network': 'none',
        'filesystems': {'/workspace': {'bytes': 1 << 30, 'inodes': 10000}}})
    return row


def test_fixed_candidate_workers_have_only_own_feature(prepared, monkeypatch):
    monkeypatch.setattr(dataset, 'task_file', lambda row, rel: json.dumps([
        {'feature_id': '1', 'description': 'feature-alpha'},
        {'feature_id': '2', 'description': 'feature-beta'}]))
    setup = dataset.SETUP[prepared['task_id']]
    registry._check_setup(setup, prepared['task_id'], 'cooperbench')
    assert 'files' not in setup and 'tool_relay' not in setup
    a, b = setup['workers']
    assert [a['id'], b['id']] == ['agent1', 'agent2']
    assert a['artifacts'] == b['artifacts'] == ['agent.patch']
    assert 'feature-beta' not in json.dumps(a)
    assert 'feature-alpha' not in json.dumps(b)
    for worker in (a, b):
        assert not any(key in json.dumps(worker) for key in ['combined.patch', 'tests1.patch', 'private/case'])
        assert worker['env']['backend'] == 'observed-v1'


def test_group_split_never_leaks_related_pair(monkeypatch):
    rows = {f'cooperbench/group{g}/pair{p}': {'group': f'group{g}'} for g in range(30) for p in range(4)}
    monkeypatch.setattr(dataset, 'roster', lambda: rows)
    split = dataset.SPLIT
    assert split['train'] and split['eval']
    assert set(split['train']).isdisjoint(split['eval'])
    sides = {tid: side for side, tids in split.items() for tid in tids}
    for group in {r['group'] for r in rows.values()}:
        assert len({sides[tid] for tid, row in rows.items() if row['group'] == group}) == 1


def test_worker_cli_is_classified_from_actual_actions(tmp_path):
    cli = tmp_path / 'cli' / 'mailbox.py'; cli.parent.mkdir()
    cli.write_text("if command == 'send_message': pass\n")
    module = SimpleNamespace(SETUP={'pair': {'workers': [
        {'files': [{'path': 'mailbox.py', 'from': str(cli)}]}]}})
    assert dataset_classes._cli_scripts(module, 'pair') == [cli]
    assert dataset_classes._declared_verbs(cli) & set(dataset_classes.ACT_VERBS)
    cli.write_text("if command == 'submit': pass\n")
    assert not dataset_classes._declared_verbs(cli) & set(dataset_classes.ACT_VERBS)


def test_changed_bytes_rejected_before_materialization(tmp_path):
    p = tmp_path / 'task.json'; p.write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        dataset.checked(p, '0' * 64)


def test_missing_runtime_is_not_silently_filtered(prepared, monkeypatch, tmp_path):
    monkeypatch.setattr(dataset, 'DATASET_ROOT', tmp_path)
    tasks, gold = dataset.load()
    assert len(tasks) == len(gold) == 1
    with pytest.raises(FileNotFoundError):
        dataset.runtime(prepared)


def test_prepared_manifest_and_selected_runtime_load():
    if not (dataset.DATASET_ROOT / 'prepared-v1/manifest.json').exists():
        pytest.skip('prepared CooperBench inputs needed for manifest integration check')
    rows = dataset.roster()  # Validate the adapter's fixed manifest, without mocks.
    assert len(rows) == 652
    selected = next((tid for tid, row in rows.items() if
                     (dataset.DATASET_ROOT / 'runtimes' / row['group'].replace('/', '-') / 'runtime.json').exists()), None)
    if selected is None:
        pytest.skip('one prepared runtime needed for selected loading')
    tasks, setups, verifiers = registry.load_tasks('cooperbench', task_ids=[selected])
    assert [task['task_id'] for task in tasks] == [selected]
    assert len(setups[selected]['workers']) == 2
    case = next(item['content'] for item in verifiers[selected]['inputs'] if item['path'] == 'private/case.json')
    assert json.loads(case)['provenance']['manifest_sha256'] == dataset.MANIFEST_SHA256
