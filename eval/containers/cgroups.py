"""Read container cgroup limits from host procfs and mounted kernel controllers.

Docker declarations are never evidence of kernel enforcement. Missing controllers,
unreadable values and namespace paths outside visible mounts remain unknown.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re


@dataclass(frozen=True)
class Layout:
    backend: str
    paths: dict[str, Path]
    roots: dict[str, Path]


def _unescape(value: str) -> str:
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def _absolute(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe cgroup path')
    return path


def resolve(pid: int, *, proc: Path = Path('/proc')) -> Layout:
    """Resolve unified or split mounts, including non-root mount roots.

    Proc membership paths are interpreted against mountinfo, not a hardcoded
    /sys/fs/cgroup/controller layout. Each required v1 controller must be visible.
    In hybrid hierarchies use v1 when required controllers are attached there.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError('invalid container PID')
    members = {}
    unified = None
    for line in (proc / str(pid) / 'cgroup').read_text().splitlines():
        hierarchy, controllers, path = line.split(':', 2)
        _absolute(path)
        if hierarchy == '0' and not controllers:
            unified = path
        for controller in controllers.split(',') if controllers else ():
            if controller in members:
                raise ValueError('duplicate cgroup controller membership')
            members[controller] = path
    mounts = []
    for line in (proc / 'self' / 'mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        fields, fs = before.split(), after.split()
        if fs[0] not in ('cgroup', 'cgroup2'):
            continue
        root = _absolute(_unescape(fields[3]))
        mount = Path(_unescape(fields[4]))
        _absolute(str(mount))
        mounts.append((root, mount, fs[0], set(fs[2].split(','))))
    use_v1 = any(key in members for key in ('cpu', 'memory', 'pids'))
    backend = 'cgroup-v1' if use_v1 else 'cgroup-v2'
    paths, roots = {}, {}
    for controller in ('cpu', 'memory', 'pids'):
        membership = members.get(controller) if use_v1 else unified
        if membership is None:
            raise ValueError('missing visible cgroup controller: ' + controller)
        path = _absolute(membership)
        candidates = []
        for root, mount, fstype, options in mounts:
            if fstype != ('cgroup' if use_v1 else 'cgroup2'):
                continue
            if use_v1 and controller not in options:
                continue
            if path.is_relative_to(root):
                candidates.append((len(root.parts), mount / str(path.relative_to(root)), mount))
        if not candidates:
            raise ValueError('container cgroup is outside visible controller mounts: ' + controller)
        _, location, root = max(candidates, key=lambda row: row[0])
        paths[controller], roots[controller] = location, root
    return Layout(backend, paths, roots)


def read(path: Path, name: str) -> str | None:
    try:
        return (path / name).read_text().strip()
    except OSError:
        return None


def positive(raw: str | None, *, memory: bool = False) -> int | None:
    if raw is None or not raw.isdecimal():
        return None
    value = int(raw)
    # v1's unlimited memory sentinel is page-rounded LONG_MAX, not the word max.
    if value <= 0 or value > 2**63 - 1 or (memory and value >= (1 << 63) - (1 << 20)):
        return None
    return value


def pids_limit(path: Path, root: Path) -> tuple[int | None, dict[str, str]]:
    if not path.is_relative_to(root):
        raise ValueError('container cgroup is outside the controller mount')
    limits, evidence = [], {}
    while True:
        raw = read(path, 'pids.max')
        if raw is None and path == root:
            # Controller roots normally have no own pids.max.
            break
        if raw is None or (raw != 'max' and positive(raw) is None):
            raise ValueError('missing or invalid kernel pids.max')
        evidence[str(path)] = raw
        if raw != 'max':
            limits.append(int(raw))
        if path == root:
            break
        path = path.parent
    return min(limits) if limits else None, evidence


def limits(layout: Layout) -> dict:
    """Normalize local CPU/memory ceilings and inherited PID caps; keep raw reads."""
    cpu, memory, pids = (layout.paths[k] for k in ('cpu', 'memory', 'pids'))
    raw = {}
    if layout.backend == 'cgroup-v2':
        for name in ('cpu.max',): raw[name] = read(cpu, name)
        for name in ('memory.max', 'memory.swap.max'): raw[name] = read(memory, name)
        parts = (raw['cpu.max'] or '').split()
        quota, period = (positive(p) for p in parts) if len(parts) == 2 else (None, None)
        memory_bytes = positive(raw['memory.max'])
        swap = raw['memory.swap.max']
        swap_bytes = int(swap) if swap is not None and swap.isdecimal() else None
    else:
        for name in ('cpu.cfs_quota_us', 'cpu.cfs_period_us'): raw[name] = read(cpu, name)
        for name in ('memory.limit_in_bytes', 'memory.memsw.limit_in_bytes'): raw[name] = read(memory, name)
        quota, period = positive(raw['cpu.cfs_quota_us']), positive(raw['cpu.cfs_period_us'])
        memory_bytes = positive(raw['memory.limit_in_bytes'], memory=True)
        total = positive(raw['memory.memsw.limit_in_bytes'], memory=True)
        swap_bytes = total - memory_bytes if total is not None and memory_bytes is not None and total >= memory_bytes else None
    raw['pids.max'] = read(pids, 'pids.max')
    try:
        pid_count, hierarchy = pids_limit(pids, layout.roots['pids'])
        pids_observed = True
    except ValueError:
        pid_count, hierarchy, pids_observed = None, {}, False
    return {'cpu_millis': quota * 1000 / period if quota and period else None,
            'memory_bytes': memory_bytes, 'swap_bytes': swap_bytes,
            'pids': pid_count, 'pids_observed': pids_observed,
            'raw': raw, 'pids_hierarchy': hierarchy,
            'backend': layout.backend, 'paths': {k: str(v) for k, v in layout.paths.items()},
            'roots': {k: str(v) for k, v in layout.roots.items()}}


def oom_events(layout: Layout) -> dict[str, int]:
    """v1 needs an actual kill counter; under_oom alone cannot prove no OOM occurred."""
    name = 'memory.events' if layout.backend == 'cgroup-v2' else 'memory.oom_control'
    raw = read(layout.paths['memory'], name)
    if raw is None:
        raise ValueError('kernel memory OOM evidence unavailable: ' + name)
    values = dict(line.split() for line in raw.splitlines())
    if 'oom_kill' not in values or any(not value.isdecimal() for value in values.values()):
        raise ValueError('kernel memory OOM kill counter unavailable: ' + name)
    return {key: int(value) for key, value in values.items()}
