"""Opt-in author episodes through real pinned Tau packages; LLM replies are scripted.

No released case/trajectory is read. Bundles contain source/domain data only.
Set HG_TAU{2,3}_CONTROL_BUNDLE and HG_TAU{2,3}_CONTROL_STORE to run.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import importlib
import json
from pathlib import Path
import threading

from harnessgrad import config, dataset_runtime
from eval.run.runner import run_one

ASSERTION = 'The assistant reports the authored arithmetic result.'


def test_real_tau_package_service_and_all_scorer(tmp_path, monkeypatch, version, mode):
    import os
    bundle = os.environ.get(f'HG_TAU{version}_CONTROL_BUNDLE')
    store = os.environ.get(f'HG_TAU{version}_CONTROL_STORE')
    if not bundle or not store:
        pytest.skip('explicit pinned source-only bundle and managed execution store required')
    bundle = Path(bundle).resolve()
    adapter = importlib.import_module(f'data.datasets.tau{version}_bench')
    runtime = dataset_runtime.resolve_execution(f'tau{version}_bench', root=store)
    root = tmp_path / 'authored'
    case = root / 'cases/authored'
    case.mkdir(parents=True)
    tid = f'tau{version}_bench/authored-control'
    private = {'task_id': tid, 'domain': 'retail',
        'release': 'v0.1.0', 'revision': 'fc0055dc4e0a316c3f83133267fbd6faaa770992',
        'task': {'id': 'authored-control',
            'user_scenario': {'instructions': 'Synthetic control: request 2 + 2, then stop.'},
            'evaluation_criteria': {
                'actions': [{'action_id': 'authored-calculate', 'name': 'calculate',
                             'arguments': {'expression': '2 + 2'}}],
                'nl_assertions': [ASSERTION], 'reward_basis': ['ACTION', 'NL_ASSERTION']}}}
    raw = json.dumps(private).encode()
    (case / 'private.json').write_bytes(raw)
    public = b'{"policy":"Authored arithmetic control","user_tools":[]}'
    (case / 'public.json').write_bytes(public)
    row = {'path': 'cases/authored', 'needs_judge': True, 'user_tools': [],
           'private_sha256': hashlib.sha256(raw).hexdigest(),
           'public_sha256': hashlib.sha256(public).hexdigest()}
    state = {'user': 0, 'judge': 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            role = 'user' if request['model'] == 'synthetic-tau-user' else 'judge'
            state[role] += 1
            if role == 'user':
                content = 'Please calculate 2 + 2.' if state[role] == 1 else '###STOP###'
            else:
                content = '{}' if mode == 'invalid-judge' else json.dumps({'results': [{
                    'expectedOutcome': ASSERTION, 'metExpectation': True, 'reasoning': 'synthetic reply'}]})
            raw = json.dumps({'model': request['model'], 'choices': [{'message': {
                'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}).encode()
            self.send_response(503 if role == 'judge' and mode == 'judge-http-failure' else 200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for key, value in {'HG_ENV_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
            'HG_JUDGE_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
            'HG_ENV_MODEL': 'synthetic-tau-user', 'HG_JUDGE_MODEL': 'synthetic-tau-judge',
            f'HG_TAU{version}_JUDGE_MODEL': 'synthetic-tau-judge',
            'HG_ENV_API_KEY': 'synthetic-tau-env-key', 'HG_JUDGE_API_KEY': 'synthetic-tau-judge-key',
            f'HG_DATA_TAU{version}_BENCH_RUNTIME': runtime['path'],
            'HG_AGENT_BACKEND': 'mock'}.items():
            monkeypatch.setenv(key, value)
        work = tmp_path / 'work'
        work.mkdir()
        monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
        monkeypatch.setattr(adapter, 'DATASET_ROOT', root)
        config.reset()
        with dataset_runtime.execution_snapshot({f'tau{version}_bench': dataset_runtime.bind(runtime, 'service')}):
            setup = adapter._setup(tid, row)
            verifier = adapter._verify_with_judge(tid, row) if version == 2 else adapter._verify(tid, row)
        # The author's selected fixture replaces only the bundle location. All
        # original relay/bootstrap/model/runtime/scorer bytes execute unchanged.
        relay = setup['tool_relay']
        spec = json.loads(relay['content'])
        spec['bundle'] = str(bundle)
        relay['content'] = json.dumps(spec)
        relay['read_paths'] = [str(bundle) if p == str(root / 'bundle') else p for p in relay['read_paths']]
        for entry in verifier['inputs']:
            if entry['path'] == f'tau{version}_bundle': entry['from'] = str(bundle)
        repo = work / 'harness'
        repo.mkdir()
        (repo / 'harness.json').write_text('{"entrypoint":"agent.py"}')
        (repo / 'agent.py').write_text('''import argparse,json,os,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
w=Path(a.workdir)
assert not (w/'tau2_private.json').exists() and not (w/'tau3_private.json').exists()
assert not any(k in os.environ for k in ('HG_ENV_API_KEY','HG_JUDGE_API_KEY','HG_JUDGE_SOCKET'))
def cli(*args):
    value=json.loads(subprocess.check_output([sys.executable, str(w/'tauVERSION.py'), *args],cwd=w,text=True))
    assert 'result' in value, value
    return value['result']
assert cli('start') == 'Please calculate 2 + 2.'
if MODE != 'wrong-action': assert '4' in cli('call','calculate','{"expression":"2 + 2"}')
cli('say','The answer is 4.')
assert json.loads(cli('status'))['termination_reason'] == 'user_stop'
(w/'answer.txt').write_text('Actor text is not the grading input.')
'''.replace('VERSION', str(version)).replace('MODE', repr(mode)))
        result = run_one(repo, {'task_id': tid, 'goal': 'Use the authored Tau arithmetic episode.'},
            setup=setup, verifier=verifier, env={'kind': 'files', 'network': 'none'},
            run_id='synthetic-tau-real-package', run_seed='7', trial_id='author-control',
            timeout_s=90, verify_timeout_s=90)
    finally:
        server.shutdown(); server.server_close(); thread.join()
        config.reset()
    assert 'synthetic-tau-env-key' not in json.dumps(result)
    assert 'synthetic-tau-judge-key' not in json.dumps(result)
    assert result['exit_code'] == 0, result
    assert state == {'user': 2, 'judge': 1}, result
    service = result['runtime_services']['tool_relay']
    assert not service['faults'], service
    assert service['cleanup']['complete'], service
    assert service['receipt']['stopped_cleanly'] is True
    assert service['python_runtime']['runtime_id'] == runtime['runtime_id']
    assert service['model']['usage'] == {'prompt_tokens': 14, 'completion_tokens': 6}
    assert service['model']['configuration']['parameters']['seed'] == 7
    verdict = result['verdict']
    if mode == 'judge-http-failure':
        assert verdict['judge']['usage_complete'] is False
        assert verdict['judge']['attempts'][0]['usage'] is None
    else:
        assert verdict['judge']['usage_complete'] is True
    if mode in ('correct', 'wrong-action'):
        assert verdict['score'] == (1 if mode == 'correct' else 0), verdict
        assert verdict['evidence']['measured'] is True
        assert verdict['evidence']['official']['reward_breakdown']['ACTION'] == (1 if mode == 'correct' else 0)
    else:
        assert verdict.get('score') is None, verdict
        assert verdict.get('evidence', {}).get('measured') is not True
        assert verdict.get('unmeasured') or result.get('invalid'), result


def test_legacy_tau_real_tools_http_service_and_scorer(tmp_path, mode):
    """The original legacy service+scorer are stdlib; no container or LLM needed.

    This component control does not claim native run_one replaces the legacy
    declared container environment. It executes its real HTTP handler, tools,
    recording persistence, GT replay, and hash/output/termination scorer.
    """
    import copy
    import importlib.util
    import subprocess
    import sys
    from urllib.request import Request, build_opener, ProxyHandler
    assets = Path(__file__).resolve().parents[2] / 'data/datasets/tau_bench'
    provenance = json.loads((assets / 'PROVENANCE.json').read_text())
    for relative, digest in provenance['files'].items():
        if relative.startswith('lib/'):
            assert hashlib.sha256((assets / relative).read_bytes()).hexdigest() == digest
    spec = importlib.util.spec_from_file_location('authored_tau_legacy_service', assets / 'service.py')
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    # Tools are the verbatim source projection, not a test replacement.
    sys.path.insert(0, str(assets / 'lib'))
    from tau_bench.envs.retail.tools import ALL_TOOLS
    tools = {tool.get_info()['function']['name']: tool for tool in ALL_TOOLS}
    import inspect
    assert Path(inspect.getfile(tools['modify_user_address'])).resolve() == (
        assets / 'lib/tau_bench/envs/retail/tools/modify_user_address.py').resolve()
    clean = {'users': {'authored-user': {'address': {'city': 'Before'}}}}
    arguments = dict(user_id='authored-user', address1='1 Author St', address2='',
                     city='After', state='CA', country='USA', zip='12345')
    gt = {'task_id': 'retail_test_999', 'domain': 'retail', 'user_id': 'authored-user',
          'actions': [{'name': 'modify_user_address', 'arguments': arguments}],
          'outputs': ['authored completion']}
    recording = tmp_path / 'episode.json'
    episode = service.Episode(gt['task_id'], 'retail', copy.deepcopy(clean), tools,
                              'Authored policy only', recording)
    server = HTTPServer(('127.0.0.1', 0), service.handler_for(episode))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    try:
        def act(payload):
            with opener.open(Request(f'http://127.0.0.1:{server.server_port}/act',
                    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}), timeout=5) as response:
                return json.load(response)
        if mode != 'wrong-db':
            assert 'After' in act({'command': 'call', 'name': 'modify_user_address',
                                  'arguments': arguments})['output']
        act({'command': 'say', 'content': 'wrong' if mode == 'wrong-output' else 'Authored completion'})
        if mode != 'unfinished': act({'command': 'done'})
    finally:
        server.shutdown(); server.server_close(); thread.join()
    if mode == 'invalid-recording':
        recording.write_text('{}')
    (tmp_path / 'gt.json').write_text(json.dumps(gt))
    (tmp_path / 'clean.json').write_text(json.dumps(clean))
    reward = tmp_path / 'reward.txt'
    evidence = tmp_path / 'evidence.json'
    completed = subprocess.run([sys.executable, '-I', '-B', str(assets / 'verify/check.py'),
        '--gt', str(tmp_path / 'gt.json'), '--clean-db', str(tmp_path / 'clean.json'),
        '--lib', str(assets / 'lib'), '--recording', str(recording),
        '--reward', str(reward), '--evidence', str(evidence)], capture_output=True, text=True, timeout=15)
    result = json.loads(evidence.read_text())
    if mode == 'invalid-recording':
        assert completed.returncode == 3, (completed.stdout, completed.stderr)
        assert not reward.exists()
        assert 'service recording unavailable or invalid' in result['error']
    else:
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert int(reward.read_text()) == (1 if mode == 'correct' else 0)
        assert result['replayed_actions'] == 1
        assert result['ground_truth_changes_database'] is True
    assert json.loads((tmp_path / 'harnessgrad.json').read_text())['usage']['calls'] == 0


TAU_MODES = ['correct', 'wrong-action', 'invalid-judge', 'judge-http-failure']
LEGACY_MODES = ['correct', 'wrong-db', 'wrong-output', 'unfinished', 'invalid-recording']

if __name__ == '__main__':
    # The platform driver needs only stdlib; upstream dependencies stay in their
    # managed service/scorer artifacts and are never imported into this process.
    import os
    import tempfile
    from contextlib import ExitStack
    from unittest.mock import patch
    class Patches:
        def __init__(self, stack): self.stack = stack
        def setenv(self, key, value): self.stack.enter_context(patch.dict(os.environ, {key: value}))
        def setattr(self, obj, key, value): self.stack.enter_context(patch.object(obj, key, value))
    for version in (2, 3):
        if not all(os.environ.get(f'HG_TAU{version}_CONTROL_{part}') for part in ('BUNDLE', 'STORE')):
            raise SystemExit(f'Configure HG_TAU{version}_CONTROL_BUNDLE and HG_TAU{version}_CONTROL_STORE')
        for mode in TAU_MODES:
            with tempfile.TemporaryDirectory(prefix='hg-tau-real-author-') as directory, ExitStack() as stack:
                test_real_tau_package_service_and_all_scorer(Path(directory), Patches(stack), version, mode)
            print(f'tau{version}/{mode}: actual package, synthetic LLM control passed', flush=True)
    for mode in LEGACY_MODES:
        with tempfile.TemporaryDirectory(prefix='hg-legacy-tau-author-') as directory:
            test_legacy_tau_real_tools_http_service_and_scorer(Path(directory), mode)
        print(f'legacy/{mode}: actual tools/service/scorer control passed', flush=True)
else:
    import pytest
    test_real_tau_package_service_and_all_scorer = pytest.mark.parametrize('version', [2, 3])(
        pytest.mark.parametrize('mode', TAU_MODES)(test_real_tau_package_service_and_all_scorer))
    test_legacy_tau_real_tools_http_service_and_scorer = pytest.mark.parametrize('mode', LEGACY_MODES)(
        test_legacy_tau_real_tools_http_service_and_scorer)
