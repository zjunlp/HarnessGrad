"""Reusable proposal mechanisms; workflows own evidence and selection policy.

Importing feedback contracts does not load a method implementation or contact a
model. Proposal stages load their method mechanisms only when called.
"""
from importlib import import_module

from .contracts import Feedback, index_feedback, is_relative_path, selected_surfaces, text_diff

_STAGE_EXPORTS = frozenset({
    'Proposal', 'MemorySkillProposal', 'skillopt_proposal', 'ace_proposal', 'evo_harness_proposal', 'gepa_proposal',
    'text_edit_proposal',
})
__all__ = ['Feedback', 'index_feedback', 'is_relative_path', 'selected_surfaces', 'text_diff',
           'Proposal', 'MemorySkillProposal', 'skillopt_proposal', 'ace_proposal', 'evo_harness_proposal',
           'gepa_proposal', 'text_edit_proposal']


def __getattr__(name):
    if name in _STAGE_EXPORTS:
        return getattr(import_module('.stages', __name__), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(set(globals()) | _STAGE_EXPORTS)
