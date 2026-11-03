"""One native WorkBench process/session; no platform scoring or model calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

DATABASE_FILES = ('processed/analytics_data.csv', 'processed/calendar_events.csv',
    'processed/customer_relationship_manager_data.csv', 'processed/emails.csv',
    'processed/project_tasks.csv', 'raw/email_addresses.csv')


class Session:
    def __init__(self, source):
        self.temp = tempfile.TemporaryDirectory(prefix='hg-workbench-native-')
        root = Path(self.temp.name)
        for name in DATABASE_FILES:
            dest = root / 'data' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(source) / 'data' / name, dest)
        script = Path(__file__).resolve().parent / 'vendor/upstream/integrations/_workbench_process.py'
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'SYSTEMROOT')}
        self.proc = subprocess.Popen([sys.executable, '-I', '-B', str(script)],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace')
        try:
            self.description = self.request({'upstream': str(source)})['tools']
        except BaseException:
            self.close()
            raise

    def request(self, value):
        self.proc.stdin.write(json.dumps(value) + '\n')
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('native WorkBench process did not return a response')
        return json.loads(line)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.temp.cleanup()
