"""The shared harness description is read-only and preserves old attribution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harnessgrad.core import components
from harnessgrad.core.harness import describe_harness, describe_harness_manifest


def manifest(root: Path, payload: object) -> Path:
    root.mkdir()
    (root / "harness.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def test_removed_legacy_component_path_fails_with_migration_guidance():
    import importlib
    with pytest.raises(ImportError, match=r'harnessgrad\.core\.components'):
        importlib.import_module('harnessgrad.components')
    assert len(components.COMPONENTS) == 10


def test_describe_and_attribute_without_executing_harness(tmp_path):
    root = manifest(tmp_path / "h", {
        "name": "example", "version": "1", "entrypoint": "agent.py",
        "backend": "cli", "env_kinds": ["files", "exec"],
        "components": {"tools": ["tool.py"], "context": ["prompt.md", "tool.py"],
                       "unattributed": ["README.md"]},
    })
    (root / "agent.py").write_text("raise RuntimeError('must not execute')", encoding="utf-8")
    before = sorted(p.name for p in root.iterdir())
    description = describe_harness(root)
    assert (description.name, description.version, description.entrypoint, description.backend) == (
        "example", "1", "agent.py", "cli")
    assert description.env_kinds == ("files", "exec")
    assert description.attribute_changes(["tool.py", "new.py", "README.md"]) == {
        "components": ["context", "tools"], "unattributed": ["README.md", "new.py"]}
    assert sorted(p.name for p in root.iterdir()) == before


def test_undeclared_components_remain_unknown(tmp_path):
    description = describe_harness(manifest(tmp_path / "h", {"name": "minimal"}))
    assert description.entrypoint is None
    assert description.env_kinds == ()
    assert description.components == ()
    assert description.attribute_changes(["agent.py"]) == {
        "components": [], "unattributed": ["agent.py"]}


@pytest.mark.parametrize("payload", [[], {"name": 1}, {"env_kinds": "files"},
                                     {"env_kinds": ["files", 1]}])
def test_malformed_declarations_fail_without_guessing(tmp_path, payload):
    with pytest.raises(ValueError):
        describe_harness(manifest(tmp_path / "h", payload))


def test_missing_manifest_fails(tmp_path):
    with pytest.raises(ValueError, match="cannot read harness manifest"):
        describe_harness(tmp_path / "absent")


def test_actual_codex_manifest_is_only_a_declaration():
    root = Path(__file__).resolve().parents[2] / "base_harness" / "codex"
    description = describe_harness(root)
    assert description.name == "codex"
    assert description.entrypoint == "agent.py"
    assert "files" in description.env_kinds
    assert description.attribute_changes(["agent.py"]) == {
        "components": ["delegation"], "unattributed": []}


def test_identity_and_components_are_parsed_from_the_same_read(tmp_path, monkeypatch):
    root = manifest(tmp_path / 'h', {'name': 'first', 'components': {'context': ['first.md']}})
    path = root / 'harness.json'
    read_text = Path.read_text
    reads = []
    def replace_after_read(self, *args, **kwargs):
        value = read_text(self, *args, **kwargs)
        if self == path:
            reads.append(self)
            path.write_text(json.dumps({'name': 'second', 'components': {'tools': ['second.py']}}))
        return value
    monkeypatch.setattr(Path, 'read_text', replace_after_read)
    description = describe_harness(root)
    assert len(reads) == 1
    assert description.name == 'first'
    assert description.attribute_changes(['first.md', 'second.py']) == {
        'components': ['context'], 'unattributed': ['second.py']}


def test_already_read_manifest_uses_same_description_rules(tmp_path):
    payload = {'name': 'fixture', 'components': {'memory': ['memory.md']}}
    root = manifest(tmp_path / 'h', payload)
    assert describe_harness_manifest(root, payload) == describe_harness(root)
    with pytest.raises(ValueError, match='must contain an object'):
        describe_harness_manifest(root, [])
