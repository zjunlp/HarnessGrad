"""Model-independent feedback and text-surface contracts for improvement workflows."""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Literal, Mapping


@dataclass(frozen=True)
class Feedback:
    """One caller-selected observation; labels are supplied, never inferred."""

    id: str
    kind: Literal['failure', 'success']
    text: str


def index_feedback(feedback: list[Feedback]) -> dict[str, Feedback]:
    """Validate and index caller-labeled feedback while preserving input order."""
    if not feedback:
        raise ValueError('at least one feedback item is required')
    result = {}
    for item in feedback:
        if not isinstance(item, Feedback):
            raise ValueError('feedback items must be Feedback objects')
        if not isinstance(item.id, str) or not item.id.strip() or item.id in result:
            raise ValueError('feedback ids must be nonempty and unique')
        if item.kind not in ('failure', 'success'):
            raise ValueError('feedback kind must be failure or success')
        if not isinstance(item.text, str) or not item.text.strip():
            raise ValueError('feedback text must be nonempty')
        result[item.id] = item
    return result


def text_diff(before: Mapping[str, str], after: Mapping[str, str]) -> str:
    """Render a deterministic unified diff for selected text surfaces."""
    chunks = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        for line in difflib.unified_diff(
            (old or '').splitlines(keepends=True), (new or '').splitlines(keepends=True),
            fromfile=f'a/{path}' if old is not None else '/dev/null',
            tofile=f'b/{path}' if new is not None else '/dev/null',
        ):
            if line.endswith('\n'):
                chunks.append(line)
            else:
                chunks.append(line + '\n\\ No newline at end of file\n')
    return ''.join(chunks)


def is_relative_path(path: str) -> bool:
    """Accept one unambiguous, relative POSIX spelling for a file path."""
    return (isinstance(path, str) and bool(path)
            and not any(c in path for c in ('\x00', '\n', '\r', '\\', ':'))
            and all(part not in ('', '.', '..') for part in path.split('/')))


def selected_surfaces(skills, instruction) -> dict[str, str]:
    """Validate selected text surfaces and return a detached path-to-text map."""
    if skills is None and instruction is None:
        raise ValueError('provide at least one skill or instruction surface')
    original = {}
    if skills is not None:
        if not isinstance(skills, Mapping):
            raise ValueError('skills must map skills/*.md relative paths to text')
        original = dict(skills)
        if not original or not all(is_relative_path(k) and k.startswith('skills/') and k.endswith('.md')
                                   and isinstance(v, str) for k, v in original.items()):
            raise ValueError('skills must map skills/*.md relative paths to text')
    if instruction is not None:
        if not isinstance(instruction, tuple) or len(instruction) != 2:
            raise ValueError('instruction must be a (path, text) pair')
        path, text = instruction
        if not is_relative_path(path) or not isinstance(text, str):
            raise ValueError('instruction path must be relative and text must be a string')
        if path in original or (skills is not None and path.startswith('skills/')):
            raise ValueError('instruction and skill surfaces must not overlap')
        original[path] = text
    return original


def unique_json_object(pairs):
    """JSON object-pairs hook that refuses duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def reject_json_constant(_value):
    """JSON parse-constant hook that refuses non-finite numbers."""
    raise ValueError('non-finite JSON number')
