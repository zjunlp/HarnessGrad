"""Changed operator limits cannot overwrite or mix an interrupted run's record."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from driver import ResumeRefused, _resume_train_state
from harnessgrad.loops.eval_side import _resume_mismatch, _run_eval_side
from harnessgrad.resume_policy import resume_policy_mismatch

ROOT = Path(__file__).resolve().parents[2]


def metadata(root, policy=None, *, legacy=False):
    root.mkdir(parents=True, exist_ok=True)
    value = {'evaluated_from': {'run_id': 'source', 'round': 0}, 'env': {'pool': 'demo'}}
    if not legacy:
        value['config'] = {'runtime': {'evaluation_limits': policy}}
    path = root / 'run_meta.json'
    path.write_text(json.dumps(value, indent=2) + '\n')
    return path


@pytest.mark.parametrize('legacy', [True, False])
@pytest.mark.parametrize('policy', [None, {}, {'judge': {}}, {'datasets': {'strongreject': {'judge': {}}}}])
def test_legacy_and_empty_policy_are_compatible(tmp_path, monkeypatch, legacy, policy):
    metadata(tmp_path, legacy=legacy)
    if policy is None:
        monkeypatch.delenv('HG_EVAL_LIMITS', raising=False)
    else:
        monkeypatch.setenv('HG_EVAL_LIMITS', json.dumps(policy))
    assert resume_policy_mismatch(tmp_path) is None


@pytest.mark.parametrize('new', [{}, {'judge': {'max_output_tokens': 4096}},
                               {'judge': {'max_output_tokens': None}}])
def test_changed_or_removed_policy_is_refused_without_writes(tmp_path, monkeypatch, new):
    path = metadata(tmp_path, {'judge': {'max_output_tokens': 1024}})
    before = path.read_bytes()
    monkeypatch.setenv('HG_EVAL_LIMITS', json.dumps(new))
    assert 'HG_EVAL_LIMITS differs' in resume_policy_mismatch(tmp_path)
    assert path.read_bytes() == before


def test_explicit_null_is_not_an_empty_policy(tmp_path, monkeypatch):
    metadata(tmp_path, legacy=True)
    monkeypatch.setenv('HG_EVAL_LIMITS', '{"judge":{"timeout_s":null}}')
    assert resume_policy_mismatch(tmp_path)


def test_equal_nonempty_policy_accepts_different_json_order(tmp_path, monkeypatch):
    metadata(tmp_path, {'judge': {'max_output_tokens': 2048, 'max_retries': 1}})
    monkeypatch.setenv('HG_EVAL_LIMITS', '{"judge":{"max_retries":1,"max_output_tokens":2048}}')
    assert resume_policy_mismatch(tmp_path) is None


@pytest.mark.parametrize('contents', ['{', '[]', '{"config":"invalid"}',
                                    '{"config":{"runtime":{"evaluation_limits":{"bad":1}}}}'])
def test_malformed_old_snapshot_refuses(tmp_path, monkeypatch, contents):
    path = tmp_path / 'run_meta.json'
    path.write_text(contents)
    monkeypatch.delenv('HG_EVAL_LIMITS', raising=False)
    assert 'cannot verify HG_EVAL_LIMITS' in resume_policy_mismatch(tmp_path)
    assert path.read_text() == contents


def test_direct_train_and_eval_entries_refuse_before_state_changes(tmp_path, monkeypatch):
    path = metadata(tmp_path, {'judge': {'max_output_tokens': 1024}})
    before = path.read_bytes()
    monkeypatch.setenv('HG_EVAL_LIMITS', '{"judge":{"max_output_tokens":4096}}')
    args = SimpleNamespace(resume=True, from_run='source', from_round=0)
    with pytest.raises(ResumeRefused, match='HG_EVAL_LIMITS'):
        _resume_train_state(args, tmp_path, None, tmp_path / 'work')
    assert 'HG_EVAL_LIMITS' in _resume_mismatch(tmp_path, args, 'demo', 'same')
    assert _run_eval_side(work=tmp_path / 'work', man={}, run_id='resume', run_dir=tmp_path,
                          tasks=[], scorable={}, setups={}, verifiers={}, envs={},
                          side_ids=[], split=None, args=args, eval_from=None) == 2
    assert not (tmp_path / 'work').exists()
    assert path.read_bytes() == before


@pytest.mark.parametrize('side', ['train', 'eval'])
def test_driver_refuses_before_overwriting_metadata(tmp_path, side):
    run_dir = tmp_path / 'runs/resume'
    path = metadata(run_dir, {'judge': {'max_output_tokens': 1024}})
    before = path.read_bytes()
    source = tmp_path / 'runs/source/state_after_round0'
    source.mkdir(parents=True)
    command = [sys.executable, 'driver.py', '--harness', 'loop_plain', '--dataset', 'demo',
               '--sampling', 'all', '--rounds', '0', '--resume', '--side', side,
               '--run-id', 'resume', '--runs-root', str(tmp_path / 'runs'),
               '--work-root', str(tmp_path / 'work')]
    if side == 'eval':
        command += ['--from-run', 'source', '--from-round', '0']
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30,
                            env={**os.environ, 'HG_AGENT_BACKEND': 'mock',
                                 'HG_EVAL_LIMITS': '{"judge":{"max_output_tokens":4096}}'})
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'HG_EVAL_LIMITS differs' in result.stdout + result.stderr
    assert path.read_bytes() == before
    assert not (run_dir / 'curve.jsonl').exists()
    assert not (tmp_path / 'work').exists()
