"""Frozen user partitions are executable protocols, never rewritten official splits.

Controls use public fixture IDs, no model endpoints or benchmark acquisition.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import custom_split, registry
from harnessgrad import splits
from harnessgrad.experiments import to_argv


@pytest.fixture
def source(monkeypatch):
    module = ModuleType('data.datasets.partition_fixture')
    tasks = [{'task_id': f'item-{i:02d}', 'goal': f'Public task {i}'} for i in range(20)]
    module.load = lambda: (tasks, {})
    module.OFFICIAL_EVAL = [t['task_id'] for t in tasks[8:]]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return 'partition_fixture', tasks, module


def save(tmp_path, value, name='partition.json'):
    path = tmp_path / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return path


def test_seeded_membership_ignores_input_order_and_changes_with_seed(source):
    name, tasks, _ = source
    first = custom_split.create(name, tasks, train_ratio=.3, seed=17)
    reordered = custom_split.create(name, list(reversed(tasks)), train_ratio=.3, seed=17)
    assert first == reordered
    other = custom_split.create(name, tasks, train_ratio=.3, seed=18)
    assert first['split'] != other['split']
    assert len(first['split']['train']) == 6
    assert set(first['split']['train']).isdisjoint(first['split']['eval'])


def test_exact_count_and_official_eval_source_exclude_original_training(source, tmp_path):
    name, tasks, module = source
    manifest = custom_split.create(name, tasks, train_count=4, seed=3, source='official-eval')
    assert len(manifest['split']['train']) == 4
    assert set(manifest['excluded']) == {t['task_id'] for t in tasks[:8]}
    assert set(manifest['split']['train'] + manifest['split']['eval']) == set(module.OFFICIAL_EVAL)
    _, receipt = custom_split.read(name, tasks, save(tmp_path, manifest))
    assert receipt['official_eval_reused_for_train'] == 4
    assert receipt['official_score_comparable'] is False
    assert receipt['counts'] == {'train': 4, 'eval': 8, 'excluded': 8}


def test_whole_groups_stay_together_and_inexact_counts_are_refused(source):
    name, tasks, module = source
    module.SPLIT_GROUPS = {t['task_id']: hashlib.sha256(str(i // 2).encode()).hexdigest()
                           for i, t in enumerate(tasks)}
    manifest = custom_split.create(name, tasks, train_ratio=.35, seed=2)
    assert len(manifest['split']['train']) in (6, 8)
    where = {t: side for side, ids in manifest['split'].items() for t in ids}
    assert all(where[tasks[i]['task_id']] == where[tasks[i+1]['task_id']] for i in range(0, 20, 2))
    with pytest.raises(ValueError, match='exact train_count'):
        custom_split.create(name, tasks, train_count=3, seed=2)


@pytest.mark.parametrize('damage', ['dataset', 'roster', 'content', 'official-declaration', 'groups', 'duplicate', 'overlap',
                                   'unknown', 'missing', 'wrong_source', 'extra_field'])
def test_frozen_manifest_rejects_changed_or_ambiguous_protocol(source, damage):
    name, tasks, module = source
    value = custom_split.create(name, tasks, train_count=5, seed=4)
    if damage == 'dataset': value['dataset'] = 'another'
    elif damage == 'roster': tasks = tasks[:-1]
    elif damage == 'content': tasks[0]['goal'] = 'A changed public task'
    elif damage == 'official-declaration': module.OFFICIAL_EVAL = [tasks[-1]['task_id']]
    elif damage == 'groups':
        module.SPLIT_GROUPS = {t['task_id']: hashlib.sha256(t['task_id'].encode()).hexdigest() for t in tasks}
    elif damage == 'duplicate': value['split']['train'].append(value['split']['train'][0])
    elif damage == 'overlap': value['split']['eval'].append(value['split']['train'][0])
    elif damage == 'unknown': value['split']['train'][0] = 'foreign'
    elif damage == 'missing': value['split']['eval'].pop()
    elif damage == 'wrong_source': value['source'] = 'official-eval'
    elif damage == 'extra_field': value['hidden_gold'] = 'not part of schema'
    with pytest.raises(ValueError): custom_split.validate(name, tasks, value)


def test_groups_cannot_cross_selected_and_excluded_source(source):
    name, tasks, module = source
    module.SPLIT_GROUPS = {t['task_id']: hashlib.sha256(t['task_id'].encode()).hexdigest() for t in tasks}
    module.SPLIT_GROUPS[tasks[8]['task_id']] = module.SPLIT_GROUPS[tasks[0]['task_id']]
    with pytest.raises(ValueError, match='SPLIT_GROUPS'):
        custom_split.create(name, tasks, train_count=3, source='official-eval')


@pytest.mark.parametrize('fields', [{'train_ratio': 0}, {'train_ratio': float('nan')},
                                   {'train_count': 0}, {'train_count': 20},
                                   {'train_count': 3, 'train_ratio': .5}])
def test_invalid_size_requests_do_not_produce_a_protocol(source, fields):
    name, tasks, _ = source
    with pytest.raises(ValueError): custom_split.create(name, tasks, **fields)


def test_single_group_cannot_manufacture_independent_sides(source):
    name, tasks, module = source
    module.SPLIT_GROUPS = {t['task_id']: 'a'*64 for t in tasks}
    with pytest.raises(ValueError, match='independent groups'):
        custom_split.create(name, tasks, train_ratio=.5)


def test_legacy_split_and_raw_file_identity_remain_self_contained(source, tmp_path):
    name, tasks, module = source
    split = {'train': [t['task_id'] for t in tasks[:9]], 'eval': [t['task_id'] for t in tasks[9:]]}
    path = save(tmp_path, split)
    division, receipt = custom_split.read(name, tasks, path)
    assert division == split
    assert receipt['official_eval_reused_for_train'] == 1
    assert receipt['manifest'] == split
    assert receipt['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text('{}')
    assert receipt['manifest'] == split  # The receipt does not depend on later file reads.
    assert registry.split_sha(division) == registry.split_sha(split)


def test_cli_freezes_checks_and_refuses_overwrite_without_loading_runtime(source, tmp_path, monkeypatch, capsys):
    name, tasks, module = source
    def forbid(*args, **kwargs): raise AssertionError('partition command loaded task runtime')
    monkeypatch.setattr(registry, 'load_tasks', forbid)
    monkeypatch.setattr(registry, 'load_envs', forbid)
    def attributes(key):
        if key in ('SETUP', 'VERIFY', 'ENV'): raise AssertionError('read private runtime '+key)
        raise AttributeError(key)
    module.__getattr__ = attributes
    output = tmp_path / 'nested/split.json'
    argv = ['create', '--dataset', name, '--train-count', '4', '--seed', '7', '--output', str(output)]
    assert splits.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    original = output.read_bytes()
    assert first['train'] == 4 and first['eval'] == 16 and first['claim'] == 'custom-heldout'
    assert splits.main(['check', '--dataset', name, '--file', str(output)]) == 0
    assert json.loads(capsys.readouterr().out)['split_sha'] == first['split_sha']
    assert splits.main(argv) == 2
    assert 'split refused' in capsys.readouterr().err
    assert output.read_bytes() == original


def test_experiment_config_preserves_split_file_argument(tmp_path):
    path = tmp_path / 'my split.json'
    assert to_argv({'dataset': 'demo', 'split': str(path)}) == ['--dataset', 'demo', '--split', str(path)]


def driver(tmp_path, run_id, split_file, *extra):
    return subprocess.run([sys.executable, 'driver.py', '--dataset', 'demo', '--harness', 'loop_plain',
        '--sampling', 'all', '--rounds', '1', '--run-id', run_id, '--split', str(split_file),
        '--runs-root', str(tmp_path / 'runs'), '--work-root', str(tmp_path / 'work'),
        '--method-entrypoint', f'{sys.executable} {ROOT / "methods/noop/run.py"}', *extra],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
        env={**os.environ, 'HG_AGENT_BACKEND': 'mock', 'PYTHONDONTWRITEBYTECODE': '1'})


def demo_manifest(tmp_path):
    tasks, _ = registry.load('demo')
    manifest = custom_split.create('demo', tasks, train_count=3, seed=42)
    return manifest, save(tmp_path, manifest)


@pytest.mark.parametrize('flag', ['--check-only', '--prepare-only'])
def test_preflight_refuses_invalid_manifest_without_creating_run(tmp_path, flag):
    path = save(tmp_path, {'schema': custom_split.SCHEMA})
    result = driver(tmp_path, 'invalid-protocol', path, flag)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'split' in result.stdout + result.stderr
    assert not (tmp_path / 'runs/invalid-protocol').exists()
    assert not (tmp_path / 'work/invalid-protocol').exists()


def test_prepare_only_saves_protocol_without_curve_or_task_execution(tmp_path):
    value, path = demo_manifest(tmp_path)
    result = driver(tmp_path, 'prepared', path, '--prepare-only')
    assert result.returncode == 0, result.stdout + result.stderr
    run = tmp_path / 'runs/prepared'
    meta = json.loads((run / 'run_meta.json').read_text())
    assert meta['split_protocol']['manifest'] == value
    assert meta['split_sha'] == registry.split_sha(value['split'])
    assert meta['launched'] is False
    assert not (run / 'curve.jsonl').exists()
    assert not (tmp_path / 'work/prepared').exists()


def test_real_sandbox_train_eval_reuse_manifest_and_hide_eval_from_method(tmp_path):
    value, path = demo_manifest(tmp_path)
    trained = driver(tmp_path, 'custom-study', path)
    assert trained.returncode == 0, trained.stdout + trained.stderr
    run = tmp_path / 'runs/custom-study'
    meta = json.loads((run / 'run_meta.json').read_text())
    points = [json.loads(line) for line in (run / 'curve.jsonl').read_text().splitlines() if line.strip()]
    assert points[-1]['round'] == 1
    assert meta['split_protocol']['manifest'] == value
    assert meta['split_protocol']['official_score_comparable'] is False
    assert points[-1]['split_protocol']['kind'] == 'custom-heldout'
    assert points[-1]['split_protocol']['file_sha256'] == meta['split_protocol']['file_sha256']
    assert 'manifest' not in points[-1]['split_protocol']
    assert set(points[-1]['per_task']) == set(value['split']['train'])
    channel = sorted((run / 'method_channel').glob('round*'))[-1]
    context = json.loads((channel / 'round.json').read_text())
    assert set(context['per_task']) == set(value['split']['train'])
    assert 'eval' not in context['split']
    assert 'manifest' not in context.get('split_protocol', {})
    for item in (channel / 'history').glob('*.json'):
        history_point = json.loads(item.read_text())
        assert 'eval' not in history_point.get('split', {})
    assert {p.stem for p in (channel / 'traces').glob('*.jsonl')} == set(value['split']['train'])
    evaluated = driver(tmp_path, 'custom-exam', path, '--side', 'eval', '--from-run', 'custom-study')
    assert evaluated.returncode == 0, evaluated.stdout + evaluated.stderr
    exam = [json.loads(line) for line in (tmp_path / 'runs/custom-exam/curve.jsonl').read_text().splitlines() if line.strip()][-1]
    assert set(exam['per_task']) == set(value['split']['eval'])
    assert exam['score_kind'] == 'exam'
    assert exam['evaluated_from']['harness_sha'] == points[-1]['identity']['harness_sha']
    from tools.run_summary import summarise, render_markdown
    assert 'Custom held-out partition' in render_markdown(summarise(tmp_path / 'runs/custom-exam'))
    # The same checkpoint cannot later call the original (overlapping) demo exam
    # a held-out report after custom study has consumed arbitrary demo tasks.
    wrong = driver(tmp_path, 'wrong-exam', 'declared', '--side', 'eval', '--from-run', 'custom-study')
    assert wrong.returncode == 2, wrong.stdout + wrong.stderr
    assert 'reuse' in wrong.stdout + wrong.stderr
    assert not (tmp_path / 'runs/wrong-exam/curve.jsonl').exists()
    # A changed file must be refused before resume overwrites any old receipt.
    before = (run / 'run_meta.json').read_bytes()
    before_curve = (run / 'curve.jsonl').read_bytes()
    path.write_text(json.dumps(value) + '\n')  # Same IDs, different protocol bytes.
    resumed = driver(tmp_path, 'custom-study', path, '--resume')
    assert resumed.returncode == 2
    assert 'frozen split manifest changed' in resumed.stdout + resumed.stderr
    assert (run / 'run_meta.json').read_bytes() == before
    assert (run / 'curve.jsonl').read_bytes() == before_curve


def test_custom_pair_guard_rejects_official_exam_after_official_eval_study(source, tmp_path):
    name, tasks, module = source
    value = custom_split.create(name, tasks, train_count=4, source='official-eval')
    split, receipt = custom_split.read(name, tasks, save(tmp_path, value))
    (tmp_path / 'run_meta.json').write_text(json.dumps({'dataset': name, 'split': split,
        'split_sha': registry.split_sha(split), 'split_protocol': receipt}))
    custom_split.check_eval_pair(tmp_path, name, split, receipt)
    with pytest.raises(ValueError, match='reuse'):
        custom_split.check_eval_pair(tmp_path, name, registry.official_split(name, tasks), None)


def test_paired_eval_rejects_source_content_drift_even_with_same_members(source, tmp_path):
    name, tasks, _ = source
    value = custom_split.create(name, tasks, train_count=4)
    split, receipt = custom_split.read(name, tasks, save(tmp_path, value))
    (tmp_path / 'run_meta.json').write_text(json.dumps({'dataset': name,
        'split_sha': registry.split_sha(split), 'split_protocol': receipt}))
    changed = copy.deepcopy(tasks)
    changed[0]['goal'] = 'A different public task with the same ID'
    new_value = custom_split.create(name, changed, train_count=4)
    new_split, new_receipt = custom_split.read(name, changed, save(tmp_path, new_value, 'changed.json'))
    assert registry.split_sha(new_split) == registry.split_sha(split)
    with pytest.raises(ValueError, match='content differs'):
        custom_split.check_eval_pair(tmp_path, name, new_split, new_receipt)
