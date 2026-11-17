"""The documented shared-stage example works offline without writing user files."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_scripted_shared_stage_composition_runs_offline(tmp_path):
    code = '''
import runpy, socket, sys
def forbidden(*args, **kwargs): raise AssertionError('unexpected network access')
socket.socket = forbidden
socket.create_connection = forbidden
runpy.run_path(sys.argv[1], run_name='__main__')
'''
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT))
    proc = subprocess.run([sys.executable, '-c', code, str(ROOT / 'examples/shared_improvement.py')],
                          cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result['status'] == 'unevaluated'
    assert 'Run the relevant tests' in result['skill_edit']['files']['skills/review.md']
    assert 'feedback-1' == result['memory_and_skills']['memories'][0]['task_id']
    assert 'skills/verification/SKILL.md' in result['memory_and_skills']['files']
    assert 'Report which checks' in result['instruction_edit']['files']['prompt.md']
    assert list(tmp_path.iterdir()) == []
