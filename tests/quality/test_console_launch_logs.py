"""Console launch logs are bounded, redacted and cannot escape their root."""
import os
import sys

from console import http_routes, launch_logs, launch_worker, server


def test_read_launch_log_redacts_configured_secrets_and_endpoints(tmp_path):
    (tmp_path / 'console-test.log').write_text(
        'failed with key secret123 for https://example.invalid/private\n')
    got = launch_logs.read('console-test', directory=tmp_path,
                           environment={}, settings={'task': {'api_key': 'secret123'}})
    assert got == {'text': 'failed with key *** for [endpoint]\n', 'truncated': False}


def test_only_bounded_ordinary_launch_log_can_be_read(tmp_path):
    (tmp_path / 'console-big.log').write_text('a' * (launch_logs.MAX_BYTES + 50))
    got = launch_logs.read('console-big', directory=tmp_path, environment={}, settings={})
    assert got['truncated'] and len(got['text']) == launch_logs.MAX_BYTES
    (tmp_path / 'console-link.log').symlink_to(tmp_path / 'console-big.log')
    os.mkfifo(tmp_path / 'console-pipe.log')
    for run_id in ('../console-big', 'console-link', 'console-pipe', 'not-console'):
        assert launch_logs.read(run_id, directory=tmp_path, environment={}, settings={}) is None


def test_start_without_run_directory_is_listed_without_inventing_a_score(tmp_path, monkeypatch):
    logs = tmp_path / 'logs'; logs.mkdir()
    runs = tmp_path / 'runs'; runs.mkdir()
    (logs / 'console-early.log').write_text('startup failed\n')
    monkeypatch.setattr(server, 'RUNS', runs)
    monkeypatch.setattr(server, 'LAUNCH_DIR', logs)
    monkeypatch.setattr(server, 'live_drivers', lambda: {})
    row = server.all_rows(force=True)[0]
    assert row['id'] == 'console-early' and row['curve_state'] == 'missing'
    assert row['status'] == 'empty' and row.get('last') is None
    detail = server.api_run('console-early')
    assert detail['row']['orphan_launch'] is True and detail['rounds'] == []
    assert server.api_run('console-absent') is None


def test_log_route_exposes_only_the_selected_console_record(tmp_path, monkeypatch):
    from types import SimpleNamespace
    logs = tmp_path / 'logs'; logs.mkdir()
    (logs / 'console-one.log').write_text('result\n')
    monkeypatch.setattr(server, 'LAUNCH_DIR', logs)
    monkeypatch.setattr(server, 'load_settings', lambda: {})
    handler = SimpleNamespace(_send=lambda data: (200, data),
                              _error=lambda text, code: (code, text))
    ports = http_routes.LaunchLogGetEndpoints(read_log=server.launch_log)
    assert http_routes.launch_log_get(handler, ['', 'runs', 'console-one', 'launch-log'], {}, '', ports) == (
        200, {'text': 'result\n', 'truncated': False})
    assert http_routes.launch_log_get(handler, ['', 'runs', '../console-one', 'launch-log'], {}, '', ports)[0] == 404


def test_exit_receipt_marks_failure_without_inventing_a_score(tmp_path, monkeypatch):
    logs = tmp_path / 'logs'; logs.mkdir()
    runs = tmp_path / 'runs'; runs.mkdir()
    (logs / 'console-failed.log').write_text('failed before run allocation')
    assert launch_worker.main([str(logs), 'console-failed', '--', sys.executable, '-c', 'raise SystemExit(7)']) == 7
    monkeypatch.setattr(server, 'RUNS', runs)
    monkeypatch.setattr(server, 'LAUNCH_DIR', logs)
    monkeypatch.setattr(server, 'live_drivers', lambda: {})
    row = server.all_rows(force=True)[0]
    assert row['status'] == 'failed' and row['launch_exit_code'] == 7
    assert row['curve_state'] == 'missing' and row.get('last') is None
    assert server.api_run('console-failed')['row']['status'] == 'failed'


def test_old_console_curve_without_exit_receipt_has_unknown_status(tmp_path):
    row = {'id': 'console-old', 'status': 'done', 'last': .5}
    assert launch_logs.with_exit(row, tmp_path) == {**row, 'status': 'unknown'}
    assert launch_logs.with_exit({'id': 'historic-study', 'status': 'done'}, tmp_path)['status'] == 'done'


def test_worker_redacts_unsaved_launch_credentials_before_writing_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('HG_AGENT_API_KEY', 'one-off-secret')
    command = "import os; print(os.environ['HG_AGENT_API_KEY']); print('https://example.invalid/private')"
    assert launch_worker.main([str(tmp_path), 'console-redact', '--', sys.executable, '-c', command]) == 0
    output = capsys.readouterr().out
    assert 'one-off-secret' not in output and 'example.invalid' not in output
    assert '***' in output and '[endpoint]' in output


def test_worker_redacts_credentials_containing_a_line_break(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('HG_AGENT_API_KEY', 'first-secret\nsecond-secret')
    command = "import os; print(os.environ['HG_AGENT_API_KEY'])"
    assert launch_worker.main([str(tmp_path), 'console-lines', '--', sys.executable, '-c', command]) == 0
    output = capsys.readouterr().out
    assert 'first-secret' not in output and 'second-secret' not in output
