"""Standalone trusted helper in an immutable container mount; stdlib only."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import tarfile
import time

ROOT = '/run/harnessgrad-runtime'


def actors():
    result = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            status = (p / 'status').read_text()
            uid = next(l for l in status.splitlines() if l.startswith('Uid:')).split()[1:]
            if '65534' in uid and '\nState:\tZ' not in status: result.append(int(p.name))
        except (FileNotFoundError, ProcessLookupError): pass
    return result


def probe():
    mounts = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ')
        fields, fs = left.split(), right.split()
        path = fields[4]
        for before, after in [('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')]:
            path = path.replace(before, after)
        row = dict(path=path, device=fields[2], fstype=fs[0],
                   writable='rw' in fields[5].split(','), character_device=False)
        if row['writable'] and fs[0] == 'tmpfs':
            info = os.stat(path)
            row['character_device'] = stat.S_ISCHR(info.st_mode)
            vfs = os.statvfs(path)
            row.update(bytes=vfs.f_blocks * vfs.f_frsize, inodes=vfs.f_files)
        mounts.append(row)
    return {'mounts': mounts, 'interfaces': sorted(p.name for p in Path('/sys/class/net').iterdir())}


def init():
    spec = json.loads(Path(ROOT + '/control/plan.json').read_text())
    work = Path(spec['workdir'])
    if spec['seed']:
        with tarfile.open(ROOT + '/control/seed.tar') as archive:
            # Trusted imported image, but refuse extraction escaping the workspace.
            archive.extractall(work, filter='data')
    src = Path(spec['staging'])
    def skip_immutable(parent, names):
        relative = Path(parent).relative_to(src)
        return [name for name in names if str(relative / name) in spec['immutable']]
    if src.exists(): shutil.copytree(src, work, dirs_exist_ok=True, symlinks=True, ignore=skip_immutable)
    for path in spec['env_dirs']: Path(path).mkdir(parents=True, exist_ok=True)
    (work / '.harnessgrad-state').mkdir(exist_ok=True)
    # Leave immutable nested mounts alone. They are verifier/runtime inputs.
    device = work.stat().st_dev
    for parent, dirs, files in os.walk(work, followlinks=False):
        dirs[:] = [d for d in dirs if (Path(parent) / d).lstat().st_dev == device]
        for path in [Path(parent), *(Path(parent) / f for f in files)]:
            if path.lstat().st_dev == device: os.chown(path, 65534, 65534, follow_symlinks=False)


def collect(root, paths, maximum, *, truncate=False):
    if maximum is not None and (type(maximum) is not int or maximum <= 0):
        raise ValueError('artifact maximum must be positive or null')
    if actors(): raise RuntimeError('artifact export requires quiescent actors')
    result = {}
    for name in paths:
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts or not rel.parts: raise ValueError('unsafe export path')
        path = Path(root) / rel
        if any(p.is_symlink() for p in [path, *path.parents]): raise ValueError('symlink export refused')
        try: fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            result[name] = {'status': 'missing'}; continue
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError('nonregular export refused')
            data = f.read() if maximum is None else f.read(maximum + 1)
        truncated = maximum is not None and len(data) > maximum
        if truncated:
            if not truncate: raise ValueError('artifact byte limit exceeded')
            data = data[:maximum]
        if maximum is not None: maximum -= len(data)
        result[name] = dict(status='submitted', bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                            base64=base64.b64encode(data).decode())
        if truncate:
            result[name].update(source_bytes=info.st_size, truncated=truncated)
    return result


if __name__ == '__main__':
    op = sys.argv[1]
    if op == 'idle':
        while True: time.sleep(3600)
    elif op == 'probe': print(json.dumps(probe()))
    elif op == 'identity':
        status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
        print(json.dumps({'uid': os.getuid(), 'effective_caps': int(status['CapEff'].strip(), 16),
                          'no_new_privileges': int(status['NoNewPrivs'].strip())}))
    elif op == 'init': init()
    elif op == 'quiesce':
        for _ in range(100):
            pids = actors()
            if not pids: break
            for pid in pids:
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass
            time.sleep(.01)
        if actors(): raise RuntimeError('actor processes survived termination')
    elif op == 'clear':
        if actors(): raise RuntimeError('result reset requires quiescent actors')
        root = Path(sys.argv[2])
        for name in json.loads(sys.argv[3]):
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts or not rel.parts: raise ValueError('unsafe reset path')
            target = root / rel
            if any(p.is_symlink() for p in target.parents): raise ValueError('symlink result parent')
            target.unlink(missing_ok=True)
    elif op == 'collect': print(json.dumps(collect(sys.argv[2], json.loads(sys.argv[3]),
        json.loads(sys.argv[4]), truncate=len(sys.argv) > 5 and sys.argv[5] == 'truncate')))
    else: raise ValueError('unknown trusted operation')
