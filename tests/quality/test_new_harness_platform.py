"""Measure both new adapters through the real platform boundary, without a model.

The scripted provider still drives each native loop and its shell tool. The
candidate edit writes a probe before that loop starts; only the platform's
post-exit verifier receives the private token and decides whether it passed.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.ckpt.git_state import commit_state, content_sha, stage_workspace
from eval.observe.tracing import loop_facts
from eval.run.runner import run_one
from tools.check_trace_contract import inspect as inspect_trace
from tools.validate_harness import source_identity


@pytest.mark.parametrize('name,answer', [
    ('miniagent', 'MINIAGENT_MOCK'), ('openclaw', 'OPENCLAW_MOCK'),
])
def test_edited_candidate_runs_readonly_and_is_scored_after_private_inputs_arrive(
        name, answer, tmp_path, monkeypatch):
    """A score must describe the edited tree that actually executed in the sandbox."""
    source = tmp_path / 'submitted'
    shutil.copytree(ROOT / 'base_harness' / name, source,
                    ignore=shutil.ignore_patterns('__pycache__'))
    submitted_before = source_identity(source)
    work_root = tmp_path / 'work'
    repo = work_root / 'acceptance' / 'workspace'
    stage_workspace(source, repo)
    base_tree = content_sha(repo)

    # A method may edit any candidate-owned code. This edit observes the boundary
    # from inside the actual entrypoint, rather than assuming argv implies it.
    marker = 'candidate-owned-edit-' + name
    (source / 'acceptance-marker.txt').write_text(marker, encoding='utf-8')
    entry = source / 'agent.py'
    future = 'from __future__ import annotations\n'
    probe = '''
import json as _hg_json
import sys as _hg_sys
from pathlib import Path as _hg_Path
_hg_candidate = _hg_Path(__file__).resolve().parent
_hg_work = _hg_Path(_hg_sys.argv[_hg_sys.argv.index('--workdir') + 1]).resolve()
try:
    (_hg_candidate / 'unexpected-write.txt').write_text('unexpected', encoding='utf-8')
    _hg_writable = True
except OSError:
    _hg_writable = False
(_hg_work / 'candidate-probe.json').write_text(_hg_json.dumps({
    'marker': (_hg_candidate / 'acceptance-marker.txt').read_text(encoding='utf-8'),
    'private_present': (_hg_work / 'private-verifier-token.txt').exists(),
    'platform_present': _hg_Path(PLATFORM_FILE).exists(),
    'candidate_writable': _hg_writable,
    'cwd_matches_workdir': _hg_Path.cwd().resolve() == _hg_work,
}), encoding='utf-8')
'''.replace('PLATFORM_FILE', repr(str(ROOT / 'driver.py')))
    text = entry.read_text(encoding='utf-8')
    assert future in text
    entry.write_text(text.replace(future, future + probe, 1), encoding='utf-8')
    submitted_after = source_identity(source)
    assert submitted_after['submission_sha256'] != submitted_before['submission_sha256']
    stage_workspace(source, repo)
    edited_tree = content_sha(repo)
    assert edited_tree != base_tree

    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work_root))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HG_AGENT_MODEL', name + '-mock')
    monkeypatch.setenv('HG_AGENT_BASE_URL', 'http://must-not-be-contacted.invalid/v1')
    monkeypatch.setenv('HG_AGENT_API_KEY', 'LOCAL-ACCEPTANCE-SECRET')
    monkeypatch.setenv('HG_AGENT_MAX_STEPS', '4')
    monkeypatch.setenv('HG_AGENT_CONTEXT_WINDOW', '32768')
    monkeypatch.setenv('HG_AGENT_MAX_OUTPUT_TOKENS', '2048')
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    private = 'verifier-only-secret-' + name
    expected_probe = {
        'marker': marker, 'private_present': False, 'platform_present': False,
        'candidate_writable': False, 'cwd_matches_workdir': True,
    }
    verifier_code = (
        'import json,pathlib; p=pathlib.Path; '
        f'assert p("answer.txt").read_text().strip()=={answer!r}; '
        f'assert json.loads(p("candidate-probe.json").read_text())=={expected_probe!r}; '
        f'assert p("private-verifier-token.txt").read_text()=={private!r}; '
        'print("artifact and isolation checked")'
    )
    result = run_one(
        repo, {'task_id': 'acceptance/' + name, 'goal': 'Write a short answer.txt.'},
        sandbox=True, timeout_s=90, verify_timeout_s=20,
        verifier={'kind': 'command', 'argv': ['python3', '-c', verifier_code],
                  'inputs': [{'path': 'private-verifier-token.txt', 'content': private}]},
        run_id='native-adapter-acceptance',
    )
    assert not result.get('invalid'), result
    assert result['exit_code'] == 0, result.get('stderr')
    assert result['answer'].strip() == answer
    assert result['verdict']['passed'] is True, result['verdict']
    assert result['identity']['harness'] == name
    assert result['identity']['agent_backend'] == 'mock'
    assert inspect_trace(result['trace'])['problems'] == []
    facts = loop_facts(result['trace'])
    assert facts['model_calls'] >= 2, facts
    assert facts['commands'] >= 1 and facts['observations'] >= 1, facts
    assert private not in result['trace']
    assert 'LOCAL-ACCEPTANCE-SECRET' not in result['trace']
    rows = [json.loads(line) for line in result['trace'].splitlines() if line.strip()]
    usage = [row['usage'] for row in rows if 'usage' in row][-1]
    assert usage['calls'] >= 2
    assert isinstance(usage.get('input_tokens'), int)
    assert isinstance(usage.get('output_tokens'), int)
    # Execution must not silently modify the source whose tree hash gets recorded.
    assert commit_state(repo, 'post-execution audit') == edited_tree
    assert source_identity(source) == submitted_after
