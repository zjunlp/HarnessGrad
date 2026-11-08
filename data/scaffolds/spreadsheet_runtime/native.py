"""Inventory an existing native deployment; never installs or kills host services."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


_NATIVE_PYTHON_NAMES = frozenset(('uno.py', 'unohelper.py', 'pyuno.so', 'pyuno.pyd'))


def native_python_component(name):
    path = Path(name)
    return (path.is_absolute() and str(path) == name and '..' not in path.parts
            and path.name in _NATIVE_PYTHON_NAMES
            and path.parent.name in ('dist-packages', 'site-packages'))


def validated_snapshots(record):
    """Only pinned native UNO entry files may cross a global-site mask."""
    snapshots = record.get('source_snapshots', {})
    if not isinstance(snapshots, dict):
        raise ValueError('invalid native component snapshot inventory')
    decoded = {}
    for name, content in snapshots.items():
        if (not native_python_component(name) or name not in record['files']
                or not isinstance(content, str) or len(content) > 16 * 1024 * 1024):
            raise ValueError('native component snapshot is not an authorized UNO file')
        source = base64.b64decode(content, validate=True)
        if hashlib.sha256(source).hexdigest() != record['files'][name]:
            raise ValueError('native component snapshot identity changed')
        decoded[name] = source
    return decoded


def snapshot_native_python(record):
    """Capture declared UNO files in the existing private manifest, not global site."""
    selected = {key: value for key, value in record.items() if key != 'soffice'}
    snapshots = dict(record.get('source_snapshots', {}))
    validated_snapshots(record)
    for name, expected in record['files'].items():
        if not native_python_component(name) or name in snapshots:
            continue
        with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
            identity = os.fstat(source.fileno())
            if not stat.S_ISREG(identity.st_mode) or identity.st_nlink != 1:
                raise ValueError('native UNO snapshot source must be an owned regular file')
            content = source.read(12 * 1024 * 1024 + 1)
        if len(content) > 12 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('native UNO snapshot source differs from selected deployment')
        snapshots[name] = base64.b64encode(content).decode('ascii')
    if snapshots:
        selected['source_snapshots'] = snapshots
    return selected


def minimal_env():
    # No actor/model/API environment reaches native code or its UNO interpreter.
    return {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'SAL_USE_VCLPLUGIN': 'svp'}


def reject_active_content(path):
    import zipfile
    with zipfile.ZipFile(path) as archive:
        names = [name.lower() for name in archive.namelist()]
        if any('vbaproject' in name or name.startswith(('xl/externallinks/', 'xl/macrosheets/',
                                                     'xl/intlmacrosheets/')) for name in names):
            raise RuntimeError('native runtime does not evaluate macros or external workbook links')


def sandbox_command(record, scratch, mounts, argv):
    """One new network/PID namespace; no actor cwd, home, sockets or credentials."""
    bwrap = shutil.which('bwrap', path='/usr/bin:/bin')
    if not bwrap:
        raise RuntimeError('native spreadsheet runtime requires bubblewrap')
    command = [bwrap, '--unshare-all', '--die-with-parent', '--new-session',
               '--clearenv', '--proc', '/proc', '--dev', '/dev',
               '--tmpfs', '/tmp', '--dir', '/run', '--dir', '/home',
               '--dir', '/etc', '--chdir', '/tmp']
    # OS runtime resources only; never bind /, /home, caller cwd, /run or host /tmp.
    for name in ('/usr', '/lib', '/lib64', '/bin', '/etc/fonts', '/etc/ld.so.cache'):
        if Path(name).exists():
            command += ['--ro-bind', name, name]
    for name in record['files']:
        if not any(name == root or name.startswith(root + '/')
                   for root in ('/usr', '/lib', '/lib64', '/bin', '/etc/fonts')):
            command += ['--ro-bind', name, name]
    command += ['--bind', str(Path(scratch).resolve()), '/work']
    snapshots = validated_snapshots(record)
    if snapshots:
        component_root = Path(scratch).resolve() / 'native-components'
        component_root.mkdir(mode=0o700, exist_ok=True)
        sites = sorted({str(Path(name).parent) for name in snapshots})
        # Construct an empty site directory: only the explicitly pinned UNO files
        # return, even when the enclosing /usr mount carries a global-site mask.
        for site in sites:
            command += ['--tmpfs', site]
        for name, content in snapshots.items():
            target = component_root / hashlib.sha256(name.encode()).hexdigest()
            # Exclusive creation rejects links/preexisting files in an unexpected scratch.
            with target.open('xb') as stream:
                stream.write(content)
            command += ['--ro-bind', str(target), name]
        for site in sites:
            command += ['--remount-ro', site]
        command += ['--ro-bind', str(component_root), '/work/native-components']
    for source, destination, writable in mounts:
        command += ['--bind' if writable else '--ro-bind', str(source), str(destination)]
    env = runtime_env(record, Path(scratch))
    # Host-side dirs are exposed solely under /work, not their original paths.
    for key in ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'TMPDIR'):
        env[key] = '/work/' + Path(env[key]).name
    for key, value in env.items():
        command += ['--setenv', key, value]
    return command + ['--', *argv]


def isolated_uno_recalculate(record, source, files):
    import importlib.util
    with tempfile.TemporaryDirectory(prefix='hg-lo-uno-sandbox-') as tmp:
        root = Path(tmp)
        copied = []
        for index, file in enumerate(files):
            reject_active_content(file)
            target = root / f'{index}.xlsx'
            shutil.copyfile(file, target)
            copied.append(target)
        helper = root / 'native.py'
        shutil.copyfile(__file__, helper)
        upstream = root / 'open_spreadsheet.py'
        shutil.copyfile(source, upstream)
        tqdm_spec = importlib.util.find_spec('tqdm')
        if tqdm_spec is None or not tqdm_spec.origin:
            raise RuntimeError('native UNO adapter requires its real tqdm package')
        shutil.copytree(Path(tqdm_spec.origin).parent, root / 'tqdm',
                        ignore=shutil.ignore_patterns('__pycache__'))
        config = {key: record[key] for key in ('python', 'program_dir', 'soffice_binary')}
        (root / 'worker.json').write_text(json.dumps(config))
        command = sandbox_command(record, root, [],
            [record['python'], '-I', '-B', '/work/native.py', 'uno-worker'])
        result = subprocess.run(command, env=minimal_env(), capture_output=True,
                                text=True, timeout=180)
        if result.returncode:
            raise RuntimeError('isolated native UNO conversion failed')
        for target, file in zip(copied, files):
            if not (root / (target.name + '.saved')).exists():
                raise RuntimeError('isolated native UNO did not save every workbook')
            shutil.copyfile(target, file)


def uno_worker():
    import types
    sys.path.insert(0, '/work')  # Only the explicitly staged, trusted tqdm package.
    root = Path('/work')
    record = json.loads((root / 'worker.json').read_text())
    source = (root / 'open_spreadsheet.py').read_text().replace('host=localhost', 'host=127.0.0.1')
    source = source.replace('LIBREOFFICE_PROGRAM_DIR = _find_libreoffice_program_dir()',
                            'LIBREOFFICE_PROGRAM_DIR = ' + repr(record['program_dir']))
    source = source.replace('desktop.loadComponentFromURL(file_url, "_blank", 0, ())',
        'desktop.loadComponentFromURL(file_url, "_blank", 0, (PropertyValue(Name="MacroExecutionMode", Value=0), PropertyValue(Name="UpdateDocMode", Value=0)))')
    module = types.ModuleType('native_spreadsheet_helper')
    exec(compile(source, '/work/open_spreadsheet.py', 'exec'), module.__dict__)
    module._lo_python = lambda: record['python']
    module._env_for_lo_python = lambda: runtime_env(record, root)
    module._soffice_bin = lambda: record['soffice_binary']
    original = subprocess.Popen
    def start(argv, **kwargs):
        if argv[0] == record['soffice_binary']:
            argv = [argv[0], '-env:UserInstallation=file:///work/profile', *argv[1:]]
        return original(argv, **kwargs)
    module.subprocess = types.SimpleNamespace(Popen=start, run=subprocess.run,
        PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)
    files = sorted(root.glob('*.xlsx'))
    for file in files:
        os.utime(file, ns=(1000000000, 1000000000))
    if not module.start_libreoffice_service():
        raise RuntimeError('isolated native UNO service failed')
    try:
        module.batch_open_files([str(file) for file in files])
        for file in files:
            if file.stat().st_mtime_ns == 1000000000:
                raise RuntimeError('isolated native UNO did not save workbook')
            (root / (file.name + '.saved')).touch()
    finally:
        module.stop_libreoffice_service()


def inventory(soffice, python):
    binary = Path(soffice).resolve(strict=True)
    interpreter = Path(python).resolve(strict=True)
    program = binary.parent
    if not (program / 'soffice.bin').is_file():
        raise RuntimeError('soffice must resolve to a LibreOffice program directory')
    env = minimal_env()
    env.update(UNO_PATH=str(program), URE_BOOTSTRAP=f'vnd.sun.star.pathname:{program}/fundamentalrc')
    probe = subprocess.run([str(interpreter), '-I', '-c',
        'import uno, pyuno, json; print(json.dumps([uno.__file__, pyuno.__file__]))'],
        env=env, check=True, capture_output=True, text=True, timeout=30)
    uno_files = [Path(p).resolve(strict=True) for p in json.loads(probe.stdout)]
    files = {binary, interpreter, *uno_files}
    # Include LO resources, filters, registry, shared native libraries and bundled fonts.
    for root in (program.parent, Path('/etc/fonts')):
        if root.is_dir():
            visited_dirs = set()
            for directory, dirs, names in os.walk(root, followlinks=True):
                resolved = Path(directory).resolve()
                if resolved in visited_dirs:
                    dirs[:] = []
                    continue
                visited_dirs.add(resolved)
                files.update((Path(directory) / name).resolve() for name in names
                             if (Path(directory) / name).is_file())
    fonts = subprocess.run(['fc-list', '--format', '%{file}\\n'], check=True,
                           capture_output=True, text=True, timeout=30, env=minimal_env())
    files.update(Path(p).resolve(strict=True) for p in fonts.stdout.splitlines() if p)
    # Record the loader-resolved host dependencies of every deployed ELF object.
    queue = list(files)
    visited = set()
    while queue:
        path = queue.pop()
        if path in visited:
            continue
        visited.add(path)
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        result = subprocess.run(['ldd', str(path)], capture_output=True, text=True, timeout=30, env=minimal_env())
        if 'not found' in result.stdout:
            raise RuntimeError(f'unresolved native dependency: {path}')
        for name in re.findall(r'(?:=>\s+)?(/[^\s()]+)', result.stdout):
            dependency = Path(name).resolve(strict=True)
            if dependency not in files:
                files.add(dependency)
                queue.append(dependency)
    version = subprocess.run([str(binary), '--version'], env=env, check=True,
                             capture_output=True, text=True, timeout=30).stdout.strip()
    return {'schema': 1, 'deployment': 'verified-host-installation',
            'soffice_binary': str(binary), 'python': str(interpreter),
            'program_dir': str(program), 'version': version,
            'files': {str(p): digest(p) for p in sorted(files)},
            'limitations': 'Identity inventory, not a hermetic native closure; host loader, font discovery and OS remain dependencies.'}


def check(manifest):
    path = Path(manifest).resolve(strict=True)
    record = json.loads(path.read_text())
    if record.get('schema') != 1 or record.get('deployment') != 'verified-host-installation':
        raise RuntimeError('unsupported native runtime manifest')
    for key in ('soffice_binary', 'python', 'program_dir'):
        if not isinstance(record.get(key), str) or not Path(record[key]).is_absolute():
            raise RuntimeError('incomplete native runtime identity')
    if not record.get('files') or any(record[key] not in record['files']
                                      for key in ('soffice_binary', 'python')):
        raise RuntimeError('incomplete native runtime files')
    snapshots = validated_snapshots(record)
    for name, expected in record['files'].items():
        if name not in snapshots and digest(name) != expected:
            raise RuntimeError(f'native runtime identity changed: {name}')
    record['soffice'] = str(path.parent / 'soffice')
    return record


def require_runtime():
    manifest = os.environ.get('HG_SPREADSHEET_NATIVE_MANIFEST')
    if not manifest:
        raise RuntimeError('prepare native LibreOffice and set HG_SPREADSHEET_NATIVE_MANIFEST')
    return check(manifest)


def runtime_env(record, scratch):
    root = Path(scratch).resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = minimal_env()
    for key, name in [('HOME', 'home'), ('XDG_CACHE_HOME', 'cache'), ('XDG_CONFIG_HOME', 'config'), ('TMPDIR', 'tmp')]:
        directory = root / name
        directory.mkdir(mode=0o700, exist_ok=True)
        env[key] = str(directory)
    profile = root / 'profile' / 'user'
    profile.mkdir(parents=True, mode=0o700, exist_ok=True)
    (profile / 'registrymodifications.xcu').write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        '</item>'
        '<item oor:path="/org.openoffice.Office.Calc/Formula/Load">'
        '<prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop>'
        '</item></oor:items>')
    env['UNO_PATH'] = record['program_dir']
    env['URE_BOOTSTRAP'] = f"vnd.sun.star.pathname:{record['program_dir']}/fundamentalrc"
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    prepare = subs.add_parser('prepare')
    prepare.add_argument('--soffice', required=True)
    prepare.add_argument('--python', required=True)
    prepare.add_argument('--directory', required=True)
    verify = subs.add_parser('check')
    verify.add_argument('--manifest', required=True)
    subs.add_parser('uno-worker')
    launch = subs.add_parser('launch')
    launch.add_argument('--manifest', required=True)
    launch.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'uno-worker':
        uno_worker()
        return 0
    if args.command == 'prepare':
        directory = Path(args.directory).resolve()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        manifest = directory / 'manifest.json'
        if manifest.exists() or (directory / 'soffice').exists():
            raise RuntimeError('prepare requires a fresh deployment directory')
        record = inventory(args.soffice, args.python)
        # Copy this standalone helper to keep launcher independent of checkout/CWD.
        shutil.copyfile(__file__, directory / 'native.py')
        manifest.write_text(json.dumps(record, indent=2) + '\n')
        import shlex
        launcher = directory / 'soffice'
        launcher.write_text('#!/bin/sh\nexec ' + ' '.join(shlex.quote(v) for v in
            [str(Path(sys.executable).resolve()), '-I', '-B', str(directory / 'native.py'),
             'launch', '--manifest', str(manifest), '--']) + ' "$@"\n')
        launcher.chmod(0o700)
        print(str(manifest))
    elif args.command == 'check':
        record = check(args.manifest)
        print(json.dumps({key: record[key] for key in ('deployment', 'version', 'soffice', 'python', 'limitations')}))
    else:
        record = check(args.manifest)
        argv = args.args[1:] if args.args[:1] == ['--'] else args.args
        # Conversion inputs are single-file mounts; no input parent/workdir is exposed.
        if '--convert-to' not in argv or '--outdir' not in argv:
            raise RuntimeError('isolated launcher only supports explicit workbook conversion')
        input_file = Path(argv[-1]).resolve(strict=True)
        reject_active_content(input_file)
        output_index = argv.index('--outdir') + 1
        output_dir = Path(argv[output_index]).resolve(strict=True)
        if not output_dir.is_dir():
            raise RuntimeError('conversion output directory is missing')
        # Output must be fresh and dedicated, never an actor/private evaluator directory.
        if any(output_dir.iterdir()):
            raise RuntimeError('conversion requires an empty dedicated output directory')
        root = Path(tempfile.mkdtemp(prefix='hg-lo-convert-sandbox-'))
        argv = [arg for arg in argv if not arg.startswith('-env:UserInstallation=')]
        output_index = argv.index('--outdir') + 1
        argv[output_index] = '/output'
        argv[-1] = '/input/' + input_file.name
        argv.insert(0, '-env:UserInstallation=file:///work/profile')
        command = sandbox_command(record, root,
            [(input_file, '/input/' + input_file.name, False), (output_dir, '/output', True)],
            [record['soffice_binary'], *argv])
        # Exec preserves PID ownership for caller timeouts. Owner may remove this private
        # /tmp/hg-lo-convert-sandbox-* directory after the conversion process exits.
        os.execve(command[0], command, minimal_env())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
