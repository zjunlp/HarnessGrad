"""Real subprocess/namespace controls for opt-in dataset runtime extensions."""
import json
from pathlib import Path

import pytest

from eval.run import runner, tool_relay
from eval.verification.result_contract import apply

ROOT = Path(__file__).resolve().parents[2]
TRANSPORT = ROOT / 'eval/service/tool_transport.py'


def relay_program(tmp_path, *, recording=True):
    p = tmp_path / 'relay.py'
    p.write_text(f'''
import sys, time, signal, json, os, argparse
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from eval.service.tool_transport import Relay
p=argparse.ArgumentParser()
p.add_argument('--socket');p.add_argument('--receipt');p.add_argument('--game')
a=p.parse_args()
done=False
answers=[]
def finish(*args):
    global done
    done=True
signal.signal(signal.SIGTERM,finish)
def handle(op,args):
    if op=='submit':
        answers.append(args['answer'])
        return 'accepted'
    return 'observation'
with Relay(a.socket, handle) as relay:
    while not done: time.sleep(0.01)
Path(a.receipt).write_text(json.dumps(relay.receipt))
if {recording!r} and 'HG_RELAY_RECORD_PATH' in os.environ:
    Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps({{'answers':answers}}))
''')
    return p


def setup_for(tmp_path, *, record=True):
    decl = {'program': str(relay_program(tmp_path)), 'content': 'private task bytes'}
    if record:
        decl['record'] = {'path': 'trusted_episode'}
    return {'tool_relay': decl, 'files': [{'path': 'transport.py', 'from': str(TRANSPORT)}]}


def harness(tmp_path, body):
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo/'harness.json').write_text(json.dumps({'entrypoint': 'agent.py'}))
    (repo/'agent.py').write_text('''import argparse,sys,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
w=Path(a.workdir);sys.path.insert(0,str(w))
'''+body)
    return repo


def test_record_is_hidden_during_actor_and_readonly_for_verifier(tmp_path, monkeypatch):
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HG_JUDGE_API_KEY', 'never-forward-this')
    repo = harness(tmp_path, '''from transport import call
assert 'HG_JUDGE_API_KEY' not in os.environ
assert not (w/'trusted_episode').exists()
call('/run/harnessgrad-tool/tool.sock','submit',{'answer':'accepted-answer'})
(w/'answer.txt').write_text('actor claim is irrelevant')
''')
    check = '''import json
from pathlib import Path
r=Path('trusted_episode/episode.json')
v=json.loads(r.read_text())
assert v['binding']['task_id']=='t'
assert v['binding']['trial_id']=='trial-7'
assert v['payload']['answers']==['accepted-answer']
try:
 r.write_text('forged')
 raise AssertionError('record is writable')
except OSError: pass
Path('verifier/reward.txt').write_text('1')
'''
    spec = {'kind':'command','argv':['python','check.py'], 'reward_file':'/verifier/reward.txt',
            'inputs':[{'path':'check.py','content':check}]}
    archive = tmp_path/'archives'
    result = runner.run_one(repo, {'task_id':'t','goal':'submit'}, setup=setup_for(tmp_path),
                            verifier=spec, run_id='r', trial_id='trial-7', recordings_root=archive)
    assert result.get('verdict', {}).get('score') == 1, result
    report = result['runtime_services']['tool_relay']
    assert report['cleanup'] == {'complete':True,'errors':[]}
    saved = Path(report['recording']['archive'])
    assert saved.is_file()
    assert json.loads(saved.read_text())['payload']['answers']==['accepted-answer']


def test_missing_record_is_unmeasured_and_cleaned(tmp_path):
    setup = setup_for(tmp_path)
    setup['tool_relay']['program'] = str(relay_program(tmp_path,recording=False))
    result=runner.run_one(harness(tmp_path,'pass'), {'task_id':'t'}, setup=setup,
        verifier={'kind':'command','argv':['true']})
    assert result['invalid']['stage']=='tool recording'
    assert result['runtime_services']['tool_relay']['cleanup']['complete']


def test_optin_record_refuses_unsandboxed_path_before_launch(tmp_path):
    result=runner.run_one(harness(tmp_path,'pass'), {'task_id':'t'}, sandbox=False,
                         setup=setup_for(tmp_path),verifier={'kind':'command','argv':['true']})
    assert result['invalid']['stage']=='tool recording admission'


def test_legacy_relay_needs_no_record_and_is_cleaned(tmp_path):
    result=runner.run_one(harness(tmp_path,"(w/'answer.txt').write_text('yes')"),
                         {'task_id':'t'}, setup=setup_for(tmp_path,record=False),
                         verifier={'kind':'answer','expected':'yes'})
    assert result['verdict']['passed'],result
    assert result['runtime_services']['tool_relay']['recording'] is None
    assert result['runtime_services']['tool_relay']['cleanup']['complete']


