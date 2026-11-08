"""Synthetic controls at the query/cache/authorization composition boundary."""
import concurrent.futures
import http.client
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from console import comparison, record_reader, server, snapshots
from console.run_query import RunQueryService


def test_list_cache_keeps_original_ttl_force_root_and_live_refresh(tmp_path):
    roots = [tmp_path / name for name in ('first', 'second')]
    for root in roots:
        (root / root.name).mkdir(parents=True)
    calls, observations = [], []
    state = {'time': 10., 'live': {}}
    def project(path, live):
        calls.append(path)
        return {'id': path.name, 'kind': 'unknown', 'mtime_s': 0., 'live': live}
    def live():
        observations.append(dict(state['live']))
        return dict(state['live'])
    service = RunQueryService(reader=SimpleNamespace(run_row=project))
    def query(root, force=False):
        return service.all_rows(root, live_drivers=live, force=force, clock=lambda: state['time'], ttl=2.)
    first = query(roots[0])
    state['live'] = {'pid': 12}
    state['time'] = 11.
    # Root/live are refreshed at the same force/TTL boundary as the original list.
    assert query(roots[1]) is first and len(observations) == 1
    second = query(roots[1], force=True)
    assert second[0]['id'] == 'second' and second[0]['live'] == {'pid': 12}
    state['time'] = 13.
    assert query(roots[0])[0]['id'] == 'first'
    assert len(calls) == len(observations) == 3
    independent = RunQueryService(reader=SimpleNamespace(run_row=project))
    assert independent.cache is not service.cache
    assert independent.all_rows(roots[1], live_drivers=live, clock=lambda: 20.)[0]['id'] == 'second'


def test_list_cache_serializes_a_single_refresh(tmp_path):
    (tmp_path / 'run').mkdir()
    observations = []
    def live():
        observations.append(True)
        return {}
    service = RunQueryService(reader=SimpleNamespace(run_row=lambda path, live:
        {'id': path.name, 'mtime_s': 1.}))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
        rows = list(workers.map(lambda _: service.all_rows(tmp_path, live_drivers=live, clock=lambda: 10.), range(8)))
    assert all(row is rows[0] for row in rows)
    assert observations == [True]


def test_query_authorization_and_resolved_file_containment(tmp_path):
    run = tmp_path / 'run'; state = run / 'state_after_round0'; state.mkdir(parents=True)
    outside = tmp_path / 'private.txt'; outside.write_text('must not expose')
    (state / 'escape.py').symlink_to(outside)
    (state / 'ok.py').write_text('value=1\n')
    lookups = []
    def lookup(name):
        lookups.append(name)
        return run if name == 'allowed' else None
    service = RunQueryService()
    assert service.api_run('denied', lookup=lookup, live_drivers=lambda: pytest.fail('unauthorized live read')) is None
    assert service.api_file('allowed', 0, 'escape.py', lookup=lookup, max_file_bytes=400000) is None
    assert service.api_file('allowed', 0, '../private.txt', lookup=lookup, max_file_bytes=400000) is None
    assert service.api_file('allowed', 0, 'ok.py', lookup=lookup, max_file_bytes=400000)['text'] == 'value=1\n'
    assert service.run_history('denied', 1001, 0, lookup=lookup)['error'] == 'bad-range'
    assert lookups == ['denied', 'allowed', 'allowed', 'allowed']


def test_reader_owner_retains_missing_invalid_and_unknown_cost(tmp_path):
    run = tmp_path / 'run'; run.mkdir()
    service = RunQueryService(reader=record_reader)
    lookup = lambda name: run
    missing = service.api_run('run', lookup=lookup, live_drivers=lambda: {})
    assert missing['curve_state'] == 'missing'
    assert missing['row']['last'] is missing['row']['calls'] is None
    (run / 'curve.jsonl').write_text('bad\n[]\n' + json.dumps(
        {'round': 0, 'score': float('nan'), 'n_invalid': 1, 'n_scored': 0,
         'cost': {'harness_model_calls': None, 'harness_tokens': None}}) + '\n')
    result = service.api_run('run', lookup=lookup, live_drivers=lambda: {})
    assert result['curve_errors'] == 2 and result['row']['invalid'] == 1
    point = result['rounds'][0]
    assert point['score'] is point['calls'] is point['tokens'] is point['calls_per_task'] is None
    assert point['model_in_loop'] is None


