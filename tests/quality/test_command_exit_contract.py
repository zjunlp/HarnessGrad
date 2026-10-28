"""Failed scorers retain diagnostics without publishing rewards."""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys

import pytest

from data.scaffolds.runtime_extensions import validate_result
from eval.verification import verifier


@pytest.mark.parametrize('required,code,score', [
    (None, 3, 1), (False, 3, 1), (True, 3, 1), (True, 78, 0),
    (True, 0, 0), (True, 0, 1),
])
@pytest.mark.parametrize('native', [False, True])
def test_command_completion_uses_process_exit_before_reward(tmp_path, monkeypatch, required, code, score, native):
    contract = {'version': 1, 'score_range': [0, 1]}
    if required is not None:
        contract['require_exit_zero'] = required
    spec = {'kind': 'command', 'argv': [sys.executable, 'check.py'],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'result_contract': contract,
            'inputs': [{'path': 'check.py', 'content':
                "from pathlib import Path\nimport sys\n"
                f"Path('verifier/reward.txt').write_text({str(score)!r})\n"
                "Path('verifier/evidence.json').write_text('{\"measured\":true}')\n"
                "Path('verifier').chmod(0o555)\n"
                f"sys.exit({code})\n"}]}
    class Native:
        def run(self, argv, *, workdir, **kwargs):
            return subprocess.run(argv, cwd=workdir, text=True, capture_output=True)
    try:
        result = verifier._verify(tmp_path, spec, '', sandbox=False, timeout_s=5,
                                  **({'native_runtime': Native()} if native else {}))
        # The output remains present and undeletable by its directory permissions;
        # refusing the score must not depend on the scorer deleting it successfully.
        assert (tmp_path / 'verifier/reward.txt').read_text() == str(score)
        if required and code:
            assert result['score'] is None and result['outcome'] == 'unmeasured'
            assert result['exit_code'] == code and 'requires exit 0' in result['unmeasured']
            assert 'evidence' not in result, 'nonzero exit must not expose a successful measurement'
        else:
            assert result['score'] == score
            assert result['evidence'] == {'measured': True}
    finally:
        (tmp_path / 'verifier').chmod(0o755)


@pytest.mark.parametrize('route', ['host', 'exec', 'snapshot'])
@pytest.mark.parametrize('output', [
    'error', 'missing', 'malformed', 'array', 'symlink', 'stale', 'measured', 'falsey',
    'wrong-directory',
])
def test_failed_command_keeps_only_fresh_explicit_nonmeasurement(tmp_path, monkeypatch, route, output):
    work = tmp_path / 'task'
    work.mkdir()
    error = {'measured': False, 'reason': 'scorer_error', 'detail': 'synthetic scorer failure'}
    # These actor-owned files must not be adopted if this check produces nothing.
    for directory in (work / 'verifier', work / '.verifier'):
        directory.mkdir()
        if output == 'stale':
            (directory / 'evidence.json').write_text(json.dumps(error))
    external = tmp_path / 'external.json'
    external.write_text(json.dumps(error))
    spec = {'kind': 'command', 'argv': ['synthetic-check'],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
    if output == 'wrong-directory':
        spec['evidence_file'] = '/other/evidence.json'

    def run(directory):
        # Even a tempting reward must never be read after strict completion failed.
        (directory / 'reward.txt').write_text('1')
        evidence = directory / 'evidence.json'
        if output in ('error', 'wrong-directory'):
            evidence.write_text(json.dumps(error))
        elif output == 'malformed':
            evidence.write_text('{')
        elif output == 'array':
            evidence.write_text(json.dumps([error]))
        elif output == 'symlink':
            evidence.symlink_to(external)
        elif output == 'measured':
            evidence.write_text('{"measured": true, "score": 1}')
        elif output == 'falsey':
            evidence.write_text('{"measured": 0}')
        return subprocess.CompletedProcess(spec['argv'], 3, '', 'check failed')

    original_read = Path.read_text

    def read(path, *args, **kwargs):
        assert path.name != 'reward.txt', 'failed command reward must not be read'
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(verifier.container_mod, 'cp_out',
                        lambda *a, **kw: pytest.fail('failed check must not read actor snapshot files'))
    if route == 'host':
        monkeypatch.setattr(verifier.subprocess, 'run', lambda *a, **kw: run(work / 'verifier'))
        result = verifier._verify(work, spec, '', sandbox=False, timeout_s=5)
    elif route == 'exec':
        def container_run(*args, **kwargs):
            mount = next(value for value in kwargs['extra_mounts']
                         if value.startswith('type=bind,src=') and value.endswith(',dst=/verifier'))
            directory = Path(mount.split('src=', 1)[1].split(',dst=', 1)[0])
            assert directory not in (work / 'verifier', work / '.verifier')
            return run(directory)
        monkeypatch.setattr(verifier.container_mod, 'run', container_run)
        result = verifier._verify(work, spec, '', sandbox=True, timeout_s=5,
                                  env={'kind': 'exec', 'image': 'synthetic', 'workdir': '/app'})
    else:
        staging = tmp_path / 'staging'
        if output == 'stale':
            stale = staging / 'verdict'
            stale.mkdir(parents=True)
            (stale / 'evidence.json').write_text(json.dumps(error))
        monkeypatch.setattr(verifier.container_mod, 'require_snapshot_identity', lambda *a: None)
        monkeypatch.setattr(verifier.container_mod, 'require_snapshot_container', lambda *a: None)
        monkeypatch.setattr(verifier.container_mod, 'snapshot_startup',
                            lambda: nullcontext((['synthetic-idle'], ())))
        monkeypatch.setattr(verifier.container_mod, 'create', lambda *a, **kw: None)
        monkeypatch.setattr(verifier.container_mod, 'start', lambda *a: None)
        monkeypatch.setattr(verifier.container_mod, 'remove_container', lambda *a: None)
        monkeypatch.setattr(verifier.container_mod, 'exec_in', lambda *a, **kw: run(staging / 'verdict'))
        result = verifier._verify_container_state('synthetic', spec, task_mount='/app',
            staging=staging, timeout_s=5, run_id='control', task_id='task', expected_snapshot='synthetic')
    assert result['score'] is None and result['passed'] is False
    assert result['outcome'] == 'unmeasured' and result['exit_code'] == 3
    if output == 'error':
        assert result['evidence'] == error
    else:
        assert 'evidence' not in result


def test_failed_host_check_rechecks_evidence_parent(tmp_path, monkeypatch):
    output = tmp_path / 'verifier'
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'evidence.json').write_text('{"measured": false, "detail": "outside"}')
    spec = {'kind': 'command', 'argv': ['synthetic-check'],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}

    def run(*args, **kwargs):
        output.rmdir()
        output.symlink_to(external, target_is_directory=True)
        return subprocess.CompletedProcess(spec['argv'], 3, '', '')

    monkeypatch.setattr(verifier.subprocess, 'run', run)
    result = verifier._verify(tmp_path, spec, '', sandbox=False, timeout_s=5)
    assert result['score'] is None and result['exit_code'] == 3
    assert 'evidence' not in result


@pytest.mark.parametrize('value', [0, 1, 'true', None, []])
def test_completion_flag_requires_boolean(value):
    with pytest.raises(ValueError, match='must be boolean'):
        validate_result({'version': 1, 'score_range': [0, 1], 'require_exit_zero': value})
