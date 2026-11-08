"""Offline crossing of Console recovery, candidate runtime, checkpoint and H0 reuse.

The dependency builder and artifact reader are doubles: no Docker, model, network,
package installer or real dataset is touched. Source framing and admission are real.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import shutil
import sys
from unittest.mock import Mock

import pytest

from harnessgrad import candidate_runtime, python_harness_runtime, snapshot
from harnessgrad.loops import eval_side
from tools import prepare_h0

ROOT = __import__('pathlib').Path(__file__).resolve().parents[2]


@pytest.fixture
def control(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('subprocess and network operations are forbidden in this offline test')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT', str(tmp_path / 'store'))
    monkeypatch.setenv('HG_CONSOLE_RUNS', str(tmp_path / 'runs'))
    monkeypatch.setenv('HG_CONSOLE_CANDIDATES', str(tmp_path / 'candidates'))
    monkeypatch.setenv('HG_CONSOLE_PLATFORM', str(ROOT))
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'harness.json').write_text(json.dumps({'name': 'fixture', 'version': '1',
        'entrypoint': 'agent.py', 'env_kinds': ['files'], 'runtime': {'kind': 'python',
        'python': f'{sys.version_info.major}.{sys.version_info.minor}', 'requirements': 'requirements.lock'}}))
    (source / 'agent.py').write_text("print('never executed')\n")
    (source / 'requirements.lock').write_text('# synthetic empty lock\n')
    spec = python_harness_runtime.descriptor(source)
    instance = tmp_path / 'artifact' / 'build-fixture'
    venv = instance / 'venv'
    artifact = {'runtime_id': 'a' * 64, 'dependency_key': python_harness_runtime._key(spec),
                'path': str(venv), 'python': str(venv / 'bin/python'),
                'manifest_path': str(instance / 'artifact.json'), 'runtime_paths': [str(venv)]}
    def fake_build(repo, spec, folder, **kwargs):
        instance.mkdir(parents=True, exist_ok=True)
        return artifact, instance
    builder = Mock(side_effect=fake_build)
    monkeypatch.setattr(python_harness_runtime, '_build', builder)
    monkeypatch.setattr(python_harness_runtime, '_published', lambda *a: artifact)
    monkeypatch.setattr(python_harness_runtime, '_read_artifact', lambda *a: artifact)
    original = candidate_runtime.prepare(source)['identity']
    assert builder.call_count == 1
    run = tmp_path / 'runs' / 'synthetic'
    run.mkdir(parents=True)
    point = {'round': 0, 'identity': {'harness_sha': 'a' * 40},
             'harness_runtime_identity': original, 'score': 1.0, 'side': 'train'}
    (run / 'curve.jsonl').write_text(json.dumps(point) + '\n')
    server_spec = importlib.util.spec_from_file_location('managed_recovery_console', ROOT / 'console/server.py')
    server = importlib.util.module_from_spec(server_spec)
    server_spec.loader.exec_module(server)
    return source, original, run, point, server, builder


@pytest.mark.parametrize('route', ['state', 'snapshot'])
def test_recovered_candidate_requires_new_binding_before_h0_checkpoint_or_resume_reuse(control, tmp_path, monkeypatch, route):
    source, original, run, point, server, builder = control
    if route == 'state':
        shutil.copytree(source, run / 'state_after_round0')
    else:
        snapshot.write(run, 0, source, run_id='synthetic', harness_sha='a' * 40)
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob('*') if p.is_file()}
    code, response = server.harness_candidate_from_round('synthetic', 0)
    assert code == 201 and response['source'] == route
    recovered = tmp_path / 'candidates' / response['candidate']
    assert json.loads((recovered / 'harness.json').read_text())['name'] == response['candidate']
    assert python_harness_runtime.source_fingerprint(recovered) != original['source_sha256']
    with pytest.raises(candidate_runtime.RuntimeUnavailable, match='source differs'):
        candidate_runtime.admit(recovered, original)
    # Original checkpoint remains admissible on its own source; the renamed one is not it.
    assert candidate_runtime.checkpoint_binding(run, point) == original
    with pytest.raises(candidate_runtime.RuntimeUnavailable, match='source differs'):
        eval_side._eval_from_record(run.parent, 'synthetic', 0, source=recovered)
    refusal = eval_side._resume_runtime_mismatch(recovered, {}, harness_runtime_identity=original)
    assert 'source differs' in refusal
    new = candidate_runtime.prepare(recovered)['identity']
    assert new['source_sha256'] != original['source_sha256']
    assert new['artifact'] == original['artifact'] and new != original
    assert candidate_runtime.admit(recovered, new) == new
    assert builder.call_count == 1  # Reuse dependency artifact, not the measured source binding.
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob('*') if p.is_file()} == before

    # H0 lookup must not reuse the old measurement for the renamed candidate.
    h0 = tmp_path / 'h0-runs' / 'h0-demo-original'
    h0.mkdir(parents=True)
    old_config = {'dataset': 'demo', 'harness_runtime_identity': original}
    new_config = {'dataset': 'demo', 'harness_runtime_identity': new}
    (h0 / 'run_meta.json').write_text(json.dumps({'h0_configuration': old_config}))
    (h0 / 'h0.json').write_text(json.dumps({'base': {'per_task': {'synthetic': 1.0}}}))
    verify = Mock(return_value={'score': 1.0, 'n_tasks': 1})
    monkeypatch.setattr(prepare_h0, 'verify', verify)
    assert prepare_h0.find_existing(old_config, h0.parent)[0] == h0
    verify.reset_mock()
    assert prepare_h0.find_existing(new_config, h0.parent) is None
    verify.assert_not_called()
