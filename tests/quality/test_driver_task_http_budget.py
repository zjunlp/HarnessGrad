"""Actual driver→worker→TaskSession→sandbox→HTTP admission, loopback fakes only."""
from contextlib import contextmanager
import hashlib
import http.server
import json
from pathlib import Path
import sys
import threading

import pytest

from harnessgrad import config

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def provider(*, usage=True):
    calls = []
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(payload)
            value = {'choices': [{'message': {'content': 'alpha'}}]}
            if usage(len(calls)) if callable(usage) else usage:
                value['usage'] = {'prompt_tokens': 7, 'completion_tokens': 2}
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', calls
    finally:
        server.shutdown(); server.server_close(); thread.join()


def authored_source(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    (source / 'harness.json').write_text(json.dumps({'name': 'task-budget-offline', 'version': '1',
        'entrypoint': 'agent.py', 'backend': 'cli', 'env_kinds': ['files']}))
    (source / 'agent.py').write_text('''import argparse,json,os,pathlib,urllib.request,urllib.error
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
body=json.dumps({'model':'fixture-model','messages':[{'role':'user','content':'fixture'}],'max_tokens':3}).encode()
trace=[]; totals={'input_tokens':0,'output_tokens':0,'calls':0}
for index in range(2):
    req=urllib.request.Request(os.environ['HG_AGENT_BASE_URL']+'/chat/completions',data=body,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as response:
            result=json.load(response)
        u=result.get('usage')
        if u:
            totals['input_tokens']+=u['prompt_tokens'];totals['output_tokens']+=u['completion_tokens'];totals['calls']+=1
            trace.append({'usage':dict(totals)})
    except urllib.error.HTTPError as exc:
        trace.append({'event':'task_budget_http_status','status':exc.code});break
pathlib.Path(a.workdir,'trace.jsonl').write_text('\\n'.join(json.dumps(v) for v in trace))
pathlib.Path(a.workdir,'answer.txt').write_text('alpha')
''')
    return source


def profile(tmp_path, *, max_calls=1, tokenizer=True):
    script = tmp_path / 'exact_fixture_tokenizer.py'
    script.write_text("import json,sys\nv=json.load(sys.stdin)\nassert v['model']=='fixture-model'\nprint(json.dumps({'exact':True,'model':v['model'],'contract_id':v['contract_id'],'prompt_tokens':7}))\n")
    policy = {'mode': 'strict', 'tokenizer_contract': 'fixture-template-seven-v1'}
    if tokenizer:
        executable = str(Path(sys.executable).absolute())
        policy['tokenizer'] = {'argv': [executable, str(script)],
            'sha256': {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (executable, str(script))},
            'models': ['fixture-model'], 'timeout_s': 5}
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps({'profile_version': 1, 'limits': {
        'wall_seconds': 120, 'max_requests': max_calls, 'max_calls': max_calls,
        'max_prompt_tokens': 100, 'max_output_tokens': 100, 'global_total_tokens': 200,
        'max_context': 100, 'rounds': 3, 'trials': 1, 'jobs': 1},
        'roles': {'task': {'max_total_tokens': 200}}, 'task_http': policy}))
    return path


def execute(tmp_path, monkeypatch, source, policy, endpoint, *, side='train', tasks='t01', rounds=2, method=None, run_id=None, storage=None):
    import driver
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'openai')
    monkeypatch.setenv('HG_AGENT_MODEL', 'fixture-model')
    monkeypatch.setenv('HG_AGENT_API_KEY', 'authored-loopback-test-key')
    monkeypatch.setenv('HG_AGENT_BASE_URL', endpoint)
    config.reset()
    monkeypatch.setattr(driver.datasets, 'load_envs', lambda name, *, task_ids:
        {tid: {'kind': 'files', 'network': 'none'} for tid in task_ids})
    storage = storage or tmp_path
    args = ['driver.py', '--harness-path', str(source), '--dataset', 'demo', '--tasks', tasks,
        '--sampling', 'all', '--rounds', str(rounds), '--trials', '1', '--jobs', '1', '--side', side,
        '--improver', 'none', '--no-summary', '--run-id', run_id or 'strict-' + side,
        '--runs-root', str(storage / 'runs'), '--work-root', str(storage / 'work'),
        '--gpu-budget-dir', str(tmp_path / 'authored-slots'),
        '--budget-profile', str(policy), '--budget-ledger', str(tmp_path / 'shared-ledger.json'),
        '--method-entrypoint', method or f'{sys.executable} {ROOT / "methods/noop/run.py"}']
    monkeypatch.setattr(sys, 'argv', args)
    try:
        return driver.main()
    finally:
        config.reset()


