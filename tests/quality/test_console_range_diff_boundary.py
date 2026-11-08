"""Real GNU diff and HTTP controls using only authored temporary canaries."""
import http.client
import json
from pathlib import Path
import subprocess
import threading

import pytest

from console import server
from console.run_query import RunQueryService


@pytest.fixture
def states(tmp_path):
    run = tmp_path/'runs'/'authored-run'
    a, b = run/'state_after_round0', run/'state_after_round1'
    a.mkdir(parents=True); b.mkdir()
    outside = tmp_path/'outside-runs'; outside.mkdir()
    (outside/'fixture.txt').write_text('AUTHORED_EXTERNAL_CANARY_73bd0fa1\n')
    return run, a, b, outside


def query(run):
    return RunQueryService().run_range_diff('authored-run', 0, 1,
        lookup=lambda name: run if name == 'authored-run' else None)


def http_query(run, monkeypatch, run_id="authored-run"):
    monkeypatch.setattr(server, 'RUNS', run.parent)
    httpd = server.ConsoleServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    try:
        connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
        try:
            connection.request('GET', f'/api/runs/{run_id}/range-diff?from=0&to=1')
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    finally:
        httpd.shutdown(); thread.join(timeout=5); httpd.server_close()


@pytest.mark.parametrize('which', [0, 1])
def test_external_snapshot_root_is_explicitly_refused_before_diff(states, monkeypatch, which):
    run, a, b, outside = states
    root = (a,b)[which]; root.rmdir(); root.symlink_to(outside, target_is_directory=True)
    def forbidden(*args, **kwargs):
        pytest.fail('unsafe snapshot must not launch diff')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    result = query(run)
    assert result['error'] == 'unsafe-state'
    status, payload = http_query(run, monkeypatch)
    assert status == 200 and payload == result
    assert 'AUTHORED_EXTERNAL_CANARY' not in json.dumps(payload)


@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_nested_external_links_are_not_dereferenced_by_real_diff_or_http(states, monkeypatch, kind):
    run, a, b, outside = states
    if kind == 'file':
        (a/'fixture.txt').symlink_to(outside/'fixture.txt')
        (b/'fixture.txt').write_text('AUTHORED_INSIDE_CONTENT\n')
    else:
        (a/'child').symlink_to(outside, target_is_directory=True)
        (b/'child').mkdir(); (b/'child/fixture.txt').write_text('AUTHORED_INSIDE_CONTENT\n')
    direct = query(run)
    assert 'error' not in direct and direct['diff']
    assert 'AUTHORED_EXTERNAL_CANARY' not in json.dumps(direct)
    assert direct['added'] == direct['removed'] == 0
    status, payload = http_query(run, monkeypatch)
    assert status == 200 and payload == direct


def test_normal_directory_diff_files_and_counts_remain_equal_to_original_gnu_commands(states, monkeypatch):
    run, a, b, outside = states
    (a/'fixture.txt').write_text('old authored line\nshared\n')
    (b/'fixture.txt').write_text('new authored line\nshared\nnew second line\n')
    (a/'__pycache__').mkdir(); (a/'__pycache__/hidden').write_text('ignored')
    original = subprocess.run(['diff','-ruN','-x','__pycache__','-x','*.pyc',a.name,b.name],
                              cwd=run,capture_output=True,text=True,errors='replace',timeout=5)
    brief = subprocess.run(['diff','-ruN','--brief','-x','__pycache__','-x','*.pyc',a.name,b.name],
                           cwd=run,capture_output=True,text=True,errors='replace',timeout=5)
    direct = query(run)
    # GNU diff records argv in each generated header; patch content stays exact.
    assert direct['diff'].replace('diff -ruN --no-dereference ', 'diff -ruN ') == original.stdout
    assert direct['files'] == [line.split(' and ')[0].split('/',1)[-1]
                              for line in brief.stdout.splitlines() if line.strip()]
    assert direct['added'] == 2 and direct['removed'] == 1
    status, payload = http_query(run, monkeypatch)
    assert status == 200 and payload == direct


def test_denied_lookup_never_stats_or_invokes_diff(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs): pytest.fail('unauthorized filesystem/process access')
    monkeypatch.setattr(Path, 'is_dir', forbidden)
    monkeypatch.setattr(Path, 'is_symlink', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    result = RunQueryService().run_range_diff('denied',0,1,lookup=lambda name: calls.append(name))
    assert result == {'error':'run-not-found','detail':'denied'}
    assert calls == ['denied']


@pytest.mark.parametrize('timeout_call', [1,2])
def test_timeout_is_explicit_and_not_an_empty_diff(states, monkeypatch, timeout_call):
    run, a, b, outside = states
    calls=[]
    def timeout(argv, **kwargs):
        calls.append(argv)
        assert kwargs['timeout'] == 30 and '--no-dereference' in argv
        if len(calls) == timeout_call: raise subprocess.TimeoutExpired(argv,30)
        return subprocess.CompletedProcess(argv,0,'','')
    monkeypatch.setattr(subprocess,'run',timeout)
    assert query(run)['error'] == 'diff-timeout'
    assert len(calls) == timeout_call


def test_inside_snapshot_root_alias_is_also_explicitly_unsupported(states):
    run, a, b, outside = states
    a.rmdir(); a.symlink_to(b, target_is_directory=True)
    assert query(run)['error'] == 'unsafe-state'


def test_root_swapped_to_external_symlink_before_diff_does_not_read_target(states, monkeypatch):
    run, a, b, outside = states
    (b/'fixture.txt').write_text('AUTHORED_INSIDE_CONTENT\n')
    original_run = subprocess.run
    calls=[]
    def swap_then_run(argv, **kwargs):
        if not calls:
            a.rmdir(); a.symlink_to(outside, target_is_directory=True)
        calls.append(argv)
        return original_run(argv, **kwargs)
    monkeypatch.setattr(subprocess,'run',swap_then_run)
    result = query(run)
    assert len(calls) == 2
    assert all('--no-dereference' in argv for argv in calls)
    assert 'AUTHORED_EXTERNAL_CANARY' not in json.dumps(result)


def test_denied_http_run_never_invokes_diff(states, monkeypatch):
    run, a, b, outside = states
    def forbidden(*args, **kwargs): pytest.fail('denied run must not launch diff')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    status, payload = http_query(run, monkeypatch, run_id='denied')
    assert status == 200 and payload == {'error':'run-not-found','detail':'denied'}
