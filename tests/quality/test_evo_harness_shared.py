"""upstream evo_harness/__init__.py:135-211: reusable unscored feedback."""
import pytest

from harnessgrad.improvement.contracts import Feedback
from harnessgrad.improvement.stages import evo_harness_proposal


def test_three_failures_compile_topic_and_general_without_experiment_state():
    feedback = [Feedback(f'f{i}', 'failure', f'failure {i}') for i in range(3)]
    feedback.append(Feedback('success', 'success', 'Worked'))
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        if '[EvoHarness: reflect]' in prompt:
            return {'lesson': 'check status', 'trigger': 'shell', 'evidence': 'failed', 'scope_hint': 'shell'}
        level = 'general' if '[EvoHarness: compile general]' in prompt else 'topic'
        return {'skills': [{'path': f'skills/{level}/SKILL.md', 'name': level,
                'description': 'Use when verifying', 'source_task_ids': ['f0', 'f1', 'f2'] if level == 'general' else ['f0'],
                'content': 'Check the result.'}]}
    source = {'skills/original.md': 'Existing'}
    result = evo_harness_proposal(source, feedback, ask=ask)
    assert result.feedback_ids == ('f0', 'f1', 'f2') and len(result.memories) == 3
    assert result.files['skills/original.md'] == 'Existing' and source == {'skills/original.md': 'Existing'}
    assert 'skills/topic/SKILL.md' in result.files and 'skills/general/SKILL.md' in result.files
    assert len(prompts) == 5 and all('Worked' not in prompt for prompt in prompts)
    assert 'source_task_ids' in result.files['skills/general/SKILL.md']


def test_success_only_never_calls_model_and_keeps_library():
    def forbidden(_): pytest.fail('model called without failures')
    result = evo_harness_proposal({'skills/a.md': 'unchanged'}, [Feedback('s', 'success', 'ok')], ask=forbidden)
    assert not result.memories and not result.feedback_ids and not result.diff
    assert result.files == {'skills/a.md': 'unchanged'}


@pytest.mark.parametrize('path,ids', [('../escape/SKILL.md', ['f']), ('skills/./bad/SKILL.md', ['f']),
                                       ('skills/good/SKILL.md', ['unknown'])])
def test_invalid_compiler_output_is_rejected(path, ids):
    def ask(prompt):
        if '[EvoHarness: reflect]' in prompt:
            return {'lesson': 'x', 'trigger': 'x', 'evidence': 'x', 'scope_hint': 'x'}
        return {'skills': [{'path': path, 'name': 'x', 'description': 'x', 'source_task_ids': ids, 'content': 'x'}]}
    with pytest.raises(ValueError):
        evo_harness_proposal({}, [Feedback('f', 'failure', 'x')], ask=ask)


def test_model_cannot_replace_caller_feedback_id():
    def ask(_):
        return {'task_id': 'forged', 'lesson': 'x', 'trigger': 'x', 'evidence': 'x', 'scope_hint': 'x'}
    with pytest.raises(ValueError, match='feedback ID'):
        evo_harness_proposal({}, [Feedback('real', 'failure', 'x')], ask=ask)
