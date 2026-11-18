"""SkillOpt _patch.py:84-141: reusable bounded edits retain slow guidance."""
from __future__ import annotations

import pytest

from methods.policy_ports.skillopt_fast import propose_fast
from methods.policy_ports.skillopt_patch import apply_skill_patch


def test_feedback_can_produce_a_skill_candidate_without_experiment_scores():
    original = {'skills/main.md': 'Existing guidance\n'}
    def ask(prompt):
        assert '[SkillOpt: failure]' in prompt
        assert 'The user corrected a missing check.' in prompt
        return {'edits': [{'path': 'skills/main.md', 'op': 'append',
                           'content': 'Run the relevant tests.'}]}
    candidate, edits = propose_fast(original, [({'feedback-1'}, set())], edit_budget=1,
        merge_batch_size=2, optimizer_memory='', ask=ask,
        evidence=lambda ids: 'The user corrected a missing check.', apply_patch=apply_skill_patch)
    assert candidate['skills/main.md'] == 'Existing guidance\n\nRun the relevant tests.\n'
    assert edits[0]['source_type'] == 'failure'
    assert original == {'skills/main.md': 'Existing guidance\n'}


@pytest.mark.parametrize('path', ['../secret.md', '/etc/secret.md', 'skills/../../x.md',
                                  'skills\\x.md', 'skills/x.py', 'other/x.md'])
def test_extracted_skill_patcher_rejects_invalid_targets(path):
    with pytest.raises(ValueError):
        apply_skill_patch({}, [{'path': path, 'op': 'append', 'content': 'X'}], 1)


def test_no_edits_may_change_protected_guidance():
    original = {'skills/main.md': 'Before\n<!-- SLOW_UPDATE_START -->\nKeep\n<!-- SLOW_UPDATE_END -->\n'}
    with pytest.raises(ValueError, match='protected'):
        apply_skill_patch(original, [{'path': 'skills/main.md', 'op': 'replace',
                                      'target': 'Keep', 'content': 'Remove'}], 1)
    assert 'Keep' in original['skills/main.md']


def test_edit_budget_is_enforced_before_any_candidate_is_returned():
    files = {'skills/main.md': 'Before'}
    edits = [{'path': 'skills/main.md', 'op': 'append', 'content': 'X'}] * 2
    with pytest.raises(ValueError, match='budget'):
        apply_skill_patch(files, edits, 1)
    assert files == {'skills/main.md': 'Before'}
