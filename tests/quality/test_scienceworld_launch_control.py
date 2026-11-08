"""Authored-engine controls through the real managed service launcher.

No ScienceWorld world or native scorer runs. The real relay.main authenticates
its managed JAR and original staged Java executable before an authored Engine
replaces only scw_env.make_env. Tests require explicitly prepared artifacts.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'data/datasets/scienceworld'

ENGINE = '''
class Engine:
    def __init__(self): self.actions=[]; self.selected=None
    def load(self, task_name, variation, simplifications, generateGoldPath=False):
        assert (task_name,variation,simplifications,generateGoldPath)==('authored-selected',7,'',False)
        self.selected=f'{task_name}/{variation}'
        audit('load')
    def step(self,action):
        if action=='explode': raise RuntimeError('PRIVATE ENGINE FAILURE')
        self.actions.append(action)
        return (f'feedback:{action}:{len(self.actions)}',0,action=='finish' or len(self.actions)>=100,{'score':min(len(self.actions)*7,100)})
    def look(self): return f'room:{self.selected}:{len(self.actions)}'
    def inventory(self): return f'inventory:{len(self.actions)}'
    def get_task_description(self): return f'task:{self.selected}'
    def close(self): audit('close')
'''

@pytest.fixture
def managed(monkeypatch):
    from harnessgrad.dataset_runtime import resolve_execution, DatasetRuntimeUnavailable
    try:
        runtime=resolve_execution('scienceworld')
    except DatasetRuntimeUnavailable as exc:
        pytest.skip('explicit ScienceWorld execution preparation required: '+str(exc))
    import data.datasets.scienceworld as dataset
    try:
        java=dataset.JRE_STAGED/'bin/java'
        if not java.is_file(): pytest.skip('original pinned staged JRE required')
    except RuntimeError as exc:
        pytest.skip(str(exc))
    dataset._runtime_payload(runtime)
    return dataset, runtime


def setup_control(tmp_path, managed):
    dataset,runtime=managed
    row={'task_id':'authored-selected/7','task_name':'authored-selected','variation':7}
    setup=dataset._setup(row)
    neighbor=tmp_path/'private-neighbor.txt';neighbor.write_text('PRIVATE NEIGHBOR')
    bootstrap=tmp_path/'trusted-bootstrap.py'
    bootstrap.write_text('''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('scienceworld_original_relay', '''+repr(str(ASSETS/'relay.py'))+''')
relay=importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
def audit(kind):
    assert not Path('''+repr(str(neighbor))+''').exists(), 'private neighbor visible'
    try:
        Path(__file__).write_text('illegal mutation')
    except OSError:
        pass
    else:
        raise AssertionError('trusted source writable')
    with Path('authored-events.jsonl').open('a') as out:
        out.write(json.dumps({'kind':kind})+'\\n')
'''+ENGINE+'''
def make_env(workdir, *, step_budget, server_path):
    assert step_budget==100
    assert str(server_path)=='''+repr(str(dataset._runtime_payload(runtime)))+'''
    return Engine()
relay.scw_env.make_env=make_env
raise SystemExit(relay.main())
''')
    setup['tool_relay']['program']=str(bootstrap)
    setup['tool_relay']['read_paths'].append(str(bootstrap))
    return setup, bootstrap, neighbor


def run_client(tmp_path, setup, runtime, socket, command):
    """Run shipped stdlib CLI in a real bwrap view using its published route."""
    from eval.containers import python_service
    state=tmp_path/'client-service'
    state.mkdir(exist_ok=True)
    (state/'state').mkdir(exist_ok=True)
    work=state/'state'
    for item in setup['files']:
        target=work/item['path']
        if not target.exists():
            if 'from' in item: shutil.copyfile(item['from'],target)
            else: target.write_text(item['content'])
    # The client selects no engine/dependency API. -I needs its own explicit
    # directory import, already performed by the shipped scw script.
    argv=[runtime['python'],'-I','-B',str(work/'scw'),*command]
    # Program lives in the writable instance; command() correctly requires a
    # read-only program, so bind the original shipped program and helper instead.
    argv[3]=str(ASSETS/'cli/scw-relay')
    plan=python_service.command(argv,directory=state,runtime_paths=runtime['runtime_paths'],
          read_paths=[str(ASSETS/'cli/scw-relay'),str(ASSETS/'cli/scw_client.py')])
    pivot=plan.index('--')
    # Disable site initialization in the actor control: the shipped client must
    # work without accessing ScienceWorld/Py4J or any installed dependency.
    plan.insert(pivot+4, '-S')
    plan[pivot:pivot]=['--ro-bind',str(ROOT/'eval/service/tool_transport.py'),'/run/harnessgrad-tool/tool_transport.py',
                       '--ro-bind',str(socket),'/run/harnessgrad-tool/tool.sock']
    return subprocess.run(plan,capture_output=True,text=True,timeout=30),work


def old_client(tmp_path, command):
    work=tmp_path/'legacy';work.mkdir(exist_ok=True)
    if not (work/'scw').exists():
        shutil.copyfile(ASSETS/'cli/scw',work/'scw')
        (work/'scw_task.json').write_text(json.dumps({'task_name':'authored-selected','variation':7,'simplifications':''}))
        (work/'scw_env.py').write_text('''from pathlib import Path
exec(compile(Path('''+repr(str(ASSETS/'lib/scw_env.py'))+''').read_text(), 'original_scw_env', 'exec'))
def audit(kind): pass
'''+ENGINE+'''
def make_env(workdir, *, step_budget=100, server_path=None):
    assert step_budget==100
    return Engine()
''')
    return subprocess.run([sys.executable,str(work/'scw'),*command],cwd=work,capture_output=True,text=True,timeout=10),work


def test_actual_managed_launcher_original_main_and_cli_parity(tmp_path,managed):
    from eval.run import tool_relay
    setup,bootstrap,neighbor=setup_control(tmp_path,managed)
    before=hashlib.sha256(bootstrap.read_bytes()).hexdigest()
    handle=tool_relay.start(setup,private_diagnostics=True,ready_timeout_s=15)
    assert handle is not None
    try:
        for command in [('look',),('do','open door'),('look',),('inventory',),('task',),('do','finish'),('do','after terminal'),('look',),('done',),('done',)]:
            old,oldwork=old_client(tmp_path,command)
            new,newwork=run_client(tmp_path,setup,managed[1],handle.path,command)
            assert (new.returncode,new.stdout,new.stderr)==(old.returncode,old.stdout,old.stderr),command
            oldlog=(oldwork/'scw_actions.jsonl').read_bytes() if (oldwork/'scw_actions.jsonl').exists() else b''
            assert (newwork/'scw_actions.jsonl').read_bytes()==oldlog
        assert (newwork/'scw_actions.jsonl').read_bytes()==(oldwork/'scw_actions.jsonl').read_bytes()
        assert 'feedback:finish:2' in run_client(tmp_path,setup,managed[1],handle.path,('look',))[0].stdout
        handle.stop()
        assert handle.fault() is None
        assert handle.receipt['transport']['calls']>0
        events=[json.loads(x)['kind'] for x in (handle.directory/'state/authored-events.jsonl').read_text().splitlines()]
        assert events.count('load')==events.count('close') and events[-1]=='close'
        assert hashlib.sha256(bootstrap.read_bytes()).hexdigest()==before
        assert neighbor.read_text()=='PRIVATE NEIGHBOR'
    finally: handle.cleanup()


def test_actual_launcher_100_budget_and_log_history(tmp_path,managed):
    from eval.run import tool_relay
    setup,_,_=setup_control(tmp_path,managed)
    handle=tool_relay.start(setup,private_diagnostics=True,ready_timeout_s=15)
    try:
        # Seed 99 ordinary actions plus ignored malformed lines in both logs.
        rows='\n'.join(json.dumps({'kind':'action','step':n,'action':'wait'}) for n in range(99))+'\nnot-json\n[]\n'
        _,oldwork=old_client(tmp_path,('help',))
        _,newwork=run_client(tmp_path,setup,managed[1],handle.path,('help',))
        for work in (oldwork,newwork): (work/'scw_actions.jsonl').write_text(rows)
        for command in [('do','final'),('do','over budget'),('look',),('done',)]:
            old,_=old_client(tmp_path,command)
            new,_=run_client(tmp_path,setup,managed[1],handle.path,command)
            assert (new.returncode,new.stdout,new.stderr)==(old.returncode,old.stdout,old.stderr),command
        assert (newwork/'scw_actions.jsonl').read_bytes()==(oldwork/'scw_actions.jsonl').read_bytes()
        handle.stop();assert handle.fault() is None
    finally: handle.cleanup()


def test_actual_launcher_engine_exception_is_unknown_not_result(tmp_path,managed):
    from eval.run import tool_relay
    from eval.service.tool_transport import ToolTransportError,call
    setup,_,_=setup_control(tmp_path,managed)
    handle=tool_relay.start(setup,private_diagnostics=True,ready_timeout_s=15)
    try:
        result,work=run_client(tmp_path,setup,managed[1],handle.path,('do','explode'))
        assert result.returncode!=0 and result.stdout==''
        assert 'PRIVATE ENGINE FAILURE' not in result.stderr
        assert (work/'scw_actions.jsonl').read_text()==''
        with pytest.raises(ToolTransportError): call(handle.path,'gold',{'actions':[]})
        handle.stop()
        assert handle.fault() is not None
        assert handle.receipt['transport']['errors']>0
        events=[json.loads(x)['kind'] for x in (handle.directory/'state/authored-events.jsonl').read_text().splitlines()]
        assert events==['load','close']
    finally: handle.cleanup()


@pytest.mark.parametrize('field',['jar','java_sha256'])
def test_original_main_refuses_changed_payload_identity_before_requests(tmp_path,managed,field):
    from eval.run import tool_relay
    setup,_,_=setup_control(tmp_path,managed)
    spec=json.loads(setup['tool_relay']['content'])
    if field=='jar':
        fake=tmp_path/'mismatched-scienceworld.jar';fake.write_bytes(b'authored mismatched JAR')
        spec['jar']=str(fake)
        setup['tool_relay']['read_paths'].append(str(fake))
    else:
        spec['java_sha256']='0'*64
    setup['tool_relay']['content']=json.dumps(spec)
    with pytest.raises(tool_relay.ToolRelayStartError) as caught:
        tool_relay.start(setup,private_diagnostics=True,ready_timeout_s=15)
    assert caught.value.report['receipt'] is None
    assert caught.value.report['faults']
    diagnostic=caught.value.diagnostic['files']['relay.log']['content']
    assert ('ScienceWorld JAR identity mismatch' if field=='jar' else 'ScienceWorld Java identity mismatch') in diagnostic


def test_unmodified_relay_main_starts_without_any_engine_or_jvm(tmp_path,managed):
    from eval.run import tool_relay
    dataset,_=managed
    setup=dataset._setup({'task_id':'authored-selected/7','task_name':'authored-selected','variation':7})
    handle=tool_relay.start(setup,private_diagnostics=True,ready_timeout_s=15)
    try:
        handle.stop()
        assert handle.fault() is None
        assert handle.receipt['transport']['calls']==0
    finally: handle.cleanup()
