"""Synthetic scorer context checks across reuse and method boundaries."""
import pytest

import driver
from harnessgrad import verification_context as context
from harnessgrad.channel import _for_method
from harnessgrad.loops.mode_a import _identical_prior, _matching_verification_context


def ctx(**values):
    return context.create({key: value * 64 for key, value in values.items()})


def test_resume_requires_fresh_context_and_projects_task_subset():
    current = ctx(t1='a', t2='b')
    driver._require_verification_context(ctx(t1='a'), current, ['t1'])
    with pytest.raises(ValueError):
        driver._require_verification_context(ctx(t1='c'), current, ['t1'])
    with pytest.raises(ValueError):
        driver._require_verification_context(None, current)
    with pytest.raises(ValueError):
        driver._require_verification_context(ctx(t1='a'), current, ['t2'])


def test_identical_harness_reuse_requires_context():
    current = ctx(t1='a')
    point = {'identity': {'harness_sha': 'same'}, 'sampling': {'trials': 1},
             'verification_context': current}
    assert _identical_prior([point], 'same', 1, verification_context=current) is point
    assert _identical_prior([point], 'same', 1, verification_context=ctx(t1='b')) is None
    assert _identical_prior([{**point, 'verification_context': None}], 'same', 1,
                            verification_context=current) is None
    assert _identical_prior([point], 'same', 1) is None


def test_method_projection_never_contains_private_context():
    private = ctx(t1='a')
    point = {'round': 0, 'score_kind': 'training', 'verification_context': private}
    visible = _for_method(point)
    assert 'verification_context' not in visible
    assert point['verification_context'] == private


def test_h0_rejects_missing_or_changed_context(tmp_path):
    import json
    from tools import prepare_h0
    original = ctx(t1='a', t2='b')
    subset = context.project(original, ['t1'])
    run = tmp_path / 'h0-demo'; run.mkdir()
    payload = {'h0_sha': 'sha', 'verification_context': original,
               'base': {'per_task': {'t1': 1.0}, 'verification_context': original}}
    (run / 'h0.json').write_text(json.dumps(payload))
    (run / 'run_meta.json').write_text(json.dumps({'verification_context': original,
        'sampling_policy':'all', 'config': {'agent': {'model':'mock', 'backend':'mock', 'base_url':''},
                                              'run': {'sampling':'all'}}}))
    (run / 'curve.jsonl').write_text(json.dumps({'verification_context': subset,
        'measured_by_platform': True, 'sampling': {'policy':'all'},
        'identity': {'agent_model':'mock', 'agent_backend':'mock'}}))
    config = {'verification_context': original, 'agent_model':'mock', 'agent_backend':'mock',
              'endpoint':'', 'sampling':'all'}
    assert prepare_h0.verify(run, None, 1, configuration=config)['n_tasks'] == 1
    payload['base'].pop('verification_context')
    (run / 'h0.json').write_text(json.dumps(payload))
    with pytest.raises(SystemExit, match='verification context missing'):
        prepare_h0.verify(run, None, 1, configuration=config)
    payload['base']['verification_context'] = ctx(t1='c')
    (run / 'h0.json').write_text(json.dumps(payload))
    with pytest.raises(SystemExit, match='verification_context changed'):
        prepare_h0.verify(run, None, 1, configuration=config)


def test_method_round_and_history_omit_context(tmp_path):
    import json
    from harnessgrad.channel import _stage_for_method
    work = tmp_path / 'work'; work.mkdir()
    private = ctx(t1='a')
    point = {'round': 1, 'score_kind': 'training', 'verification_context': private}
    past = {'round': 0, 'score_kind': 'training', 'verification_context': private}
    _stage_for_method(work, point, {}, history=[past])
    staged = work / '_harnessgrad'
    assert 'verification_context' not in json.loads((staged / 'round.json').read_text())
    assert 'verification_context' not in json.loads((staged / 'history/round-0.json').read_text())
    assert point['verification_context'] == past['verification_context'] == private