def test_real_driver_reserves_before_second_http_and_refuses_h0_curve(tmp_path, monkeypatch):
    source, policy = authored_source(tmp_path), profile(tmp_path)
    with provider() as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint) == 2
    assert len(calls) == 1
    ledger = json.loads((tmp_path / 'shared-ledger.train.json').read_text())
    assert ledger['counts']['calls'] == ledger['counts']['requests'] == 1
    assert ledger['spent']['prompt_tokens'] == 7 and ledger['spent']['output_tokens'] == 2
    assert not (tmp_path / 'runs/strict-train/curve.jsonl').exists()
    raw = json.loads((tmp_path / 'runs/strict-train/h0.json').read_text())['base']
    assert raw['per_task'] == {} and raw['invalid']['t01']['stage'] == 'task budget'
    assert raw['task_budget_stop']['scope'] == 'task'
    assert raw['task_budget_usage']['counters']['prompt_tokens'] == 7
    progress = json.loads((tmp_path / 'runs/strict-train/progress/round-0/t01.json').read_text())
    assert not progress.get('complete')
    assert progress['harness_output']['model_gateway']['budget_stops']
    meta = json.loads((tmp_path / 'runs/strict-train/run_meta.json').read_text())
    assert meta['task_http_budget']['split'] == 'train'
    assert meta['task_budget_stop']['tasks']['t01']


def test_unknown_provider_usage_remains_reserved_and_incomplete(tmp_path, monkeypatch):
    source, policy = authored_source(tmp_path), profile(tmp_path, max_calls=4)
    with provider(usage=False) as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint) == 2
    assert len(calls) == 1
    ledger = json.loads((tmp_path / 'shared-ledger.train.json').read_text())
    assert ledger['unknown_requests']
    raw = json.loads((tmp_path / 'runs/strict-train/h0.json').read_text())['base']
    assert raw['task_budget_usage']['counters']['prompt_tokens'] is None
    assert raw['task_budget_usage']['counters']['output_tokens'] is None
    assert raw['per_task'] == {}
    assert not (tmp_path / 'runs/strict-train/curve.jsonl').exists()


def test_strict_profile_without_tokenizer_refuses_before_any_actor(tmp_path, monkeypatch):
    source, policy = authored_source(tmp_path), profile(tmp_path, tokenizer=False)
    with provider() as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint) == 2
    assert calls == []
    assert not (tmp_path / 'runs/strict-train/h0.json').exists()


def test_supported_success_path_keeps_normal_noop_loop(tmp_path, monkeypatch):
    source, policy = authored_source(tmp_path), profile(tmp_path, max_calls=5)
    with provider() as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint) == 0
    assert len(calls) == 2
    curve = [json.loads(line) for line in (tmp_path / 'runs/strict-train/curve.jsonl').read_text().splitlines()]
    assert len(curve) == 2  # The actual noop method explicitly stops after its first invocation.
    assert all(point['per_task'] == {'t01': 1.0} for point in curve)
    ledger = json.loads((tmp_path / 'shared-ledger.train.json').read_text())
    assert ledger['counts']['calls'] == 2
    assert ledger['spent']['prompt_tokens'] == 14 and ledger['spent']['output_tokens'] == 4
    assert curve[0]['cost']['harness_tokens'] == 18


def test_stop_prevents_later_task_launch_and_keeps_task_context(tmp_path, monkeypatch):
    source, policy = authored_source(tmp_path), profile(tmp_path)
    with provider() as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint, tasks='t01,t02') == 2
    assert len(calls) == 1
    progress = json.loads((tmp_path / 'runs/strict-train/progress/round-0/t02.json').read_text())
    assert not progress.get('complete') and progress['invalid']['stage'] == 'task budget'
    assert set(progress['verification_context']['tasks']) == {'t02'}


def test_train_and_audit_use_distinct_durable_task_ledgers(tmp_path, monkeypatch):
    source, policy = authored_source(tmp_path), profile(tmp_path)
    with provider() as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint) == 2
        assert execute(tmp_path, monkeypatch, source, policy, endpoint,
                       side='eval', tasks='t05', rounds=0) == 2
    assert len(calls) == 2  # Each split receives its independent first reservation.
    for split in ('train', 'audit'):
        ledger = json.loads((tmp_path / f'shared-ledger.{split}.json').read_text())
        assert ledger['counts']['calls'] == 1
        assert ledger['spent']['prompt_tokens'] == 7
        assert ledger['run_id'].endswith('::task:' + split + '::agent')
    meta = json.loads((tmp_path / 'runs/strict-eval/run_meta.json').read_text())
    assert meta['task_http_budget']['split'] == 'audit'
    assert not (tmp_path / 'runs/strict-eval/curve.jsonl').exists()


