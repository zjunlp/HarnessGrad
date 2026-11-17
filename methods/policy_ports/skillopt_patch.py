"""SkillOpt exact skill edits; no model, task execution or file writes.

The caller supplies in-memory files and edits. This method-specific patcher is
not a generic path security boundary or an authorization to publish changes.
"""
from __future__ import annotations

from pathlib import Path


def _safe(root, name):
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or Path(name).is_absolute()
        or ".." in Path(name).parts
    ):
        raise ValueError("resource path must be inside the harness")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("resource path leaves the harness")
    return path



def apply_skill_patch(files, edits, budget, *, safe_path=_safe):
    """SkillOpt exact edits with a protected slow-update region."""
    if not isinstance(edits, list) or len(edits) > budget:
        raise ValueError("skill patch exceeds edit budget")
    updated = dict(files)
    for edit in edits:
        path = edit.get("path")
        safe_path(Path("/virtual"), path)
        if not path.startswith("skills/") or not path.endswith(".md"):
            raise ValueError("edit must target the skill component")
        op = edit.get("op")
        old = updated.get(path, "")
        content = edit.get("content", "")
        target = edit.get("target", "")
        if op not in ("append", "insert_after", "replace", "delete"):
            raise ValueError("unsupported skill edit operation")
        if op != "delete" and (not isinstance(content, str) or not content.strip()):
            raise ValueError("skill edit requires content")
        left = "<!-- SLOW_UPDATE_START -->"
        right = "<!-- SLOW_UPDATE_END -->"
        if old.count(left) != old.count(right) or old.count(left) > 1:
            raise ValueError("unbalanced slow guidance")
        protected = (
            old[old.index(left) : old.index(right) + len(right)]
            if left in old
            else None
        )
        if op == "append":
            new = old.rstrip() + "\n\n" + content.rstrip() + "\n"
        else:
            if not isinstance(target, str) or not target or old.count(target) != 1:
                raise ValueError("edit target must occur exactly once")
            at = old.index(target)
            if (
                protected
                and at < old.index(right) + len(right)
                and at + len(target) > old.index(left)
            ):
                raise ValueError("fast edits cannot target protected slow guidance")
            replacement = (
                target + "\n" + content
                if op == "insert_after"
                else content if op == "replace" else ""
            )
            new = old.replace(target, replacement, 1)
        newprotected = (
            new[new.index(left) : new.index(right) + len(right)]
            if left in new and right in new
            else None
        )
        if (
            protected != newprotected
            or new.count(left) != old.count(left)
            or new.count(right) != old.count(right)
        ):
            raise ValueError("fast edits cannot change SLOW_UPDATE")
        updated[path] = new
    return updated
