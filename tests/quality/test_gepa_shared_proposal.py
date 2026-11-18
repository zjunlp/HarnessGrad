"""GEPA's proposal role accepts explicit feedback without platform scores."""
import json

import pytest

from harnessgrad.improvement import Feedback, gepa_proposal


def test_gepa_proposal_uses_only_selected_failures_and_never_mutates_input():
    feedback = [Feedback('a', 'failure', 'The tests were skipped.'),
                Feedback('b', 'success', 'The build completed.')]
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        return {'instruction': 'Check tests before finishing.\n'}
    result = gepa_proposal('prompt.md', 'Original\n', feedback, ask=ask)
    assert result.stage == 'gepa-reflect-propose'
    assert result.feedback_ids == ('a',)
    assert result.files == {'prompt.md': 'Check tests before finishing.\n'}
    assert '-Original' in result.diff and '+Check tests before finishing.' in result.diff
    assert json.loads(prompts[0].split('Original\n\n', 1)[1]) == [
        {'id': 'a', 'kind': 'failure', 'text': 'The tests were skipped.'}]
    assert [f.kind for f in feedback] == ['failure', 'success']


@pytest.mark.parametrize('feedback,path', [([Feedback('a', 'success', 'ok')], 'prompt.md'),
                                           ([Feedback('a', 'failure', 'bad')], '../prompt.md')])
def test_gepa_rejects_missing_failure_or_invalid_path_before_call(feedback, path):
    with pytest.raises(ValueError):
        gepa_proposal(path, 'text', feedback, ask=lambda _: pytest.fail('unexpected model call'))


@pytest.mark.parametrize('reply', [{}, {'instruction': ''}, {'instruction': 42}])
def test_gepa_rejects_invalid_model_reply(reply):
    with pytest.raises(ValueError, match='nonempty instruction'):
        gepa_proposal('prompt.md', 'text', [Feedback('a', 'failure', 'bad')], ask=lambda _: reply)


@pytest.mark.parametrize('reply', [None, [], 'unexpected'])
def test_gepa_rejects_nonobject_model_reply(reply):
    with pytest.raises(ValueError, match='model reply must be one JSON object'):
        gepa_proposal('prompt.md', 'text', [Feedback('a', 'failure', 'bad')], ask=lambda _: reply)
