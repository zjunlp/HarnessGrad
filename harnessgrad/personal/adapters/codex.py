"""Explicit Codex probing and read-only, project-scoped surface mapping."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import os
import re
import stat
import subprocess
import sys
import tempfile

from harnessgrad.core.readiness import Readiness
from harnessgrad.improvement.contracts import text_diff
from harnessgrad.personal.adapters.contracts import (
    PREPARED_SCHEMA,
    SELECTED_SCHEMA,
    DirectorySnapshot,
    FileSnapshot,
    HostBinding,
    LoadRequirement,
    PreparedExtension,
    SelectedSnapshot,
    SurfaceError,
    SurfaceMapping,
    TargetSelection,
    binding_digest,
    prepared_digest,
    snapshot_digest,
)
from harnessgrad.personal.contracts import FileChange, SurfaceRef, digest

MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_DIRECTORY_ENTRIES = 64


@dataclass(frozen=True)
class AdapterCapabilities:
    version: str | None
    checks: tuple[Readiness, ...]

    def as_dict(self) -> dict:
        return {'adapter': 'codex', 'version': self.version,
                'checks': [check.as_dict() for check in self.checks]}


def version_output(executable: Path) -> str:
    # Explicit binary only, private startup environment, no account/config reuse.
    with tempfile.TemporaryDirectory(prefix='hg-personal-version-') as directory:
        env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'PATH') if key in os.environ}
        env.update(HOME=directory, USERPROFILE=directory, CODEX_HOME=directory,
                   APPDATA=directory, LOCALAPPDATA=directory, TMP=directory, TEMP=directory)
        result = subprocess.run([str(executable), '--version'], env=env, cwd=directory,
                                capture_output=True, timeout=5,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0 or len(result.stdout) > 4096 or len(result.stderr) > 4096:
            raise ValueError('Codex version probe failed')
        return result.stdout.decode('utf-8').strip()


class CodexAdapter:
    def probe(self, executable: Path | None, *, managed_hooks: bool = False,
              read_version=version_output) -> AdapterCapabilities:
        version = None
        status, detail = 'unavailable', 'No explicit native Codex executable was selected.'
        if executable is not None:
            try:
                path = executable.expanduser()
                if path.is_symlink() or not path.is_file():
                    raise FileNotFoundError
                output = read_version(path.resolve())
                match = re.fullmatch(r'codex-cli (\d+\.\d+\.\d+)(?:[ -].*)?', output)
                if not match:
                    raise ValueError('unrecognized version')
                version = match[1]
                parts = tuple(int(p) for p in version.split('.'))
                status = 'ready' if parts >= (0, 154, 0) else 'unavailable'
                detail = ('Executable version measured; protocol and permissions need separate checks.'
                          if status == 'ready' else 'Older than the stage-0 reference; protocol support unverified.')
            except FileNotFoundError:
                detail = 'Selected native Codex executable is missing or a link.'
            except PermissionError:
                status, detail = 'refused', 'Permission denied for the selected version probe.'
            except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
                status, detail = 'invalid', 'Selected executable could not provide a bounded Codex version.'
        checks = (
            Readiness(status, 'codex.executable_version', detail),
            Readiness('ready', 'personal.synthetic_import', 'Only explicitly imported synthetic sessions.'),
            Readiness('ready', 'codex.selected_surface_read',
                      'Only caller-selected files below one explicit project binding are read.'),
            Readiness('ready', 'codex.selected_surface_render',
                      'Candidates are rendered from an immutable selected snapshot without writing.'),
            Readiness('unchecked', 'codex.selected_surface_loading',
                      'Native loading needs separate version- and mode-bound host evidence.'),
            Readiness('unchecked', 'codex.existing_session_observation', 'No daily session discovery or hook installed.'),
            Readiness('refused' if managed_hooks else 'unchecked', 'codex.hooks',
                      'Managed-hook policy declared; no registration attempted.' if managed_hooks else 'Native trust not inspected or granted.'),
            Readiness('refused', 'codex.harness_writes', 'Stage A is read-only; no install or apply entry point.'),
        )
        return AdapterCapabilities(version, checks)

    def collect_selected(
        self,
        binding: HostBinding,
        selected: tuple[TargetSelection, ...],
    ) -> SelectedSnapshot:
        """Read only explicitly selected instruction and standalone Skill surfaces."""
        normalized = _binding(binding)
        targets = _targets(normalized, selected)
        files: list[FileSnapshot] = []
        directories: list[DirectorySnapshot] = []
        mappings: list[SurfaceMapping] = []
        total = 0
        with _root(normalized) as (root_fd, root_identity):
            for target in targets:
                if target.kind == 'instruction':
                    snapshot, size = _read_relative(
                        root_fd, target.entry, MAX_TOTAL_BYTES - total, target.resource_id)
                    virtual = 'instruction.md'
                else:
                    entries = _skill_entries(root_fd, target)
                    directories.append(DirectorySnapshot(
                        target.entry, entries, digest(list(entries))))
                    surface = SurfaceRef(target.entry.root_id,
                                         target.entry.path + '/SKILL.md')
                    snapshot, size = _read_relative(
                        root_fd, surface, MAX_TOTAL_BYTES - total, target.resource_id)
                    _standalone_skill(snapshot.text, target.resource_id)
                    virtual = ('skills/' + hashlib.sha256(
                        target.resource_id.encode('utf-8')).hexdigest() + '.md')
                files.append(snapshot)
                total += size
                mappings.append(SurfaceMapping(virtual, target.resource_id, snapshot.surface))
        identities = [row.file_identity for row in files]
        if len(identities) != len(set(identities)):
            raise SurfaceError('invalid_path', 'Selected targets resolve to the same native file.')
        canonical_targets = tuple(sorted(targets, key=lambda row: (row.kind, row.resource_id,
                                                                    row.entry.path)))
        canonical_files = tuple(sorted(files, key=lambda row: (row.surface.root_id,
                                                                row.surface.path)))
        canonical_directories = tuple(sorted(directories,
                                              key=lambda row: (row.surface.root_id,
                                                               row.surface.path)))
        canonical_mappings = tuple(sorted(mappings, key=lambda row: row.virtual_path))
        binding_sha256 = binding_digest(normalized, root_identity)
        snapshot_sha256 = snapshot_digest(
            schema=SELECTED_SCHEMA, binding_sha256=binding_sha256,
            targets=canonical_targets, files=canonical_files,
            directories=canonical_directories, surface_map=canonical_mappings)
        return SelectedSnapshot(
            SELECTED_SCHEMA, normalized, root_identity, canonical_targets, canonical_files,
            canonical_directories, canonical_mappings, binding_sha256, snapshot_sha256)

    def prepare_extension(
        self,
        snapshot: SelectedSnapshot,
        files: Mapping[str, str],
    ) -> PreparedExtension:
        """Purely render a complete virtual candidate set to native candidate changes."""
        if not _snapshot_intact(snapshot):
            raise SurfaceError('invalid_candidate', 'A valid selected snapshot is required.')
        if not isinstance(files, Mapping):
            raise SurfaceError('invalid_candidate', 'Candidate files must be a complete mapping.')
        try:
            candidates = dict(files.items())
        except (AttributeError, TypeError, ValueError):
            raise SurfaceError('invalid_candidate', 'Candidate files must be a complete mapping.') from None
        expected = {row.virtual_path for row in snapshot.surface_map}
        if set(candidates) != expected or any(not isinstance(key, str) for key in candidates):
            raise SurfaceError('invalid_candidate',
                               'Candidate keys must exactly match the selected virtual files.')
        if any(not isinstance(value, str) for value in candidates.values()):
            raise SurfaceError('invalid_candidate', 'Every candidate body must be text.')
        total = 0
        for value in candidates.values():
            try:
                size = len(value.encode('utf-8'))
            except UnicodeEncodeError:
                raise SurfaceError('invalid_candidate', 'Candidate text must be valid UTF-8.') from None
            if size > MAX_FILE_BYTES:
                raise SurfaceError('input_limit', 'A candidate file exceeds 256 KiB.')
            total += size
        if total > MAX_TOTAL_BYTES:
            raise SurfaceError('input_limit', 'Candidate text exceeds 1 MiB.')

        native_before: dict[str, str] = {}
        native_after: dict[str, str] = {}
        changes: list[FileChange] = []
        changed_resources: set[str] = set()
        file_by_surface = {(row.surface.root_id, row.surface.path): row
                           for row in snapshot.files}
        target_by_resource = {row.resource_id: row for row in snapshot.targets}
        for mapping in snapshot.surface_map:
            baseline = file_by_surface[(mapping.surface.root_id, mapping.surface.path)]
            candidate = candidates[mapping.virtual_path]
            target = target_by_resource[mapping.resource_id]
            if target.kind == 'skill':
                try:
                    prefix = _skill_frontmatter(baseline.text, mapping.resource_id)
                except SurfaceError:
                    raise SurfaceError('invalid_candidate',
                                       'The selected Skill snapshot is not a supported standalone Skill.',
                                       mapping.resource_id) from None
                if not candidate.startswith(prefix):
                    raise SurfaceError('invalid_candidate',
                                       'Skill frontmatter must remain byte-for-byte unchanged.',
                                       mapping.resource_id)
                try:
                    _standalone_skill(candidate, mapping.resource_id)
                except SurfaceError:
                    raise SurfaceError('invalid_candidate',
                                       'A Skill candidate cannot add local dependencies.',
                                       mapping.resource_id) from None
            label = mapping.surface.root_id + '/' + mapping.surface.path
            native_before[label] = baseline.text
            native_after[label] = candidate
            if candidate != baseline.text:
                changed_resources.add(mapping.resource_id)
                changes.append(FileChange(
                    mapping.surface, (mapping.resource_id,), baseline.sha256, candidate,
                    baseline.mode, baseline.mode))
        canonical_changes = tuple(sorted(changes,
                                         key=lambda row: (row.surface.root_id,
                                                          row.surface.path)))
        rendered_diff = text_diff(native_before, native_after)
        requirements = tuple(LoadRequirement(
            resource_id, 'unknown',
            'No version- and mode-bound native loading evidence is attached.')
            for resource_id in sorted(changed_resources))
        prepared_sha256 = prepared_digest(
            schema=PREPARED_SCHEMA, project_id=snapshot.binding.project_id,
            profile_id=snapshot.binding.profile_id,
            binding_sha256=snapshot.binding_sha256,
            snapshot_sha256=snapshot.snapshot_sha256, renderer_id='codex',
            renderer_version=1, changes=canonical_changes, diff=rendered_diff,
            load_requirements=requirements)
        return PreparedExtension(
            PREPARED_SCHEMA, snapshot.binding.project_id, snapshot.binding.profile_id,
            snapshot.binding_sha256, snapshot.snapshot_sha256, 'codex', 1,
            canonical_changes, rendered_diff, requirements, prepared_sha256)

    def verify_selected(
        self,
        binding: HostBinding,
        snapshot: SelectedSnapshot,
    ) -> None:
        """Re-read the bounded selected set and reject any binding or surface drift."""
        if not _snapshot_intact(snapshot):
            raise SurfaceError('stale_snapshot', 'The selected snapshot is not valid.')
        try:
            normalized = _binding(binding)
        except SurfaceError as exc:
            if exc.code == 'refused':
                raise
            raise SurfaceError('stale_snapshot', 'The host binding no longer matches the snapshot.') from None
        if normalized != snapshot.binding:
            raise SurfaceError('stale_snapshot', 'The host binding no longer matches the snapshot.')
        try:
            current = self.collect_selected(normalized, snapshot.targets)
        except SurfaceError as exc:
            if exc.code == 'refused':
                raise
            if exc.code == 'unavailable' and 'platform' in exc.detail.lower():
                raise
            raise SurfaceError('stale_snapshot',
                               'A selected file, directory, mode, or identity changed.',
                               exc.resource_id) from None
        if (current.binding_sha256 != snapshot.binding_sha256
                or current.snapshot_sha256 != snapshot.snapshot_sha256):
            raise SurfaceError('stale_snapshot',
                               'A selected file, directory, mode, or identity changed.')


def _binding(binding: HostBinding) -> HostBinding:
    if not isinstance(binding, HostBinding):
        raise SurfaceError('invalid_binding', 'A trusted host binding is required.')
    path = binding.root_path
    if not path.is_absolute() or path == Path(path.anchor):
        raise SurfaceError('invalid_binding', 'The project root must be a bounded absolute path.')
    if binding.os_name != sys.platform:
        raise SurfaceError('invalid_binding', 'The binding operating system does not match this host.')
    normalized = Path(os.path.normpath(os.fspath(path)))
    try:
        return HostBinding(binding.profile_id, binding.project_id, binding.root_id,
                           normalized, binding.host_version, binding.os_name, binding.mode)
    except ValueError:
        raise SurfaceError('invalid_binding', 'The host binding contains invalid fields.') from None


def _snapshot_intact(snapshot: object) -> bool:
    if not isinstance(snapshot, SelectedSnapshot):
        return False
    try:
        binding_sha256 = binding_digest(snapshot.binding, snapshot.root_identity)
        snapshot_sha256 = snapshot_digest(
            schema=snapshot.schema, binding_sha256=snapshot.binding_sha256,
            targets=snapshot.targets, files=snapshot.files,
            directories=snapshot.directories, surface_map=snapshot.surface_map)
    except (AttributeError, TypeError, ValueError):
        return False
    return (binding_sha256 == snapshot.binding_sha256
            and snapshot_sha256 == snapshot.snapshot_sha256)


def _targets(binding: HostBinding, selected: tuple[TargetSelection, ...]) -> tuple[TargetSelection, ...]:
    if not isinstance(selected, tuple) or not 1 <= len(selected) <= 2:
        raise SurfaceError('unsupported_surface', 'Select one or two supported resources.')
    if any(not isinstance(row, TargetSelection) for row in selected):
        raise SurfaceError('unsupported_surface', 'Every selected target must use the host contract.')
    if any(row.entry.root_id != binding.root_id for row in selected):
        raise SurfaceError('invalid_path', 'A selected target uses a different authorized root.')
    if len({row.resource_id for row in selected}) != len(selected):
        raise SurfaceError('invalid_path', 'Selected resource ids must be unique.')
    if any(sum(row.kind == kind for row in selected) > 1
           for kind in ('instruction', 'skill')):
        raise SurfaceError('unsupported_surface', 'Only one resource of each kind is supported.')
    native = [row.entry.path if row.kind == 'instruction'
              else row.entry.path + '/SKILL.md' for row in selected]
    if len({value.casefold() for value in native}) != len(native):
        raise SurfaceError('invalid_path', 'Selected native targets overlap by path alias.')
    return tuple(sorted(selected, key=lambda row: (row.kind, row.resource_id,
                                                   row.entry.path)))


def _identity(value: os.stat_result) -> str:
    dev, ino = getattr(value, 'st_dev', None), getattr(value, 'st_ino', None)
    if type(dev) is not int or type(ino) is not int or dev < 0 or ino <= 0:
        raise SurfaceError('unavailable', 'Stable filesystem identity is unavailable on this platform.')
    return f'fs-v1:{dev}:{ino}'


def _require_secure_access() -> None:
    names = ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')
    if (not all(hasattr(os, name) for name in names)
            or os.open not in os.supports_dir_fd):
        raise SurfaceError('unavailable',
                           'Descriptor-relative no-follow access is unavailable on this platform.')


@contextmanager
def _root(binding: HostBinding):
    _require_secure_access()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        try:
            fd = os.open(binding.root_path.anchor, flags)
            stack.callback(os.close, fd)
            for component in binding.root_path.parts[1:]:
                child = os.open(component, flags, dir_fd=fd)
                stack.callback(os.close, child)
                fd = child
        except PermissionError:
            raise SurfaceError('refused', 'Permission denied for the selected project root.') from None
        except FileNotFoundError:
            raise SurfaceError('unavailable', 'The selected project root is unavailable.') from None
        except OSError:
            raise SurfaceError(
                'invalid_binding',
                'The project root or one of its parents is linked or unsafe.') from None
        row = os.fstat(fd)
        if not stat.S_ISDIR(row.st_mode):
            raise SurfaceError('invalid_binding', 'The selected project root is not a directory.')
        yield fd, _identity(row)


def _open_directory(root_fd: int, parts: tuple[str, ...], stack: ExitStack,
                    resource_id: str) -> int:
    directory = os.dup(root_fd)
    stack.callback(os.close, directory)
    try:
        for component in parts:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            stack.callback(os.close, child)
            directory = child
        return directory
    except PermissionError:
        raise SurfaceError('refused', 'Permission denied for a selected target.', resource_id) from None
    except FileNotFoundError:
        raise SurfaceError('unavailable', 'A selected target is unavailable.', resource_id) from None
    except OSError:
        raise SurfaceError('invalid_path',
                           'A selected path contains a link or unsafe directory.', resource_id) from None


def _skill_entries(root_fd: int, target: TargetSelection) -> tuple[str, ...]:
    parts = tuple(target.entry.path.split('/'))
    with ExitStack() as stack:
        directory = _open_directory(root_fd, parts, stack, target.resource_id)
        entries: list[str] = []
        try:
            with os.scandir(directory) as rows:
                for row in rows:
                    entries.append(row.name)
                    if len(entries) >= MAX_DIRECTORY_ENTRIES + 1:
                        raise SurfaceError('input_limit',
                                           'The selected Skill directory exceeds 64 entries.',
                                           target.resource_id)
        except PermissionError:
            raise SurfaceError('refused', 'Permission denied for a selected Skill directory.',
                               target.resource_id) from None
        except OSError:
            raise SurfaceError('unavailable', 'A selected Skill directory cannot be enumerated.',
                               target.resource_id) from None
    result = tuple(sorted(entries))
    if result != ('SKILL.md',):
        raise SurfaceError('unsupported_surface',
                           'A selected Skill must contain only SKILL.md.', target.resource_id)
    return result


def _read_relative(root_fd: int, surface: SurfaceRef, remaining: int,
                   resource_id: str) -> tuple[FileSnapshot, int]:
    parts = tuple(surface.path.split('/'))
    with ExitStack() as stack:
        directory = _open_directory(root_fd, parts[:-1], stack, resource_id)
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
            stack.callback(os.close, fd)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise SurfaceError('invalid_path',
                                   'A selected target is not a regular single-link file.',
                                   resource_id)
            cap = min(MAX_FILE_BYTES, remaining)
            if cap < 0 or before.st_size > cap:
                raise SurfaceError('input_limit', 'Selected text exceeds the input limit.',
                                   resource_id)
            chunks: list[bytes] = []
            size = 0
            while size <= cap:
                chunk = os.read(fd, min(65536, cap + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > cap:
                raise SurfaceError('input_limit', 'Selected text exceeds the input limit.',
                                   resource_id)
            after = os.fstat(fd)
        except PermissionError:
            raise SurfaceError('refused', 'Permission denied for a selected target.',
                               resource_id) from None
        except FileNotFoundError:
            raise SurfaceError('unavailable', 'A selected target is unavailable.',
                               resource_id) from None
        except SurfaceError:
            raise
        except OSError:
            raise SurfaceError('invalid_path', 'A selected target is linked or unsafe.',
                               resource_id) from None
    fields = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns',
              'st_ctime_ns')
    if any(getattr(before, name) != getattr(after, name) for name in fields):
        raise SurfaceError('unavailable', 'A selected target changed while it was read.',
                           resource_id)
    raw = b''.join(chunks)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise SurfaceError('unsupported_surface', 'A selected target is not UTF-8 text.',
                           resource_id) from None
    return FileSnapshot(surface, text, hashlib.sha256(raw).hexdigest(),
                        stat.S_IMODE(before.st_mode), _identity(before)), len(raw)


def _skill_frontmatter(text: str, resource_id: str) -> str:
    offset = 1 if text.startswith('\ufeff') else 0
    rest = text[offset:]
    if not (rest.startswith('---\n') or rest.startswith('---\r\n')):
        raise SurfaceError('unsupported_surface',
                           'A selected Skill needs explicit frontmatter.', resource_id)
    position = offset
    lines = text[offset:].splitlines(keepends=True)
    position += len(lines[0])
    for line in lines[1:]:
        position += len(line)
        if line.rstrip('\r\n') == '---':
            return text[:position]
    raise SurfaceError('unsupported_surface',
                       'A selected Skill has incomplete frontmatter.', resource_id)


def _standalone_skill(text: str, resource_id: str) -> None:
    prefix = _skill_frontmatter(text, resource_id)
    if re.search(r'(?im)^(?:dependencies|scripts|references|assets|files)\s*:', prefix):
        raise SurfaceError('unsupported_surface',
                           'A selected Skill declares local dependencies.', resource_id)
    if re.search(r'(?i)(?:^|[\s`"\'(])(?:\./)?(?:scripts|references|assets)/[^\s)`"\']+', text):
        raise SurfaceError('unsupported_surface',
                           'A selected Skill references local supporting files.', resource_id)
