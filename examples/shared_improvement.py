"""Compose shared proposal stages with scripted, synthetic feedback.

Run from the repository root with:
    PYTHONPATH=. python examples/shared_improvement.py

The callback below is a deterministic stand-in for a model. No endpoint, tasks,
evaluation, file writes or candidate adoption are involved.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from harnessgrad.improvement import Feedback, ace_proposal, evo_harness_proposal, skillopt_proposal


def scripted_model(prompt: str) -> dict:
    """Return fixed responses for this example, not a general model adapter."""
    if prompt.startswith('[SkillOpt: failure]'):
        return {'edits': [{'path': 'skills/review.md', 'op': 'append',
                           'content': 'Run the relevant tests before reporting completion.'}]}
    if prompt.startswith('[EvoHarness: reflect]'):
        return {'lesson': 'Verify changes', 'trigger': 'code modification',
                'evidence': 'tests were skipped', 'scope_hint': 'development'}
    if prompt.startswith('[EvoHarness: compile topic]'):
        return {'skills': [{'path': 'skills/verification/SKILL.md', 'name': 'verification',
                            'description': 'Check code changes', 'source_task_ids': ['feedback-1'],
                            'content': 'Run relevant tests and report their result.'}]}
    if prompt.startswith('[ACE: reflect]'):
        return {'key_insight': 'Report verification results', 'bullet_tags': []}
    if prompt.startswith('[ACE: curate]'):
        return {'operations': [{'type': 'ADD', 'section': 'general',
                                'content': 'Report which checks ran and their outcomes.'}]}
    raise ValueError('unexpected prompt in scripted example')


def main() -> None:
    feedback = [Feedback('feedback-1', 'failure', 'The change was reported without running tests.')]
    skills = {'skills/review.md': 'Review the requested code change.\n'}
    instruction = 'Complete the requested task.\n'

    fast = skillopt_proposal(skills, feedback, ask=scripted_model)
    memory_and_skills = evo_harness_proposal(fast.files, feedback, ask=scripted_model)
    instruction_edit = ace_proposal('prompt.md', instruction, feedback, ask=scripted_model)
    print(json.dumps({'status': 'unevaluated', 'skill_edit': asdict(fast),
                      'memory_and_skills': asdict(memory_and_skills),
                      'instruction_edit': asdict(instruction_edit)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
