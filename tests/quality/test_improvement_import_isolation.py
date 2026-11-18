"""Shared proposal stages load only the selected method mechanism."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_shared_public_api_is_lazy_and_old_workflow_contract_refuses(tmp_path):
    code = r'''
import importlib, sys
import harnessgrad.improvement as api
assert 'harnessgrad.improvement.stages' not in sys.modules
assert not any(n == 'methods' or n.startswith('methods.') for n in sys.modules)
from harnessgrad.improvement import Feedback, Proposal, skillopt_proposal
from harnessgrad.improvement.contracts import Feedback as canonical_feedback
from harnessgrad.improvement.stages import Proposal as canonical_proposal
assert Feedback is canonical_feedback and Proposal is canonical_proposal
assert not any(n == 'methods' or n.startswith('methods.') for n in sys.modules)
result = skillopt_proposal({'skills/main.md': 'Original'}, [Feedback('f', 'failure', 'Missing check')],
    ask=lambda _: {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Check.'}]})
assert isinstance(result, Proposal) and 'Check.' in result.files['skills/main.md']
assert 'methods.policy_ports.skillopt_fast' in sys.modules
assert 'methods.policy_ports.ace_proposal' not in sys.modules
try:
    importlib.import_module('harnessgrad.customization.contracts')
except ImportError as exc:
    assert 'harnessgrad.improvement.contracts' in str(exc)
else:
    raise AssertionError('old contract import unexpectedly succeeded')
'''
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT))
    proc = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                          text=True, capture_output=True, timeout=20)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize('stage', ['none', 'skillopt', 'ace', 'evo'])
def test_stage_imports_are_independent(stage, tmp_path):
    code = r'''
import sys
from harnessgrad.improvement.contracts import Feedback
from harnessgrad.improvement import stages
assert not any(n == 'methods' or n.startswith('methods.') for n in sys.modules)
feedback = [Feedback('f', 'failure', 'Missing check')]
if sys.argv[1] == 'skillopt':
    stages.skillopt_proposal({'skills/main.md': 'Original'}, feedback,
        ask=lambda _: {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Check.'}]})
elif sys.argv[1] == 'ace':
    def ask(prompt):
        if '[ACE: reflect]' in prompt: return {'key_insight': 'Check', 'bullet_tags': []}
        return {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Check.'}]}
    stages.ace_proposal('prompt.md', 'Original', feedback, ask=ask)
elif sys.argv[1] == 'evo':
    stages.evo_harness_proposal({}, [Feedback('s', 'success', 'OK')],
        ask=lambda _: (_ for _ in ()).throw(AssertionError('unexpected call')))
loaded = set(sys.modules)
for stage, prefix in [('skillopt', 'methods.policy_ports.skillopt_fast'),
                      ('ace', 'methods.policy_ports.ace_proposal'),
                      ('evo', 'methods.policy_ports.evo_harness_proposal')]:
    assert (prefix in loaded) == (stage == sys.argv[1]), (stage, sorted(loaded))
'''
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT))
    proc = subprocess.run([sys.executable, '-c', code, stage], cwd=tmp_path, env=env,
                          text=True, capture_output=True, timeout=20)
    assert proc.returncode == 0, proc.stderr


def test_public_contracts_are_reusable_without_loading_methods():
    import json
    import harnessgrad.improvement as api
    from harnessgrad.improvement.contracts import reject_json_constant, unique_json_object

    entries = [api.Feedback('first', 'failure', 'Check'), api.Feedback('next', 'success', 'Done')]
    assert list(api.index_feedback(entries)) == ['first', 'next']
    assert api.is_relative_path('skills/check.md') and not api.is_relative_path('../check.md')
    assert api.selected_surfaces({'skills/check.md': 'Before\n'}, None) == {'skills/check.md': 'Before\n'}
    assert '-Before\n+After\n' in api.text_diff({'a': 'Before\n'}, {'a': 'After\n'})
    with pytest.raises(ValueError, match='duplicate'):
        json.loads('{"a":1,"a":2}', object_pairs_hook=unique_json_object)
    with pytest.raises(ValueError, match='non-finite'):
        json.loads('NaN', parse_constant=reject_json_constant)


def test_customization_does_not_import_private_shared_contracts():
    import ast
    paths = list((ROOT / 'harnessgrad/customization').glob('*.py'))
    paths.append(ROOT / 'harnessgrad/entrypoints/customization.py')
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == 'harnessgrad.improvement.contracts':
                assert all(not item.name.startswith('_') for item in node.names), path
