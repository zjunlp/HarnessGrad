"""Reproduce the complete reviewed test scope; retain output, delete basetemp."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
os.environ.update(TMPDIR='/tmp/banhung-tmp', HG_AGENT_BACKEND='mock', HG_METHOD_BACKEND='mock')
core = ['tests/quality/test_' + name + '.py' for name in (
    'sandbox', 'container', 'stop_semantics', 'unified_budget', 'services', 'verifier')]
scope = Path(__file__).with_name('timeout-scope.txt').read_text().splitlines()
extra = ['tests/quality/test_' + name + '.py' for name in (
    'timeout_w_banhung', 'trials_and_unmeasured', 'harness_failure_visible',
    'eval_package_compat', 'import_direction', 'parallel_tasks', 'chessbench_stepwise_runner')]
base = '/tmp/banhung-tmp/final-suites'
command = [sys.executable, '-m', 'pytest', '-q', '-ra', '--basetemp=' + base,
           *dict.fromkeys(core + scope + extra)]
print('COMMAND', repr(command), flush=True)
try:
    result = subprocess.run(command)
finally:
    shutil.rmtree(base, ignore_errors=True)
sys.exit(result.returncode)
