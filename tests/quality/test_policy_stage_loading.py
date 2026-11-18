"""Policy ports load only the extracted proposal mechanisms they actually use."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_unrelated_policy_start_and_path_checks_do_not_load_stages(tmp_path):
    code = r'''
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
import _siblings
runtime = _siblings.load('policy_ports/runtime')
names = {'_hg_skillopt_fast', '_hg_skillopt_patch', '_hg_ace_proposal', '_hg_evo_harness_proposal', '_hg_gepa_proposal'}
assert not names.intersection(sys.modules)
root = Path(sys.argv[2])
assert runtime._safe(root, 'skills/main.md') == root / 'skills/main.md'
try:
    runtime._safe(root, '../outside')
except ValueError:
    pass
else:
    raise AssertionError('unsafe resource accepted')
assert not names.intersection(sys.modules)
assert runtime.apply_skill_patch({'skills/main.md': 'text'}, [], 1) == {'skills/main.md': 'text'}
assert names.intersection(sys.modules) == {'_hg_skillopt_patch'}
stage = runtime._proposal_stage('ace_proposal')
assert runtime._proposal_stage('ace_proposal') is stage
assert names.intersection(sys.modules) == {'_hg_skillopt_patch', '_hg_ace_proposal'}
'''
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT / 'methods'), str(tmp_path)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
