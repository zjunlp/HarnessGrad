import importlib.util
from pathlib import Path
import pytest
REPO=Path(__file__).resolve().parents[2]
def module(name,path):
 s=importlib.util.spec_from_file_location(name,REPO/path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
relay=module('scw_synthetic_relay','data/datasets/scienceworld/relay.py')
class Engine:
 def __init__(self): self.closed=False;self.actions=[]
 def step(self,a):
  self.actions.append(a);return ('feedback '+a,0,len(self.actions)>=2,{'score':len(self.actions)*10})
 def look(self): return 'room '+str(len(self.actions))
 def inventory(self): return 'inventory'
 def get_task_description(self): return 'selected task'
 def close(self): self.closed=True

def test_replay_closed_and_budget():
 instances=[]
 def factory():
  e=Engine();instances.append(e);return e
 g=relay.Game({},make_env=factory)
 result=g.handle('look',{'actions':['one','two','ignored']})
 assert result['text']=='room 2'
 assert result['last'][3]['score']==20
 assert instances[-1].actions==['one','two'] and instances[-1].closed
 assert g.handle('task',{'actions':[]})=={'text':'selected task'}
 for op,args in [('gold',{'actions':[]}),('look',{'actions':[],'task':'other'}),('look',{'actions':['x']*101})]:
  with pytest.raises(ValueError): g.handle(op,args)

def test_error_redacted_and_closed():
 class Broken(Engine):
  def look(self): raise RuntimeError('private reference')
 e=Broken();g=relay.Game({},make_env=lambda:e)
 with pytest.raises(RuntimeError,match='^ScienceWorld environment unavailable$'):
  g.handle('look',{'actions':[]})
 assert e.closed

def test_payload_uses_bound_prefix_and_hash(tmp_path):
 runtime=module('scw_payload','data/datasets/scienceworld/runtime.py')
 with pytest.raises(RuntimeError,match='sealed scorer'):runtime.payload({'path':str(tmp_path)})

def test_stdlib_cli_keeps_status_and_log(tmp_path, monkeypatch, capsys):
    import runpy
    import sys
    from types import SimpleNamespace
    requests=[]
    def call(socket, operation, arguments, **kwargs):
        requests.append((operation,arguments))
        return {'text':'room', 'last': ['feedback',0,False,{'score':10}] if arguments['actions'] else None}
    monkeypatch.setitem(sys.modules,'tool_transport',SimpleNamespace(call=call))
    cli=runpy.run_path(str(REPO/'data/datasets/scienceworld/cli/scw-relay'))
    assert cli['main'](['--workdir',str(tmp_path),'do','open door'])==0
    assert requests==[('look',{'actions':['open door']})]
    assert 'move 1/100; score 10/100; in progress' in capsys.readouterr().out
    assert (tmp_path/'scw_actions.jsonl').read_text()=='{"kind": "action", "step": 0, "action": "open door"}\n'
    assert cli['main'](['--workdir',str(tmp_path),'done'])==0
    assert len(requests)==1


def test_setup_and_verifier_do_not_find_platform_package(tmp_path,monkeypatch):
    import data.datasets.scienceworld as dataset
    import harnessgrad.dataset_runtime as runtime
    import importlib.util
    import hashlib
    jar=tmp_path/'scienceworld.jar';jar.write_bytes(b'authored jar')
    java=tmp_path/'jre/bin/java';java.parent.mkdir(parents=True);java.write_bytes(b'authored java')
    binding={'python':'/managed/bin/python','path':'/managed','runtime_id':'authored'}
    monkeypatch.setattr(runtime,'resolve_execution',lambda name:binding)
    monkeypatch.setattr(runtime,'bind',lambda selected,role: selected)
    monkeypatch.setattr(dataset,'_runtime_payload',lambda *args:jar)
    monkeypatch.setattr(dataset,'JRE_STAGED',java.parent.parent)
    monkeypatch.setattr(importlib.util,'find_spec',lambda *args:pytest.fail('platform package lookup'))
    record={'task_id':'boil/1','task_name':'boil','variation':1}
    setup=dataset._setup(record)
    assert [p['path'] for p in setup['files']]==['scw_task.json','scw_actions.jsonl','scw','scw_client.py']
    import json
    spec=json.loads(setup['tool_relay']['content'])
    assert spec['task']=={'task_name':'boil','variation':1,'simplifications':''}
    assert spec['java_sha256']==hashlib.sha256(b'authored java').hexdigest()
    verify=dataset._verifier(record)
    assert verify['native_runtime']['profile']=='public-native-local-v1'
    assert 'manifest' not in verify['native_runtime']
    assert next(p for p in verify['inputs'] if p['path']=='scienceworld.jar')['from']==str(jar)


def test_environment_catalog_mapping_needs_no_managed_runtime(monkeypatch):
    import data.datasets.scienceworld as dataset
    import harnessgrad.dataset_runtime as runtime
    monkeypatch.setattr(runtime,'resolve_execution',lambda *args:pytest.fail('ENV must stay read-only and dependency-free'))
    mapping=dataset._Lazy().get('ENV')
    assert len(mapping)==1744
    assert all(value=={'kind':'files'} for value in mapping.values())


def test_payload_hashes_actual_bytes_and_refuses_changed_or_symlinked_jar(tmp_path, monkeypatch):
    import hashlib
    runtime = module('scw_payload_bytes', 'data/datasets/scienceworld/runtime.py')
    jar = tmp_path / 'lib/python3.12/site-packages/scienceworld/scienceworld.jar'
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b'synthetic managed jar')
    monkeypatch.setattr(runtime, 'JAR_SHA256', hashlib.sha256(jar.read_bytes()).hexdigest())
    assert runtime.payload({'path': str(tmp_path)}) == jar
    jar.write_bytes(b'changed managed jar')
    with pytest.raises(RuntimeError, match='sealed scorer'):
        runtime.payload({'path': str(tmp_path)})
    target = jar.with_name('other.jar')
    jar.rename(target)
    jar.symlink_to(target)
    monkeypatch.setattr(runtime, 'JAR_SHA256', hashlib.sha256(target.read_bytes()).hexdigest())
    with pytest.raises(RuntimeError, match='sealed scorer'):
        runtime.payload({'path': str(tmp_path)})


