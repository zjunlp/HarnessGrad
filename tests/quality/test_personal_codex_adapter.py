"""B5 maps only explicit Codex resources and never mutates their native files."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import stat
import sys

import pytest

import harnessgrad.personal.adapters.codex as codex_module
from harnessgrad.personal.adapters.codex import CodexAdapter
from harnessgrad.personal.adapters.contracts import (
    HostBinding,
    PreparedExtension,
    SelectedSnapshot,
    SurfaceError,
    TargetSelection,
)
from harnessgrad.personal.contracts import SurfaceRef


def binding(root: Path, *, project_id: str = 'project-1', version: str | None = None,
            mode: str = 'controlled') -> HostBinding:
    return HostBinding('profile-1', project_id, 'project-root', root, version,
                       sys.platform, mode)


def targets(*, reverse: bool = False) -> tuple[TargetSelection, ...]:
    rows = (
        TargetSelection('project-instructions', 'instruction',
                        SurfaceRef('project-root', 'AGENTS.md')),
        TargetSelection('verify-skill', 'skill',
                        SurfaceRef('project-root', 'skills/verify')),
    )
    return tuple(reversed(rows)) if reverse else rows


@pytest.fixture
def project(tmp_path):
    if not codex_module._secure_access_available():
        pytest.skip('selected filesystem reads require descriptor-relative no-follow access')
    root = tmp_path / 'project'
    skill = root / 'skills' / 'verify'
    skill.mkdir(parents=True)
    (root / 'AGENTS.md').write_bytes(b'Run focused tests.\r\n')
    (skill / 'SKILL.md').write_bytes(
        b'---\r\nname: verify\r\ndescription: Verify changes\r\n---\r\nRun tests.\r\n')
    return root


def image(root: Path) -> dict[str, tuple[bytes, int]]:
    return {str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            for path in root.rglob('*') if path.is_file()}


def candidates(snapshot: SelectedSnapshot) -> dict[str, str]:
    by_surface = {(row.surface.root_id, row.surface.path): row.text
                  for row in snapshot.files}
    return {row.virtual_path: by_surface[(row.surface.root_id, row.surface.path)]
            for row in snapshot.surface_map}


def error_code(exc) -> str:
    return exc.value.code


def test_collect_maps_instruction_and_standalone_skill_without_mutating(project):
    before = image(project)
    snapshot = CodexAdapter().collect_selected(binding(project), targets(reverse=True))
    assert [row.virtual_path for row in snapshot.surface_map][0] == 'instruction.md'
    skill_path = 'skills/' + hashlib.sha256(b'verify-skill').hexdigest() + '.md'
    assert [row.virtual_path for row in snapshot.surface_map] == ['instruction.md', skill_path]
    assert {row.surface.path for row in snapshot.files} == {
        'AGENTS.md', 'skills/verify/SKILL.md'}
    assert snapshot.directories[0].entries == ('SKILL.md',)
    assert snapshot.files[0].text == 'Run focused tests.\r\n'
    assert snapshot.files[0].sha256 == hashlib.sha256(
        b'Run focused tests.\r\n').hexdigest()
    assert image(project) == before


def test_prepare_changes_only_modified_native_file_and_preserves_frontmatter(project):
    adapter = CodexAdapter()
    snapshot = adapter.collect_selected(binding(project), targets())
    proposed = candidates(snapshot)
    skill_virtual = next(path for path in proposed if path.startswith('skills/'))
    prefix = proposed[skill_virtual].split('---\r\n', 2)[:2]
    proposed[skill_virtual] = proposed[skill_virtual].replace('Run tests.',
                                                              'Run related tests first.')
    prepared = adapter.prepare_extension(snapshot, proposed)
    assert len(prepared.changes) == 1
    assert prepared.changes[0].surface.path == 'skills/verify/SKILL.md'
    assert prepared.changes[0].after_text.startswith('---\r\nname: verify\r\n')
    assert 'a/project-root/skills/verify/SKILL.md' in prepared.diff
    assert skill_virtual not in prepared.diff
    assert prepared.load_requirements[0].timing == 'unknown'
    assert prefix
    assert (project / 'skills/verify/SKILL.md').read_bytes().endswith(b'Run tests.\r\n')


def test_prepare_no_change_is_empty(project):
    adapter = CodexAdapter()
    snapshot = adapter.collect_selected(binding(project), targets())
    prepared = adapter.prepare_extension(snapshot, candidates(snapshot))
    assert prepared.changes == () and prepared.diff == ''
    assert prepared.load_requirements == ()


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'none', 'absolute'])
def test_candidate_keys_and_bodies_must_be_exact(project, mutation):
    adapter = CodexAdapter()
    snapshot = adapter.collect_selected(binding(project), targets())
    proposed = candidates(snapshot)
    if mutation == 'missing':
        proposed.pop(next(iter(proposed)))
    elif mutation == 'extra':
        proposed['skills/extra.md'] = 'extra'
    elif mutation == 'none':
        proposed[next(iter(proposed))] = None
    else:
        proposed[str(project / 'AGENTS.md')] = proposed.pop('instruction.md')
    with pytest.raises(SurfaceError) as exc:
        adapter.prepare_extension(snapshot, proposed)
    assert error_code(exc) == 'invalid_candidate'


def test_skill_frontmatter_cannot_be_changed(project):
    adapter = CodexAdapter()
    snapshot = adapter.collect_selected(binding(project), targets())
    proposed = candidates(snapshot)
    skill = next(path for path in proposed if path.startswith('skills/'))
    proposed[skill] = proposed[skill].replace('name: verify', 'name: replaced')
    with pytest.raises(SurfaceError) as exc:
        adapter.prepare_extension(snapshot, proposed)
    assert error_code(exc) == 'invalid_candidate'


@pytest.mark.parametrize('dependency', [
    'Run scripts/check.py.\n', 'Follow [steps](../../runbook.md).\n',
])
def test_skill_candidate_cannot_add_an_unmapped_local_dependency(project, dependency):
    adapter = CodexAdapter()
    snapshot = adapter.collect_selected(binding(project), targets())
    proposed = candidates(snapshot)
    skill = next(path for path in proposed if path.startswith('skills/'))
    proposed[skill] += dependency
    with pytest.raises(SurfaceError) as exc:
        adapter.prepare_extension(snapshot, proposed)
    assert error_code(exc) == 'invalid_candidate'


@pytest.mark.parametrize('extra,make_directory', [
    ('README.md', False), ('scripts', True), ('references', True),
])
def test_multifile_skill_is_explicitly_unsupported(project, extra, make_directory):
    path = project / 'skills/verify' / extra
    path.mkdir() if make_directory else path.write_text('supporting material')
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets())
    assert error_code(exc) == 'unsupported_surface'


@pytest.mark.parametrize('body', [
    '---\nname: verify\ndependencies:\n  - helper.py\n---\nBody\n',
    '---\nname: verify\n---\nUse scripts/check.py before delivery.\n',
    '---\nname: verify\n---\nFollow [steps](../../runbook.md).\n',
])
def test_declared_local_skill_dependencies_are_unsupported(project, body):
    (project / 'skills/verify/SKILL.md').write_text(body)
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets())
    assert error_code(exc) == 'unsupported_surface'


@pytest.mark.parametrize('raw', [
    b'', b'\xef\xbb\xbfChinese: \xe4\xb8\xad\xe6\x96\x87\r\n', b'no final newline',
    b'line one\nline two\n',
])
def test_instruction_bytes_round_trip_and_diff_is_native(raw, project):
    (project / 'AGENTS.md').write_bytes(raw)
    adapter = CodexAdapter()
    selected = adapter.collect_selected(binding(project), targets()[:1])
    row = selected.files[0]
    assert row.text.encode('utf-8') == raw
    assert row.sha256 == hashlib.sha256(raw).hexdigest()
    proposed = candidates(selected)
    proposed['instruction.md'] += 'updated'
    prepared = adapter.prepare_extension(selected, proposed)
    assert prepared.changes[0].after_text == proposed['instruction.md']
    assert 'project-root/AGENTS.md' in prepared.diff


def test_links_hardlinks_special_files_and_root_links_are_refused(project, tmp_path):
    outside = tmp_path / 'outside.md'
    outside.write_text('private outside text')
    original = project / 'AGENTS.md'
    original.unlink()
    original.symlink_to(outside)
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets()[:1])
    assert error_code(exc) == 'invalid_path'
    assert 'private outside text' not in exc.value.detail

    original.unlink()
    original.write_text('instruction')
    hardlink = tmp_path / 'hardlink.md'
    os.link(original, hardlink)
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets()[:1])
    assert error_code(exc) == 'invalid_path'
    hardlink.unlink()

    original.unlink()
    os.mkfifo(original)
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets()[:1])
    assert error_code(exc) == 'invalid_path'
    original.unlink()
    original.write_text('instruction')

    alias = tmp_path / 'project-alias'
    alias.symlink_to(project, target_is_directory=True)
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(alias), targets()[:1])
    assert error_code(exc) == 'invalid_binding'


def test_non_utf8_and_limits_fail_without_a_snapshot(project):
    path = project / 'AGENTS.md'
    path.write_bytes(b'\xff')
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets()[:1])
    assert error_code(exc) == 'unsupported_surface'
    path.write_bytes(b'x' * (256 * 1024 + 1))
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets()[:1])
    assert error_code(exc) == 'input_limit'


def test_directory_enumeration_and_candidate_limits_are_bounded(project):
    skill = project / 'skills/verify'
    for index in range(64):
        (skill / f'extra-{index}').write_text('not read')
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets())
    assert error_code(exc) == 'input_limit'
    for path in skill.glob('extra-*'):
        path.unlink()
    adapter = CodexAdapter()
    selected = adapter.collect_selected(binding(project), targets())
    proposed = candidates(selected)
    proposed['instruction.md'] = 'x' * (256 * 1024 + 1)
    with pytest.raises(SurfaceError) as exc:
        adapter.prepare_extension(selected, proposed)
    assert error_code(exc) == 'input_limit'


def test_replacement_during_read_does_not_return_a_snapshot(project, monkeypatch):
    instruction = project / 'AGENTS.md'
    instruction.write_bytes(b'x' * 70000)
    original_read = codex_module.os.read
    changed = False

    def racing_read(fd, size):
        nonlocal changed
        result = original_read(fd, size)
        if result and not changed:
            changed = True
            instruction.write_bytes(b'y' * 70000)
        return result

    monkeypatch.setattr(codex_module.os, 'read', racing_read)
    with pytest.raises(SurfaceError) as exc:
        CodexAdapter().collect_selected(binding(project), targets()[:1])
    assert error_code(exc) == 'unavailable'


def test_permission_loss_is_refused_when_the_platform_enforces_it(project):
    instruction = project / 'AGENTS.md'
    original_mode = stat.S_IMODE(instruction.stat().st_mode)
    instruction.chmod(0)
    try:
        if os.access(instruction, os.R_OK):
            pytest.skip('current account bypasses file permission bits')
        with pytest.raises(SurfaceError) as exc:
            CodexAdapter().collect_selected(binding(project), targets()[:1])
        assert error_code(exc) == 'refused'
    finally:
        instruction.chmod(original_mode)


@pytest.mark.parametrize('drift', ['text', 'mode', 'identity', 'directory'])
def test_verify_detects_selected_read_set_drift(project, drift):
    adapter = CodexAdapter()
    selected = adapter.collect_selected(binding(project), targets())
    instruction = project / 'AGENTS.md'
    if drift == 'text':
        instruction.write_text('changed')
    elif drift == 'mode':
        instruction.chmod(stat.S_IMODE(instruction.stat().st_mode) ^ stat.S_IXUSR)
    elif drift == 'identity':
        replacement = project / 'replacement'
        replacement.write_bytes(instruction.read_bytes())
        replacement.chmod(stat.S_IMODE(instruction.stat().st_mode))
        os.replace(replacement, instruction)
    else:
        (project / 'skills/verify/extra.txt').write_text('new entry')
    with pytest.raises(SurfaceError) as exc:
        adapter.verify_selected(binding(project), selected)
    assert error_code(exc) == 'stale_snapshot'


@pytest.mark.parametrize('changed', ['version', 'mode', 'project', 'root'])
def test_verify_rejects_binding_drift(project, tmp_path, changed):
    adapter = CodexAdapter()
    selected = adapter.collect_selected(binding(project), targets())
    kwargs = {'version': None, 'mode': 'controlled', 'project_id': 'project-1'}
    root = project
    if changed == 'version': kwargs['version'] = '0.200.0'
    if changed == 'mode': kwargs['mode'] = 'bypass'
    if changed == 'project': kwargs['project_id'] = 'project-2'
    if changed == 'root':
        root = tmp_path / 'other'; root.mkdir()
    with pytest.raises(SurfaceError) as exc:
        adapter.verify_selected(binding(root, **kwargs), selected)
    assert error_code(exc) == 'stale_snapshot'


def test_contract_round_trip_is_canonical_detached_and_tamper_evident(project):
    adapter = CodexAdapter()
    first = adapter.collect_selected(binding(project), targets())
    second = adapter.collect_selected(binding(project), targets(reverse=True))
    assert first.snapshot_sha256 == second.snapshot_sha256
    value = first.as_dict()
    restored = SelectedSnapshot.from_json(first.to_json())
    assert restored == first
    value['files'][0]['text'] = 'tampered'
    with pytest.raises(ValueError, match='digest'):
        SelectedSnapshot.from_dict(value)
    unknown = first.as_dict(); unknown['unexpected'] = True
    with pytest.raises(ValueError, match='unknown'):
        SelectedSnapshot.from_dict(unknown)
    with pytest.raises(ValueError, match='unique'):
        SelectedSnapshot.from_json('{"schema":1,"schema":1}')
    detached = first.as_dict()
    detached['targets'].clear()
    assert len(first.targets) == 2
    prepared = adapter.prepare_extension(first, candidates(first))
    assert PreparedExtension.from_json(prepared.to_json()) == prepared
    object.__setattr__(first, 'snapshot_sha256', '0' * 64)
    with pytest.raises(SurfaceError) as exc:
        adapter.prepare_extension(first, candidates(restored))
    assert error_code(exc) == 'invalid_candidate'


def test_prepare_is_deterministic_and_does_not_read_the_current_project(project, tmp_path):
    adapter = CodexAdapter()
    selected = adapter.collect_selected(binding(project), targets())
    proposed = candidates(selected)
    proposed['instruction.md'] += 'Candidate only.\n'
    moved = tmp_path / 'moved-away'
    project.rename(moved)
    first = adapter.prepare_extension(selected, proposed)
    second = adapter.prepare_extension(selected, copy.deepcopy(proposed))
    assert first == second and first.changes[0].surface.path == 'AGENTS.md'
    with pytest.raises(SurfaceError) as exc:
        adapter.verify_selected(binding(project), selected)
    assert error_code(exc) == 'stale_snapshot'


def test_same_named_resources_in_two_projects_never_share_a_snapshot(project, tmp_path):
    other = tmp_path / 'other'
    skill = other / 'skills/verify'; skill.mkdir(parents=True)
    (other / 'AGENTS.md').write_text('other instructions')
    (skill / 'SKILL.md').write_text('---\nname: verify\n---\nOther body\n')
    adapter = CodexAdapter()
    first = adapter.collect_selected(binding(project, project_id='one'), targets())
    second = adapter.collect_selected(binding(other, project_id='two'), targets())
    assert first.binding_sha256 != second.binding_sha256
    assert first.snapshot_sha256 != second.snapshot_sha256
    with pytest.raises(SurfaceError) as exc:
        adapter.verify_selected(binding(other, project_id='two'), first)
    assert error_code(exc) == 'stale_snapshot'


def test_probe_reports_mapping_separately_from_unverified_native_loading():
    checks = {row.scope: row.status for row in CodexAdapter().probe(None).checks}
    expected = 'ready' if codex_module._secure_access_available() else 'unavailable'
    assert checks['codex.selected_surface_read'] == expected
    assert checks['codex.selected_surface_render'] == 'ready'
    assert checks['codex.selected_surface_loading'] == 'unchecked'
    assert checks['codex.harness_writes'] == 'refused'


def test_unavailable_platform_is_reported_before_any_selected_file_access(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_module, '_secure_access_available', lambda: False)

    def unexpected_access(*args, **kwargs):
        pytest.fail('an unavailable platform must not open selected files')

    monkeypatch.setattr(os, 'open', unexpected_access)
    adapter = CodexAdapter()
    checks = {row.scope: row.status for row in adapter.probe(None).checks}
    assert checks['codex.selected_surface_read'] == 'unavailable'
    assert checks['codex.selected_surface_render'] == 'ready'
    with pytest.raises(SurfaceError) as exc:
        adapter.collect_selected(binding(tmp_path), targets()[:1])
    assert error_code(exc) == 'unavailable'


@pytest.mark.parametrize('reference,local', [
    ('[steps](../../runbook.md)', True),
    ('[steps [required] here](../../runbook.md)', True),
    ('[steps][guide]\n[guide]: ../runbook.md', True),
    ('![diagram](<../diagram one.png>)', True),
    ('[steps](file:///tmp/runbook.md)', True),
    ('<file:///tmp/runbook.md>', True),
    ('[steps](https://example.com/runbook.md)', False),
    ('[steps](//example.com/runbook.md)', False),
    ('[steps](#checks)', False),
])
def test_standalone_skill_rejects_local_markdown_dependencies(reference, local):
    text = '---\nname: verify\n---\n' + reference + '\n'
    if local:
        with pytest.raises(SurfaceError) as exc:
            codex_module._standalone_skill(text, 'verify-skill')
        assert error_code(exc) == 'unsupported_surface'
    else:
        codex_module._standalone_skill(text, 'verify-skill')
