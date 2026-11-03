"""Native Harbor startup/stop cannot leave Docker cleanup to a blocked worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from eval.run import native_supervisor as supervisor


@pytest.mark.parametrize('profile,names', [
    ('observed', ['hg-observed-' + 'b' * 20, 'hg-observed-' + 'b' * 20 + '-seed']),
    ('public', ['hg-native-public-' + 'c' * 20]),
])
def test_harbor_stuck_startup_is_killed_then_exact_owned_names_reaped(tmp_path, monkeypatch, profile, names):
    docker = tmp_path / 'docker'
    log = tmp_path / 'docker.jsonl'
    docker.write_text('''#!/usr/bin/env python3
import json,os,sys
with open(os.environ['CONTROL_DOCKER_LOG'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1]=='inspect':
 print('Error response from daemon: no such object',file=sys.stderr);raise SystemExit(1)
''')
    docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('CONTROL_DOCKER_LOG', str(log))
    helper = tmp_path / 'blocked-startup.py'
    ready = tmp_path / 'ready'
    helper.write_text('''import json,os,signal,time
from pathlib import Path
Path(os.environ['HG_NATIVE_CLEANUP_PATH']).write_text(json.dumps({'schema':'harnessgrad.native-cleanup.v1','containers':NAMES}))
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path(READY).write_text('runtime allocated; startup blocked')
while True:time.sleep(.01)
'''.replace('NAMES', repr(names)).replace('READY', repr(str(ready))))
    receipt = tmp_path / 'receipt.json'
    args = SimpleNamespace(receipt=receipt)
    def interrupt():
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        os.kill(os.getpid(), signal.SIGTERM)
    sender = threading.Thread(target=interrupt)
    sender.start()
    before = time.monotonic()
    try:
        status = supervisor.supervise(args, helper, [], worker_flag='HG_NATIVE_HARBOR_RELAY_WORKER',
                                      grace_s=.1, cleanup_s=2, label='harbor')
    finally:
        sender.join(timeout=3)
    assert status == 3 and time.monotonic() - before < 3
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[0] == ['rm', '--force', '--volumes', *names]
    assert [row[-1] for row in rows[1:]] == names
    assert not receipt.exists(), 'forced startup must not fabricate a successful episode'


def test_owned_name_publication_precedes_runtime_entry(tmp_path, monkeypatch):
    path = tmp_path / 'owned.json'
    monkeypatch.setenv('HG_NATIVE_CLEANUP_PATH', str(path))
    runtime = SimpleNamespace(name='hg-native-public-' + 'f' * 20)
    supervisor.publish_runtime(runtime)
    assert json.loads(path.read_text()) == {
        'schema': 'harnessgrad.native-cleanup.v1', 'containers': [runtime.name]}
    with pytest.raises(FileExistsError):
        supervisor.publish_runtime(runtime)
