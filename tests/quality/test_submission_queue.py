"""FIFO service ownership and recovery, independent of benchmark scoring."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from console import submissions as backend

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def service_factory(tmp_path,monkeypatch):
    instances=[]
    def make(name='store',**kw):
        root=tmp_path/name
        for part in ('submissions','runs','budget'):
            (root/part).mkdir(parents=True,exist_ok=True)
        obj=backend.SubmissionService(platform_root=ROOT,submissions_root=root/'submissions',
            runs_root=root/'runs',work_root=root/'work',budget_dir=root/'budget',
            liveness=kw.pop('liveness',lambda:(True,{})),**kw)
        instances.append(obj)
        return obj
    yield make
    for instance in reversed(instances):
        close=getattr(instance,'close',None)
        if close:
            close()


def start(instance):
    assert callable(getattr(instance,'start',None)), 'A6 lifecycle is missing'
    instance.start()


def test_only_one_service_owns_root(service_factory):
    first=service_factory()
    second=service_factory()
    start(first)
    with pytest.raises(backend.SubmissionRejected) as caught:
        start(second)
    assert caught.value.problems[0]['code']=='service-not-ready'
    first.close()
    start(second)


def seed(instance,identity,status,**fields):
    (instance.root/identity).mkdir(exist_ok=True)
    data={'submission_id':identity,'run_id':identity,'created_at':'2026-10-30T00:00:00Z',
          'scenario':{'name':'demo-smoke','side':'train'},'status':status,
          'history':[{'status':status,'at':'2026-10-30T00:00:00Z'}],**fields}
    instance._save(data)
    return data


def test_execution_lease_survives_parent_exit(tmp_path):
    assert callable(getattr(backend,'_lease',None)), 'A6 execution lease is missing'
    lock=tmp_path/'execution.lock'
    # A real child inherits the actual FD; no fake PID/return-code isolation claim.
    parent_code='''import os,subprocess,sys
from pathlib import Path
from console.submissions import _lease
fd=_lease(Path(sys.argv[1]))
p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(1.5)'],pass_fds=(fd,))
Path(sys.argv[2]).write_text(str(p.pid))
os._exit(0)
'''
    receipt=tmp_path/'child.pid'
    subprocess.run([sys.executable,'-c',parent_code,str(lock),str(receipt)],cwd=ROOT,check=True)
    assert receipt.exists()
    assert backend._lease(lock) is None
    until=time.monotonic()+5
    while time.monotonic()<until:
        fd=backend._lease(lock)
        if fd is not None:
            os.close(fd)
            break
        time.sleep(.03)
    else:
        pytest.fail('inherited lease was never released')


def wait_until(predicate,timeout=5):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if predicate():
            return
        time.sleep(.01)
    pytest.fail('condition did not become true')


def admissions(service_factory,monkeypatch):
    monkeypatch.setattr(backend,'scenario_status',lambda *a,**kw:{'available':True})
    return service_factory


def test_fifo_two_archives_same_tree_and_true_queue_full(service_factory,tmp_path,monkeypatch):
    from test_submission_gate import archive
    instance=admissions(service_factory,monkeypatch)(max_queue=2)
    start(instance)
    fd=backend._lease(instance.root/'.control/execute.lock')
    seen=[]
    def run(plan,env,lease_fd):
        seen.append(plan['submission_id'])
        instance._update(plan['submission_id'],status='failed',error={'code':'internal-error','detail':'fixture'})
    monkeypatch.setattr(instance,'_run',run)
    try:
        one=archive(tmp_path,kind='tar',name='one.tar')
        two=archive(tmp_path,kind='zip',name='two.zip')
        first_code,first=instance.submit(one,'demo-smoke',driver_env={})
        second_code,second=instance.submit(two,'demo-smoke',driver_env={})
        assert [first_code,second_code]==[202,202]
        assert [first['queue_position'],second['queue_position']]==[1,2]
        assert first['tree_sha256']==second['tree_sha256']
        with pytest.raises(backend.SubmissionRejected) as duplicate:
            instance.submit(one,'demo-smoke',driver_env={})
        assert duplicate.value.problems[0]['code']=='duplicate-submission'
        three=archive(tmp_path,kind='tar.gz',name='three.tar.gz')
        with pytest.raises(backend.SubmissionRejected) as full:
            instance.submit(three,'demo-smoke',driver_env={})
        assert full.value.problems[0]['code']=='queue-full'
        assert not seen
    finally:
        os.close(fd)
    wait_until(lambda:len(seen)==2)
    assert seen==[first['submission_id'],second['submission_id']]


@pytest.mark.parametrize('before,after',[
    ({'HG_AGENT_MODEL':'reviewed'}, {'HG_AGENT_MODEL':'changed'}),
    ({'HG_SANDBOX_MEMORY_MB':'4096'}, {'HG_SANDBOX_MEMORY_MB':'8192'}),
    ({'HG_SANDBOX_CPUS':'2'}, {'HG_SANDBOX_CPUS':'4'}),
    ({'HG_SANDBOX_PIDS':'1024'}, {'HG_SANDBOX_PIDS':'2048'}),
])
def test_restart_only_runs_never_launched_queue_and_compares_environment(service_factory,tmp_path,monkeypatch,before,after):
    from test_submission_gate import archive
    make=admissions(service_factory,monkeypatch)
    instance=make()
    start(instance)
    fd=backend._lease(instance.root/'.control/execute.lock')
    try:
        _,ack=instance.submit(archive(tmp_path),'demo-smoke',driver_env=before)
        instance.close()
        recovered=make(env_resolver=lambda name:after)
        calls=[]
        monkeypatch.setattr(recovered,'_run',lambda *a:calls.append(a))
        start(recovered)
    finally:
        os.close(fd)
    wait_until(lambda:recovered._load(ack['submission_id'])['status']=='failed')
    assert not calls
    assert 'environment' in recovered._load(ack['submission_id'])['error']['detail']


def test_recovery_never_guesses_exit_zero_or_restarts_running(service_factory,monkeypatch):
    instance=service_factory()
    identity='sub-20261030T000000Z-1234abcd'
    seed(instance,identity,'running',pid=999999999)
    calls=[]
    monkeypatch.setattr(instance,'_run',lambda *a:calls.append(a))
    start(instance)
    assert instance._load(identity)['status']=='failed'
    assert instance._load(identity)['error']['code']=='internal-error'
    assert not calls


def test_recovery_rejects_unreadable_head_and_dispatches_valid_next(service_factory,tmp_path,monkeypatch):
    def resolve(name):
        backend._scenario(name,ROOT)
        return {}
    instance=service_factory(env_resolver=resolve)
    first='sub-20261101T000000Z-11111111'
    second='sub-20261101T000001Z-22222222'
    fields={'environment':instance._environment({}),'scenario_receipt':{},
            'tree_sha256':'fixture','harness_dir':str(tmp_path/'not-launched'), 'argv':[]}
    seed(instance,first,'queued',scenario={'name':'removed-scene','side':'train'},queue_seq=1,**fields)
    seed(instance,second,'queued',queue_seq=2,**fields)
    launched=[]
    def run(plan,env,lease_fd):
        launched.append(plan['submission_id'])
        instance._update(plan['submission_id'],status='failed',error={'code':'internal-error','detail':'fixture; no driver'})
    monkeypatch.setattr(instance,'_run',run)
    start(instance)
    wait_until(lambda:instance._load(second)['status']=='failed')
    assert instance._load(first)['status']=='failed'
    assert instance._load(first)['error']['code']=='scenario-not-available'
    assert launched==[second] and not instance._queue
    assert not list(instance.runs.iterdir())


def test_queued_receipt_publication_error_rejects_without_stranding(service_factory,tmp_path,monkeypatch):
    from test_submission_gate import archive
    instance=admissions(service_factory,monkeypatch)()
    start(instance)
    fd=backend._lease(instance.root/'.control/execute.lock')
    real=instance._update
    def publish(identity,**changes):
        data=real(identity,**changes)
        if changes.get('status')=='queued':
            raise OSError('directory fsync failed after replace')
        return data
    monkeypatch.setattr(instance,'_update',publish)
    try:
        with pytest.raises(OSError):
            instance.submit(archive(tmp_path),'demo-smoke',driver_env={})
        data=instance._receipts()[0]
        assert data['status']=='rejected'
        assert not instance._queue and not instance._tokens
        assert [p.name for p in (instance.root/data['submission_id']).iterdir()]==['receipt.json']
    finally:
        os.close(fd)
