"""Offline managed-worker admission controls; never invokes Docker or judges."""
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from data.scaffolds.native_research import special_runtime, swe_native, harvey_native
from harnessgrad import dataset_runtime


def test_no_platform_or_operator_interpreter_fallback(monkeypatch):
    monkeypatch.setenv('HG_SWE_PYTHON', sys.executable)
    def absent(name):
        raise RuntimeError('prepare --runtime execution')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', absent)
    with pytest.raises(RuntimeError, match='prepare'):
        swe_native.python()


def test_official_admits_exact_selected_binding_before_spawn(monkeypatch):
    selected = {'runtime_id': 'selected'}
    seen = []
    monkeypatch.setattr(special_runtime, 'admit', lambda x: seen.append(x) or '/managed/python')
    def launch(argv, **kwargs):
        assert seen == [selected]
        assert argv[0] == '/managed/python'
        assert argv[1:3] == ['-I', '-B']
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setattr(swe_native.subprocess, 'run', launch)
    assert swe_native.official('check', selected_binding=selected) == {}


def test_code_drift_prevents_worker_launch(monkeypatch):
    monkeypatch.setattr(swe_native.subprocess, 'run', lambda *a, **k: pytest.fail('launched'))
    with pytest.raises(ValueError, match='source identity changed'):
        swe_native.official('check', selected_binding={'runtime_id': 'x'}, expected_identity={})


def test_harvey_wheel_must_match_fixed_release_digest(tmp_path, monkeypatch):
    wheel = tmp_path/'foreign.whl'
    wheel.write_bytes(b'not the official release')
    monkeypatch.setenv('HG_JUDGE_HARVEY_WHEEL', str(wheel))
    with pytest.raises(ValueError, match='digest mismatch'):
        special_runtime.harvey_source()


def test_internal_identity_projection_excludes_host_paths_and_private_case_digest(tmp_path):
    module = tmp_path/'worker.py';module.write_text('pass')
    secret_digest = hashlib.sha256(b'private-rubric').hexdigest()
    selected = {'runtime_id': 'a'*64, 'python': '/private/host/python',
                'manifest_path': '/private/host/artifact.json'}
    internal = special_runtime.fingerprint(selected, {'worker': module})
    public = special_runtime.public_harvey_runtime({
        'execution_identity': internal, 'python': '/private/host/python',
        'pandoc': '/private/host/pandoc', 'lab_core': '1.2.0',
        'private_context_sha256': secret_digest})
    import json
    text = json.dumps(public)
    assert '/private/host' not in text
    assert secret_digest not in text
    assert 'private_context_sha256' not in text
    assert public['execution_identity']['runtime_id'] == 'a'*64
    assert 'binding' not in public['execution_identity']
    from harnessgrad.channel import _stage_task_context
    _stage_task_context(tmp_path, [{'task_id': 'control', 'goal': 'public goal'}],
                        {}, {}, {'verdicts': {'control': {'evidence': {'native_runtime': public}}}}, ['control'])
    staged = ''.join(p.read_text() for p in (tmp_path/'_harnessgrad/tasks').rglob('*') if p.is_file())
    assert secret_digest not in staged
    assert '/private/host' not in staged


def test_missing_swe_artifact_is_unmeasured_before_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(special_runtime, 'binding', lambda name: (_ for _ in ()).throw(RuntimeError('missing artifact')))
    monkeypatch.setattr(swe_native, 'selected', lambda *a, **kw: pytest.fail('Docker admission reached'))
    result = swe_native.run({}, tmp_path, task_id='control', run_id='r', trial_id='t', timeout_s=10)
    assert result['outcome'] == 'unmeasured'
    assert result['score'] is None
    assert result['evidence']['measured'] is False
