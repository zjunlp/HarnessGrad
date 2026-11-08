"""Resolved-path containment shared by console readers and write guards."""
from __future__ import annotations
import pathlib

def within(base: pathlib.Path, target: pathlib.Path) -> bool:
    """target 是否真的在 base 下面（resolve 过，符号链接逃逸也会被挡住）。"""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False
