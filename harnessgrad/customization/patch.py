"""Export a reviewed proposal as a patch without modifying a harness."""
from __future__ import annotations

import os
import re
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from harnessgrad.improvement.contracts import text_diff
from .review import review_proposal


def _ensure_absent(root: Path, path: str) -> None:
    """Check one new path beneath a trusted root without following symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        with ExitStack() as stack:
            fd = os.open(root, flags)
            stack.callback(os.close, fd)
            parts = path.split('/')
            for part in parts[:-1]:
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    return
                stack.callback(os.close, child)
                fd = child
            try:
                os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('new candidate path already exists')
    except OSError:
        raise ValueError('candidate path is unavailable or unsafe') from None


def export_patch(request: dict[str, Any], document: Any, *, root: str | Path) -> str:
    """Verify selected source and return a unified diff, without writing files.

    The root and its ancestors are trusted. Checks are not atomic across files and
    do not reserve paths: a later application must recheck its working tree.
    """
    review = review_proposal(request, document, root=root)
    if any(any(ord(char) < 32 or ord(char) == 127 for char in path)
           for path in review['changed_paths']):
        raise ValueError('patch paths cannot contain control characters')
    original = dict(request.get('skills') or {})
    instruction = request.get('instruction')
    if instruction is not None:
        original[instruction[0]] = instruction[1]
    files = document['proposal']['files']
    # difflib treats these as line separators but Git patches do not. Refuse
    # instead of exporting a patch that cannot reproduce the candidate bytes.
    unsupported = re.compile(r'\r(?!\n)|[\x00\x0b\x0c\x1c-\x1e\x85\u2028\u2029]')
    for path in review['changed_paths']:
        if unsupported.search(original.get(path, '')) or unsupported.search(files[path]):
            raise ValueError('patch cannot represent this text line format')
    for path in sorted(files.keys() - original.keys()):
        _ensure_absent(Path(root), path)
        # A unified diff has no hunk for a new empty file; do not emit a patch
        # that silently omits a candidate file.
        if not text_diff({}, {path: files[path]}):
            raise ValueError('patch cannot represent a new empty file')
    return document['proposal']['diff']
