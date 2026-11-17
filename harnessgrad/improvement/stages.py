"""Reusable in-memory proposal stages fed by explicit feedback.

These are partial method adaptations: callers own evidence, evaluation and adoption.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Mapping

from .contracts import Feedback, text_diff, index_feedback, is_relative_path


@dataclass(frozen=True)
class Proposal:
    """A candidate and its diff, not an evaluated or approved improvement."""

    stage: str
    files: dict[str, str]
    diff: str
    feedback_ids: tuple[str, ...]


def skillopt_proposal(files: Mapping[str, str], feedback: list[Feedback], *,
                      ask: Callable[[str], dict], edit_budget: int = 4,
                      batch_size: int = 8, merge_batch_size: int = 8,
                      optimizer_memory: str = '') -> Proposal:
    """Propose SkillOpt fast edits from caller-categorized observations.

    Paths follow the existing port's ``skills/*.md`` contract. The caller must
    decide whether feedback can be sent to ``ask`` and must review the result.
    """
    items = index_feedback(feedback)
    if type(edit_budget) is not int or edit_budget < 1:
        raise ValueError('edit_budget must be a positive integer')
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError('batch_size must be a positive integer')
    current = dict(files)
    if not current or not all(is_relative_path(k) and k.startswith('skills/') and k.endswith('.md')
                              and isinstance(v, str) for k, v in current.items()):
        raise ValueError('files must map skills/*.md relative paths to text')
    ordered = list(items.values())
    batches = []
    for offset in range(0, len(ordered), batch_size):
        group = ordered[offset:offset + batch_size]
        batches.append(({f.id for f in group if f.kind == 'failure'},
                        {f.id for f in group if f.kind == 'success'}))
    def evidence(ids):
        return json.dumps([{'id': entry.id, 'kind': entry.kind, 'text': entry.text}
                           for entry in ordered if entry.id in ids], ensure_ascii=False)
    from methods.policy_ports.skillopt_fast import propose_fast
    from methods.policy_ports.skillopt_patch import apply_skill_patch
    updated, _edits = propose_fast(current, batches, edit_budget=edit_budget,
        merge_batch_size=merge_batch_size, optimizer_memory=optimizer_memory,
        ask=ask, evidence=evidence, apply_patch=apply_skill_patch)
    if not all(is_relative_path(path) and path.startswith('skills/') and path.endswith('.md')
               for path in updated):
        raise ValueError('candidate contains invalid skill paths')
    return Proposal('skillopt-fast', updated, text_diff(current, updated), tuple(items))


def ace_proposal(path: str, text: str, feedback: list[Feedback], *,
                 ask: Callable[[str], dict], edit_budget: int = 4,
                 playbook_token_budget: int = 80000) -> Proposal:
    """Propose one ACE reflection/curation step from explicit observations."""
    items = index_feedback(feedback)
    if not is_relative_path(path) or not isinstance(text, str):
        raise ValueError('path must be relative and instruction text must be a string')
    if type(edit_budget) is not int or edit_budget < 1:
        raise ValueError('edit_budget must be a positive integer')
    if type(playbook_token_budget) is not int or playbook_token_budget < 1:
        raise ValueError('playbook_token_budget must be a positive integer')
    def evidence():
        return json.dumps([{'id': entry.id, 'kind': entry.kind, 'text': entry.text}
                           for entry in items.values()], ensure_ascii=False)
    from methods.policy_ports.ace_proposal import propose_playbook
    from methods.policy_ports.vendor.ace import _playbook as ace_playbook
    candidate, _tags = propose_playbook(text, evidence=evidence, ask=ask, native=ace_playbook,
        call_index=1, curator_frequency=1, edit_budget=edit_budget,
        playbook_token_budget=playbook_token_budget)
    before, after = {path: text}, {path: candidate}
    return Proposal('ace-reflect-curate', after, text_diff(before, after), tuple(items))


def gepa_proposal(path: str, text: str, feedback: list[Feedback], *,
                  ask: Callable[[str], dict]) -> Proposal:
    """Propose an instruction using caller-selected failure feedback.

    This is the GEPA port's proposal role, not its measured parent selection,
    Pareto frontier, evaluation or adoption strategy.
    """
    items = index_feedback(feedback)
    if not is_relative_path(path) or not isinstance(text, str):
        raise ValueError('path must be relative and instruction text must be a string')
    failures = [item for item in items.values() if item.kind == 'failure']
    if not failures:
        raise ValueError('GEPA proposals require failure feedback')
    evidence = json.dumps([{'id': item.id, 'kind': item.kind, 'text': item.text}
                           for item in failures], ensure_ascii=False)
    from methods.policy_ports.gepa_proposal import format_prompt, instruction_from_reply
    reply = ask(format_prompt(text, evidence))
    if not isinstance(reply, dict):
        raise ValueError('model reply must be one JSON object')
    candidate = instruction_from_reply(reply)
    before, after = {path: text}, {path: candidate}
    return Proposal('gepa-reflect-propose', after, text_diff(before, after),
                    tuple(item.id for item in failures))


@dataclass(frozen=True)
class MemorySkillProposal:
    """Unevaluated skill candidate and its model-generated memory records."""

    stage: str
    files: dict[str, str]
    diff: str
    feedback_ids: tuple[str, ...]
    memories: tuple[dict, ...]


def evo_harness_proposal(files: Mapping[str, str], feedback: list[Feedback], *,
                         ask: Callable[[str], dict], general_min_tasks: int = 3,
                         max_skill_chars: int = 2000, max_general_skills: int = 10,
                         max_topic_skills: int = 30, max_library_chars: int = 48000) -> MemorySkillProposal:
    """Reflect on caller-labeled failures and compile topic/general skill proposals.

    Memory records are model outputs, not verified facts. The caller controls
    access to feedback, model transport, candidate evaluation and adoption.
    """
    items = index_feedback(feedback)
    limits = {'general_min_tasks': general_min_tasks, 'max_skill_chars': max_skill_chars,
              'max_general_skills': max_general_skills, 'max_topic_skills': max_topic_skills,
              'max_library_chars': max_library_chars}
    if any(type(value) is not int or value < 1 for value in limits.values()):
        raise ValueError('skill proposal limits must be positive integers')
    if not isinstance(files, Mapping):
        raise ValueError('files must map skills/*.md relative paths to text')
    current = dict(files)
    if not all(is_relative_path(path) and path.startswith('skills/') and path.endswith('.md')
               and isinstance(text, str) for path, text in current.items()):
        raise ValueError('files must map skills/*.md relative paths to text')
    failures = [item for item in items.values() if item.kind == 'failure']
    by_id = {item.id: item for item in failures}

    def evidence(identifier):
        item = by_id[identifier]
        return json.dumps({'id': item.id, 'kind': item.kind, 'text': item.text}, ensure_ascii=False)

    def safe_path(path):
        if not is_relative_path(path):
            raise ValueError('candidate skill path must be a relative POSIX path')

    def guarded_ask(prompt):
        reply = ask(prompt)
        # The experiment port retains its historical model-output semantics.
        # In this shared adapter, only the caller may assign feedback identity.
        if prompt.startswith('[EvoHarness: reflect]') and isinstance(reply, dict) and 'task_id' in reply:
            raise ValueError('model memory cannot override a feedback ID')
        return reply

    from methods.policy_ports.evo_harness_proposal import propose_skills
    before, updated, memories = propose_skills(
        (item.id for item in failures), cfg=limits, ask=guarded_ask, evidence=evidence,
        skills=lambda: current, safe_path=safe_path)
    return MemorySkillProposal('evo-harness-skill-curation', updated, text_diff(before, updated),
                               tuple(item.id for item in failures), tuple(memories))
