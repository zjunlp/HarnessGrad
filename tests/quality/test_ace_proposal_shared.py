"""ACE strategy.py:92-154 and _playbook.py:56-157: reflection and ADD-only curation."""
from __future__ import annotations

import pytest

from methods.policy_ports.ace_proposal import propose_playbook
from methods.policy_ports.vendor.ace import _playbook as native


def test_unscored_feedback_can_propose_a_playbook_without_running_tasks():
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        if '[ACE: reflect]' in prompt:
            assert 'User corrected the missing test.' in prompt
            return {'key_insight': 'Verify tests', 'bullet_tags': []}
        return {'operations': [{'type': 'ADD', 'section': 'general',
                                'content': 'Run project checks.'}]}
    candidate, tags = propose_playbook('Baseline', evidence=lambda: 'User corrected the missing test.',
        ask=ask, native=native, call_index=1, curator_frequency=1,
        edit_budget=1, playbook_token_budget=1000)
    assert candidate.startswith('Baseline\n\n## ACE PLAYBOOK\n')
    assert 'Run project checks.' in candidate and 'helpful=0 harmful=0' in candidate
    assert tags == [] and len(prompts) == 2


def test_cadence_preserves_existing_bullet_and_tags():
    text = 'Baseline\n\n## ACE PLAYBOOK\n[general-00001] helpful=0 harmful=0 :: Original'
    calls = []
    def ask(prompt):
        calls.append(prompt)
        return {'bullet_tags': [{'id': 'general-00001', 'tag': 'helpful'}]}
    result, tags = propose_playbook(text, evidence=lambda: 'feedback', ask=ask, native=native,
        call_index=1, curator_frequency=2, edit_budget=1, playbook_token_budget=1000)
    assert len(calls) == 1 and tags == [('general-00001', 'helpful')]
    assert 'Original' in result and 'helpful=1 harmful=0' in result


def test_non_add_edits_and_excess_playbook_are_rejected():
    replies = iter([{'key_insight': 'x'}, {'operations': [{'type': 'DELETE', 'content': 'x'}]}])
    with pytest.raises(native.PlaybookError, match='only ADD'):
        propose_playbook('Baseline', evidence=lambda: 'feedback', ask=lambda _: next(replies),
                         native=native, call_index=1, curator_frequency=1,
                         edit_budget=1, playbook_token_budget=1000)
    with pytest.raises(ValueError, match='token budget'):
        propose_playbook('Baseline\n\n## ACE PLAYBOOK\n' + ('x' * 20),
                         evidence=lambda: 'feedback', ask=lambda _: {}, native=native,
                         call_index=1, curator_frequency=2, edit_budget=1, playbook_token_budget=1)
