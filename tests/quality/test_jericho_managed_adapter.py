"""Synthetic adapter custody checks with no ROM or package dependency."""
import json
import pytest
from pathlib import Path

from data.datasets import jericho
from harnessgrad import dataset_runtime
from harnessgrad.verification_context import capture


def record(tmp_path):
    rom = tmp_path / 'authored.z5'
    rom.write_bytes(b'authored fixture, not a ROM')
    return {'task_id': 'authored', 'rom': rom.name, 'path': rom, 'seed': 0,
            'walkthrough': ['PRIVATE-AUTHORED-REFERENCE']}


def test_actor_has_only_client_and_bound_single_game_service(tmp_path, monkeypatch):
    item = record(tmp_path)
    binding = {'python': '/synthetic/python', 'runtime_id': 'frozen-artifact'}
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: {'name': name})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda runtime, role: binding)
    setup = jericho._setup(item)
    assert jericho._env() == jericho.DEFAULT_ENV
    assert {f['path'] for f in setup['files']} == {'jer', 'jer_game.json', 'jer_actions.jsonl'}
    assert 'PRIVATE-AUTHORED-REFERENCE' not in json.dumps(setup)
    assert setup['tool_relay']['python_runtime'] is binding
    spec = json.loads(setup['tool_relay']['content'])
    assert spec['seed'] == 0 and spec['rom_path'] == str(item['path'])
    assert str(item['path']) in setup['tool_relay']['read_paths']
    assert str(tmp_path) not in setup['tool_relay']['read_paths']


def test_private_control_keeps_reference_out_of_task_and_actor_files(tmp_path, monkeypatch):
    item = record(tmp_path)
    monkeypatch.setattr(jericho, '_RECORDS', [item], raising=False)
    monkeypatch.setattr(jericho, '_LOAD_ERROR', None)
    monkeypatch.setattr(jericho, '_setup', lambda record: {'files': []})
    snapshot = jericho.command_control('authored')
    assert snapshot['oracle_files'] == {'.oracle/actions.json': json.dumps(item['walkthrough'])}
    assert 'PRIVATE-AUTHORED-REFERENCE' not in json.dumps(snapshot['task'])
    assert snapshot['verifier']['native_runtime']['profile'] == 'public-native-local-v1'
    assert 'manifest' not in snapshot['verifier']['native_runtime']


def test_rom_content_changes_verification_context(tmp_path):
    item = record(tmp_path)
    task = {'task_id': item['task_id'], 'goal': 'authored'}
    setup = {'tool_relay': {'program': str(jericho.ASSETS / 'relay.py'),
                            'read_paths': [str(item['path'])]}}
    before = capture([task], {'authored': ''}, {'authored': setup})
    item['path'].write_bytes(b'different authored input')
    assert capture([task], {'authored': ''}, {'authored': setup}) != before


