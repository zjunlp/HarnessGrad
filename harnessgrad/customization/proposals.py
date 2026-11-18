"""Compose reusable proposal stages for the customization workflow."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from harnessgrad.improvement.contracts import Feedback, text_diff, index_feedback, is_relative_path, selected_surfaces
from harnessgrad.improvement.stages import Proposal, ace_proposal, gepa_proposal, skillopt_proposal


@dataclass(frozen=True)
class CustomizationProposal:
    """A combined, unevaluated candidate; stage names record its provenance."""

    files: dict[str, str]
    diff: str
    feedback_ids: tuple[str, ...]
    stages: tuple[str, ...]


def propose_customization(feedback: list[Feedback], *, ask: Callable[[str], dict],
                          skills: Mapping[str, str] | None = None,
                          instruction: tuple[str, str] | None = None,
                          edit_budget: int = 4, batch_size: int = 8,
                          merge_batch_size: int = 8,
                          playbook_token_budget: int = 80000,
                          refine_instruction: bool = False) -> CustomizationProposal:
    """Propose edits to the supplied surfaces without asking the user to pick an RSI method.

    A skill surface uses SkillOpt's fast proposal stage; a separate instruction
    surface uses ACE's reflection and curation. When both are provided the stages
    run in that order and their in-memory changes are combined. Optional instruction
    refinement adds a failure-focused full rewrite after curation. The caller owns
    feedback access, model transport, candidate validation, evaluation and review.
    Neither stage measures effectiveness or writes to a harness.
    """
    items = index_feedback(feedback)
    if skills is None and instruction is None:
        raise ValueError('provide at least one skill or instruction surface')
    for name, value in (('edit_budget', edit_budget), ('batch_size', batch_size),
                        ('playbook_token_budget', playbook_token_budget)):
        if type(value) is not int or value < 1:
            raise ValueError(f'{name} must be a positive integer')
    if type(merge_batch_size) is not int or merge_batch_size < 2:
        raise ValueError('merge_batch_size must be >=2')
    original = selected_surfaces(skills, instruction)
    if type(refine_instruction) is not bool:
        raise ValueError('refine_instruction must be a boolean')
    if refine_instruction and (instruction is None or not any(item.kind == 'failure' for item in items.values())):
        raise ValueError('instruction refinement requires an instruction and failure feedback')
    candidate = dict(original)
    stages = []
    if skills is not None:
        result = skillopt_proposal(skills, feedback, ask=ask, edit_budget=edit_budget,
                                   batch_size=batch_size, merge_batch_size=merge_batch_size)
        candidate.update(result.files)
        stages.append(result.stage)
    if instruction is not None:
        result = ace_proposal(instruction[0], instruction[1], feedback, ask=ask,
                              edit_budget=edit_budget, playbook_token_budget=playbook_token_budget)
        candidate.update(result.files)
        stages.append(result.stage)
        if refine_instruction:
            result = gepa_proposal(instruction[0], candidate[instruction[0]], feedback, ask=ask)
            candidate.update(result.files)
            stages.append(result.stage)
    return CustomizationProposal(candidate, text_diff(original, candidate), tuple(items), tuple(stages))


def propose_with_memory(feedback: list[Feedback], *, ask: Callable[[str], dict],
                        skills: Mapping[str, str] | None = None,
                        instruction: tuple[str, str] | None = None,
                        refine_instruction: bool = False) -> tuple[CustomizationProposal, tuple[dict, ...]]:
    """Add an optional memory-to-skill step to the existing proposal composition.

    The returned memories are model-generated, unverified records. Nothing is
    evaluated, written, persisted or applied by this function.
    """
    if skills is None:
        raise ValueError('memory proposals require selected skills')
    items = index_feedback(feedback)
    if not any(item.kind == 'failure' for item in items.values()):
        raise ValueError('memory proposals require failure feedback')
    original = selected_surfaces(skills, instruction)
    base = propose_customization(feedback, ask=ask, skills=skills, instruction=instruction,
                                 refine_instruction=refine_instruction)
    from harnessgrad.improvement.stages import evo_harness_proposal
    selected_skills = {path: value for path, value in base.files.items() if path.startswith('skills/')}
    memory = evo_harness_proposal(selected_skills, feedback, ask=ask)
    candidate = dict(base.files)
    candidate.update(memory.files)
    return (CustomizationProposal(candidate, text_diff(original, candidate), base.feedback_ids,
                                  base.stages + (memory.stage,)), memory.memories)
