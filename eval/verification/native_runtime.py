"""Sealed native userland pins for explicitly supported host scorer routes.

Hash while copying into sealed memfds; execute only those snapshots in an empty
bubblewrap root. Never hash then execute a mutable source path. Missing bytes,
manifest drift, missing namespace support, and startup failure are unmeasured.
The host kernel/CPU and namespace launcher remain platform compatibility/TCB.
The captured kernel release is build metadata; admission checks Linux capabilities
and records the actual execution host instead of requiring an identical release.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

from eval.containers.immutable_runtime import ImmutableRuntimeUnavailable, targets

ROOT = Path(__file__).resolve().parents[2]
CHECK_PATHS = {name: 'check.py' for name in
               ('bird_minidev', 'jericho', 'alfworld', 'scienceworld')}
CHECK_PATHS['chessbench_puzzles'] = 'qa/lib/verify/check.py'
SUPPORTED_DATASETS = frozenset(CHECK_PATHS)


class NativeRuntimeUnavailable(RuntimeError):
    """The admitted native runtime cannot be established. Never fall back."""


def validate_route(spec: dict, *, sandbox: bool, env: dict | None = None,
                   service_specs=None, running=None) -> dict:
    """Validate the native scorer declaration before execution or cache reuse."""
    from eval.verification.isolation import declared_class

    if not sandbox:
        raise NativeRuntimeUnavailable('native runtime requires sandbox=True')
    binding = spec.get('native_runtime')
    if not isinstance(binding, dict) or binding.get('dataset') not in SUPPORTED_DATASETS:
        raise NativeRuntimeUnavailable('native runtime dataset is outside supported host scope')
    if ((env or {}).get('kind', 'files') != 'files'
            or (env or {}).get('services') or service_specs or running
            or spec.get('kind') != 'command'
            or declared_class(spec) not in ('unknown', 'actor-workdir')
            or spec.get('cwd') not in (None, '.')
            or spec.get('reward_file') != '/verifier/reward.txt'
            or spec.get('evidence_file') != '/verifier/evidence.json'
            or spec.get('argv', [])[:2] != ['python3', CHECK_PATHS[binding['dataset']]]):
        raise NativeRuntimeUnavailable('native runtime binding requires the supported host check route')
    return binding


# Linux UAPI constants; some Conda builds omit their Python bindings. Values
# independently captured from system Python and linux/{memfd,fcntl}.h in hg_whostpin.
F_ADD_SEALS, F_GET_SEALS = 1033, 1034
F_SEAL_SEAL, F_SEAL_SHRINK, F_SEAL_GROW, F_SEAL_WRITE = 1, 2, 4, 8


def _snapshot(source: Path, expected: str, expected_size: int | None = None) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        create = libc.memfd_create
    except AttributeError as exc:
        raise NativeRuntimeUnavailable('native runtime requires Linux memfd_create support') from exc
    create.argtypes, create.restype = [ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
    fd = create(b'hg-native-runtime', 3)  # MFD_CLOEXEC | MFD_ALLOW_SEALING
    if fd < 0:
        raise NativeRuntimeUnavailable(
            f'native runtime memfd_create unavailable: {os.strerror(ctypes.get_errno())}')
    try:
        digest = hashlib.sha256()
        with source.open('rb') as stream, os.fdopen(os.dup(fd), 'wb') as output:
            if expected_size is not None and os.fstat(stream.fileno()).st_size != expected_size:
                raise NativeRuntimeUnavailable(f'native runtime payload size mismatch: {source}')
            copied = 0
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                copied += len(block)
                if expected_size is not None and copied > expected_size:
                    raise NativeRuntimeUnavailable(f'native runtime payload size mismatch: {source}')
                output.write(block)
                digest.update(block)
            if expected_size is not None and copied != expected_size:
                raise NativeRuntimeUnavailable(f'native runtime payload size mismatch: {source}')
        if digest.hexdigest() != expected:
            raise NativeRuntimeUnavailable(f'native runtime payload digest mismatch: {source}')
        os.fchmod(fd, 0o555)
        os.lseek(fd, 0, os.SEEK_SET)
        required = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL
        try:
            fcntl.fcntl(fd, F_ADD_SEALS, required)
            if fcntl.fcntl(fd, F_GET_SEALS) & required != required:
                raise NativeRuntimeUnavailable('native runtime file sealing is incomplete')
        except OSError as exc:
            raise NativeRuntimeUnavailable(f'native runtime file sealing unavailable: {exc}') from exc
        return fd
    except BaseException:
        os.close(fd)
        raise


class FrozenRuntime:
    def __init__(self, manifest: dict, digest: str, fds: dict[str, int], launcher: int):
        self.manifest, self.fds, self.launcher = manifest, fds, launcher
        self.receipt = {'dataset': manifest['dataset'], 'sha256': digest, 'verified': True,
                        'mechanism': 'sealed-memfd-private-root-v1',
                        'identity': manifest['identity'], 'files': len(fds)}

    def run(self, argv: list[str], *, workdir: Path, reward_dir: Path,
            timeout_s: int, immutable_runtimes=()) -> subprocess.CompletedProcess:
        """Launch inside an empty root; no ambient /usr, /etc, PATH or Python site."""
        plan = [f'/proc/self/fd/{self.launcher}', '--unshare-all', '--die-with-parent',
                '--new-session', '--cap-drop', 'ALL', '--proc', '/proc', '--dev', '/dev',
                '--tmpfs', '/tmp', '--bind' if immutable_runtimes else '--ro-bind', str(workdir), '/task',
                '--bind', str(reward_dir), '/task/verifier', '--chdir', '/task', '--clearenv']
        for name, value in self.manifest['environment'].items():
            plan += ['--setenv', name, value]
        for target, fd in self.fds.items():
            os.lseek(fd, 0, os.SEEK_SET)
            # Only ELF payloads need execute permission. Data/code files use
            # bwrap's default 0600, still on read-only mounts. Omitting the two
            # mode args for those files keeps larger closures under its arg cap.
            if os.pread(fd, 4, 0) == b'\x7fELF':
                plan += ['--perms', '0555']
            plan += ['--ro-bind-data', str(fd), target]
        # Exact shared directory sources, never copied into the sealed closure or
        # task state. As in the host bwrap plan, child read-only binds follow the
        # writable parent; freeze the parent non-recursively so they survive.
        for source, target in targets(workdir, immutable_runtimes,
                                      writable_paths=(reward_dir,)):
            plan += ['--ro-bind', str(source), '/task/' + str(target.relative_to(workdir.resolve()))]
        if immutable_runtimes:
            plan += ['--remount-ro', '/task']
        # Even the interpreter's first loader/cache resolution is explicit. Child
        # sys.executable launches use PT_INTERP in the same closed namespace.
        interpreter = self.manifest['interpreter']
        command = [self.manifest['loader'], '--inhibit-cache', '--library-path',
                   ':'.join(self.manifest['library_path']), interpreter,
                   *self.manifest['python_flags'], *argv[1:]]
        check_path = CHECK_PATHS[self.manifest['dataset']]
        if argv[1] == check_path:
            command[command.index(check_path)] = '/scorer/check.py'
        if self.manifest.get('python_path'):
            # -I -S excludes ambient sites. Add only digest-bound package roots.
            # The child control uses the same explicit bootstrap when it needs
            # packages; ordinary sys.executable retains isolated stdlib defaults.
            bootstrap = ('import runpy,sys; sys.path[:0]='
                         + repr(self.manifest['python_path'])
                         + '; args=sys.argv[1:]; sys.argv=args; '
                         + 'exec(args[1]) if args[0]=="-c" else '
                         + 'runpy.run_path(args[0],run_name="__main__")')
            offset = command.index(interpreter) + 1 + len(self.manifest['python_flags'])
            command[offset:] = ['-c', bootstrap, *command[offset:]]
        plan += ['--', *command]
        try:
            proc = subprocess.run(plan, pass_fds=(*self.fds.values(), self.launcher),
                                  capture_output=True, text=True, errors="replace", timeout=timeout_s,
                                  env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        except OSError as exc:
            if immutable_runtimes:
                raise ImmutableRuntimeUnavailable(str(exc)) from exc
            raise
        if immutable_runtimes and proc.returncode and 'bwrap:' in proc.stderr:
            raise ImmutableRuntimeUnavailable(f'immutable runtime sandbox failed: {proc.stderr.strip()}')
        return proc


_HOST_NAMESPACES = ('user', 'mnt', 'pid', 'net', 'ipc', 'uts')
_HOST_PROBE = r'''
import errno, json, os, subprocess, sys
from pathlib import Path

expected = json.loads(Path('/task/probe.json').read_text())
if not (sys.flags.isolated and sys.flags.no_site):
    raise RuntimeError('isolated Python startup unavailable')
for name, inode in expected['namespaces'].items():
    if os.stat('/proc/self/ns/' + name).st_ino == inode:
        raise RuntimeError('host namespace retained: ' + name)
if os.path.lexists(expected['private']):
    raise RuntimeError('host storage visible in native root')
for path in ('/scorer/check.py', '/task/probe.json'):
    try:
        fd = os.open(path, os.O_WRONLY)
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise RuntimeError('read-only mount check failed: ' + path) from exc
    else:
        os.close(fd)
        raise RuntimeError('writable native mount: ' + path)
child = subprocess.run(
    [sys.executable, '-I', '-S', '-B', '-c',
     "import sys; sys.exit(0 if sys.flags.isolated and sys.flags.no_site else 1)"],
    capture_output=True, text=True, timeout=3)
if child.returncode:
    raise RuntimeError('sealed child Python failed: ' + child.stderr)
Path('/task/verifier/probe-output').write_text(expected['nonce'])
print(expected['nonce'])
'''


def _check_host(runtime: FrozenRuntime) -> dict:
    """Exercise the pinned launcher and userland without running a scorer/task."""
    host = {'system': platform.system(), 'machine': platform.machine(),
            'kernel': platform.release(), 'admission_policy': 'linux-capabilities-v1'}
    try:
        namespaces = {name: os.stat('/proc/self/ns/' + name).st_ino
                      for name in _HOST_NAMESPACES}
        with tempfile.TemporaryDirectory(prefix='hg-native-host-') as directory:
            root = Path(directory)
            work = root / 'task'
            work.mkdir()
            reward = work / 'verifier'
            reward.mkdir()
            private = root / 'host-private'
            private.write_text('native host isolation probe')
            nonce = os.urandom(16).hex()
            (work / 'probe.json').write_text(json.dumps(
                {'namespaces': namespaces, 'private': str(private), 'nonce': nonce}))
            proc = runtime.run(['python3', '-c', _HOST_PROBE], workdir=work,
                               reward_dir=reward, timeout_s=10)
            if proc.returncode or proc.stdout.strip() != nonce:
                detail = (proc.stderr or proc.stdout or 'probe confirmation missing').strip()[-2000:]
                raise NativeRuntimeUnavailable(f'native runtime host capability check failed: {detail}')
            if (reward / 'probe-output').read_text() != nonce:
                raise NativeRuntimeUnavailable('native runtime host capability output mismatch')
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeRuntimeUnavailable(f'native runtime host capability check failed: {exc}') from exc
    host['capabilities'] = {'sealed_memfd': True, 'private_namespaces': list(_HOST_NAMESPACES),
                            'read_only_runtime': True, 'read_only_task': True,
                            'isolated_python': True, 'child_python': True}
    return host


@contextmanager
def admit(binding: dict):
    """Authenticate the selected manifest and retain exact sealed bytes until exit."""
    fds = {}
    launcher = None
    try:
        if binding['dataset'] not in SUPPORTED_DATASETS:
            raise NativeRuntimeUnavailable('native runtime dataset is outside supported host scope')
        path = Path(binding['manifest'])
        raw = (path if path.is_absolute() else ROOT / path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != binding['sha256']:
            raise NativeRuntimeUnavailable('native runtime manifest digest mismatch')
        manifest = json.loads(raw)
        if manifest['schema'] not in (1, 2) or manifest['dataset'] != binding['dataset']:
            raise NativeRuntimeUnavailable('native runtime manifest schema/dataset mismatch')
        if platform.system() != 'Linux':
            raise NativeRuntimeUnavailable('native runtime requires Linux')
        if manifest['identity']['machine'] != platform.machine():
            raise NativeRuntimeUnavailable('native runtime machine mismatch')
        package = None
        if manifest['schema'] == 2:
            from harnessgrad.native_runtime_store import entries, selected_package, source_path
            entries(manifest)
            package = selected_package(manifest, digest, manifest_size=len(raw))
        for target, entry in manifest['files'].items():
            p = Path(target)
            if (not p.is_absolute() or '..' in p.parts
                    or p.parts[1] in {'task', 'tmp', 'proc', 'dev', 'etc'}):
                raise NativeRuntimeUnavailable(f'unsafe native runtime destination: {target}')
            if package is not None:
                source = source_path(package, entry['source'])
            else:
                source = Path(entry['source'])
                source = source if source.is_absolute() else ROOT / source
            fds[target] = (_snapshot(source, entry['sha256'], entry['size']) if package is not None
                           else _snapshot(source, entry['sha256']))
        for required in (manifest['interpreter'], manifest['loader'], '/scorer/check.py'):
            if required not in fds:
                raise NativeRuntimeUnavailable(f'native runtime missing entry: {required}')
        launcher_source = (source_path(package, manifest['launcher']['source']) if package is not None
                           else Path(manifest['launcher']['path']))
        launcher = (_snapshot(launcher_source, manifest['launcher']['sha256'], manifest['launcher']['size'])
                    if package is not None else _snapshot(launcher_source, manifest['launcher']['sha256']))
        runtime = FrozenRuntime(manifest, digest, fds, launcher)
        runtime.receipt['host'] = _check_host(runtime)
        yield runtime
    except ImmutableRuntimeUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise NativeRuntimeUnavailable(f'native runtime refused: {exc}') from exc
    finally:
        for fd in fds.values():
            os.close(fd)
        if launcher is not None:
            os.close(launcher)
