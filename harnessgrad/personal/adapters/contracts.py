"""Serializable, model-independent contracts at the personal host boundary."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from harnessgrad.improvement.contracts import (
    is_relative_path,
    reject_json_constant,
    unique_json_object,
)
from harnessgrad.personal.contracts import FileChange, SurfaceRef, digest, encode, identifier

MAX_DTO_BYTES = 8 * 1024 * 1024
SELECTED_SCHEMA = 'harnessgrad.personal.selected-surfaces.v1'
PREPARED_SCHEMA = 'harnessgrad.personal.prepared-extension.v1'
_DIGEST = re.compile(r'[0-9a-f]{64}')
_IDENTITY = re.compile(r'fs-v1:[0-9]+:[0-9]+')


class SurfaceError(ValueError):
    """Stable host-boundary failure without selected content or absolute paths."""

    CODES = frozenset({
        'invalid_binding', 'invalid_path', 'unsupported_surface', 'input_limit',
        'unavailable', 'refused', 'stale_snapshot', 'invalid_candidate',
    })

    def __init__(self, code: str, detail: str, resource_id: str | None = None):
        if code not in self.CODES:
            raise ValueError('unknown surface error code')
        if not isinstance(detail, str) or not detail or len(detail) > 512:
            raise ValueError('surface error detail must be bounded text')
        if resource_id is not None:
            identifier(resource_id)
        self.code = code
        self.detail = detail
        self.resource_id = resource_id
        super().__init__(detail)

    def as_dict(self) -> dict:
        return {'code': self.code, 'detail': self.detail, 'resource_id': self.resource_id}


def _exact(value: object, fields: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f'{name} contains unknown or missing fields')
    return value


def _tuple(value: object, name: str) -> tuple:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{name} must be an array')
    return tuple(value)


def _digest_value(value: str, name: str) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f'invalid {name}')


def _identity_value(value: str, name: str = 'filesystem identity') -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ValueError(f'invalid {name}')


def _payload_size(value: object) -> None:
    try:
        size = len(encode(value).encode('utf-8'))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError('surface DTO must contain canonical JSON values') from None
    if size > MAX_DTO_BYTES:
        raise ValueError('surface DTO exceeds 8 MiB')


def decode_dto(raw: bytes | str) -> dict:
    """Decode an independent host-boundary payload with its own 8 MiB limit."""
    try:
        size = len(raw if isinstance(raw, bytes) else raw.encode('utf-8'))
    except (AttributeError, UnicodeError):
        raise ValueError('surface DTO must be UTF-8 JSON') from None
    if size > MAX_DTO_BYTES:
        raise ValueError('surface DTO exceeds 8 MiB')
    try:
        value = json.loads(raw, object_pairs_hook=unique_json_object,
                           parse_constant=reject_json_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('surface DTO must be JSON with unique keys') from None
    if not isinstance(value, dict):
        raise ValueError('surface DTO must contain an object')
    return value


@dataclass(frozen=True)
class HostBinding:
    profile_id: str
    project_id: str
    root_id: str
    root_path: Path
    host_version: str | None
    os_name: str
    mode: str

    def __post_init__(self):
        for value in (self.profile_id, self.project_id, self.root_id, self.os_name):
            identifier(value)
        if not isinstance(self.root_path, Path):
            raise ValueError('binding root_path must be a Path')
        if self.host_version is not None:
            identifier(self.host_version)
        if self.mode not in ('controlled', 'bypass'):
            raise ValueError('binding mode must be controlled or bypass')

    def as_dict(self) -> dict:
        return {
            'profile_id': self.profile_id,
            'project_id': self.project_id,
            'root_id': self.root_id,
            'root_path': str(self.root_path),
            'host_version': self.host_version,
            'os_name': self.os_name,
            'mode': self.mode,
        }

    @classmethod
    def from_dict(cls, value: dict) -> 'HostBinding':
        value = _exact(value, {'profile_id', 'project_id', 'root_id', 'root_path',
                               'host_version', 'os_name', 'mode'}, 'host binding')
        if not isinstance(value['root_path'], str):
            raise ValueError('binding root_path must be text')
        try:
            return cls(**{**value, 'root_path': Path(value['root_path'])})
        except TypeError:
            raise ValueError('invalid host binding') from None


@dataclass(frozen=True)
class TargetSelection:
    resource_id: str
    kind: str
    entry: SurfaceRef

    def __post_init__(self):
        identifier(self.resource_id)
        if self.kind not in ('instruction', 'skill'):
            raise ValueError('target kind must be instruction or skill')
        if not isinstance(self.entry, SurfaceRef):
            raise ValueError('target entry must be a SurfaceRef')

    def as_dict(self) -> dict:
        return {'resource_id': self.resource_id, 'kind': self.kind,
                'entry': self.entry.as_dict()}

    @classmethod
    def from_dict(cls, value: dict) -> 'TargetSelection':
        value = _exact(value, {'resource_id', 'kind', 'entry'}, 'target selection')
        try:
            return cls(value['resource_id'], value['kind'], SurfaceRef.from_dict(value['entry']))
        except TypeError:
            raise ValueError('invalid target selection') from None


@dataclass(frozen=True)
class FileSnapshot:
    surface: SurfaceRef
    text: str
    sha256: str
    mode: int
    file_identity: str

    def __post_init__(self):
        if not isinstance(self.surface, SurfaceRef) or not isinstance(self.text, str):
            raise ValueError('file snapshot needs a surface and text')
        if len(self.text.encode('utf-8')) > 256 * 1024:
            raise ValueError('file snapshot exceeds 256 KiB')
        _digest_value(self.sha256, 'file digest')
        if hashlib.sha256(self.text.encode('utf-8')).hexdigest() != self.sha256:
            raise ValueError('file snapshot text does not match its digest')
        if type(self.mode) is not int or not 0 <= self.mode <= 0o7777:
            raise ValueError('file snapshot mode is invalid')
        _identity_value(self.file_identity, 'file identity')

    def as_dict(self) -> dict:
        return {
            'surface': self.surface.as_dict(), 'text': self.text, 'sha256': self.sha256,
            'mode': self.mode, 'file_identity': self.file_identity,
        }

    @classmethod
    def from_dict(cls, value: dict) -> 'FileSnapshot':
        value = _exact(value, {'surface', 'text', 'sha256', 'mode', 'file_identity'},
                       'file snapshot')
        try:
            return cls(SurfaceRef.from_dict(value['surface']), value['text'], value['sha256'],
                       value['mode'], value['file_identity'])
        except TypeError:
            raise ValueError('invalid file snapshot') from None


@dataclass(frozen=True)
class DirectorySnapshot:
    surface: SurfaceRef
    entries: tuple[str, ...]
    sha256: str

    def __post_init__(self):
        if not isinstance(self.surface, SurfaceRef) or not isinstance(self.entries, tuple):
            raise ValueError('directory snapshot needs a surface and entries')
        if any(not isinstance(value, str) or not value or any(c in value for c in '\x00/\\')
               for value in self.entries):
            raise ValueError('directory entries must be plain names')
        entries = tuple(sorted(self.entries))
        if len(set(entries)) != len(entries):
            raise ValueError('directory entries must be unique')
        object.__setattr__(self, 'entries', entries)
        _digest_value(self.sha256, 'directory digest')
        if digest(list(entries)) != self.sha256:
            raise ValueError('directory entries do not match their digest')

    def as_dict(self) -> dict:
        return {'surface': self.surface.as_dict(), 'entries': list(self.entries),
                'sha256': self.sha256}

    @classmethod
    def from_dict(cls, value: dict) -> 'DirectorySnapshot':
        value = _exact(value, {'surface', 'entries', 'sha256'}, 'directory snapshot')
        try:
            return cls(SurfaceRef.from_dict(value['surface']),
                       _tuple(value['entries'], 'directory entries'), value['sha256'])
        except TypeError:
            raise ValueError('invalid directory snapshot') from None


@dataclass(frozen=True)
class SurfaceMapping:
    virtual_path: str
    resource_id: str
    surface: SurfaceRef

    def __post_init__(self):
        if not is_relative_path(self.virtual_path):
            raise ValueError('virtual path must be a relative POSIX path')
        identifier(self.resource_id)
        if not isinstance(self.surface, SurfaceRef):
            raise ValueError('surface mapping needs a SurfaceRef')

    def as_dict(self) -> dict:
        return {'virtual_path': self.virtual_path, 'resource_id': self.resource_id,
                'surface': self.surface.as_dict()}

    @classmethod
    def from_dict(cls, value: dict) -> 'SurfaceMapping':
        value = _exact(value, {'virtual_path', 'resource_id', 'surface'}, 'surface mapping')
        try:
            return cls(value['virtual_path'], value['resource_id'],
                       SurfaceRef.from_dict(value['surface']))
        except TypeError:
            raise ValueError('invalid surface mapping') from None


@dataclass(frozen=True)
class LoadRequirement:
    resource_id: str
    timing: str
    detail: str

    def __post_init__(self):
        identifier(self.resource_id)
        if self.timing not in ('new_task', 'manual_refresh', 'unknown'):
            raise ValueError('unknown load timing')
        if not isinstance(self.detail, str) or not self.detail or len(self.detail) > 512:
            raise ValueError('load detail must be bounded text')

    def as_dict(self) -> dict:
        return {'resource_id': self.resource_id, 'timing': self.timing, 'detail': self.detail}

    @classmethod
    def from_dict(cls, value: dict) -> 'LoadRequirement':
        value = _exact(value, {'resource_id', 'timing', 'detail'}, 'load requirement')
        try:
            return cls(**value)
        except TypeError:
            raise ValueError('invalid load requirement') from None


def binding_digest(binding: HostBinding, root_identity: str) -> str:
    _identity_value(root_identity, 'root identity')
    return digest({'binding': binding.as_dict(), 'root_identity': root_identity})


def snapshot_digest(*, schema: str, binding_sha256: str,
                    targets: tuple[TargetSelection, ...], files: tuple[FileSnapshot, ...],
                    directories: tuple[DirectorySnapshot, ...],
                    surface_map: tuple[SurfaceMapping, ...]) -> str:
    return digest({
        'schema': schema,
        'binding_sha256': binding_sha256,
        'targets': [value.as_dict() for value in targets],
        'files': [{key: value for key, value in row.as_dict().items() if key != 'text'}
                  for row in files],
        'directories': [value.as_dict() for value in directories],
        'surface_map': [value.as_dict() for value in surface_map],
    })


@dataclass(frozen=True)
class SelectedSnapshot:
    schema: str
    binding: HostBinding
    root_identity: str
    targets: tuple[TargetSelection, ...]
    files: tuple[FileSnapshot, ...]
    directories: tuple[DirectorySnapshot, ...]
    surface_map: tuple[SurfaceMapping, ...]
    binding_sha256: str
    snapshot_sha256: str

    def __post_init__(self):
        if self.schema != SELECTED_SCHEMA or not isinstance(self.binding, HostBinding):
            raise ValueError('invalid selected snapshot schema or binding')
        _identity_value(self.root_identity, 'root identity')
        for name in ('targets', 'files', 'directories', 'surface_map'):
            if not isinstance(getattr(self, name), tuple):
                raise ValueError(f'selected snapshot {name} must be a tuple')
        if not 1 <= len(self.targets) <= 2:
            raise ValueError('selected snapshot needs one or two targets')
        expected_types = ((self.targets, TargetSelection), (self.files, FileSnapshot),
                          (self.directories, DirectorySnapshot),
                          (self.surface_map, SurfaceMapping))
        if any(any(not isinstance(value, expected) for value in values)
               for values, expected in expected_types):
            raise ValueError('selected snapshot contains invalid row types')
        targets = tuple(sorted(self.targets, key=lambda row: (row.kind, row.resource_id,
                                                               row.entry.root_id, row.entry.path)))
        files = tuple(sorted(self.files, key=lambda row: (row.surface.root_id, row.surface.path)))
        directories = tuple(sorted(self.directories,
                                   key=lambda row: (row.surface.root_id, row.surface.path)))
        mappings = tuple(sorted(self.surface_map, key=lambda row: row.virtual_path))
        object.__setattr__(self, 'targets', targets)
        object.__setattr__(self, 'files', files)
        object.__setattr__(self, 'directories', directories)
        object.__setattr__(self, 'surface_map', mappings)
        self._validate_relationships()
        _digest_value(self.binding_sha256, 'binding digest')
        _digest_value(self.snapshot_sha256, 'snapshot digest')
        if binding_digest(self.binding, self.root_identity) != self.binding_sha256:
            raise ValueError('selected snapshot binding digest mismatch')
        expected = snapshot_digest(schema=self.schema, binding_sha256=self.binding_sha256,
                                   targets=targets, files=files, directories=directories,
                                   surface_map=mappings)
        if expected != self.snapshot_sha256:
            raise ValueError('selected snapshot digest mismatch')
        _payload_size(self.as_dict())

    def _validate_relationships(self) -> None:
        resources = [row.resource_id for row in self.targets]
        if len(resources) != len(set(resources)):
            raise ValueError('selected target resource ids must be unique')
        if any(sum(row.kind == kind for row in self.targets) > 1
               for kind in ('instruction', 'skill')):
            raise ValueError('only one target of each kind is supported')
        if any(row.entry.root_id != self.binding.root_id for row in self.targets):
            raise ValueError('selected target is outside the bound root')
        if (not self.binding.root_path.is_absolute()
                or self.binding.root_path == Path(self.binding.root_path.anchor)):
            raise ValueError('selected snapshot binding root must be a bounded absolute path')
        if len(self.surface_map) != len(self.targets):
            raise ValueError('each selected target needs one surface mapping')
        by_resource = {row.resource_id: row for row in self.targets}
        mapped = [row.resource_id for row in self.surface_map]
        if len(mapped) != len(set(mapped)) or set(mapped) != set(by_resource):
            raise ValueError('surface mappings must match selected resources')
        file_surfaces = {(row.surface.root_id, row.surface.path) for row in self.files}
        mapped_surfaces = {(row.surface.root_id, row.surface.path) for row in self.surface_map}
        if len(file_surfaces) != len(self.files) or file_surfaces != mapped_surfaces:
            raise ValueError('file snapshots must match mapped native surfaces')
        if len({row.file_identity for row in self.files}) != len(self.files):
            raise ValueError('file snapshots must have distinct native identities')
        for row in self.surface_map:
            target = by_resource[row.resource_id]
            expected = ('instruction.md' if target.kind == 'instruction' else
                        'skills/' + hashlib.sha256(row.resource_id.encode('utf-8')).hexdigest() + '.md')
            native = target.entry if target.kind == 'instruction' else SurfaceRef(
                target.entry.root_id, target.entry.path + '/SKILL.md')
            if row.virtual_path != expected or row.surface != native:
                raise ValueError('surface mapping does not match the selected target')
        expected_directories = {(row.entry.root_id, row.entry.path)
                                for row in self.targets if row.kind == 'skill'}
        actual_directories = {(row.surface.root_id, row.surface.path)
                              for row in self.directories}
        if len(actual_directories) != len(self.directories) or actual_directories != expected_directories:
            raise ValueError('directory snapshots must match selected skills')
        if any(row.entries != ('SKILL.md',) for row in self.directories):
            raise ValueError('selected Skill directory snapshot must contain only SKILL.md')

    def as_dict(self) -> dict:
        return {
            'schema': self.schema,
            'binding': self.binding.as_dict(),
            'root_identity': self.root_identity,
            'targets': [value.as_dict() for value in self.targets],
            'files': [value.as_dict() for value in self.files],
            'directories': [value.as_dict() for value in self.directories],
            'surface_map': [value.as_dict() for value in self.surface_map],
            'binding_sha256': self.binding_sha256,
            'snapshot_sha256': self.snapshot_sha256,
        }

    def to_json(self) -> str:
        return encode(self.as_dict())

    @classmethod
    def from_json(cls, raw: bytes | str) -> 'SelectedSnapshot':
        return cls.from_dict(decode_dto(raw))

    @classmethod
    def from_dict(cls, value: dict) -> 'SelectedSnapshot':
        _payload_size(value)
        value = _exact(value, {'schema', 'binding', 'root_identity', 'targets', 'files',
                               'directories', 'surface_map', 'binding_sha256',
                               'snapshot_sha256'}, 'selected snapshot')
        try:
            return cls(
                schema=value['schema'], binding=HostBinding.from_dict(value['binding']),
                root_identity=value['root_identity'],
                targets=tuple(TargetSelection.from_dict(row) for row in
                              _tuple(value['targets'], 'targets')),
                files=tuple(FileSnapshot.from_dict(row) for row in
                            _tuple(value['files'], 'files')),
                directories=tuple(DirectorySnapshot.from_dict(row) for row in
                                  _tuple(value['directories'], 'directories')),
                surface_map=tuple(SurfaceMapping.from_dict(row) for row in
                                  _tuple(value['surface_map'], 'surface map')),
                binding_sha256=value['binding_sha256'],
                snapshot_sha256=value['snapshot_sha256'],
            )
        except TypeError:
            raise ValueError('invalid selected snapshot') from None


def prepared_digest(*, schema: str, project_id: str, profile_id: str,
                    binding_sha256: str, snapshot_sha256: str, renderer_id: str,
                    renderer_version: int, changes: tuple[FileChange, ...], diff: str,
                    load_requirements: tuple[LoadRequirement, ...]) -> str:
    return digest({
        'schema': schema, 'project_id': project_id, 'profile_id': profile_id,
        'binding_sha256': binding_sha256, 'snapshot_sha256': snapshot_sha256,
        'renderer_id': renderer_id, 'renderer_version': renderer_version,
        'changes': [value.as_dict() for value in changes], 'diff': diff,
        'load_requirements': [value.as_dict() for value in load_requirements],
    })


@dataclass(frozen=True)
class PreparedExtension:
    schema: str
    project_id: str
    profile_id: str
    binding_sha256: str
    snapshot_sha256: str
    renderer_id: str
    renderer_version: int
    changes: tuple[FileChange, ...]
    diff: str
    load_requirements: tuple[LoadRequirement, ...]
    prepared_sha256: str

    def __post_init__(self):
        if self.schema != PREPARED_SCHEMA:
            raise ValueError('invalid prepared extension schema')
        identifier(self.project_id)
        identifier(self.profile_id)
        _digest_value(self.binding_sha256, 'binding digest')
        _digest_value(self.snapshot_sha256, 'snapshot digest')
        if self.renderer_id != 'codex' or self.renderer_version != 1:
            raise ValueError('unsupported prepared extension renderer')
        if not isinstance(self.changes, tuple) or any(not isinstance(row, FileChange)
                                                      for row in self.changes):
            raise ValueError('prepared changes must be FileChange objects')
        if not isinstance(self.diff, str):
            raise ValueError('prepared diff must be text')
        if not isinstance(self.load_requirements, tuple) or any(
                not isinstance(row, LoadRequirement) for row in self.load_requirements):
            raise ValueError('prepared load requirements are invalid')
        changes = tuple(sorted(self.changes,
                               key=lambda row: (row.surface.root_id, row.surface.path)))
        requirements = tuple(sorted(self.load_requirements, key=lambda row: row.resource_id))
        if len({(row.surface.root_id, row.surface.path) for row in changes}) != len(changes):
            raise ValueError('prepared changes must have unique surfaces')
        if len({row.resource_id for row in requirements}) != len(requirements):
            raise ValueError('load requirements must have unique resources')
        changed_resources = {resource_id for row in changes for resource_id in row.resource_ids}
        if changed_resources != {row.resource_id for row in requirements}:
            raise ValueError('load requirements must match changed resources')
        if any(row.before_sha256 is None or row.after_text is None
               or row.before_mode is None or row.after_mode != row.before_mode
               or len(row.after_text.encode('utf-8')) > 256 * 1024
               for row in changes):
            raise ValueError('prepared changes may only revise bounded existing files')
        if sum(len(row.after_text.encode('utf-8')) for row in changes) > 1024 * 1024:
            raise ValueError('prepared change text exceeds 1 MiB')
        object.__setattr__(self, 'changes', changes)
        object.__setattr__(self, 'load_requirements', requirements)
        _digest_value(self.prepared_sha256, 'prepared digest')
        expected = prepared_digest(
            schema=self.schema, project_id=self.project_id, profile_id=self.profile_id,
            binding_sha256=self.binding_sha256, snapshot_sha256=self.snapshot_sha256,
            renderer_id=self.renderer_id, renderer_version=self.renderer_version,
            changes=changes, diff=self.diff, load_requirements=requirements,
        )
        if expected != self.prepared_sha256:
            raise ValueError('prepared extension digest mismatch')
        _payload_size(self.as_dict())

    def as_dict(self) -> dict:
        return {
            'schema': self.schema, 'project_id': self.project_id,
            'profile_id': self.profile_id, 'binding_sha256': self.binding_sha256,
            'snapshot_sha256': self.snapshot_sha256, 'renderer_id': self.renderer_id,
            'renderer_version': self.renderer_version,
            'changes': [value.as_dict() for value in self.changes], 'diff': self.diff,
            'load_requirements': [value.as_dict() for value in self.load_requirements],
            'prepared_sha256': self.prepared_sha256,
        }

    def to_json(self) -> str:
        return encode(self.as_dict())

    @classmethod
    def from_json(cls, raw: bytes | str) -> 'PreparedExtension':
        return cls.from_dict(decode_dto(raw))

    @classmethod
    def from_dict(cls, value: dict) -> 'PreparedExtension':
        _payload_size(value)
        value = _exact(value, {'schema', 'project_id', 'profile_id', 'binding_sha256',
                               'snapshot_sha256', 'renderer_id', 'renderer_version',
                               'changes', 'diff', 'load_requirements', 'prepared_sha256'},
                       'prepared extension')
        try:
            return cls(
                schema=value['schema'], project_id=value['project_id'],
                profile_id=value['profile_id'], binding_sha256=value['binding_sha256'],
                snapshot_sha256=value['snapshot_sha256'], renderer_id=value['renderer_id'],
                renderer_version=value['renderer_version'],
                changes=tuple(FileChange.from_dict(row) for row in
                              _tuple(value['changes'], 'changes')),
                diff=value['diff'],
                load_requirements=tuple(LoadRequirement.from_dict(row) for row in
                                        _tuple(value['load_requirements'],
                                               'load requirements')),
                prepared_sha256=value['prepared_sha256'],
            )
        except TypeError:
            raise ValueError('invalid prepared extension') from None
