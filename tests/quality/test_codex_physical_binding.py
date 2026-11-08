"""Prepared companions must retain physical identities and sandbox reachability."""
import json
from pathlib import Path
import tempfile

import pytest

from base_harness.codex import codex_runtime
from tools import configure_codex_harness as preparation


@pytest.fixture
def prepared(tmp_path):
    with tempfile.TemporaryDirectory(prefix='hg-codex-physical-', dir='/var/tmp') as directory:
        root = Path(directory)
        program = root / 'installation/bin/codex'
        program.parent.mkdir(parents=True)
        program.write_text('#!/bin/sh\nexit 0\n')
        program.chmod(0o755)
        first = root / 'first/helper'
        first.parent.mkdir()
        first.write_text('same helper bytes')
        second = root / 'second/helper'
        second.parent.mkdir()
        second.write_bytes(first.read_bytes())
        alias = program.with_name('codex-exec-server')
        alias.symlink_to(first)
        selected = codex_runtime.installation([str(program)])
        harness = tmp_path / 'candidate'
        harness.mkdir()
        record = {'schema': 1, 'version': 'fixture', 'selection': [str(program)], **selected}
        (harness / 'harness.json').write_text(json.dumps({
            'runtime_paths': selected['runtime_paths'], codex_runtime.RECORD: record}))
        yield harness, selected, alias, first, second


def test_equal_bytes_retarget_outside_bindings_is_refused(prepared):
    harness, _, alias, _, second = prepared
    assert codex_runtime.resolve(harness, {})
    alias.unlink()
    alias.symlink_to(second)
    with pytest.raises(ValueError, match='physical|outside|changed'):
        codex_runtime.resolve(harness, {})


def test_equal_bytes_retarget_inside_binding_still_changes_identity(prepared):
    harness, _, alias, _, _ = prepared
    replacement = alias.parent / 'identical-helper'
    replacement.write_bytes(alias.read_bytes())
    alias.unlink()
    alias.symlink_to(replacement)
    with pytest.raises(ValueError, match='physical|changed'):
        codex_runtime.resolve(harness, {})


def test_legacy_records_still_require_physical_binding_coverage(prepared):
    harness, _, alias, _, second = prepared
    path = harness / 'harness.json'
    manifest = json.loads(path.read_text())
    for row in manifest[codex_runtime.RECORD]['files']:
        row.pop('realpath', None)
    path.write_text(json.dumps(manifest))
    assert codex_runtime.resolve(harness, {})
    alias.unlink()
    alias.symlink_to(second)
    with pytest.raises(ValueError, match='outside'):
        codex_runtime.resolve(harness, {})


def test_probe_retarget_does_not_publish_manifest(prepared, monkeypatch):
    harness, _, alias, _, second = prepared
    path = harness / 'harness.json'
    path.write_text(json.dumps({'name': 'codex', 'entrypoint': 'agent.py'}))
    before = path.read_bytes()
    monkeypatch.setattr(preparation, '_adapter', lambda _: codex_runtime)

    def probe(selected, *, native):
        if native:
            alias.unlink()
            alias.symlink_to(second)
        return 'fixture'

    monkeypatch.setattr(preparation, '_version', probe)
    with pytest.raises(ValueError, match='physical|outside|changed'):
        preparation.configure(harness, str(alias.with_name('codex')), env={})
    assert path.read_bytes() == before


def test_trusted_caller_can_exclude_hidden_platform_roots(prepared):
    harness, _, alias, _, _ = prepared
    with pytest.raises(ValueError, match='overlaps'):
        codex_runtime.resolve(harness, {}, forbidden=(alias.parent.parent,))