def test_h0_preflight_captures_actual_embedded_demo_without_side_effects(tmp_path):
    import os
    from tools import prepare_h0
    result = prepare_h0._verification_preflight('demo', 'train', [], 'declared', 'all',
        {'HOME': str(tmp_path), 'PATH': os.defpath, 'HG_AGENT_BACKEND': 'mock',
         'HG_REVIEWED_ENVIRONMENT_ONLY': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    assert context.validate(result) == result
    assert len(result['tasks']) == 4
    assert list(tmp_path.iterdir()) == []


def test_changed_scorer_source_blocks_both_reuse_routes(tmp_path):
    source = tmp_path / 'scorer.py'; source.write_text('version one')
    tasks = [{'task_id': 't1'}]
    verifiers = {'t1': {'kind':'command', 'inputs': [{'path':'scorer.py', 'from':str(source)}]}}
    before = context.capture(tasks, {}, {}, verifiers, {})
    point = {'identity': {'harness_sha':'same'}, 'sampling': {'trials':1},
             'verification_context': before}
    assert _matching_verification_context(point, before)
    assert _identical_prior([point], 'same', 1, verification_context=before) is point
    source.write_text('version two')
    after = context.capture(tasks, {}, {}, verifiers, {})
    assert not _matching_verification_context(point, after)
    assert _identical_prior([point], 'same', 1, verification_context=after) is None


def test_prepared_run_captures_current_context_only_when_consumed(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from data.scaffolds import registry

    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'HG_AGENT_BACKEND': 'mock', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    cmd = [sys.executable, str(repo / 'driver.py'), '--dataset', 'demo', '--harness', 'loop_plain',
           '--sampling', 'all', '--rounds', '0', '--improver', 'none', '--run-id', 'prepared',
           '--runs-root', str(tmp_path / 'runs'), '--work-root', str(tmp_path / 'work')]
    prepared = subprocess.run(cmd + ['--prepare-only'], cwd=repo, env=env, text=True,
                              capture_output=True, timeout=120)
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    run = tmp_path / 'runs/prepared'
    assert 'verification_context' not in json.loads((run / 'run_meta.json').read_text())
    consumed = subprocess.run(cmd, cwd=repo, env=env, text=True, capture_output=True, timeout=120)
    assert consumed.returncode == 0, consumed.stdout + consumed.stderr
    tasks, scorable, split = registry.load_split('demo')
    ids = split['train']
    _, setups, verifiers = registry.load_tasks('demo', task_ids=ids)
    envs = registry.load_envs('demo', task_ids=ids)
    current = context.capture([task for task in tasks if task['task_id'] in ids], scorable,
                              setups, verifiers, envs)
    meta = json.loads((run / 'run_meta.json').read_text())
    assert meta['verification_context'] == current
    before = (run / 'run_meta.json').read_bytes()
    duplicate = subprocess.run(cmd, cwd=repo, env=env, text=True, capture_output=True, timeout=120)
    assert duplicate.returncode == 2 and (run / 'run_meta.json').read_bytes() == before


def test_eval_refuses_scorer_drift_after_preflight(tmp_path):
    from harnessgrad.loops.eval_side import _checked_verification_context
    source = tmp_path / 'score.py'; source.write_text('before')
    tasks = [{'task_id': 't1'}]
    verifiers = {'t1': {'kind':'command', 'inputs':[{'path':'score.py', 'from':str(source)}]}}
    expected = context.capture(tasks, {}, {}, verifiers, {})
    assert _checked_verification_context(tasks, {}, {}, verifiers, {}, expected) == expected
    source.write_text('after')
    with pytest.raises(ValueError, match='changed after preflight'):
        _checked_verification_context(tasks, {}, {}, verifiers, {}, expected)


def test_eval_window_refuses_before_commit_and_preserves_existing_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from harnessgrad.loops import eval_side
    source = tmp_path / 'score.py'; source.write_text('before')
    tasks = [{'task_id': 't1'}]
    verifiers = {'t1': {'kind':'command', 'inputs':[{'path':'score.py', 'from':str(source)}]}}
    expected = context.capture(tasks, {}, {}, verifiers, {})
    work = tmp_path / 'workspace'; work.mkdir(); (work / 'sentinel').write_bytes(b'old workspace')
    run = tmp_path / 'run'; run.mkdir(); (run / 'run_meta.json').write_bytes(b'old metadata')
    source.write_text('after')
    monkeypatch.setattr(eval_side.candidate_runtime, 'admit', lambda *a, **k: None)
    monkeypatch.setattr(eval_side.dataset_runtime_identity_mod, 'for_evaluation', lambda *a, **k: None)
    monkeypatch.setattr(eval_side, 'commit_state', lambda *a, **k: pytest.fail('must not commit'))
    args = SimpleNamespace(current_harness_runtime_identity=None, dataset_runtime_identity=None,
                           verification_context=expected, sandbox=True)
    result = eval_side._run_eval_side(work=work, man={}, run_id='same', run_dir=run, tasks=tasks,
        scorable={}, setups={}, verifiers=verifiers, envs={}, side_ids=['t1'], split=None,
        args=args, eval_from=None)
    assert result == 2
    assert (work / 'sentinel').read_bytes() == b'old workspace'
    assert (run / 'run_meta.json').read_bytes() == b'old metadata'
    assert sorted(p.name for p in run.iterdir()) == ['run_meta.json']
