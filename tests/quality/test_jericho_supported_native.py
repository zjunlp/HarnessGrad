"""Opt-in actual Jericho 3.3.1 TextWorld-ABI game, relay, client and shipped scorer.

Only original CC0 story bytes are used. No released game, model or benchmark.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT/'data/datasets/jericho'

PROBE = r'''
import importlib.util,json,sys
from pathlib import Path
root,rom=map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location('native_relay',root/'data/datasets/jericho/relay.py')
relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)
e=relay._make_public_env(rom,seed=0)
try:
    assert e.is_fully_supported and e._seed==0 and e.bindings=={}
    assert e.get_max_score()==2 and e.get_inventory()==[]
    # Completion requires the real take-token transition, not just an end marker.
    _,reward,done,info=e.step('finish')
    assert reward==0 and not done and info=={'moves':1,'score':0}
    e.reset()
    _,reward,done,info=e.step('take token')
    assert reward==1 and not done and info=={'moves':1,'score':1}
    inventory=e.get_inventory()
    assert len(inventory)==1 and inventory[0].num==3 and inventory[0].parent==2
    _,reward,done,info=e.step('finish')
    assert reward==1 and done and info=={'moves':2,'score':2}
finally: e.close()
print(json.dumps({'fully_supported':True,'max_score':2,'inventory_object':3,'terminal_score':2,'done':True}))
'''


@pytest.fixture
def short_relay_socket():
    # Linux pathname AF_UNIX sockets have a small fixed path limit. Keep this
    # owned IPC endpoint independent of the per-file pytest directory depth.
    with tempfile.TemporaryDirectory(prefix='hg-jericho-', dir='/tmp') as directory:
        yield Path(directory) / 'tool.sock'


def test_supported_authored_game_native_relay_client_and_shipped_score(tmp_path, monkeypatch, short_relay_socket):
    store=os.environ.get('HG_TEST_JERICHO_RUNTIME_ROOT')
    if not store:
        pytest.skip('explicit native control requires HG_TEST_JERICHO_RUNTIME_ROOT')
    from harnessgrad import dataset_runtime
    from data.scaffolds.scorer_input import inventory_digest
    from eval.service.tool_transport import call
    runtime=dataset_runtime.resolve_execution('jericho',root=store)
    binding=dataset_runtime.bind(runtime,'service')
    dataset_runtime.admit_binding(binding,'service')
    env=dataset_runtime._child_environment()
    rom=tmp_path/'tw-authored-token.z5'
    rom.write_bytes(runpy.run_path(str(ASSETS/'authored_story.py'))['story_bytes']())
    rom_bytes=rom.read_bytes()
    probe=subprocess.run([runtime['python'],'-I','-B','-c',PROBE,str(ROOT),str(rom)],
                         env=env,text=True,capture_output=True,timeout=30)
    assert probe.returncode==0,probe.stdout+probe.stderr
    assert json.loads(probe.stdout)['fully_supported']
    case=tmp_path/'case.json'
    case.write_text(json.dumps({'rom_path':str(rom),'rom_sha256':hashlib.sha256(rom_bytes).hexdigest(),'seed':0}))
    sock=short_relay_socket;receipt=tmp_path/'relay-receipt.json'
    client=runpy.run_path(str(ASSETS/'cli/jer-relay'))['main']
    client.__globals__['SOCKET']=str(sock)
    game=tmp_path/'jer_game.json'
    game.write_text(json.dumps({'task_id':'authored-native-control','rom':rom.name,'seed':0}))
    dataset_runtime.admit_binding(binding,'service')
    with (tmp_path/'relay.stdout').open('w') as stdout,(tmp_path/'relay.stderr').open('w') as stderr:
        proc=subprocess.Popen([runtime['python'],'-I','-B',str(ASSETS/'relay.py'),
            '--case-spec',str(case),'--socket',str(sock),'--receipt',str(receipt)],env=env,stdout=stdout,stderr=stderr)
        try:
            deadline=time.monotonic()+20
            while not sock.exists():
                assert proc.poll() is None,(tmp_path/'relay.stderr').read_text()
                assert time.monotonic()<deadline,'native relay socket not ready'
                time.sleep(.02)
            def command(*arguments):
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    assert client(['--workdir',str(tmp_path),*arguments],transport=call)==0
                return output.getvalue()
            assert 'score 0/2' in command('look')
            assert not (tmp_path/'jer_actions.jsonl').exists()
            assert 'score 1/2' in command('do','take','token')
            assert 'token' in command('inventory')
            assert 'score 1/2; moves 1; actions logged 1' in command('score')
            assert 'game is over' in command('do','finish')
            assert 'score 2/2; moves 2; actions logged 2' in command('score')
            command('done');command('done')
        finally:
            if proc.poll() is None: proc.terminate()
            proc.wait(timeout=15)
    assert proc.returncode==0,(tmp_path/'relay.stderr').read_text()
    assert not sock.exists()
    relay_receipt=json.loads(receipt.read_text())
    assert relay_receipt['stopped_cleanly']
    assert relay_receipt['transport']['errors']==relay_receipt['transport']['refused']==0
    dataset_runtime.admit_binding(binding,'service')
    log=[json.loads(line) for line in (tmp_path/'jer_actions.jsonl').read_text().splitlines()]
    assert log==[{'kind':'action','step':0,'action':'take token'},
                 {'kind':'action','step':1,'action':'finish'},{'kind':'done','step':2}]
    files={game.name:hashlib.sha256(game.read_bytes()).hexdigest(),rom.name:hashlib.sha256(rom_bytes).hexdigest()}
    score_args=['--workdir','.', '--game',game.name,'--rom',rom.name,
                '--reward-file','reward.txt','--evidence-file','evidence.json',
                '--task-id','authored-native-control','--scorer-input-sha256',inventory_digest(files),
                '--scorer-input-files',json.dumps(files,sort_keys=True)]
    bootstrap=("import runpy,sys;root=sys.argv.pop(1);sys.path[:0]=[root,root+'/data/datasets/jericho/lib'];"
               "sys.argv[0]=root+'/data/datasets/jericho/verify/check.py';runpy.run_path(sys.argv[0],run_name='__main__')")
    scored=subprocess.run([runtime['python'],'-I','-B','-c',bootstrap,str(ROOT),*score_args],
                          cwd=tmp_path,env=env,text=True,capture_output=True,timeout=30)
    assert scored.returncode==0,scored.stdout+scored.stderr
    evidence=json.loads((tmp_path/'evidence.json').read_text())
    assert evidence['measured'] and evidence['scorer_input']['verified']
    assert (evidence['score'],evidence['max_score'],evidence['moves'],evidence['reward'])==(2,2,2,1)
    assert evidence['game_over'] and evidence['agent_declared_done']
    assert [(s['score'],s['done']) for s in evidence['steps']]==[(1,False),(2,True)]
    assert rom.read_bytes()==rom_bytes
    dataset_runtime.admit_binding(binding,'service')
    sealed_receipt = None
    native_store = os.environ.get('HG_TEST_JERICHO_NATIVE_STORE')
    if native_store:
        from data.runtimes import NATIVE_PINS
        from eval.verification.native_runtime import admit
        monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', native_store)
        outputs = tmp_path/'verifier'; outputs.mkdir()
        sealed_args = list(score_args)
        sealed_args[sealed_args.index('reward.txt')] = 'verifier/reward.txt'
        sealed_args[sealed_args.index('evidence.json')] = 'verifier/evidence.json'
        with admit(NATIVE_PINS['jericho']) as frozen:
            sealed = frozen.run(['python3','check.py',*sealed_args], workdir=tmp_path,
                                reward_dir=outputs, timeout_s=30)
            sealed_receipt = dict(frozen.receipt)
        assert sealed.returncode == 0, sealed.stdout+sealed.stderr
        sealed_evidence = json.loads((outputs/'evidence.json').read_text())
        assert sealed_evidence['scorer_input']['verified'] and sealed_evidence['measured']
        assert (sealed_evidence['score'],sealed_evidence['max_score'],sealed_evidence['reward']) == (2,2,1)
    (tmp_path/'native-control.json').write_text(json.dumps({'schema':'harnessgrad.jericho-authored-native.v1',
        'story_sha256':hashlib.sha256(rom_bytes).hexdigest(),'binding':binding,
        'fully_supported':True,'scorer_route':'shipped-check-in-execution-artifact',
        'sealed_native_scorer_verified':sealed_receipt is not None, 'sealed_receipt':sealed_receipt,'released_benchmark_measured':False,
        'relay':relay_receipt,'native_probe':json.loads(probe.stdout),'scorer_input_verified':True,
        'score':2,'maximum':2,'reward':1},indent=2)+'\n')
