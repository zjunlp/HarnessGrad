"""Native relay boundaries, actual sandbox controls and selected-pool reporting."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from data.datasets import memoryagentbench as dataset
from data.datasets.memoryagentbench import control
from data.datasets.memoryagentbench.lib import storage, scoring
from data.datasets.memoryagentbench.relay import Session
from data.scaffolds import registry


def test_registered_memory_classification_uses_sequential_cli(prepared):
    from tools import dataset_classes

    assert dataset.KIND == "dataset"
    assert "memoryagentbench" in registry.published()
    row = dataset_classes.classify("memoryagentbench")
    assert (row["demand"], row["surface"]) == ("memory", "supplied_history")
    assert row["interaction"] == "closed_loop"
    assert row["action_surface"] == ["mab[ack,next,submit]"]
    assert set(row["exercises"]) == {"context", "control", "memory"}
    assert not row["adversarial"]


def test_declared_split_partitions_complete_released_episodes(monkeypatch):
    frozen = json.loads((dataset.ASSETS / 'specs/frozen-episodes.json').read_text())
    rows = {}
    for episode in frozen['episodes']:
        _, partition, index = episode['episode_id'].split('/')
        tid = storage.task_id(partition, int(index))
        rows[tid] = dict(task_id=tid, source=episode['source'],
                         chunk_count=episode['chunks'], question_count=episode['questions'])
    monkeypatch.setattr(dataset, '_rows', lambda: rows)
    tasks, scorable, split = registry.load_split('memoryagentbench')
    assert dataset.SPLIT_SOURCE == 'hash'
    assert len(tasks) == len(scorable) == 146
    assert split == registry.default_split(tasks)
    assert not set(split['train']) & set(split['eval'])
    assert set(split['train']) | set(split['eval']) == set(rows)
    assert {side: (len(ids), sum(rows[tid]['question_count'] for tid in ids))
            for side, ids in split.items()} == {'train': (100, 2069), 'eval': (46, 1602)}
    assert set(registry.official_split('memoryagentbench', tasks)['eval']) == set(rows)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / 'prepared'
    root.mkdir()
    tid = storage.task_id('Conflict_Resolution', 0)
    service = {'schema': 'hg-mab-service-input-v1', 'task_id': tid, 'episode_id': tid,
               'source': 'factconsolidation_sh_6k', 'chunks': ['Mira lives in Paris.', 'Mira moved to Berlin.'],
               'questions': [{'id': storage.question_id(tid, i), 'text': text} for i, text in enumerate(
                   ['Where now?', 'Where before?'])]}
    service['input_sha256'] = storage.canonical_sha256(service)
    gold = {'schema': 'hg-mab-gold-v1', 'task_id': tid, 'episode_id': tid, 'source': service['source'],
            'input_sha256': service['input_sha256'],
            'gold_by_id': {q['id']: [answer] for q, answer in zip(service['questions'], ['Berlin', 'Paris'])},
            'judge_metadata_by_id': {q['id']: dict(question=q['text'], question_type=None,
                question_id=None, qa_pair_id=None, keypoints=None) for q in service['questions']}}
    row = {key: service[key] for key in ('task_id', 'episode_id', 'source', 'input_sha256')}
    row.update(split='Conflict_Resolution', row_index=0, chunk_count=2, question_count=2)
    artifacts = {'pool.json': {'schema': 'hg-mab-pool-v1', 'dataset_revision': dataset.REVISION,
                              'tasks': [row]},
                 f'service/{tid}.json': service, f'gold/{tid}.json': gold, 'entity2id.json': {'Example': 1}}
    hashes = {}
    for name, value in artifacts.items():
        target = root / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(value))
        hashes[name] = storage.file_sha256(target)
    (root / 'manifest.json').write_text(json.dumps({'schema': 'hg-mab-manifest-v1',
        'dataset_revision': dataset.REVISION, 'artifacts': hashes}))
    monkeypatch.setattr(dataset, 'PREPARED_ROOT', root)
    dataset._rows.cache_clear()
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('PATH', str(Path(sys.executable).resolve().parent) + os.pathsep + os.environ['PATH'])
    yield tid, service, gold
    dataset._rows.cache_clear()


def test_setup_only_stages_selected_private_episode(prepared):
    tid, service, _ = prepared
    setup, verify = dataset.SETUP[tid], dataset.VERIFY[tid]
    assert json.loads(setup['tool_relay']['content']) == service
    assert setup['tool_relay']['record']['path'] == 'trusted_episode'
    for entry in setup['files']:
        assert Path(entry['from']).is_file()
        assert 'gold' not in entry['path'] and 'questions' not in entry['path']
    assert 'Berlin' not in dataset.load()[0][0]['goal']
    assert verify['result_contract'] == {'version': 1, 'score_range': [0, 1],
                                        'require_exit_zero': True}
    assert dataset.ENV[tid]['kind'] == 'files'


@pytest.mark.parametrize('operation', ['seal', 'reset', 'judge', 'admin', 'list', 'future'])
def test_actor_cannot_reach_controller_operations(prepared, operation):
    _, payload, _ = prepared
    session = Session(payload)
    assert session.handle(operation, {})['accepted'] is False
    assert session.record()['seal']['status'] == 'incomplete'
    assert session.rejected == 1


def test_protocol_rejections_are_not_transport_failures(prepared):
    _, payload, _ = prepared
    session = Session(payload)
    assert session.handle('ack', {'chunk_id': 'chunk:0', 'request_id': 'r'})['error'] == 'not_issued'
    assert session.handle('next', {})['text'] == payload['chunks'][0]
    assert session.handle('ack', {'chunk_id': 'chunk:0', 'request_id': 'r'})['accepted']
    assert session.handle('ack', {'chunk_id': 'chunk:0', 'request_id': 'r'})['accepted']
    assert session.handle('ack', {'chunk_id': 'chunk:1', 'request_id': 'r'})['error'] == 'request_conflict'
    assert session.handle('submit', {'question_id': 'q', 'answer': 'x' * 65537,
                                     'request_id': 's'})['error'] == 'wrong_phase'
    assert session.record()['seal']['acked_chunk_ids'] == ['chunk:0']


@pytest.mark.parametrize('mode,score', [('oracle', 1), ('wrong', 0), ('empty', 0), ('noop', None)])
@pytest.mark.parametrize('platform', [False, True])
def test_controls_through_selected_episode_and_actual_verifier(prepared, tmp_path, monkeypatch, mode, score, platform):
    work = tmp_path / 'control'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    result = control.run(mode, platform, prepared[0], work)
    assert result['score'] == score, result
    if mode == 'noop':
        assert result['evidence']['reason'] == 'incomplete_episode'
        assert result['evidence']['result']['coverage']['submitted'] == 0
    else:
        assert result['evidence']['result']['coverage']['measured'] == 2
    if platform:
        relay = result['detail']['runtime_services']['tool_relay']
        assert relay['cleanup'] == {'complete': True, 'errors': []}
        archive = Path(relay['recording']['archive'])
        payload = json.loads(archive.read_text())
        assert payload['binding']['task_id'] == prepared[0]
        assert payload['payload']['seal']['status'] == ('incomplete' if mode == 'noop' else 'complete')


def test_verifier_rejects_cross_task_or_modified_seal(prepared):
    tid, service, gold = prepared
    spec = importlib.util.spec_from_file_location('mab_check_test', dataset.ASSETS / 'verify/check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = Session(service)
    relay_sha = hashlib.sha256(dataset._service_content(dataset._rows()[tid]).encode()).hexdigest()
    record = {'schema': 'harnessgrad.interaction-record.v1', 'binding': {'task_id': tid,
              'input_sha256': relay_sha, 'run_id': 'run', 'trial_id': 'trial'}, 'payload': session.record()}
    for corrupt in ('task', 'input', 'seal'):
        wrong = copy.deepcopy(record)
        if corrupt == 'task':
            wrong['binding']['task_id'] = 'another-task'
        elif corrupt == 'input':
            wrong['payload']['input_sha256'] = '0' * 64
        else:
            wrong['payload']['seal']['status'] = 'complete'
        with pytest.raises(ValueError):
            module.verify(gold, wrong, task_id=tid, input_sha256=service['input_sha256'], relay_sha256=relay_sha)


def test_nonzero_checker_completion_rejects_its_real_reward(prepared, tmp_path, monkeypatch):
    """Fault injection after the real checker has scored a sealed episode."""
    original = dataset._verify

    def interrupted(row):
        spec = original(row)
        wrapper = ("from pathlib import Path\nimport subprocess, sys\n"
                   f"result = subprocess.run({spec['argv']!r}, check=False)\n"
                   "assert result.returncode == 0\n"
                   "reward = Path('verifier/reward.txt').read_text().strip()\n"
                   "assert float(reward) == 1\n"
                   "print('completed scorer left reward=' + reward, flush=True)\n"
                   "sys.exit(78)\n")
        spec['inputs'].append({'path': 'fail-after-scoring.py', 'content': wrapper})
        spec['argv'] = ['python3', '-I', 'fail-after-scoring.py']
        return spec

    monkeypatch.setattr(dataset, 'VERIFY', dataset._TaskMapping(interrupted))
    work = tmp_path / 'failed-checker'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    result = control.run('oracle', True, prepared[0], work)
    verdict = result['detail']['verdict']
    assert result['score'] is None and verdict.get('score') is None, result
    assert verdict['exit_code'] == 78 and verdict['outcome'] == 'unmeasured'
    assert 'completed scorer left reward=1' in verdict['detail']


@pytest.mark.parametrize('fault,reason', [('missing-record', 'FileNotFoundError'),
                                        ('invalid-episode', 'gold identity')])
def test_checker_input_failure_stays_unmeasured_with_reason(prepared, tmp_path, monkeypatch, fault, reason):
    original = dataset._verify

    def damaged(row):
        spec = original(row)
        if fault == 'missing-record':
            spec['argv'] += ['--record', 'missing-record.json']
        else:
            spec['argv'][spec['argv'].index('--task-id') + 1] = 'invalid-episode'
        return spec

    monkeypatch.setattr(dataset, 'VERIFY', dataset._TaskMapping(damaged))
    work = tmp_path / fault
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    result = control.run('oracle', True, prepared[0], work)
    verdict = result['detail']['verdict']
    assert result['score'] is None and verdict['score'] is None
    assert verdict['exit_code'] == 3 and verdict['outcome'] == 'unmeasured'
    assert 'scorer_error:' in verdict['detail'] and reason in verdict['detail']


def test_unmeasured_episode_is_retained_in_source_report(prepared, tmp_path):
    from data.datasets.memoryagentbench.lib.reporting import summarize
    tid, _, _ = prepared
    work = tmp_path / 'manual'
    result = control.run('noop', False, tid, work)
    report = summarize([result['evidence']], [dataset._rows()[tid]])
    source = report['sources']['factconsolidation_sh_6k']
    assert source['status'] == 'incomplete'
    assert 'score' not in source
    assert source['unmeasured_questions'] == 2
    assert report['coverage']['measured_tasks'] == 0


def _evidence(row, value):
    count = row['question_count']
    return {'schema': 'hg-mab-verifier-v1', 'task_id': row['task_id'], 'source': row['source'],
            'metric': 'substring_exact_match', 'measured': True,
            'artifacts': {'input_sha256': row['input_sha256']},
            'result': {'status': 'measured', 'episode_id': row['task_id'], 'source': row['source'],
                       'metric': 'substring_exact_match', 'aggregation': 'question_mean_within_episode',
                       'score': value, 'coverage': dict(expected=count, submitted=count, missing=0,
                                                     measured=count, unmeasured=0),
                       'questions': [{'question_id': storage.question_id(row['task_id'], i),
                                      'status': 'measured', 'metric': 'substring_exact_match', 'score': value}
                                     for i in range(count)]}}


def test_source_means_weight_questions_and_never_hide_missing_episodes(prepared):
    from data.datasets.memoryagentbench.lib.reporting import summarize
    row = dict(dataset._rows()[prepared[0]], question_count=1)
    second = dict(row, task_id=storage.task_id('Conflict_Resolution', 1), question_count=3)
    second['episode_id'] = second['task_id']
    report = summarize([_evidence(row, 1), _evidence(second, 0)], [row, second])
    assert report['sources'][row['source']]['score'] == .25
    assert report['platform_measured_episode_mean'] == .5
    partial = summarize([_evidence(row, 1)], [row, second])
    assert 'score' not in partial['sources'][row['source']]
    assert partial['coverage']['unmeasured_questions'] == 3


@pytest.mark.parametrize('corruption', ['duplicate', 'foreign', 'input', 'metric', 'nan', 'coverage', 'mean'])
def test_report_rejects_unbound_or_inconsistent_evidence(prepared, corruption):
    from data.datasets.memoryagentbench.lib.reporting import summarize
    row = dataset._rows()[prepared[0]]
    evidence = _evidence(row, 1)
    records = [evidence]
    if corruption == 'duplicate':
        records *= 2
    elif corruption == 'foreign':
        evidence['task_id'] = 'wrong-task'
    elif corruption == 'input':
        evidence['artifacts']['input_sha256'] = '0' * 64
    elif corruption == 'metric':
        evidence['metric'] = 'invented'
    elif corruption == 'nan':
        evidence['result']['questions'][0]['score'] = float('nan')
    elif corruption == 'coverage':
        evidence['result']['questions'].pop()
    else:
        evidence['result']['score'] = 0
    with pytest.raises(ValueError):
        summarize(records, [row])


def test_import_is_lazy_and_default_data_root_is_portable(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[2]
    script = f"""import sys