def test_complete_h0_at_task_cap_does_not_buy_a_method_attempt(tmp_path, monkeypatch):
    source, policy = authored_source(tmp_path), profile(tmp_path, max_calls=2)
    counter = tmp_path / 'method-invoked'
    method = tmp_path / 'counting-noop.py'
    method.write_text(f"from pathlib import Path\nimport runpy\nPath({str(counter)!r}).write_text('invoked')\nrunpy.run_path({str(ROOT / 'methods/noop/run.py')!r},run_name='__main__')\n")
    with provider() as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint, rounds=1,
                       method=f'{sys.executable} {method}') == 0
    assert len(calls) == 2
    assert not counter.exists()
    curve = [json.loads(line) for line in (tmp_path / 'runs/strict-train/curve.jsonl').read_text().splitlines()]
    assert curve[0]['per_task'] == {'t01': 1.0}
    assert len(curve) == 1
    raw = json.loads((tmp_path / 'runs/strict-train/h0.json').read_text())['base']
    assert not raw.get('task_budget_stop')


def test_aliased_train_ledger_cannot_impersonate_audit_phase(tmp_path, monkeypatch):
    source, policy = authored_source(tmp_path), profile(tmp_path)
    with provider() as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint,
                       run_id='same-outward-id', storage=tmp_path / 'train-storage') == 2
        train = tmp_path / 'shared-ledger.train.json'
        before = train.read_bytes()
        (tmp_path / 'shared-ledger.audit.json').symlink_to(train)
        assert execute(tmp_path, monkeypatch, source, policy, endpoint,
                       side='eval', tasks='t05', rounds=0, run_id='same-outward-id',
                       storage=tmp_path / 'audit-storage') == 2
    assert len(calls) == 1
    assert train.read_bytes() == before
    assert not (tmp_path / 'audit-storage/runs/same-outward-id/h0.json').exists()
    assert not (tmp_path / 'audit-storage/runs/same-outward-id/curve.jsonl').exists()
    ledger = json.loads(before)
    assert ledger['run_id'] == 'same-outward-id::task:train::agent'


@pytest.mark.parametrize('unknown', [False, True], ids=['exhausted', 'unknown-usage'])
def test_candidate_task_stop_preserves_h0_and_never_buys_next_method(tmp_path, monkeypatch, unknown):
    source = authored_source(tmp_path)
    policy = profile(tmp_path, max_calls=8 if unknown else 3)
    method = tmp_path / 'changed-candidate.py'
    method.write_text('''import json,shutil,sys
from pathlib import Path
req=json.load(sys.stdin);work=Path(req['workspace'])
(work/'candidate-session.log').write_text(json.dumps({'round':req['round_index']}))
candidate=work/'candidate';shutil.copytree(req['base_harness'],candidate)
with (candidate/'agent.py').open('a') as f:f.write('\\n# genuinely changed authored candidate\\n')
Path(req['trajectory_out']).write_text(json.dumps({'steps':[{'harness_dir':str(candidate),'claimed_cost':{'generation_tokens':0}}]}))
print(json.dumps({'changed':True,'stop':False,'steps':1,'method_usage':{'input':0,'output':0,'total_tokens':0}}))
''')
    with provider(usage=(lambda number: number <= 2) if unknown else True) as (endpoint, calls):
        assert execute(tmp_path, monkeypatch, source, policy, endpoint,
                       method=f'{sys.executable} {method}', rounds=2) == 0
    assert len(calls) == 3
    run = tmp_path / 'runs/strict-train'
    curve = [json.loads(line) for line in (run / 'curve.jsonl').read_text().splitlines()]
    assert len(curve) == 1 and curve[0]['round'] == 0
    assert curve[0]['per_task'] == {'t01': 1.0} and curve[0]['cost']['harness_tokens'] == 18
    attempts = json.loads((run / 'method_attempts.json').read_text())['attempts']
    assert len(attempts) == 1 and attempts[0]['round'] == 1
    assert attempts[0]['terminal_stop']['code'] == 'task_budget_stopped'
    assert attempts[0]['terminal_stop']['scope'] == 'task_run'
    assert len(list((run / 'method_logs').glob('round-*/candidate-session.log'))) == 1
    stop = json.loads((run / 'round1-task-budget-stop.json').read_text())
    assert stop['score'] is None and stop['task_budget_stop']['scope'] == 'task'
    progress = json.loads((run / 'progress/round-1/t01.json').read_text())
    assert not progress.get('complete') and progress['invalid']['stage'] == 'task budget'
    gateway = progress['harness_output']['model_gateway']
    assert gateway['budget_stops']
    observed = progress['harness_output']['runtime_services']['task_budget']['observed_verdict']
    assert observed['passed'] is True  # Preserve the native verdict, without inventing a score field.
    ledger = json.loads((tmp_path / 'shared-ledger.train.json').read_text())
    if unknown:
        assert ledger['unknown_requests']
        assert stop['task_budget_usage']['counters']['prompt_tokens'] is None
        assert stop['cost']['harness_tokens'] is None
    else:
        assert ledger['spent']['prompt_tokens'] == 21 and ledger['spent']['output_tokens'] == 6
        assert stop['cost']['harness_tokens'] == 9
