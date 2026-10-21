"""Method-side source copying; platform helpers are hidden from this sandbox.

The trusted copy remains in ckpt/source_tree.py. Keep the Bun exclusion
contract aligned without letting methods replace the platform's implementation.
"""
import json
import shutil
from pathlib import Path


def copy_harness(source, target, **kwargs):
    try:
        declaration = json.loads((Path(source) / "harness.json").read_text()).get("runtime")
        bun = isinstance(declaration, dict) and declaration.get("kind") == "bun"
    except (OSError, ValueError, AttributeError):
        bun = False
    if bun:
        excluded = {".git", "node_modules", ".state", "_harnessgrad",
                    "__pycache__", ".opencode-state"}
        previous = kwargs.get("ignore")
        kwargs["ignore"] = lambda directory, names: (
            set(names) & excluded
        ) | (set(previous(directory, names)) if previous else set())
        kwargs["symlinks"] = True
    return shutil.copytree(source, target, **kwargs)
