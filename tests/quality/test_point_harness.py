"""A declared runtime path with a Node version in it is correct until the version moves.

`tools/point_harness.py` is the one mechanical answer: find the same tail under a sibling
version directory. These tests use a fake nvm tree, because the real one changes under us and
the point is what the tool does when a path *does* move.

What it deliberately does not do: resolve at task time. What a subject may see is part of the
sandbox plan (`eval/containers/sandbox.py`), and a plan that changes with the host's Node
version is one that cannot be quoted in a paper -- so the choice is recorded in the manifest,
per machine, once.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import point_harness                                            # noqa: E402


def _nvm(tmp_path: Path, versions: list[str], *, install: list[str]) -> Path:
    """A fake nvm root; `install` names the versions that carry the package."""
    root = tmp_path / "nvm" / "versions" / "node"
    for version in versions:
        package = (root / version / "lib" / "node_modules" / "pkg" / "bin")
        (package if version in install else package.parent).mkdir(parents=True)
        if version in install:
            (package / "tool").write_text("#!/bin/sh\n", encoding="utf-8")
    return root


def test_a_missing_version_is_repointed_at_the_newest_one_that_has_it(tmp_path):
    root = _nvm(tmp_path, ["v22.16.0", "v22.19.0", "v22.23.4"], install=["v22.19.0", "v22.23.4"])
    declared = root / "v22.16.0" / "lib" / "node_modules" / "pkg" / "bin"
    assert not declared.exists()
    assert point_harness.resolve(declared) == (root / "v22.23.4" / "lib"
                                               / "node_modules" / "pkg" / "bin")


def test_an_existing_path_is_left_alone(tmp_path):
    root = _nvm(tmp_path, ["v22.16.0"], install=["v22.16.0"])
    declared = root / "v22.16.0" / "lib" / "node_modules" / "pkg" / "bin"
    assert point_harness.resolve(declared) == declared


def test_nothing_installed_is_reported_not_invented(tmp_path):
    root = _nvm(tmp_path, ["v22.16.0", "v22.19.0"], install=[])
    declared = root / "v22.16.0" / "lib" / "node_modules" / "pkg" / "bin"
    assert point_harness.resolve(declared) is None


def test_a_path_without_a_version_component_is_not_guessed_at(tmp_path):
    missing = tmp_path / "not-installed" / "tree"
    assert point_harness.resolve(missing) is None


def _manifest(tmp_path: Path, paths: list[Path]) -> Path:
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "harness.json").write_text(json.dumps({
        "name": "fake", "version": "0.1.0", "path": ".", "entrypoint": "agent.py",
        "backend": "cli", "env_kinds": ["files"],
        "runtime_paths": [str(path) for path in paths]}, indent=2) + "\n", encoding="utf-8")
    return harness


def test_write_rewrites_only_the_moved_paths(tmp_path):
    root = _nvm(tmp_path, ["v22.16.0", "v22.19.0"], install=["v22.19.0"])
    stable = tmp_path / "stable"
    stable.mkdir()
    declared = root / "v22.16.0" / "lib" / "node_modules" / "pkg" / "bin"
    harness = _manifest(tmp_path, [declared, stable])
    assert point_harness.main([str(harness)]) == 1, "reporting only: nothing written yet"
    assert json.loads((harness / "harness.json").read_text())["runtime_paths"][0] == str(declared)
    assert point_harness.main([str(harness), "--write"]) == 1
    paths = json.loads((harness / "harness.json").read_text())["runtime_paths"]
    assert paths == [str(root / "v22.19.0" / "lib" / "node_modules" / "pkg" / "bin"), str(stable)]


def test_a_clean_harness_exits_zero(tmp_path, capsys):
    harness = _manifest(tmp_path, [tmp_path])
    assert point_harness.main([str(harness)]) == 0
    assert "all resolve" in capsys.readouterr().out


@pytest.mark.parametrize("harness", ["codex", "opencode", "deepseek-harness", "openclaw"])
def test_the_node_harnesses_in_this_repository_resolve(harness):
    """The check that would have caught a stale pin, run over the real manifests."""
    rows = point_harness.check(ROOT / "base_harness" / harness / "harness.json")
    assert rows, f"{harness} declares no runtime paths"
    assert [row for row in rows if row["state"] != "ok"] == []
