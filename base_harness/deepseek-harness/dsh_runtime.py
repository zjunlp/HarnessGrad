"""Read-only prepared DSH installation identities, using only the standard library."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import struct
import tempfile

RECORD = '_dsh_runtime'
REMEDY = 'prepare this Harness with tools/configure_dsh_harness.py'
SCOPE = 'declared-source-node-farm-content; host OS libraries and shell tools are platform capabilities'


def _native_elf(path: Path) -> bool:
    """Recognize a Linux executable image without trusting its filename or running it."""
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        with path.open('rb') as stream:
            header = stream.read(64)
        if (len(header) < 52 or header[:4] != b'\x7fELF' or header[4] not in (1, 2)
                or header[5] not in (1, 2) or header[6] != 1):
            return False
        order = '<' if header[5] == 1 else '>'
        kind, machine, version = struct.unpack_from(order + 'HHI', header, 16)
        if kind not in (2, 3) or not machine or version != 1:
            return False
        if header[4] == 2:
            if len(header) < 64:
                return False
            offset = struct.unpack_from(order + 'Q', header, 32)[0]
            size, entry_size, count = struct.unpack_from(order + 'HHH', header, 52)
            expected_size, expected_entry = 64, 56
        else:
            offset = struct.unpack_from(order + 'I', header, 28)[0]
            size, entry_size, count = struct.unpack_from(order + 'HHH', header, 40)
            expected_size, expected_entry = 52, 32
        return (size == expected_size and entry_size == expected_entry and count > 0
                and offset >= size and offset + count * entry_size <= path.stat().st_size)
    except (OSError, ValueError, struct.error):
        return False


def require_clean_startup(env: dict) -> None:
    for name in ('NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH'):
        if env.get(name):
            raise ValueError(f'prepared runtime forbids ambient startup variable {name}; {REMEDY}')


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_binding(path: Path, *, forbidden: tuple[Path, ...] = ()) -> None:
    if not path.is_absolute() or '..' in path.parts or not path.exists():
        raise ValueError(f'DSH runtime needs an existing absolute path: {path}; {REMEDY}')
    for value in (path, path.resolve(strict=True)):
        if any(c in str(value) for c in (',', '\n', '\r')):
            raise ValueError(f'DSH runtime cannot be expressed as a bind: {path}')
        if value.is_relative_to('/tmp') or value.is_relative_to(Path('/tmp').resolve()):
            raise ValueError(f'DSH runtime is under hidden /tmp: {path}; {REMEDY}')
        for root in forbidden:
            root = root.resolve()
            if value.is_relative_to(root) or (path.is_dir() and root.is_relative_to(value)):
                raise ValueError(f'DSH runtime overlaps the platform or candidate: {path}')
        if path.is_dir():
            shared = (Path('/var/tmp'), Path(tempfile.gettempdir()), Path.home())
            system = tuple(map(Path, ('/usr', '/bin', '/sbin', '/lib', '/lib64',
                                      '/etc', '/dev', '/proc', '/sys', '/run')))
            if (len(value.parts) < 3 or value.parent in (Path('/home'), Path('/Users'))
                    or any(root.resolve().is_relative_to(value) for root in shared)
                    or any(value.is_relative_to(root) for root in system)):
                raise ValueError(f'DSH runtime needs a dedicated installation directory: {path}')


def _covered(path: Path, bindings: list[Path]) -> bool:
    return any(path == root or (root.is_dir() and path.is_relative_to(root)) for root in bindings)


def inventory(bindings: list[Path]) -> dict:
    """Hash every bound file, directory mode and link target; never follow an unbound link."""
    roots = sorted(set(bindings), key=lambda p: (len(p.parts), str(p)))
    roots = [root for i, root in enumerate(roots)
             if not any(parent.is_dir() and root.is_relative_to(parent) for parent in roots[:i])]
    rows = []
    counts = {'files': 0, 'directories': 0, 'symlinks': 0}

    def add(path: Path):
        mode = path.lstat().st_mode
        row = [str(path), stat.S_IMODE(mode)]
        if stat.S_ISLNK(mode):
            target = path.resolve(strict=True)
            if not _covered(target, bindings):
                raise ValueError(f'DSH symlink target needs an explicit --runtime binding: {path} -> {target}')
            if not target.is_file() and not target.is_dir():
                raise ValueError(f'DSH symlink target is not a regular file or directory: {path}')
            row += ['symlink', os.readlink(path), str(target), digest(target) if target.is_file() else None]
            counts['symlinks'] += 1
        elif stat.S_ISREG(mode):
            row += ['file', digest(path)]
            counts['files'] += 1
        elif stat.S_ISDIR(mode):
            row += ['directory']
            counts['directories'] += 1
        else:
            raise ValueError(f'DSH runtime contains a special file: {path}')
        rows.append(row)

    def failed_walk(error):
        raise error

    for root in roots:
        add(root)
        if root.is_dir() and not root.is_symlink():
            for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed_walk):
                dirs[:] = sorted(name for name in dirs if name != '.git')
                for name in sorted([*dirs, *files]):
                    add(Path(directory) / name)
    payload = json.dumps(sorted(rows), ensure_ascii=True, separators=(',', ':')).encode()
    return {'sha256': hashlib.sha256(payload).hexdigest(), **counts, 'excluded_directories': ['.git']}


def _farm(harness: Path, root: Path) -> list[list[str]]:
    path = harness / 'dsh_farm.tsv'
    if not path.resolve(strict=True).is_relative_to(harness.resolve(strict=True)):
        raise ValueError('DSH farm manifest must belong to the Harness')
    result, names = [], set()
    for line in path.read_text(encoding='utf-8').splitlines():
        fields = line.split('\t')
        if len(fields) != 2:
            raise ValueError('invalid DSH farm row')
        name, relative = fields
        parts = Path(name).parts
        target = root / relative
        if (name in names or not name or Path(name).as_posix() != name
                or Path(name).is_absolute() or '..' in parts
                or len(parts) != (2 if name.startswith('@') else 1)
                or not relative or Path(relative).as_posix() != relative or relative == '.'
                or Path(relative).is_absolute() or '..' in Path(relative).parts
                or not target.is_dir() or not target.resolve().is_relative_to(root)):
            raise ValueError('invalid DSH farm target')
        names.add(name)
        result.append([name, str(target.resolve())])
    return result


def installation(harness: Path, selected: dict, bindings: list[str],
                 farm: list[tuple[str, Path]], *, forbidden: tuple[Path, ...] = ()) -> dict:
    paths = [Path(value) for value in bindings]
    for path in paths:
        validate_binding(path, forbidden=forbidden)
    root, node, cli = Path(selected['root']), Path(selected['node']), Path(selected['cli'])
    if not node.is_file() or not os.access(node, os.X_OK):
        raise ValueError('DSH selected Node is not executable')
    if not all(_covered(path, paths) for path in (root, node, cli)):
        raise ValueError('DSH selected command is outside its runtime bindings')
    if _farm(harness, root) != [[name, str(path.resolve())] for name, path in farm]:
        raise ValueError('DSH prepared farm differs from selected declarations')
    return {'schema': 1, 'cacheable': _native_elf(node), 'scope': SCOPE,
            'root': str(root), 'node': str(node), 'cli': str(cli),
            'argv': [str(node), str(cli)], 'runtime_paths': bindings,
            'farm_sha256': digest(harness / 'dsh_farm.tsv'),
            'farm_targets': _farm(harness, root),
            'inventory': inventory(paths),
            'system': platform.system(), 'machine': platform.machine()}


def check(record: dict) -> None:
    bindings = [Path(value) for value in record['runtime_paths']]
    if inventory(bindings) != record['inventory']:
        raise ValueError(f'prepared DSH source, Node or dependency contents changed; {REMEDY}')


def resolve(harness: Path, env: dict, *, forbidden: tuple[Path, ...] = ()) -> dict:
    require_clean_startup(env)
    manifest_path = harness / 'harness.json'
    if not manifest_path.resolve(strict=True).is_relative_to(harness.resolve(strict=True)):
        raise ValueError('DSH manifest must belong to the Harness')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    record = manifest.get(RECORD)
    if not isinstance(record, dict) or record.get('schema') != 1:
        raise ValueError(f'DSH Harness is not prepared with an exact identity; {REMEDY}')
    paths = record.get('runtime_paths')
    if (not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths)
            or manifest.get('runtime_paths') != paths or not isinstance(record.get('inventory'), dict)
            or not isinstance(record.get('farm_targets'), list)
            or any(not isinstance(record.get(key), str) for key in ('root', 'node', 'cli', 'farm_sha256'))):
        raise ValueError(f'invalid prepared DSH identity; {REMEDY}')
    root, node, cli = (Path(record[key]) for key in ('root', 'node', 'cli'))
    bindings = [Path(value) for value in paths]
    for path in bindings:
        validate_binding(path, forbidden=forbidden)
    if (record.get('argv') != [str(node), str(cli)]
            or cli != root / 'apps/cli/lib/bin.js'
            or any(not _covered(p, bindings) for p in (root, node, cli))
            or any(not p.is_absolute() or p.resolve(strict=True) != p for p in (root, node, cli))
            or not node.is_file() or not os.access(node, os.X_OK)):
        raise ValueError(f'invalid prepared DSH command; {REMEDY}')
    if (('cacheable' in record and (not isinstance(record['cacheable'], bool)
                                  or record['cacheable'] != _native_elf(node)))
            or record.get('scope', SCOPE) != SCOPE):
        raise ValueError(f'invalid prepared DSH runtime scope; {REMEDY}')
    if record.get('system') != platform.system() or record.get('machine') != platform.machine():
        raise ValueError(f'prepared DSH platform changed; {REMEDY}')
    farm = _farm(harness, root)
    if digest(harness / 'dsh_farm.tsv') != record['farm_sha256'] or farm != record['farm_targets']:
        raise ValueError(f'prepared DSH farm changed; {REMEDY}')
    for key, expected in [('HG_AGENT_DSH_ROOT', root), ('HG_AGENT_DSH', cli), ('HG_AGENT_NODE', node)]:
        value = env.get(key)
        if value and key == 'HG_AGENT_NODE':
            value = shutil.which(str(Path(value).expanduser()), path=env.get('PATH', os.defpath)) or value
        if value and Path(value).expanduser().resolve() != expected:
            raise ValueError(f'{key} differs from the prepared DSH installation; {REMEDY}')
    if env.get('HG_AGENT_DSH_COMMAND', '').strip():
        command = shlex.split(env['HG_AGENT_DSH_COMMAND'])
        if command:
            command[0] = shutil.which(str(Path(command[0]).expanduser()), path=env.get('PATH', os.defpath)) or command[0]
        if len(command) != 2 or [str(Path(p).expanduser().resolve()) for p in command] != record['argv']:
            raise ValueError(f'DSH command override differs from the prepared installation; {REMEDY}')
    for entry in record['farm_targets']:
        if (not isinstance(entry, list) or len(entry) != 2 or not all(isinstance(v, str) for v in entry)
                or not Path(entry[1]).is_dir() or not Path(entry[1]).resolve().is_relative_to(root)):
            raise ValueError(f'invalid prepared DSH farm target; {REMEDY}')
    check(record)
    return record
