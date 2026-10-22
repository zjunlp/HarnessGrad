"""Print exact commands/results for isolated authored controls, then remove scratch."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
Path('/tmp/banhung-tmp').mkdir(exist_ok=True)
for mode,behavior in (('host','wait'),('sandbox','wait'),('host','kill'),('sandbox','kill'),
                      ('host','kill-child'),('host','orphan'),('sandbox','orphan')):
    with tempfile.TemporaryDirectory(prefix='receipt-',dir='/tmp/banhung-tmp') as td:
        command=[sys.executable,str(ROOT/'tests/quality/banhung_timeout_control.py'),
                 mode,str(Path(td)/'control'),behavior]
        print('COMMAND',repr(command),flush=True)
        result=subprocess.run(command,capture_output=True,text=True,timeout=8,
                              env={**os.environ,'TMPDIR':'/tmp/banhung-tmp'})
        print('returncode',result.returncode)
        print(result.stdout,end='')
        print('stderr',repr(result.stderr),flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
