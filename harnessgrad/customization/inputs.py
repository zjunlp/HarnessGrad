"""Read explicitly selected harness text for an offline customization request.

The caller chooses a trusted root and exact paths. This module does not discover
sessions, load a harness, call a model or write files.
"""
from __future__ import annotations

import json
import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Sequence

from harnessgrad.improvement.contracts import Feedback, reject_json_constant, index_feedback, unique_json_object, is_relative_path

MAX_FILES = 64
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


def _parts(relative: str) -> tuple[str, ...]:
    if not is_relative_path(relative):
        raise ValueError('selected path must be a relative POSIX path')
    return tuple(relative.split('/'))


def _read_file(root_fd: int, parts: tuple[str, ...], remaining: int) -> tuple[str, int]:
    with ExitStack() as stack:
        directory = root_fd
        for component in parts[:-1]:
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            stack.callback(os.close, directory)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('selected input must be a regular file with one link')
        cap = min(MAX_FILE_BYTES, remaining)
        if before.st_size > cap:
            raise ValueError('selected input exceeds the byte limit')
        chunks, size = [], 0
        while size <= cap:
            chunk = os.read(fd, min(65536, cap + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > cap:
            raise ValueError('selected input exceeds the byte limit')
        after = os.fstat(fd)
        if any(getattr(before, key) != getattr(after, key) for key in
               ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')):
            raise ValueError('selected input changed while reading')
        try:
            return b''.join(chunks).decode('utf-8'), size
        except UnicodeDecodeError:
            raise ValueError('selected input must be UTF-8 text') from None


def collect_inputs(root: str | Path, skills: Sequence[str], instruction: str | None,
                   feedback: list[Feedback]) -> dict:
    """Return the existing request shape from explicit files and feedback.

    Paths and file descriptors are checked without following symlinks under the
    root. Parent directories of the root are trusted; the result is not an atomic
    snapshot across files. No ambient file or runtime configuration is read.
    """
    selected_feedback = index_feedback(feedback)
    if isinstance(skills, (str, bytes)):
        raise ValueError('skills must be a sequence of selected paths')
    skill_paths = list(skills)
    if not skill_paths and instruction is None:
        raise ValueError('select at least one skill or instruction file')
    if len(skill_paths) + (instruction is not None) > MAX_FILES:
        raise ValueError('too many selected files')
    names = skill_paths + ([instruction] if instruction is not None else [])
    parts = [_parts(name) for name in names]
    if len(set(names)) != len(names):
        raise ValueError('selected files must be unique')
    if any(not name.startswith('skills/') or not name.endswith('.md') for name in skill_paths):
        raise ValueError('skills must be Markdown files under skills/')
    if instruction is not None and skill_paths and instruction.startswith('skills/'):
        raise ValueError('instruction and skill surfaces must not overlap')
    output = _read_selected(root, names, parts)
    return {
        'feedback': [{'id': f.id, 'kind': f.kind, 'text': f.text} for f in selected_feedback.values()],
        **({'skills': {name: output[name] for name in skill_paths}} if skill_paths else {}),
        **({'instruction': {'path': instruction, 'text': output[instruction]}} if instruction is not None else {}),
    }


def _read_selected(root: str | Path, names: list[str], parts: list[tuple[str, ...]]) -> dict[str, str]:
    if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')) or os.open not in os.supports_dir_fd:
        raise RuntimeError('descriptor-relative no-follow file access is unavailable')
    output, total = {}, 0
    try:
        with ExitStack() as stack:
            fd = os.open(Path(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            for name, components in zip(names, parts):
                text, size = _read_file(fd, components, MAX_TOTAL_BYTES - total)
                output[name] = text
                total += size
    except OSError:
        raise ValueError('selected root or file is unavailable or unsafe') from None
    return output


def read_component_manifest(root: str | Path) -> dict:
    """Read a bounded, non-linked harness manifest for optional attribution."""
    value = _read_selected(root, ['harness.json'], [('harness.json',)])['harness.json']
    try:
        result = json.loads(value, object_pairs_hook=unique_json_object, parse_constant=reject_json_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('harness manifest must be valid JSON with unique keys') from None
    if not isinstance(result, dict):
        raise ValueError('harness manifest must contain an object')
    return result
