"""Explicit submission ceilings and bounded log retention have different semantics."""
import base64
from copy import deepcopy
import sys

import pytest

from data.scaffolds.runtime_extensions import validate_container_runtime, validate_workers
from eval.containers import observed, observed_helper
from eval.run.artifacts import collect


@pytest.mark.parametrize('collector', ['native', 'container'])
def test_null_artifact_limit_accepts_large_submission_and_finite_limit_still_checks(tmp_path, monkeypatch, collector):
    monkeypatch.setattr(observed_helper, 'actors', lambda: [])
    payload = b'p' * ((16 << 20) + 1)
    (tmp_path / 'patch').write_bytes(payload)
    read = collect if collector == 'native' else lambda root, paths, **kw: observed_helper.collect(root, paths, kw['max_bytes'])
    actual = read(tmp_path, ['patch'], max_bytes=None)['patch']
    assert actual['bytes'] == len(payload)
    assert base64.b64decode(actual['base64']) == payload
    with pytest.raises(ValueError, match='byte limit'):
        read(tmp_path, ['patch'], max_bytes=16 << 20)


@pytest.mark.parametrize('collector', ['native', 'container'])
def test_finite_artifact_limit_applies_to_aggregate_not_each_file(tmp_path, monkeypatch, collector):
    monkeypatch.setattr(observed_helper, 'actors', lambda: [])
    for name in ('one', 'two'):
        (tmp_path / name).write_bytes(b'123')
    read = collect if collector == 'native' else lambda root, paths, **kw: observed_helper.collect(root, paths, kw['max_bytes'])
    assert len(read(tmp_path, ['one', 'two'], max_bytes=6)) == 2
    with pytest.raises(ValueError, match='byte limit'):
        read(tmp_path, ['one', 'two'], max_bytes=5)


def test_container_file_logs_are_explicitly_truncated_without_rejecting_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(observed_helper, 'actors', lambda: [])
    (tmp_path / 'answer').write_bytes(b'123456')
    (tmp_path / 'trace').write_bytes(b'789')
    logs = observed_helper.collect(tmp_path, ['answer', 'trace'], 4, truncate=True)
    assert base64.b64decode(logs['answer']['base64']) == b'1234'
    assert logs['answer']['source_bytes'] == 6 and logs['answer']['truncated']
    assert base64.b64decode(logs['trace']['base64']) == b''
    assert logs['trace']['source_bytes'] == 3 and logs['trace']['truncated']


def test_console_truncation_drains_both_pipes_and_preserves_real_exit(tmp_path):
    marker = tmp_path / 'completed'
    script = ('import sys,pathlib;sys.stdout.write("a"*200000);'
              'sys.stderr.write("b"*1100000);'
              f'pathlib.Path({str(marker)!r}).write_text("completed");sys.exit(7)')
    result = observed.bounded([sys.executable, '-c', script], limit=1024, truncate=True)
    assert result.returncode == 7 and marker.read_text() == 'completed'
    assert len(result.stdout) == 1024 and len(result.stderr) == 1 << 20
    assert result.output_retention == {
        'stdout': {'source_bytes': 200000, 'bytes': 1024, 'truncated': True},
        'stderr': {'source_bytes': 1100000, 'bytes': 1 << 20, 'truncated': True}}


def test_structured_exports_keep_declared_limit_and_allow_explicit_null():
    argv = [sys.executable, '-c', 'print("a"*10000)']
    with pytest.raises(RuntimeError, match='byte limit'):
        observed.bounded(argv, limit=1024)
    assert len(observed.bounded(argv, limit=None).stdout) == 10001


@pytest.mark.parametrize('truncated', [True, False, None])
def test_retained_trace_prefix_cannot_claim_final_usage(truncated):
    from eval.run.cooperative import _worker_usage
    result = {'trace': '{"usage":{"input_tokens":1}}\n{"usage":{"input_tokens":2}}\n'}
    if truncated is not None:
        result['runtime_services'] = {'container': {'log_files': {
            'trace.jsonl': {'truncated': truncated, 'source_bytes': 1000, 'bytes': 100}}}}
    assert _worker_usage(result) == (None if truncated else {'input_tokens': 2})


def workers(**fields):
    return [dict(id=name, goal='fixture', artifacts=['patch'], **deepcopy(fields)) for name in ('a', 'b')]


@pytest.mark.parametrize('value', [None, 100, 32 << 20])
def test_worker_submission_limit_is_explicit_nullable(value):
    validate_workers(workers(artifact_max_bytes=value))


@pytest.mark.parametrize('value', [False, 0, -1, '16', 1.2])
def test_invalid_worker_submission_limits_rejected(value):
    with pytest.raises(ValueError, match='artifact_max_bytes'):
        validate_workers(workers(artifact_max_bytes=value))


def test_worker_model_limits_allow_null_but_reject_unknown_or_invalid_settings():
    fields = ('max_connections', 'request_max_bytes', 'response_max_bytes', 'header_timeout_s')
    validate_workers(workers(model_route=dict.fromkeys(fields)))
    for field in fields:
        with pytest.raises(ValueError, match='model_route'):
            validate_workers(workers(model_route={field: False}))
    with pytest.raises(ValueError, match='unsupported'):
        validate_workers(workers(model_route={'unknown': 1}))


def test_explicit_system_scratch_quotas_are_validated_and_unrelated_paths_rejected():
    filesystems = {path: {'bytes': 128 << 20, 'inodes': 32768}
                   for path in ('/workspace', '/tmp', '/dev', '/dev/shm')}
    spec = dict(kind='exec', backend='observed-v1', image='sha256:' + '0' * 64, filesystems=filesystems)
    assert validate_container_runtime(spec) == '/workspace'
    runtime = observed.Runtime(spec, binding={}, timeout_s=1)
    try:
        assert runtime.filesystems == filesystems
    finally:
        runtime.close()
    assert validate_container_runtime({**spec, 'cpus': 128}) == '/workspace'
    filesystems['/other'] = {'bytes': 4096, 'inodes': 4}
    with pytest.raises(ValueError, match='filesystems'):
        validate_container_runtime(spec)


@pytest.mark.parametrize('limit', [None, 1024, 8 << 20])
def test_observed_result_export_accepts_explicit_nullable_limit(limit):
    from data.scaffolds.registry import _check_verifier
    spec = dict(kind='command', argv=['true'], result_max_bytes=limit,
        reward_file='/verifier/reward.txt', result_contract={'version': 1, 'score_range': [0, 1]},
        container_runtime=dict(kind='exec', backend='observed-v1', image='sha256:' + '0' * 64,
            filesystems={'/workspace': {'bytes': 4096, 'inodes': 4}}))
    _check_verifier(spec, 'fixture', 'fixture')
    spec.pop('container_runtime')
    with pytest.raises(ValueError, match='requires container_runtime'):
        _check_verifier(spec, 'fixture', 'fixture')