from pathlib import Path
sys.path.insert(0, {str(root)!r})
from data.datasets import memoryagentbench as dataset
assert not dataset._rows.cache_info().currsize
assert dataset.DATASET_ROOT == Path({str(root.parent / 'datasets/memoryagentbench')!r})
assert not any(m in sys.modules for m in ('numpy', 'nltk', 'rouge_score', 'pyarrow', 'tiktoken'))
"""
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('HG_MAB_DATA', 'HG_MAB_PREPARED')}
    subprocess.run([sys.executable, '-I', '-S', '-c', script], cwd=tmp_path,
                   env=environment, check=True)


def test_all_prepared_sources_have_scoring_paths(monkeypatch):
    rows = []
    for index, source in enumerate(('factconsolidation_sh_6k', *sorted(scoring.JUDGE_SOURCES))):
        rows.append({'task_id': str(index), 'source': source})
    monkeypatch.setattr(storage, 'load_pool', lambda root: {'tasks': rows})
    dataset._rows.cache_clear()
    try:
        assert set(dataset._rows()) == {'0', '1', '2'}
    finally:
        dataset._rows.cache_clear()


def test_actor_cannot_inject_scorer_package_or_entrypoint(prepared, tmp_path, monkeypatch):
    work = tmp_path / 'injection'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    attack = "from pathlib import Path\nPath('forged-package-executed').write_text('yes')\nraise RuntimeError('untrusted scorer executed')\n"
    result = control.run('wrong', True, prepared[0], work, actor_preload={
        'mab_scorer/lib/__init__.py': attack,
        'mab_scorer/lib/scoring.py': attack,
        'mab_scorer/verify/check.py': attack,
        'mab_verify/lib/__init__.py': attack,
        'lib/__init__.py': attack,
        'sitecustomize.py': attack})
    assert result['score'] == 0, result
    assert result['evidence']['measured'] is True
    assert result['detail']['runtime_services']['tool_relay']['cleanup']['complete']
    assert any(entry.get('immutable') and entry['path'] == 'mab_scorer'
               for entry in dataset.VERIFY[prepared[0]]['inputs'])


def test_actor_scorer_symlink_fails_closed(prepared, tmp_path, monkeypatch):
    work = tmp_path / 'symlink'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    result = control.run('wrong', True, prepared[0], work, actor_preload={
        'actor-shadow/marker': 'untrusted', 'mab_scorer': {'symlink': 'actor-shadow'}})
    assert result['score'] is None, result
    assert 'symlink' in json.dumps(result['detail']).lower(), result
    assert result['detail']['runtime_services']['tool_relay']['cleanup']['complete']


def test_no_extra_runtime_caps_and_service_digest_is_bound(prepared):
    tid, service, _ = prepared
    setup = dataset.SETUP[tid]
    assert json.loads(setup['tool_relay']['content']) == service
    assert setup['tool_relay']['max_calls'] is None
    assert setup['tool_relay']['max_line_bytes'] is None
    assert setup['tool_relay']['record']['max_bytes'] is None
    assert dataset.ENV[tid] == {'kind': 'files'}
    verify = dataset.VERIFY[tid]
    assert 'timeout_s' not in verify
    digest = verify['argv'][verify['argv'].index('--relay-sha256') + 1]
    assert digest == hashlib.sha256(setup['tool_relay']['content'].encode()).hexdigest()


def test_large_utf8_answer_and_request_id_survive_cli_and_seal(prepared, tmp_path, monkeypatch, capsys):
    import runpy
    import types
    _, service, _ = prepared
    session = Session(service)
    for i in range(2):
        session.handle('next', {})
        assert session.handle('ack', {'chunk_id': f'chunk:{i}', 'request_id': f'a{i}'})['accepted']
    qid = session.handle('next', {})['question_id']
    answer = '证据🙂' * 12000
    request_id = 'r' * 1024
    answer_file = tmp_path / 'answer.txt'
    answer_file.write_text(answer)
    def call(socket, op, args, **kwargs):
        assert kwargs == {'timeout_s': None, 'max_line_bytes': None}
        return session.handle(op, args)
    monkeypatch.setitem(sys.modules, 'mab_transport', types.SimpleNamespace(call=call))
    monkeypatch.setattr(sys, 'argv', ['mab', 'submit', '--question-id', qid,
        '--request-id', request_id, '--answer-file', str(answer_file)])
    with pytest.raises(SystemExit) as end:
        runpy.run_path(str(dataset.ASSETS / 'cli/mab'), run_name='__main__')
    assert end.value.code == 0
    assert json.loads(capsys.readouterr().out)['accepted']
    args = {'question_id': qid, 'answer': answer, 'request_id': request_id}
    assert session.handle('submit', args)['accepted']
    assert session.handle('submit', {**args, 'answer': 'changed'})['error'] == 'request_conflict'
    assert session.record()['seal']['answers'][0]['answer'] == answer
