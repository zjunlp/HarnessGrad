"""Targeted controls must work with the mixed/heldout prompt catalog absent."""
from __future__ import annotations
import json
import runpy
import sys
from types import SimpleNamespace
import pytest
from data.datasets import agentdojo
from data.scaffolds import command_control

def test_explicit_control_uses_only_its_prepared_case(tmp_path, monkeypatch):
    case = tmp_path / agentdojo.PREPARED / '7/case.json'; case.parent.mkdir(parents=True)
    case.write_text(json.dumps({'suite':'banking','user':'user_task_0','injection':'injection_task_0','prompt':'synthetic'}))
    monkeypatch.setattr(agentdojo,'DATASET_ROOT',tmp_path)
    monkeypatch.setattr(agentdojo,'records',lambda: pytest.fail('mixed catalog read'))
    task=command_control.pick(agentdojo,'agentdojo/7'); assert task['task_id']=='agentdojo/7'; assert task['goal'].startswith('synthetic\n')
    for name in ('ENV','SETUP','VERIFY'): assert set(getattr(agentdojo,name))=={'agentdojo/7'}


@pytest.mark.parametrize('count', [0, 2, 6])
def test_count_and_load_follow_the_catalog(count, monkeypatch):
    monkeypatch.setattr(agentdojo, 'records', lambda: [{'prompt': 'synthetic'}] * count)
    tasks, gold = agentdojo.load()
    assert agentdojo.TASK_COUNT == len(tasks) == len(gold) == count
    assert all(task['goal'] == 'synthetic\n\nUse the AgentDojo tools through this CLI:\n'
               '  python3 dojo.py tools\n'
               '  python3 dojo.py call \'{"function":"name","args":{}}\'\n'
               '  python3 dojo.py submit "final answer"\n' for task in tasks)

def test_control_requires_explicit_id_before_any_catalog_read(monkeypatch):
    monkeypatch.setattr(agentdojo,'records',lambda: pytest.fail('mixed catalog read'))
    with pytest.raises(SystemExit,match='require --task-id'): command_control.pick(agentdojo,None)

@pytest.mark.parametrize('task_id',['agentdojo/949','agentdojo/-1','agentdojo/007','other/7','agentdojo/../7'])
def test_invalid_control_id_is_rejected_before_any_read(task_id,monkeypatch):
    monkeypatch.setattr(agentdojo.Path,'read_text',lambda *a,**k: pytest.fail('case read'))
    with pytest.raises(SystemExit,match='unknown task id'): command_control.pick(agentdojo,task_id)

def test_the_adapter_declares_no_division_and_a_caller_supplies_the_group_rule(monkeypatch):
    """The `(suite, user)` grouping was the adapter's hash split; the adapter has none now.

    AgentDojo's 949 pairs are the benchmark's evaluation material, so there is no division to
    declare (2026-10-24). What still has to hold is that a **caller-supplied** division can
    keep same-user variants together -- which is why the leakage-free division is a
    `--split <file.json>` and not `--split default` (task-id hash, group-blind).
    """
    from data.scaffolds import registry

    rows = [{'suite': suite, 'user': user, 'injection': str(v)}
            for suite in ('banking', 'slack')
            for user in ('user_task_0', 'user_task_1', 'user_task_2') for v in range(4)]
    monkeypatch.setattr(agentdojo, 'records', lambda: rows)
    assert agentdojo.SPLIT is None and agentdojo.SPLIT_SOURCE == 'none'
    ids = [f'agentdojo/{i}' for i in range(len(rows))]
    by_group = {}
    for index, row in enumerate(rows):
        by_group.setdefault((row['suite'], row['user']), []).append(ids[index])
    groups = list(by_group.values())
    supplied = registry.validate_split('agentdojo', [{'task_id': t} for t in ids], {
        'train': [t for group in groups[1:] for t in group],
        'eval': list(groups[0])})
    sides = {t: side for side, members in supplied.items() for t in members}
    assert all(len({sides[t] for t in group}) == 1 for group in groups)

def test_other_adapters_keep_existing_explicit_selection():
    module=SimpleNamespace(load=lambda:([{'task_id':'fixture/0'}],{})); assert command_control.pick(module,'fixture/0')=={'task_id':'fixture/0'}


def test_attack_preparation_preserves_legacy_and_refuses_overwrite(tmp_path, monkeypatch):
    prepare = runpy.run_path(str(agentdojo.ASSETS / 'prepare.py'))['prepare_cases']
    monkeypatch.setitem(prepare.__globals__, 'DATA', tmp_path)
    (tmp_path / '_src').mkdir()
    legacy = tmp_path / 'prepared/0'
    legacy.mkdir(parents=True)
    (legacy / 'initial.json').write_text('retained legacy snapshot')
    case = {'prompt': 'synthetic'}
    bridge = SimpleNamespace(ATTACK_NAME='ignore_previous', metadata=lambda: [case],
                             snapshot=lambda _: {'attack': 'ignore_previous'},
                             oracle=lambda _: {'calls': [], 'output': ''})
    monkeypatch.setitem(sys.modules, 'bridge', bridge)
    assert prepare() == 1
    new = tmp_path / agentdojo.PREPARED / '0'
    assert json.loads((new / 'initial.json').read_text()) == {'attack': 'ignore_previous'}
    before = {p: p.read_bytes() for p in tmp_path.rglob('*.json')}
    with pytest.raises(FileExistsError):
        prepare()
    assert {p: p.read_bytes() for p in tmp_path.rglob('*.json')} == before
    assert (legacy / 'initial.json').read_text() == 'retained legacy snapshot'


def test_public_roster_uses_canonical_ids_without_opening_cases(tmp_path, monkeypatch):
    for name in ('0', '2', '02', '-1', 'unrelated'):
        (tmp_path / 'prepared' / name).mkdir(parents=True)
    (tmp_path / 'prepared/3').write_text('not a directory')
    monkeypatch.setattr(agentdojo, 'DATASET_ROOT', tmp_path)
    monkeypatch.setattr(agentdojo, 'records', lambda: pytest.fail('mixed catalog read'))
    monkeypatch.setattr(agentdojo.Path, 'read_text', lambda *a, **k: pytest.fail('case read'))
    assert agentdojo._indices() == [0, 2]
    assert set(agentdojo.SETUP) == {'agentdojo/0', 'agentdojo/2'}
    # Nothing agent-visible comes from the prepared tree or the tool-only runtime: the
    # environment is served by the relay, so SETUP is two programs and no task data.
    assert not any('/prepared' in entry['from'] or '/agent_runtime' in entry['from']
                   for spec in agentdojo.SETUP.values() for entry in spec['files'])
