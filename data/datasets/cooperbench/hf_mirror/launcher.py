#!/usr/local/bin/python3
"""Dedicated runtime interpreter: launch the immutable mirror only at container idle."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/opt/hg-hf')
PRIVATE = Path('/tmp/.hg-hf-private')
PUBLIC = Path('/tmp/hg-hf-identity.json')
ENDPOINT = 'http://127.0.0.1:18765'
HELPER = '/run/harnessgrad-runtime/helper.py'


def process_identity(pid):
    root = Path('/proc') / str(pid)
    uid = (root / 'status').read_text().split('Uid:', 1)[1].splitlines()[0].split()
    # /proc stat's parenthesized comm may contain spaces.
    start = (root / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    return [int(x) for x in uid], start, (root / 'cmdline').read_bytes().split(b'\0')


def health(config):
    info = PUBLIC.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222:
        raise RuntimeError('mirror public identity is not root-owned/read-only')
    expected = json.loads(PUBLIC.read_text())
    if expected['manifest_sha256'] != config['manifest_sha256']:
        raise RuntimeError('mirror manifest identity changed')
    uid, start, cmd = process_identity(expected['pid'])
    if uid != [0, 0, 0, 0] or start != expected['starttime'] or str(ROOT / 'server.py').encode() not in cmd:
        raise RuntimeError('mirror process identity changed')
    # Never inherit a proxy setting; this endpoint is container-local only.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(ENDPOINT + '/health', timeout=2) as response:
        actual = json.loads(response.read(4096))
    if actual != expected:
        raise RuntimeError('mirror health identity differs')
    return expected


def start(config):
    if os.getuid() != 0: raise RuntimeError('only trusted container idle may launch the mirror')
    PRIVATE.mkdir(mode=0o700)
    nonce = secrets.token_hex(32)
    state = PRIVATE / 'launch.json'
    fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'manifest_sha256': config['manifest_sha256'], 'nonce': nonce}, out)
    proc = subprocess.Popen([config['python'], '-I', '-S', str(ROOT / 'server.py'),
                             '--root', str(ROOT / 'assets'), '--identity', str(state),
                             '--public-identity', str(PUBLIC)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None: raise RuntimeError('mirror startup failed')
            try:
                result = health(config)
                if result['pid'] != proc.pid or result['nonce_sha256'] != hashlib.sha256(nonce.encode()).hexdigest():
                    raise RuntimeError('mirror ready process differs')
                return
            except (OSError, ValueError, RuntimeError):
                time.sleep(.05)
        raise RuntimeError('mirror readiness deadline exceeded')
    except BaseException:
        proc.terminate()
        try: proc.wait(timeout=2)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=2)
        raise


def main():
    config = json.loads((ROOT / 'config.json').read_text())
    args = sys.argv[1:]
    # The existing harness-overlay lookup probes an image before its observed
    # container exists. Permit only its exact, side-effect-free version query.
    if args == ['-c', "import sys;print('%d.%d' % sys.version_info[:2])"]:
        os.execv(config['python'], [config['python'], *args])
    helper = len(args) >= 4 and args[:3] == ['-I', '-S', HELPER]
    if helper and args[-1] == 'idle':
        start(config)
    elif not helper:
        verified = health(config)
    # Trusted probe/init/quiesce must remain usable even if the mirror died.
    # Subsequent wrappers never restart it and cannot overwrite root state.
    if args == ['--hf-mirror-health']:
        print(json.dumps(verified, sort_keys=True)); return 0
    os.environ.update(HF_ENDPOINT=ENDPOINT, HF_HUB_OFFLINE='0',
                      HF_HOME='/workspace/.hf-cache', HF_HUB_CACHE='/workspace/.hf-cache/hub',
                      HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                      HF_HUB_DISABLE_XET='1', NO_PROXY='127.0.0.1,localhost')
    if helper:
        os.execv(config['python'], [config['python'], *args])
    # Preserve program I/O and exit status, but retain a trusted outer process
    # to notice service loss even when candidate code swallows HTTP errors.
    result = subprocess.run([config['python'], *args])
    health(config)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        print('HF dependency mirror unavailable: ' + str(exc), file=sys.stderr)
        sys.exit(78)
