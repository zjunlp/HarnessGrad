"""No-model tests for the first-use checker; all paths and receipts are synthetic."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import check_first_use as check
from test_evidence_export import save_run


def test_console_checks_and_exports_only_local_synthetic_records(tmp_path, monkeypatch):
    runs = tmp_path / 'runs'
    save_run(runs, 'smoke')
    save_run(runs, 'prepared')
    before = check.snapshot(runs)
    original_request = check.request
    checked_delete = []
    def request(port, method, path, body=None):
        if not checked_delete:
            conn = check.http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            try:
                conn.request('DELETE', '/api/submissions/not-a-real-submission')
                response = conn.getresponse()
                assert response.status == 405
                response.read()
                checked_delete.append(True)
            finally:
                conn.close()
        return original_request(port, method, path, body)
    monkeypatch.setattr(check, 'request', request)
    check.check_console(tmp_path, runs)
    assert checked_delete
    assert (tmp_path / 'evidence-export.json').is_file()
    assert (tmp_path / 'console.log').is_file()
    assert check.snapshot(runs) == before


def test_failure_report_is_retained_and_nonzero(tmp_path, monkeypatch, capsys):
    def fail(*args):
        raise check.AcceptanceError('synthetic failure')
    monkeypatch.setattr(check, 'execute', fail)
    assert check.main(['--output-root', str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'failed' and 'synthetic failure' in report['error']
    assert 'does not establish benchmark capability' in report['scope']
    assert json.loads((Path(report['control_directory']) / 'report.json').read_text()) == report


def test_rejects_output_inside_platform(tmp_path):
    with pytest.raises(SystemExit) as exc:
        check.main(['--output-root', str(check.ROOT / 'not-created-first-use')])
    assert exc.value.code == 2
    assert not (check.ROOT / 'not-created-first-use').exists()


def test_default_runtime_store_respects_xdg_cache(tmp_path, monkeypatch):
    monkeypatch.delenv('HG_HARNESS_RUNTIME_ROOT', raising=False)
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    observed = []
    monkeypatch.setattr(check, 'execute', lambda control, runtime: observed.append(runtime))
    assert check.main(['--output-root', str(tmp_path / 'reports')]) == 0
    assert observed == [tmp_path / 'cache/harnessgrad/harness-runtimes']


def test_command_timeout_retains_log(tmp_path):
    log = tmp_path / 'timeout.log'
    with pytest.raises(check.AcceptanceError, match='timed out'):
        check.run_command([sys.executable, '-c', 'import time; print("started", flush=True); time.sleep(10)'],
                          env={}, log=log, timeout=0.2)
    assert b'started' in log.read_bytes()


def test_failed_preparation_is_not_a_pass(tmp_path, monkeypatch):
    from harnessgrad import python_exec_runtime
    monkeypatch.setattr(python_exec_runtime, 'prepare', lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('missing synthetic runtime')))
    with pytest.raises(RuntimeError, match='missing synthetic runtime'):
        check.execute(tmp_path, tmp_path / 'runtime')


@pytest.mark.parametrize('corrupt_duplicate', [False, True])
def test_execution_enforces_isolation_and_preservation(tmp_path, monkeypatch, corrupt_duplicate):
    from harnessgrad import python_exec_runtime
    monkeypatch.setattr(python_exec_runtime, 'prepare', lambda *a, **k: {'installed': False})
    monkeypatch.setenv('XDG_RUNTIME_DIR', '/run/user/test')
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/test/bus')
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy.invalid')
    monkeypatch.setenv('HG_AGENT_API_KEY', 'synthetic-key')
    observed = []
    def fake_run(argv, *, env, log, timeout=check.TIMEOUT):
        assert env['HG_AGENT_BACKEND'] == 'mock'
        assert env['HG_REVIEWED_ENVIRONMENT_ONLY'] == '1'
        assert env['XDG_RUNTIME_DIR'] == '/run/user/test'
        assert env['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/run/user/test/bus'
        assert 'HTTP_PROXY' not in env and 'HG_AGENT_API_KEY' not in env
        assert '--improver' in argv and argv[argv.index('--improver') + 1] == 'none'
        run_id = argv[argv.index('--run-id') + 1]
        run = Path(argv[argv.index('--runs-root') + 1]) / run_id
        observed.append(log.stem)
        if log.stem == 'duplicate':
            if corrupt_duplicate:
                (run / 'run_meta.json').write_text('{}')
            log.write_text('refusing: use a fresh --run-id')
            return 2
        if '--prepare-only' in argv:
            run.mkdir(parents=True)
            (run / 'run_meta.json').write_text('{"prepared": true}')
        else:
            run.mkdir(parents=True, exist_ok=True)
            (run / 'run_meta.json').write_text('{"agent_backend": "mock", "completion": {"status": "complete"}}')
            prep = run / 'harness-preparation/round-0'
            prep.mkdir(parents=True)
            (prep / 'attempt-0001.json').write_text('{"receipt": {"installed": false, "cache_hit": true}}')
            (run / 'curve.jsonl').write_text(json.dumps({'round': 0, 'measured_by_platform': True,
                'score': 0.0, 'n_invalid': 0, 'n_harness_failed': 0,
                'per_task': dict.fromkeys(['t01', 't02', 't03', 't04'], 0)}) + '\n')
            (run / 'state_after_round0').mkdir()
        log.write_text('synthetic')
        return 0
    monkeypatch.setattr(check, 'run_command', fake_run)
    monkeypatch.setattr(check, 'check_console', lambda *a: observed.append('console'))
    if corrupt_duplicate:
        with pytest.raises(check.AcceptanceError, match='changed the existing record'):
            check.execute(tmp_path, tmp_path / 'runtime')
        assert observed == ['smoke', 'duplicate']
    else:
        check.execute(tmp_path, tmp_path / 'runtime')
        assert observed == ['smoke', 'duplicate', 'prepare', 'consume', 'console']