def test_startup_failure_cleans_private_directory(tmp_path, monkeypatch):
    p=tmp_path/'broken.py';p.write_text('raise RuntimeError("startup")')
    dirs=[]
    original=tool_relay.tempfile.mkdtemp
    def create(*a,**k):
        d=original(*a,**k);dirs.append(Path(d));return d
    monkeypatch.setattr(tool_relay.tempfile,'mkdtemp',create)
    with pytest.raises(RuntimeError,match='startup'):
        tool_relay.start({'tool_relay':{'program':str(p),'content':'x'}})
    assert dirs and all(not d.exists() for d in dirs)


def test_stop_and_cleanup_are_idempotent(tmp_path):
    h=tool_relay.start(setup_for(tmp_path,record=False))
    h.stop(); receipt=dict(h.receipt);h.stop();h.cleanup();h.cleanup()
    assert h.receipt==receipt and not h.fault()
    assert not h.directory.exists()


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-1,2,None])
def test_strict_scores_reject_invalid_values_without_changing_legacy(bad):
    v={'score':bad,'passed':True}
    assert apply(v,None) is v
    strict=apply(v,{'version':1,'score_range':[0,1]})
    assert strict['score'] is None and strict['outcome']=='unmeasured'


def test_explicit_unmeasured_wins_over_reward_only_when_opted_in():
    v={'score':0,'passed':False,'evidence':{'measured':False,'error':'judge timeout'}}
    assert apply(v,None)['score']==0
    assert apply(v,{'version':1,'score_range':[0,1]})['score'] is None


def test_cleanup_failure_is_reported_without_overwriting_receipt(tmp_path,monkeypatch):
    h=tool_relay.start(setup_for(tmp_path,record=False));h.stop()
    original=tool_relay.shutil.rmtree
    def fail(path,*args,**kw):raise PermissionError('fixture cleanup failure')
    monkeypatch.setattr(tool_relay.shutil,'rmtree',fail)
    assert h.cleanup() is None
    assert h.report()['receipt'] is not None
    assert h.report()['cleanup']['errors']
    monkeypatch.setattr(tool_relay.shutil,'rmtree',original)
    h.cleanup()


@pytest.mark.parametrize('frame,error', [
    (b'{not-json}', 'bad_request'),
    (b'\xff', 'bad_request'),
    (b'[' * 2000, 'bad_request'),
    (b'[]', 'bad_request'),
    (b'null', 'bad_request'),
    (b'{}', 'bad_op'),
    (b'{"op": ""}', 'bad_op'),
    (b'{"op": 1}', 'bad_op'),
    (b'{"op": "ping", "args": [1]}', 'bad_args'),
])
def test_invalid_json_request_does_not_kill_server(tmp_path, frame, error):
    from eval.service.tool_transport import Relay,call
    import socket
    with Relay(tmp_path/'test.sock',lambda op,args:'alive') as server:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.connect(str(server.path));client.sendall(frame + b'\n')
            assert json.loads(client.recv(4096)) == {'ok': False, 'error': error}
        assert call(server.path,'ping')=='alive'
        assert server.receipt['calls'] == 1
        assert server.receipt['errors'] == server.receipt['request_errors'] == 1
        assert server.receipt['refused'] == 0


@pytest.mark.parametrize('failure', ['handler', 'serialization'])
def test_service_errors_are_not_classified_as_invalid_requests(tmp_path, failure):
    from eval.service.tool_transport import Relay, ToolTransportError, call
    def handle(op, args):
        if failure == 'handler':
            raise RuntimeError('fixture service failed')
        return {1, 2, 3}
    with Relay(tmp_path/'test.sock', handle) as server:
        with pytest.raises(ToolTransportError):
            call(server.path, 'ping')
        assert server.receipt['errors'] == 1
        assert server.receipt['request_errors'] == 0


def test_parser_runtime_failure_is_not_an_invalid_request(tmp_path, monkeypatch):
    from eval.service import tool_transport
    def failed_parser(raw):
        raise MemoryError('fixture parser failure')
    monkeypatch.setattr(tool_transport.json, 'loads', failed_parser)
    server = tool_transport.Relay(tmp_path/'test.sock', lambda *_: 'unused')
    assert server._handle(b'{}') == {'ok': False, 'error': 'bad_request'}
    assert server.receipt['errors'] == 1
    assert server.receipt['request_errors'] == 0


def test_recording_symlink_is_rejected(tmp_path):
    h=tool_relay.start(setup_for(tmp_path),context={'task_id':'t','run_id':'r','trial_id':'1'})
    try:
        h.stop()
        target=h.directory/'episode.json';target.unlink();target.symlink_to('/etc/passwd')
        with pytest.raises(OSError):h.recording_input()
    finally:h.cleanup()