@pytest.fixture
def comparison_runs(tmp_path):
    stamp = '20261017T123952Z'
    def write(tail, points):
        path = tmp_path / f'rsi10-{stamp}-sica_ci-{tail}'; path.mkdir()
        (path / 'run_meta.json').write_text('{"dataset":"fixture"}')
        (path / 'curve.jsonl').write_text(''.join(json.dumps(point) + '\n' for point in points))
        return path
    train = write('train', [{'round': 0, 'side': 'train', 'score': 0},
                           {'round': 1, 'side': 'train', 'score': None},
                           {'round': 2, 'side': 'train', 'score': .5, 'n_scored': 2,
                            'cost': {'harness_model_calls': 0}}])
    write('ckpt-r2-valid_unseen', [{'round': 0, 'side': 'eval', 'score': .75,
                                  'n_scored': 134, 'n_invalid': 0, 'n_harness_failed': 0}])
    write('exam-valid_seen', [{'round': 0, 'side': 'eval', 'score': .25,
                             'evaluated_from': {'round': 2}, 'n_scored': 139,
                             'n_invalid': 1, 'n_harness_failed': 0}])
    return tmp_path, train


def test_comparison_projection_and_coverage_do_not_invent_missing_measurements(comparison_runs):
    root, _ = comparison_runs
    rows = RunQueryService().all_rows(root, live_drivers=lambda: {}, force=True)
    result = comparison.api_compare('', 'fixture', rows=rows, runs=root)
    assert result['methods'] == ['sica_ci']
    assert result['study']['rounds'][1]['train'] is None
    series = result['series'][-1]
    assert series['valid_unseen'] == .75 and series['valid_train'] is None
    assert series['calls_per_task'] == 0 and series['model_in_loop'] is False
    coverage = comparison.api_coverage(runs=root)
    study = next(item for item in coverage['methods'] if item['name'] == 'sica_ci')
    assert study['expected'] == 6 and study['valid'] == 1
    seen = next(cell for cell in study['cells'] if cell['round'] == 2 and cell['pool'] == 'valid_seen')
    assert not seen['ok'] and '139' in seen['why_if_not'] and 'n_invalid 1' in seen['why_if_not']


def test_http_query_compare_and_evidence_still_share_configured_root(comparison_runs, monkeypatch):
    root, train = comparison_runs
    monkeypatch.setattr(server, 'RUNS', root)
    monkeypatch.setattr(server, '_run_queries', RunQueryService())
    monkeypatch.setattr(server, 'live_drivers', lambda: {})
    httpd = server.ConsoleServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    def request(path):
        conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
        try:
            conn.request('GET', path)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()
    try:
        status, listing = request('/api/runs')
        assert status == 200 and len(listing['rows']) == 3
        status, result = request('/api/compare?dataset=fixture')
        assert status == 200 and result['study']['run_id'] == train.name
        status, history = request(f'/api/runs/{train.name}/history?from=1&to=1')
        assert status == 200 and history['rounds'][0]['stored'] is False
        status, catalog = request('/api/evidence/catalog')
        assert status == 200 and catalog['scope']['run_count'] == 3
        assert all(run['status'] != 'measured' for run in catalog['runs'])
        assert request('/api/runs/..')[0] == 404
    finally:
        httpd.shutdown(); thread.join(); httpd.server_close()


def test_snapshot_reader_uses_only_the_configured_platform(tmp_path):
    assert snapshots.load(tmp_path) is None
    source = tmp_path / 'harnessgrad' / 'snapshot.py'; source.parent.mkdir()
    source.write_text('TOKEN = "fixture"\n')
    assert snapshots.load(tmp_path).TOKEN == 'fixture'
    source.write_text('invalid python !')
    assert snapshots.load(tmp_path) is None
