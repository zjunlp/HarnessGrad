"""Customization consumes existing ACE/SkillOpt proposal stages without scoring."""
from __future__ import annotations

import pytest

from harnessgrad.customization.proposals import Feedback, ace_proposal, skillopt_proposal


def test_skillopt_real_stage_returns_reviewable_candidate_from_feedback():
    original = {'skills/main.md': 'Run the command.'}
    feedback = [Feedback('correction-1', 'failure', 'It skipped project tests.')]
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        assert 'It skipped project tests.' in prompt
        return {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Run tests.'}]}
    proposal = skillopt_proposal(original, feedback, ask=ask)
    assert proposal.stage == 'skillopt-fast'
    assert proposal.files['skills/main.md'] == 'Run the command.\n\nRun tests.\n'
    assert '-Run the command.\n\\ No newline at end of file\n' in proposal.diff
    assert '+Run tests.\n' in proposal.diff
    assert proposal.feedback_ids == ('correction-1',)
    assert original == {'skills/main.md': 'Run the command.'}
    assert len(prompts) == 1


def test_ace_real_stage_returns_candidate_without_any_score():
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        if '[ACE: reflect]' in prompt:
            assert 'The user asked for tests.' in prompt
            return {'key_insight': 'Verify output', 'bullet_tags': []}
        return {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Run tests.'}]}
    proposal = ace_proposal('prompt.md', 'Baseline\n',
                            [Feedback('feedback', 'failure', 'The user asked for tests.')], ask=ask)
    assert proposal.stage == 'ace-reflect-curate'
    assert 'Run tests.' in proposal.files['prompt.md']
    assert '+[g-00001]' in proposal.diff
    assert proposal.feedback_ids == ('feedback',) and len(prompts) == 2


@pytest.mark.parametrize('feedback', [[], [Feedback('', 'failure', 'x')],
    [Feedback('a', 'unknown', 'x')], [Feedback('a', 'success', '')],
    [Feedback('a', 'success', 'x'), Feedback('a', 'failure', 'y')]])
def test_invalid_feedback_is_rejected_before_invoking_model(feedback):
    def forbidden(_): pytest.fail('model called for invalid feedback')
    with pytest.raises(ValueError):
        skillopt_proposal({'skills/main.md': 'x'}, feedback, ask=forbidden)
    with pytest.raises(ValueError):
        ace_proposal('prompt.md', 'x', feedback, ask=forbidden)


@pytest.mark.parametrize('path', ['../prompt.md', '/tmp/prompt.md', 'dir\\prompt.md', 'a\nb'])
def test_instruction_path_cannot_make_an_ambiguous_diff(path):
    with pytest.raises(ValueError, match='relative'):
        ace_proposal(path, 'x', [Feedback('a', 'failure', 'x')], ask=lambda _: {})


def test_invalid_model_edit_does_not_mutate_the_input():
    source = {'skills/main.md': 'Keep'}
    with pytest.raises(ValueError):
        skillopt_proposal(source, [Feedback('a', 'failure', 'x')], ask=lambda _: {
            'edits': [{'path': '../secret.md', 'op': 'append', 'content': 'x'}]})
    assert source == {'skills/main.md': 'Keep'}


def test_model_failures_propagate_without_a_fake_success():
    def fail(_): raise RuntimeError('provider unavailable')
    with pytest.raises(RuntimeError, match='provider unavailable'):
        skillopt_proposal({'skills/main.md': 'x'}, [Feedback('a', 'failure', 'x')], ask=fail)


def test_combined_customization_uses_both_stages_without_a_method_choice():
    from harnessgrad.customization.proposals import propose_customization

    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        if '[SkillOpt: failure]' in prompt:
            return {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Run tests.'}]}
        if '[ACE: reflect]' in prompt:
            return {'key_insight': 'Check output', 'bullet_tags': []}
        if '[ACE: curate]' in prompt:
            return {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Check output.'}]}
        pytest.fail(f'unexpected prompt: {prompt}')
    skills = {'skills/main.md': 'Existing skill\n'}
    result = propose_customization([Feedback('one', 'failure', 'Checks were skipped.')],
        ask=ask, skills=skills, instruction=('prompt.md', 'Baseline\n'))
    assert 'Run tests.' in result.files['skills/main.md']
    assert 'Check output.' in result.files['prompt.md']
    assert result.stages == ('skillopt-fast', 'ace-reflect-curate')
    assert result.feedback_ids == ('one',) and len(prompts) == 3
    assert 'a/skills/main.md' in result.diff and 'a/prompt.md' in result.diff
    assert skills == {'skills/main.md': 'Existing skill\n'}


def test_combined_customization_validates_all_inputs_before_model_call():
    from harnessgrad.customization.proposals import propose_customization

    def forbidden(_): pytest.fail('model called before input validation')
    feedback = [Feedback('one', 'failure', 'failure')]
    with pytest.raises(ValueError, match='overlap'):
        propose_customization(feedback, ask=forbidden, skills={'skills/main.md': 'x'},
                              instruction=('skills/main.md', 'other'))
    with pytest.raises(ValueError, match='overlap'):
        propose_customization(feedback, ask=forbidden, skills={'skills/main.md': 'x'},
                              instruction=('skills/new.md', 'other'))
    with pytest.raises(ValueError, match='at least one'):
        propose_customization(feedback, ask=forbidden)
    with pytest.raises(ValueError, match='relative'):
        propose_customization(feedback, ask=forbidden, skills={'skills/main.md': 'x'},
                              instruction=('../outside', 'text'))


def test_combined_customization_does_not_return_partial_candidate_on_failure():
    from harnessgrad.customization.proposals import propose_customization

    skills = {'skills/main.md': 'Original'}
    def ask(prompt):
        if '[SkillOpt: failure]' in prompt:
            return {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Tests.'}]}
        raise RuntimeError('model error')
    with pytest.raises(RuntimeError, match='model error'):
        propose_customization([Feedback('one', 'failure', 'No tests')], ask=ask,
                              skills=skills, instruction=('prompt.md', 'Original'))
    assert skills == {'skills/main.md': 'Original'}


def test_feedback_identity_is_shared_with_model_independent_contract():
    from harnessgrad.improvement import Feedback as PublicFeedback
    from harnessgrad.improvement.contracts import Feedback as SharedFeedback
    from harnessgrad.improvement.stages import ace_proposal as shared_ace, skillopt_proposal as shared_skillopt
    assert Feedback is PublicFeedback is SharedFeedback
    assert ace_proposal is shared_ace and skillopt_proposal is shared_skillopt


def test_collect_and_review_import_without_loading_method_implementations():
    import os
    import subprocess
    import sys
    from pathlib import Path

    source = '''
import sys
import harnessgrad.customization.inputs
import harnessgrad.customization.review
import harnessgrad.entrypoints.customization
assert not any(name == 'methods' or name.startswith('methods.') for name in sys.modules)
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', source],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('path', ['./skills/main.md', 'skills//main.md', 'skills/./main.md',
                                  'skills/main.md/', 'skills/main:copy.md'])
def test_ambiguous_input_paths_are_rejected_before_model_call(path):
    from harnessgrad.customization.proposals import propose_customization
    feedback = [Feedback('one', 'failure', 'Check tests')]
    def forbidden(_):
        pytest.fail('model called with ambiguous input paths')
    with pytest.raises(ValueError):
        propose_customization(feedback, ask=forbidden,
                              skills={'skills/main.md': 'original'}, instruction=(path, 'other'))
    with pytest.raises(ValueError):
        skillopt_proposal({path: 'original'}, feedback, ask=forbidden)


def test_ambiguous_model_created_skill_path_is_not_returned():
    feedback = [Feedback('one', 'failure', 'Check tests')]
    with pytest.raises(ValueError, match='invalid skill paths'):
        skillopt_proposal({'skills/main.md': 'original'}, feedback, ask=lambda _: {
            'edits': [{'path': 'skills/./main.md', 'op': 'append', 'content': 'Checks'}]})


def test_shared_stages_do_not_depend_on_customization_workflow():
    import os
    import subprocess
    import sys
    from pathlib import Path

    source = """
import sys
from harnessgrad.improvement.contracts import Feedback
from harnessgrad.improvement.stages import skillopt_proposal
result = skillopt_proposal({'skills/main.md': 'Original'},
    [Feedback('f', 'failure', 'Missing check')],
    ask=lambda _: {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Check.'}]})
assert 'Check.' in result.files['skills/main.md']
assert not any(n == 'harnessgrad.customization' or n.startswith('harnessgrad.customization.')
               for n in sys.modules)
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    proc = subprocess.run([sys.executable, '-c', source], cwd=Path(__file__).resolve().parents[2],
                          env=env, text=True, capture_output=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
