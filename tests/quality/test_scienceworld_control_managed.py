"""Official control wiring using source fixtures and authored operator engines.

No real ScienceWorld task or native scorer is executed.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import pytest
from test_scienceworld_launch_control import managed

ROOT=Path(__file__).resolve().parents[2]
ASSETS=ROOT/'data/datasets/scienceworld'


def control():
    spec=importlib.util.spec_from_file_location('scienceworld_operator_control',ASSETS/'control.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('platform',[False,True])
@pytest.mark.parametrize('oracle',[False,True])
def test_both_formal_flags_preserve_relay_and_sealed_verifier(tmp_path,monkeypatch,platform,oracle):
    from eval.run import runner
    from data.runtimes import NATIVE_PINS
    module=control()
    record={'task_id':'authored/7','task_name':'authored','variation':7}
    relay={'program':'/authored/trusted-relay','python_runtime':{'runtime_id':'authored-bound'}}
    setup={'files':[{'path':'scw','content':'authored stdlib client'}],'tool_relay':relay}
    monkeypatch.setattr(module,'_record',lambda selected:record)
    monkeypatch.setattr(module.scienceworld,'_setup',lambda selected:setup)
    monkeypatch.setattr(module.scienceworld,'_verifier',lambda selected:{'native_runtime':NATIVE_PINS['scienceworld'].copy()})
    monkeypatch.setattr(module.scienceworld,'load',lambda:([{'task_id':'authored/7','goal':'source fixture','inputs':[]}],{}))
    calls=[]
    def gold(selected,workdir):
        assert oracle
        calls.append('trusted-reference')
        return ['authored action']*101
    monkeypatch.setattr(module,'_gold',gold)
    def run_one(**kwargs):
        calls.append(kwargs)
        assert kwargs['setup']['tool_relay']==relay
        assert kwargs['verifier']['native_runtime']==NATIVE_PINS['scienceworld']
        reference=[f for f in kwargs['setup']['files'] if f['path']=='.oracle/actions.json']
        assert bool(reference)==oracle
        if oracle: assert len(json.loads(reference[0]['content']))==100
        return {'exit_code':0,'verdict':{'score':1.0 if oracle else 0.0,'evidence':{'measured':True}}}
    monkeypatch.setattr(runner,'run_one',run_one)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT','preexisting-operator-root')
    flags=['--oracle' if oracle else '--noop','--task-id','authored/7','--workdir',str(tmp_path/'work')]
    if platform: flags.append('--platform')
    assert module.main(flags)==0
    assert os.environ['HARNESSGRAD_WORK_ROOT']=='preexisting-operator-root'
    assert len(calls)==(2 if oracle else 1)
    assert setup['files']==[{'path':'scw','content':'authored stdlib client'}]


def test_control_missing_measurement_is_unknown(tmp_path,monkeypatch):
    from eval.run import runner
    module=control()
    monkeypatch.setattr(module.scienceworld,'load',lambda:([{'task_id':'authored/7'}],{}))
    monkeypatch.setattr(module.scienceworld,'_setup',lambda record:{'files':[],'tool_relay':{'program':'authored'}})
    monkeypatch.setattr(module.scienceworld,'_verifier',lambda record:{})
    monkeypatch.setattr(runner,'run_one',lambda **kwargs:{'exit_code':0,'verdict':{'score':0.0,'evidence':{'measured':False}}})
    assert module.run_platform(module.scienceworld,{'task_id':'authored/7'},oracle=False,workdir=tmp_path)==2


@pytest.mark.parametrize('broken',[False,True])
def test_operator_gold_uses_real_managed_bwrap_worker(tmp_path,monkeypatch,managed,broken):
    """Replace only worker engine; keep original worker validation and main."""
    from eval.containers import python_service
    dataset,runtime=managed
    module=control()
    neighbor=tmp_path/'private-neighbor';neighbor.write_text('PRIVATE OPERATOR NEIGHBOR')
    bootstrap=tmp_path/'trusted-operator-bootstrap.py'
    bootstrap.write_text('''import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('original_scienceworld_oracle_worker', '''+repr(str(ASSETS/'oracle_worker.py'))+''')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
created=[]
class Engine:
    def __init__(self): self.closed=False
    def load(self,task,variation,simplifications,generateGoldPath):
        assert (task,variation,simplifications,generateGoldPath)==('authored-selected',7,'',True)
        assert not Path('''+repr(str(neighbor))+''').exists()
        try: Path(__file__).write_text('mutation')
        except OSError: pass
        else: raise AssertionError('operator source writable')
    def get_gold_action_sequence(self):
        if '''+repr(broken)+''': raise RuntimeError('PRIVATE OPERATOR ERROR')
        return ['authored step','authored finish']
    def close(self): self.closed=True
def make_env(workdir,*,step_budget,server_path):
    assert step_budget==100
    assert str(server_path)=='''+repr(str(dataset._runtime_payload(runtime)))+'''
    engine=Engine();created.append(engine);return engine
worker.scw_env.make_env=make_env
try: result=worker.main()
finally:
    assert len(created)==1 and created[0].closed
raise SystemExit(result)
''')
    original=python_service.command
    commands=[]
    def command(argv,**kwargs):
        assert argv[:3]==[runtime['python'],'-I','-B']
        assert argv[3]==str(ASSETS/'oracle_worker.py')
        assert kwargs['runtime_paths']==runtime['runtime_paths']
        argv=[*argv[:3],str(bootstrap),*argv[4:]]
        kwargs['read_paths']=[*kwargs['read_paths'],str(bootstrap)]
        result=original(argv,**kwargs)
        commands.append(result)
        return result
    monkeypatch.setattr(python_service,'command',command)
    row={'task_id':'authored-selected/7','task_name':'authored-selected','variation':7}
    if broken:
        with pytest.raises(RuntimeError,match='unmeasured') as caught: module._gold(row,tmp_path)
        assert 'PRIVATE OPERATOR ERROR' not in str(caught.value)
    else:
        assert module._gold(row,tmp_path)==['authored step','authored finish']
    assert len(commands)==1 and '--unshare-net' in commands[0]
    assert neighbor.read_text()=='PRIVATE OPERATOR NEIGHBOR'
