"""Supported native/npm identities cover dependency contents, not just entry files."""
import json
import os
import shutil
from pathlib import Path
import tempfile

import pytest

from base_harness.codex import codex_runtime


@pytest.fixture(params=['native', 'npm'])
def installation(request, tmp_path):
    with tempfile.TemporaryDirectory(prefix='hg-codex-content-', dir='/var/tmp') as directory:
        root = Path(directory)
        if request.param == 'native':
            program = root / 'native/bin/codex'
            program.parent.mkdir(parents=True)
            shutil.copyfile('/bin/true', program)
            program.with_name('codex-exec-server').write_bytes(b'authored helper')
            dependency = program.parent / 'runtime.dat'
            argv = [str(program)]
        else:
            program = root / 'node/bin/node'
            program.parent.mkdir(parents=True)
            shutil.copyfile('/bin/true', program)
            package = root / 'packages/codex'
            (package / 'bin').mkdir(parents=True)
            (package / 'package.json').write_text('{"name":"@openai/codex"}')
            script = package / 'bin/codex.js'
            script.write_text('// authored wrapper; never executed')
            dependency = package / 'module.js'
            argv = [str(program), str(script)]
        program.chmod(0o755)
        dependency.write_text('first bytes')
        selected = codex_runtime.installation(argv)
        harness = tmp_path / 'candidate'
        harness.mkdir()
        record = {'schema': 1, 'version': 'fixture', 'selection': argv, **selected}
        manifest = {'runtime_paths': selected['runtime_paths'], codex_runtime.RECORD: record}
        (harness / 'harness.json').write_text(json.dumps(manifest))
        yield harness, dependency, record, root


def test_supported_receipt_is_content_bound(installation):
    harness, dependency, record, _ = installation
    assert record['cacheable'] is True
    assert record['closure'] in ('native', 'npm')
    assert codex_runtime.resolve(harness, {})['content_inventory']['files'] >= 3
    before = dependency.stat()
    dependency.write_text('other bytes')
    os.utime(dependency, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match='dependency contents changed'):
        codex_runtime.resolve(harness, {})


def test_dependency_mode_is_part_of_identity(installation):
    harness, dependency, _, _ = installation
    dependency.chmod(0o700)
    with pytest.raises(ValueError, match='dependency contents changed'):
        codex_runtime.resolve(harness, {})


def test_new_dependency_file_is_not_silently_adopted(installation):
    harness, dependency, _, _ = installation
    dependency.with_name('new-code.js').write_text('new module')
    with pytest.raises(ValueError, match='dependency contents changed'):
        codex_runtime.resolve(harness, {})


def test_unbound_external_link_requires_explicit_binding(installation):
    harness, dependency, record, root = installation
    outside = root / 'external/library.js'
    outside.parent.mkdir()
    outside.write_text('external bytes')
    dependency.with_name('external-link').symlink_to(outside)
    with pytest.raises(ValueError, match='explicit --runtime'):
        codex_runtime.installation(record['argv'])
    admitted = codex_runtime.installation(record['argv'], extra=(outside,))
    assert admitted['content_inventory']['symlinks'] >= 1
    outside.write_text('mutated external bytes')
    with pytest.raises(ValueError, match='changed'):
        codex_runtime.check_files(admitted)


def test_opaque_and_legacy_receipts_do_not_claim_complete_closure(tmp_path):
    with tempfile.TemporaryDirectory(prefix='hg-codex-opaque-', dir='/var/tmp') as directory:
        program = Path(directory) / 'codex'
        program.write_text('#!/bin/sh\nprintf "fixture"\n')
        program.chmod(0o755)
        selected = codex_runtime.installation([str(program)])
        assert selected['closure'] == 'opaque'
        assert selected['cacheable'] is False
        assert selected['content_inventory'] is None


@pytest.mark.parametrize('program_name', ['codex', 'node'])
@pytest.mark.parametrize('body', ['#!/bin/sh\nexit 0\n', '#!/usr/bin/python3\nprint(0)\n', 'no ELF header'])
def test_masquerading_executable_names_are_opaque(tmp_path, program_name, body):
    program = tmp_path / program_name
    program.write_text(body)
    program.chmod(0o755)
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'package.json').write_text('{"name":"@openai/codex"}')
    script = package / 'codex.js'
    script.write_text('// fixture')
    argv = [str(program), str(script)] if program_name == 'node' else [str(program)]
    assert codex_runtime._variant(argv) == 'opaque'


def test_native_parameter_wrapper_is_opaque(tmp_path):
    program = tmp_path / 'codex'
    shutil.copyfile('/bin/true', program)
    program.chmod(0o755)
    assert codex_runtime._variant([str(program), '--wrapper']) == 'opaque'
    assert not codex_runtime._native_elf(Path(__file__))


def test_node_parameter_wrapper_is_opaque(tmp_path):
    node = tmp_path / 'node'
    shutil.copyfile('/bin/true', node)
    node.chmod(0o755)
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'package.json').write_text('{"name":"@openai/codex"}')
    script = package / 'codex.js'
    script.write_text('// fixture')
    assert codex_runtime._variant([str(node), '--require', '/unbound/inject.js', str(script)]) == 'opaque'
