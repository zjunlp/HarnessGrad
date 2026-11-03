"""SWE relay shutdown must reap exactly its allocated containers before outer SIGKILL."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from data.scaffolds.flashrsi_research import swe_relay as relay
from data.scaffolds.flashrsi_research import swe_native as native


@pytest.mark.parametrize('ignore_signal', [False, True])
def test_stop_during_startup_reaps_published_names_with_bounded_grace(tmp_path, monkeypatch, ignore_signal):
    fake = tmp_path / 'docker'
    log = tmp_path / 'docker-calls.jsonl'
    fake.write_text('''#!/usr/bin/env python3
import json,os,sys
with open(os.environ['HG_TEST_DOCKER_LOG'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1]=='inspect':
 print('Error: No such container',file=sys.stderr);raise SystemExit(1)
''')
    fake.chmod(0o755)
    monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    monkeypatch.setenv('HG_TEST_DOCKER_LOG',str(log))
    helper=tmp_path/'worker.py'
    helper.write_text('''import json,os,signal,time
from pathlib import Path
Path(os.environ['HG_SWE_CLEANUP_PATH']).write_text(json.dumps({'schema':'harnessgrad.swe-cleanup.v1','containers':['hg-observed-aaaaaaaaaaaaaaaaaaaa','hg-observed-aaaaaaaaaaaaaaaaaaaa-seed']}))
signal.signal(signal.SIGTERM, signal.SIG_IGN if IGNORE else lambda *_:exit(3))
Path(READY).write_text('ready')
while True:time.sleep(.05)
'''.replace('IGNORE',repr(ignore_signal)).replace('READY',repr(str(tmp_path/'ready'))))
    actual_popen=subprocess.Popen
    def child(_argv,**kwargs):
        return actual_popen(_argv if _argv[0]=='docker' else [sys.executable,str(helper)],**kwargs)
    monkeypatch.setattr(relay.subprocess,'Popen',child)
    monkeypatch.setattr(relay,'arguments',lambda:SimpleNamespace(receipt=tmp_path/'receipt.json'))
    monkeypatch.setattr(relay,'GRACE_S',.15)
    original={s:signal.getsignal(s) for s in (signal.SIGTERM,signal.SIGINT)}
    def interrupt():
        deadline=time.monotonic()+3
        while not (tmp_path/'ready').exists() and time.monotonic()<deadline:time.sleep(.01)
        os.kill(os.getpid(),signal.SIGTERM)
    sender=threading.Thread(target=interrupt);sender.start()
    start=time.monotonic()
    try:
        assert relay.main()==3
    finally:
        sender.join(timeout=3)
        for sig,handler in original.items():signal.signal(sig,handler)
    assert time.monotonic()-start<3
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0]==['rm','--force','--volumes','hg-observed-aaaaaaaaaaaaaaaaaaaa','hg-observed-aaaaaaaaaaaaaaaaaaaa-seed']
    assert [call[-1] for call in calls[1:]]==['hg-observed-aaaaaaaaaaaaaaaaaaaa','hg-observed-aaaaaaaaaaaaaaaaaaaa-seed']


def test_cleanup_rejects_foreign_container_name_without_running_docker(tmp_path, monkeypatch):
    path=tmp_path/'ownership.json'
    path.write_text(json.dumps({'schema':'harnessgrad.swe-cleanup.v1','containers':['another-users-container']}))
    def forbidden(*a,**kw):raise AssertionError('foreign name reached docker')
    monkeypatch.setattr(relay.subprocess,'run',forbidden)
    result=relay.cleanup_names(path)
    assert result['complete'] is False and result['error_type']=='ValueError'


def test_actor_close_is_idempotent_and_retains_first_failure(monkeypatch):
    calls=[]
    runtime=SimpleNamespace(receipt={'cleanup':{'complete':False,'errors':['original cleanup failure']}},close=lambda:calls.append('close'))
    session=object.__new__(native.Session)
    session.runtime=runtime;session._closed=False;session._close_error=None
    with pytest.raises(RuntimeError,match='cleanup failed'):session.close()
    with pytest.raises(RuntimeError,match='cleanup failed'):session.close()
    assert calls==['close']
    assert runtime.receipt['cleanup']['errors']==['original cleanup failure']
