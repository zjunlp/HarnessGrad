"""External eval uses the existing exam engine and preserves real source identity."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from tools.validate_harness import source_identity

ROOT = Path(__file__).resolve().parents[2]
ACTOR = '''import argparse
import json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--task')
p.add_argument('--workdir')
a = p.parse_args()
goal = json.loads(Path(a.task).read_text())['goal']
answer = goal.removeprefix('Answer with the word ').removesuffix('.')
Path(a.workdir, 'answer.txt').write_text(answer)
Path(a.workdir, 'trace.jsonl').write_text(json.dumps({
    'step': 0, 'action': 'finish', 'answer': answer, 'source': 'task.goal',
}) + chr(10))
'''


def _harness(parent):
    source = parent / 'external'
    source.mkdir(parents=True)
    (source / 'harness.json').write_text(json.dumps({
        'name': 'external-eval-actor', 'version': '1', 'entrypoint': 'agent.py',
        'backend': 'cli', 'env_kinds': ['files'],
    }))
    (source / 'agent.py').write_text(ACTOR)
    return source


def _args(root, *source, side='eval', run_id='a3-test', rounds=0, sampling='all'):
    args = ['driver.py', *source, '--dataset', 'demo', '--split', 'declared',
            '--side', side, '--sampling', sampling, '--jobs', '1', '--trials', '1',
            '--seed', 'a3-fixed', '--improver', 'none', '--run-id', run_id,
            '--runs-root', str(root / 'runs'), '--work-root', str(root / 'work')]
    if rounds is not None:
        args += ['--rounds', str(rounds)]
    return args


def _run(args):
    return subprocess.run([sys.executable, *args], cwd=ROOT,
                          env={**os.environ, 'HG_AGENT_BACKEND': 'mock'},
                          capture_output=True, text=True, timeout=60)


def _refused(proc, word):
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert word.lower() in proc.stderr.lower(), proc.stderr
    assert 'Traceback' not in proc.stderr


def _record(root, name):
    run = root / 'runs' / name
    meta = json.loads((run / 'run_meta.json').read_text())
    points = [json.loads(line) for line in (run / 'curve.jsonl').read_text().splitlines()]
    assert len(points) == 1
    return meta, points[0]


@pytest.fixture(scope='module')
def measured(tmp_path_factory):
    root = tmp_path_factory.mktemp('external-eval')
    source = _harness(root)
    tag = uuid.uuid4().hex[:8]
    names = {key: f'a3-{key}-{tag}' for key in ('train', 'direct', 'checkpoint')}
    method = root / 'method.py'
    marker = root / 'method-was-called'
    method.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("called")\nraise SystemExit(73)\n')
    train = _run(_args(root, '--harness-path', str(source), side='train', run_id=names['train']))
    assert train.returncode == 0, train.stdout + train.stderr
    direct_args = _args(root, '--harness-path', str(source), run_id=names['direct'])
    direct_args += ['--method-entrypoint', f'{sys.executable} {method}']
    direct = _run(direct_args)
    checkpoint = _run(_args(root, '--from-run', names['train'], '--from-round', '0',
                            run_id=names['checkpoint']))
    return root, source, names, marker, (direct, checkpoint)


def _verified(measured):
    root, source, names, marker, processes = measured
    for proc in processes:
        assert proc.returncode == 0, proc.stdout + proc.stderr
    return root, source, names, marker


def test_external_eval_runs_and_records_source(measured):
    root, source, names, _ = _verified(measured)
    meta, point = _record(root, names['direct'])
    assert meta['harness'] == 'external-eval-actor'
    assert meta['harness_source'] == source_identity(source)
    assert meta.get('evaluated_from') is None
    assert 'evaluated_from' not in point
    assert point['side'] == 'eval' and point['score_kind'] == 'exam'
    assert point['score'] == 1.0 and point['measured_by_platform'] is True
    assert set(point['per_task']) == {'t05', 't06', 't07', 't08'}
    assert point['n_invalid'] == point['n_timeout'] == point['n_harness_failed'] == 0


def test_external_and_checkpoint_eval_agree_on_same_pool(measured):
    root, source, names, _ = _verified(measured)
    train_meta, train = _record(root, names['train'])
    direct_meta, direct = _record(root, names['direct'])
    check_meta, checkpoint = _record(root, names['checkpoint'])
    assert set(train['per_task']) == {'t01', 't02', 't03', 't04'}
    assert not set(train['per_task']) & set(direct['per_task'])
    assert train_meta['split_sha'] == direct_meta['split_sha'] == check_meta['split_sha']
    assert direct['identity']['harness_sha'] == checkpoint['identity']['harness_sha']
    assert direct['sampling'] == checkpoint['sampling']
    for field in ('jobs', 'trials', 'agent_backend', 'agent_model'):
        assert direct['identity'].get(field) == checkpoint['identity'].get(field)
    assert direct_meta['config']['run']['seed'] == check_meta['config']['run']['seed'] == 'a3-fixed'
    assert direct['per_task'] == checkpoint['per_task']
    assert direct['score'] == checkpoint['score'] == 1.0
    assert checkpoint['evaluated_from']['run_id'] == names['train']
    assert checkpoint['evaluated_from']['round'] == 0
    for name in ('direct', 'checkpoint'):
        verdicts = list((root / 'runs' / names[name] / 'verdicts').rglob('*.json'))
        assert len(verdicts) == 4
        assert {json.loads(p.read_text())['task_id'] for p in verdicts} == {'t05', 't06', 't07', 't08'}


def test_external_eval_is_one_point_without_method(measured):
    root, _, names, marker = _verified(measured)
    _, point = _record(root, names['direct'])
    assert point['round'] == 0
    assert point['sampling']['selected_within'] == 'eval'
    assert not marker.exists()
    traces = root / 'runs' / names['direct'] / 'round0_traces'
    assert not any(p.stem in {'t01', 't02', 't03', 't04'} for p in traces.rglob('*.jsonl'))


@pytest.mark.parametrize('options', [
    ['--from-run', 'train-x'], ['--from-run', ''], ['--from-round', '0'],
    ['--from-run', 'train-x', '--from-round', '0'],
])
def test_external_eval_rejects_conflicting_source_options(tmp_path, options):
    source = _harness(tmp_path)
    proc = _run(_args(tmp_path, '--harness-path', str(source), *options))
    _refused(proc, 'combine')
    assert not (tmp_path / 'runs' / 'a3-test').exists()


@pytest.mark.parametrize('kind,word', [
    ('relative', 'absolute'), ('missing', 'directory'), ('manifest', 'harness.json'),
    ('entrypoint', 'entrypoint'), ('escape', 'outside'),
])
def test_external_eval_keeps_admission_boundaries(tmp_path, kind, word):
    source = _harness(tmp_path)
    if kind == 'relative':
        source = Path('relative-source')
    elif kind == 'missing':
        source = tmp_path / 'absent'
    elif kind == 'manifest':
        (source / 'harness.json').write_text('{bad')
    elif kind == 'entrypoint':
        (source / 'agent.py').unlink()
    else:
        outside = tmp_path / 'private.py'
        outside.write_text('private')
        (source / 'agent.py').unlink()
        (source / 'agent.py').symlink_to(outside)
    _refused(_run(_args(tmp_path, '--harness-path', str(source))), word)
    assert not (tmp_path / 'runs' / 'a3-test').exists()


def test_external_eval_defaults_to_zero_rounds(tmp_path):
    source = _harness(tmp_path)
    proc = _run(_args(tmp_path, '--harness-path', str(source), rounds=None))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    meta, point = _record(tmp_path, 'a3-test')
    assert meta['config']['run']['rounds'] == 0
    assert point['round'] == 0


@pytest.mark.parametrize('rounds', [-1, 1, 3])
def test_external_eval_rejects_nonzero_rounds(tmp_path, rounds):
    source = _harness(tmp_path)
    _refused(_run(_args(tmp_path, '--harness-path', str(source), rounds=rounds)), 'rounds')
    assert not (tmp_path / 'runs' / 'a3-test').exists()


@pytest.mark.parametrize('sampling', ['diagnostic', 'pilot_10pct', 'random_1'])
def test_external_eval_rejects_unimplemented_sampling(tmp_path, sampling):
    source = _harness(tmp_path)
    _refused(_run(_args(tmp_path, '--harness-path', str(source), sampling=sampling)), 'sampling')
    assert not (tmp_path / 'runs' / 'a3-test').exists()


def test_external_eval_resume_is_refused_before_metadata_mutation(tmp_path):
    source = _harness(tmp_path)
    run = tmp_path / 'runs' / 'a3-test'
    run.mkdir(parents=True)
    metadata = run / 'run_meta.json'
    original = b'{"keep": "original", "platform_content_sha": "old"}\n'
    metadata.write_bytes(original)
    proc = _run([*_args(tmp_path, '--harness-path', str(source)), '--resume'])
    _refused(proc, 'resume')
    assert metadata.read_bytes() == original
    assert list(run.iterdir()) == [metadata]


def test_external_eval_cannot_select_train_tasks(tmp_path):
    source = _harness(tmp_path)
    proc = _run([*_args(tmp_path, '--harness-path', str(source)), '--tasks', 't01'])
    _refused(proc, 'eval side')
    assert not (tmp_path / 'runs' / 'a3-test').exists()


def test_checkpoint_eval_still_accepts_legacy_state_names(measured):
    root, _, names, _ = _verified(measured)
    donor = root / 'runs' / names['train']
    current = donor / 'state_after_round0'
    legacy = donor / 'round0_base'
    shutil.copytree(current, legacy)
    isolated = root / 'runs' / 'legacy-donor'
    isolated.mkdir()
    shutil.copytree(legacy, isolated / 'round0_base')
    proc = _run(_args(root, '--from-run', 'legacy-donor', run_id='legacy-eval'))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _, point = _record(root, 'legacy-eval')
    assert point['evaluated_from']['round'] == 0
    assert point['score'] == 1.0