@pytest.mark.parametrize("provider_loop", [False, True])
def test_registry_to_runner_authored_game(tmp_path, monkeypatch, provider_loop):
    """Use the real selected service artifact with an authored environment only."""
    import os
    import pytest
    from data.scaffolds import registry
    from eval.run.runner import run_one
    store = os.environ.get('HG_TEST_JERICHO_RUNTIME_ROOT')
    if not store:
        pytest.skip('set HG_TEST_JERICHO_RUNTIME_ROOT to a prepared external artifact')
    artifact = dataset_runtime.resolve_execution('jericho', root=store)
    item = record(tmp_path)
    monkeypatch.setattr(jericho, '_RECORDS', [item], raising=False)
    monkeypatch.setattr(jericho, '_LOAD_ERROR', None)
    fake = tmp_path / 'fake_service.py'
    fake.write_text('''import importlib.util
from pathlib import Path
path = Path(RELAY)
spec = importlib.util.spec_from_file_location('authored_relay', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class Environment:
    def __init__(self, rom, *, seed):
        assert seed == 0
        assert Path(rom).read_bytes() == b'authored fixture, not a ROM'
        self.actions = []
    def step(self, action):
        self.actions.append(action)
        return ('authored ' + action, 0, False, {'score': len(self.actions), 'moves': len(self.actions)})
    def get_max_score(self): return 1
    def get_inventory(self): return 'authored key'
module._make_public_env = Environment
raise SystemExit(module.main())
'''.replace('RELAY', repr(str(jericho.ASSETS / 'relay.py'))))
    original_setup = jericho._setup
    def setup(record):
        value = original_setup(record)
        value['tool_relay']['program'] = str(fake)
        value['tool_relay']['read_paths'].append(str(fake))
        return value
    def verifier(record):
        return {'kind': 'command', 'argv': ['python3', 'check.py'],
            'inputs': [{'path': 'check.py', 'content': '''import json
from pathlib import Path
rows = [json.loads(line) for line in Path('jer_actions.jsonl').read_text().splitlines()]
assert rows == [{'kind':'action','step':0,'action':'open door'}, {'kind':'done','step':1}]
Path('verifier/reward.txt').write_text('1')
Path('verifier/evidence.json').write_text(json.dumps({'measured':True, 'synthetic':True}))
'''}], 'reward_file':'/verifier/reward.txt', 'evidence_file':'/verifier/evidence.json'}
    monkeypatch.setattr(jericho, '_setup', setup)
    monkeypatch.setattr(jericho, '_verifier', verifier)
    if provider_loop:
        from harnessgrad import dataset_provider
        # Inject only the authored game table, engine entrypoint and scorer. The
        # original isolated Python bootstrap and worker protocol still execute.
        bootstrap = tmp_path / 'authored_provider.py'
        bootstrap.write_text("from pathlib import Path, PosixPath\nimport os\n"
            "from data.datasets import jericho\n"
            + "Path(" + repr(str(tmp_path / 'worker-pids')) + ").open('a').write(str(os.getpid()) + '\\n')\n"
            + "jericho._RECORDS = " + repr([item]) + "\njericho._LOAD_ERROR = None\n"
            + "original = jericho._setup\n"
            + "def setup(record):\n value = original(record)\n"
            + " value['tool_relay']['program'] = " + repr(str(fake)) + "\n"
            + " value['tool_relay']['read_paths'].append(" + repr(str(fake)) + ")\n return value\n"
            + "jericho._setup = setup\njericho._verifier = lambda record: "
            + repr(verifier(item)) + "\n")
        monkeypatch.setattr(dataset_provider, '_BOOTSTRAP',
            "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_path("
            + repr(str(bootstrap)) + ");runpy.run_module('data.scaffolds.provider_worker',run_name='__main__')")
        monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', store)
        monkeypatch.setenv('HG_DATA_JERICHO', str(tmp_path / 'dataset'))
        with registry.provider_session():
            catalog, scorable, split = registry.load_split('jericho')
            tasks, setups, verifiers = registry.load_tasks('jericho', task_ids=['authored'])
            envs = registry.load_envs('jericho', task_ids=['authored'])
            assert registry.provider_runtime('jericho')['python'] != __import__('sys').executable
            assert catalog == tasks and split is None
            assert capture(tasks, scorable, setups, verifiers, envs)
        pids = [int(pid) for pid in (tmp_path / 'worker-pids').read_text().splitlines()]
        assert len(pids) >= 3 and all(pid != os.getpid() for pid in pids)
    else:
        with registry._local_provider(), dataset_runtime.execution_snapshot({
                'jericho': dataset_runtime.bind(artifact, 'service')}):
            tasks, setups, verifiers = registry.load_tasks('jericho', task_ids=['authored'])
            envs = registry.load_envs('jericho', task_ids=['authored'])
    harness = tmp_path / 'harness'
    harness.mkdir()
    (harness / 'harness.json').write_text(json.dumps({'entrypoint':'agent.py'}))
    (harness / 'agent.py').write_text('''import subprocess, sys
for args in [('look',), ('do','open door'), ('score',), ('inventory',), ('done',)]:
    subprocess.run([sys.executable, 'jer', *args], check=True)
''')
    (tmp_path / 'work').mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path / 'work'))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    if provider_loop:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from harnessgrad import candidate_runtime, config
        harness = Path(__file__).resolve().parents[2] / 'base_harness/loop'
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append(body)
                action = ({'tool':'bash', 'command':
                    '''python3 -I -c 'import importlib.util; assert all(importlib.util.find_spec(n) is None for n in ("jericho","numpy"))' && python3 jer look && python3 jer do "open door" && python3 jer score && python3 jer inventory && python3 jer done'''}
                    if len(seen) == 1 else {'answer':'AUTHORED COMPLETE'})
                data = json.dumps({'id':'authored','object':'chat.completion','created':0,
                    'model':body['model'],'choices':[{'index':0,'finish_reason':'stop',
                    'message':{'role':'assistant','content':json.dumps(action)}}],
                    'usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10}}).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        for key in list(os.environ):
            if key.upper().endswith('_PROXY'):
                monkeypatch.delenv(key)
        monkeypatch.setenv('HG_AGENT_BACKEND','openai')
        monkeypatch.setenv('HG_AGENT_BASE_URL', f'http://127.0.0.1:{server.server_port}/v1')
        monkeypatch.setenv('HG_AGENT_API_KEY','authored-key')
        monkeypatch.setenv('HG_AGENT_MODEL','authored-model')
        monkeypatch.setenv('HG_AGENT_MAX_STEPS','3')
        config.reset()
        binding = candidate_runtime.prepare(harness, envs=envs, setups=setups)['identity']
    else:
        binding = None
    try:
        result = run_one(harness, tasks[0], setup=setups['authored'],
                         verifier=verifiers['authored'], env=envs['authored'],
                         sandbox=True, timeout_s=60, verify_timeout_s=30,
                         harness_runtime_identity=binding)
    finally:
        if provider_loop:
            server.shutdown()
            server.server_close()
            thread.join()
            config.reset()
    (tmp_path / 'result.json').write_text(json.dumps(result, indent=2))
    if provider_loop:
        (tmp_path / 'endpoint-requests.json').write_text(json.dumps(seen, indent=2))
        assert len(seen) == 2
        public = json.dumps(seen) + result.get('trace', '')
        assert 'PRIVATE-AUTHORED-REFERENCE' not in public
        assert str(item['path']) not in public
        assert str(jericho.LIB_DIR) not in public
        assert str(bootstrap) not in public
        assert 'Traceback' not in public
        assert result['answer'].strip() == 'AUTHORED COMPLETE'
        assert result['harness_runtime_identity'] == binding
    assert result['exit_code'] == 0, json.dumps(result, indent=2)
    assert result['verdict']['score'] == 1, result
    assert result['verdict']['evidence']['measured'] is True
    assert result['verdict']['evidence']['synthetic'] is True


@pytest.mark.parametrize('failure', ['artifact', 'launch', 'rom-digest'])
def test_managed_relay_failure_is_unmeasured(tmp_path, monkeypatch, failure):
    """A broken binding/service/input cannot turn an authored task into scored zero."""
    import os
    from eval.run.runner import run_one
    store = os.environ.get('HG_TEST_JERICHO_RUNTIME_ROOT')
    if not store:
        pytest.skip('set HG_TEST_JERICHO_RUNTIME_ROOT to a prepared external artifact')
    artifact = dataset_runtime.resolve_execution('jericho', root=store)
    item = record(tmp_path)
    with dataset_runtime.execution_snapshot({'jericho': dataset_runtime.bind(artifact, 'service')}):
        setup = jericho._setup(item)
    if failure == 'artifact':
        setup['tool_relay']['python_runtime']['runtime_id'] = '0' * 64
    elif failure == 'rom-digest':
        item['path'].write_bytes(b'changed after provider selection')
    else:
        broken = tmp_path / 'broken_service.py'
        broken.write_text("raise RuntimeError('PRIVATE-AUTHORED-REFERENCE')\n")
        setup['tool_relay']['program'] = str(broken)
        setup['tool_relay']['read_paths'].append(str(broken))
    harness = tmp_path / 'harness'
    harness.mkdir()
    (harness / 'harness.json').write_text(json.dumps({'entrypoint':'agent.py'}))
    (harness / 'agent.py').write_text("raise AssertionError('actor must not run')\n")
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    result = run_one(harness, {'task_id':'authored', 'goal':'authored'},
        setup=setup, env=jericho._env(), verifier={'kind':'answer','expected':'never'},
        sandbox=True, timeout_s=10, verify_timeout_s=10)
    (tmp_path / 'result.json').write_text(json.dumps(result, indent=2))
    assert result.get('invalid'), result
    assert (result.get('verdict') or {}).get('score') is None, result
    assert 'PRIVATE-AUTHORED-REFERENCE' not in result.get('trace', '')
    assert 'PRIVATE-AUTHORED-REFERENCE' not in result.get('stderr', '')
