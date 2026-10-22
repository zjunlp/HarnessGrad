"""Launch the real scorer/LibreOffice in an isolated network namespace and trace execs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
SCRATCH = Path('/mnt/20t/xubuqiang/hg-work/spreadrt')
work = SCRATCH / ('synthetic-' + uuid.uuid4().hex)
trace = SCRATCH / (work.name + '-exec.trace')
env = dict(os.environ, TMPDIR=str(SCRATCH / 'tmp'), PYTHONDONTWRITEBYTECODE='1',
           SPREADRT_PARENT_NETNS=os.readlink('/proc/self/ns/net'))
argv = ['/usr/bin/bwrap', '--unshare-net', '--ro-bind', '/', '/',
        '--bind', str(SCRATCH), str(SCRATCH), '--bind', str(SCRATCH / 'tmp'), '/tmp',
        '--proc', '/proc', '--',
        '/usr/bin/strace', '-f', '-s', '2000', '-e', 'trace=execve', '-o', str(trace),
        sys.executable, str(HERE / 'synthetic_control.py'), str(work)]
proc = subprocess.run(argv, env=env, capture_output=True, text=True)
lines = ['COMMAND=' + json.dumps(argv), 'ENV_OVERRIDES=' + json.dumps({
    k: env[k] for k in ('TMPDIR', 'PYTHONDONTWRITEBYTECODE', 'SPREADRT_PARENT_NETNS')}, sort_keys=True),
    'EXIT=' + str(proc.returncode), 'STDOUT:', proc.stdout, 'STDERR:', proc.stderr,
    'EXEC_TRACE:', trace.read_text() if trace.exists() else 'ABSENT']
(HERE / 'synthetic-output.txt').write_text('\n'.join(lines) + '\n')
if trace.exists():
    successful = [line for line in trace.read_text().splitlines() if 'execve(' in line and '= 0' in line]
    assert any('/soffice.bin' in line and '--convert-to' in line for line in successful)
    lines = []
    for path in sorted(set(line.split('execve("', 1)[1].split('"', 1)[0] for line in successful)):
        p = Path(path)
        lines.append({'path': path, 'resolved': str(p.resolve()), 'bytes': p.stat().st_size,
                      'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    (HERE / 'executed-artifacts.json').write_text(json.dumps(lines, indent=2) + '\n')
assert proc.returncode == 0
print('synthetic_control=PASSED; real soffice.bin --convert-to execution observed')
print('evidence=tests/fixtures/hg_spreadrt/synthetic-output.txt')
shutil.rmtree(work)
print('authored_workbooks_removed=' + str(not work.exists()))