def test_isolated_relay_starts_without_platform_scaffolds_or_task_execution(tmp_path, monkeypatch):
    import hashlib
    import json
    import shutil
    import subprocess
    import sys
    from eval.containers import python_service
    import data.datasets.scienceworld as dataset
    if shutil.which('bwrap') is None:
        pytest.skip('bubblewrap is not installed')
    # The real relay only imports the runtime constant; no environment/task is run.
    # Substitute authored payload hashes in a test-only bootstrap, never source files.
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    jar = inputs / 'fixture.jar'
    jar.write_bytes(b'synthetic fixture jar')
    java = inputs / 'jre/bin/java'
    java.parent.mkdir(parents=True)
    java.write_bytes(b'never executed')
    monkeypatch.setattr(dataset, 'JRE_STAGED', java.parent.parent)
    binding = {'python': sys.executable, 'path': str(inputs)}
    setup = dataset._setup({'task_name': 'never loaded', 'variation': 0}, binding=binding, world_jar=jar)
    declaration = setup['tool_relay']
    bootstrap = inputs / 'bootstrap.py'
    bootstrap.write_text(f'''
import importlib.util, pathlib, runpy, sys
assert not pathlib.Path({str(REPO / 'data/scaffolds')!r}).exists()
assert not pathlib.Path({str(REPO / 'data/probe_set.py')!r}).exists()
sys.path.insert(0, {str(dataset.ASSETS)!r})
import runtime
assert 'data.scaffolds.scorer_input' not in sys.modules
sys.path.insert(0, {str(REPO)!r})
assert importlib.util.find_spec('data.scaffolds') is None
runtime.JAR_SHA256 = {hashlib.sha256(jar.read_bytes()).hexdigest()!r}
runpy.run_path({str(dataset.ASSETS / 'relay.py')!r}, run_name='__main__')
''')
    directory = tmp_path / 'service'
    (directory / 'state').mkdir(parents=True)
    spec = directory / 'case.json'
    spec.write_text(declaration['content'])
    socket = directory / 'tool.sock'
    receipt = directory / 'receipt.json'
    argv = [sys.executable, '-I', '-B', str(bootstrap), '--case-spec', str(spec),
            '--socket', str(socket), '--receipt', str(receipt), '--deadline-s', '1']
    env = python_service.environment(directory, {})
    command = python_service.command(argv, directory=directory, runtime_paths=[str(Path(sys.prefix).resolve())],
        read_paths=[*declaration['read_paths'], str(bootstrap), str(jar)])
    ran = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    # Idle deadline ends the actual Relay loop with no requests or engine startup.
    assert ran.returncode == 1, ran.stderr
    assert receipt.exists(), ran.stderr
    result = json.loads(receipt.read_text())
    assert result['schema'] == 'harnessgrad.scienceworld-relay.v1'
    assert result['transport']['calls'] == 0
    assert 'ModuleNotFoundError' not in ran.stderr
