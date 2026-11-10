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
