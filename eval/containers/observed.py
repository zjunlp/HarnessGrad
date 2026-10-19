"""Opt-in, observed Docker runtime for fixed workers and their combined verifier.

No legacy exec admission is relaxed. A trusted process starts first, the host
reads its kernel cgroup, and admit_runtime checks the complete writable inventory
before any candidate code runs. Only declared artifacts leave the container.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

from eval.admission.runner_admission import admit_runtime
from eval.containers import container
from data.scaffolds.runtime_extensions import validate_container_runtime as validate

HELPER = Path(__file__).with_name('observed_helper.py')
RUNTIME = '/run/harnessgrad-runtime'
# Include Docker's writable device/shm mounts, not only the task directory.
SYSTEM_FS = {'/tmp': {'bytes': 64 << 20, 'inodes': 16384},
             '/dev': {'bytes': 8 << 20, 'inodes': 512},
             '/dev/shm': {'bytes': 8 << 20, 'inodes': 512}}


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def bounded(argv, *, timeout=60, limit=32 << 20, output_path=None, truncate=False):
    """Bound CLI retention; actor logs are drained and truncated, never killed.

    Structured exports retain strict limits when declared. None disables the
    stdout export limit without changing the timeout or stderr diagnostics cap.
    """
    sink = open(output_path, "xb") if output_path is not None else None
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except BaseException:
        if sink is not None: sink.close()
        raise
    outputs = [bytearray(), bytearray()]
    counts = [0, 0]
    overflow = threading.Event()
    io_errors = []
    def read(pipe, dst, cap, index):
        try:
            with pipe:
                while chunk := pipe.read(65536):
                    before = counts[index]
                    counts[index] += len(chunk)
                    if cap is not None and counts[index] > cap:
                        if not truncate:
                            overflow.set(); p.kill(); break
                        chunk = chunk[:max(0, cap - before)]
                    if dst is outputs[0] and sink is not None: sink.write(chunk)
                    else: dst.extend(chunk)
        except (OSError, ValueError) as exc:
            io_errors.append(str(exc))
            p.kill()
    threads = [threading.Thread(target=read, args=(pipe, dst, cap, index), daemon=True)
               for index, (pipe, dst, cap) in enumerate(zip((p.stdout, p.stderr), outputs, (limit, 1 << 20)))]
    for t in threads: t.start()
    try:
        p.wait(timeout=timeout)
    except BaseException:
        p.kill(); p.wait(); raise
    finally:
        for t in threads: t.join(timeout=5)
        if sink is not None: sink.close()
    if io_errors: raise RuntimeError('container output collection failed: ' + '; '.join(io_errors))
    if overflow.is_set():
        raise RuntimeError('container output exceeds platform byte limit')
    result = subprocess.CompletedProcess(argv, p.returncode, bytes(outputs[0]), bytes(outputs[1]))
    result.output_retention = {name: {'source_bytes': count, 'bytes': len(data), 'truncated': count > len(data)}
        for name, count, data in zip(('stdout', 'stderr'), counts, outputs)}
    return result


def checked(*args, timeout=60, limit=32 << 20, output_path=None):
    p = bounded(['docker', *map(str, args)], timeout=timeout, limit=limit, output_path=output_path)
    if p.returncode:
        raise RuntimeError(f'docker {args[0]} failed: {p.stderr.decode(errors="replace")[-1200:]}')
    return p.stdout


def inspect(name):
    return json.loads(checked('inspect', name))[0]


def observe_pids(cgroup, *, root=Path('/sys/fs/cgroup')):
    """Read local and inherited PID ceilings; None means all observed levels say max.

    A parent limit still constrains a container whose own pids.max is max. Keep
    the raw hierarchy in the receipt rather than claiming that disabling the
    container-specific limit removes the host's resource policy.
    """
    cgroup, root = Path(cgroup), Path(root)
    if not cgroup.is_relative_to(root):
        raise RuntimeError('container cgroup is outside the cgroup root')
    limits = []
    evidence = {}
    current = cgroup
    while True:
        path = current / 'pids.max'
        # The unified root has no pids.max on ordinary cgroup-v2 hosts.
        if current != root or path.exists():
            raw = path.read_text().strip()
            evidence[str(current)] = raw
            if raw != 'max':
                if not raw.isdecimal() or not 0 < int(raw) <= 2**63 - 1:
                    raise RuntimeError('invalid kernel pids.max')
                limits.append(int(raw))
        if current == root:
            break
        current = current.parent
    return min(limits) if limits else None, evidence


def observe(name, binding, policy, *, supervisor, python, expected_mounts):
    """Kernel reads are collected before actor launch; never accept adapter facts."""
    info = inspect(name)
    pid = info['State']['Pid']
    group = Path(f'/proc/{pid}/cgroup').read_text().strip().splitlines()
    unified = [line[3:] for line in group if line.startswith('0::')]
    if len(unified) != 1:
        raise RuntimeError('observed-v1 requires cgroup v2')
    cg = Path('/sys/fs/cgroup') / unified[0].lstrip('/')
    raw = {key: (cg / key).read_text().strip() for key in
           ('cpu.max', 'memory.max', 'memory.swap.max', 'pids.max')}
    pids, pids_hierarchy = observe_pids(cg)
    quota, period = map(int, raw['cpu.max'].split())
    if quota * 1000 % period:
        raise RuntimeError('kernel CPU quota is not whole millicores')
    kernel = json.loads(checked('exec', name, python, '-I', '-S',
                               RUNTIME + '/helper.py', 'probe'))
    actual_mounts = {row['Destination']: row for row in info['Mounts'] if row['Type'] == 'bind'}
    if set(actual_mounts) != set(expected_mounts) or any(
            actual_mounts[dst]['Source'] != src or actual_mounts[dst]['RW']
            for dst, src in expected_mounts.items()):
        raise RuntimeError('container channel/input mount inventory mismatch')
    mounts = kernel['mounts']
    # Only file-storage mounts need quota entries. Kernel proc/devpts/mqueue and
    # masked character devices are separately inventoried, never guessed by name.
    writable = {}
    special = []
    for row in mounts:
        if not row['writable']: continue
        if row['fstype'] in ('proc', 'devpts', 'mqueue') or row['character_device']:
            special.append(row); continue
        if row['fstype'] != 'tmpfs':
            raise RuntimeError('unbounded writable container mount: ' + row['path'])
        writable[row['path']] = {
            'bytes': row['bytes'], 'inodes': row['inodes'], 'backend': 'tmpfs',
            'scope_id': f'{info["Id"][:16]}:{row["device"]}', 'evidence_sha256': digest(row)}
    endpoints = info['NetworkSettings']['Networks']
    attachments = [n for n, v in endpoints.items() if n != 'none' or v.get('IPAddress') or v.get('Gateway')]
    if kernel['interfaces'] != ['lo']:
        attachments.append('kernel-interfaces:' + ','.join(kernel['interfaces']))
    root = next(row for row in mounts if row['path'] == '/')
    facts = {'binding': binding, 'resources': {
        'cpu_millis': quota * 1000 // period, 'memory_bytes': int(raw['memory.max']),
        'swap_bytes': int(raw['memory.swap.max']), 'pids': pids,
        'timeout_ms': supervisor['timeout_ms']},
        'resource_backend': 'cgroup-v2', 'timeout_backend': 'host-supervisor',
        'network': info['HostConfig']['NetworkMode'], 'attachments': attachments,
        'read_only_root': not root['writable'], 'filesystems': writable}
    evidence = {'container_id': info['Id'], 'image_id': info['Image'], 'pid': pid,
                'cgroup': str(cg), 'cgroup_values': raw, 'pids_hierarchy': pids_hierarchy, 'kernel': kernel,
                'supervisor': supervisor, 'special_mounts': special, 'readonly_binds': expected_mounts}
    facts['evidence_sha256'] = digest(evidence)
    receipt = admit_runtime(binding, policy, facts)
    return {'admission': receipt, 'observed': facts, 'evidence': evidence}


class Runtime:
    def __init__(self, spec, *, binding, timeout_s, mounts=(), env=None):
        self.workdir = validate(spec)
        self.spec, self.binding, self.timeout_s = spec, binding, timeout_s
        self.python = spec.get('python', '/usr/local/bin/python3')
        self.mounts, self.env = list(mounts), dict(env or {})
        self.name = 'hg-observed-' + uuid.uuid4().hex[:20]
        self.seed = self.name + '-seed'
        self.receipt = {'binding': binding, 'backend': 'observed-v1'}
        self.temp = tempfile.TemporaryDirectory(prefix='hg-observed-')
        self.directory = Path(self.temp.name)
        self.filesystems = {**SYSTEM_FS, **spec['filesystems']}
        self.policy = {'network': 'none', 'filesystems': self.filesystems, 'resources': {
            'cpu_millis': int(spec.get('cpus', 2) * 1000),
            'memory_bytes': spec.get('memory_mb', 4096) << 20, 'swap_bytes': 0,
            'pids': spec.get('pids', 1024), 'timeout_ms': int(timeout_s * 1000)}}
        self.created = []
        self.started = False

    def __enter__(self):
        try:
            # image inspect even for a digest: never let Docker implicitly pull.
            image = json.loads(checked('image', 'inspect', self.spec['image']))[0]
            if image['Config'].get('Volumes'):
                raise ValueError('observed image may not declare anonymous volumes')
            # Stable interpreter route, pinned to this image. Images without this path
            # must supply an import-time runtime; do not inject the host environment.
            source = self.spec.get('image_workspace')
            if source:
                self.created.append(self.seed)
                checked('create', '--pull=never', '--network=none', '--read-only', '--entrypoint',
                        '/bin/true', '--name', self.seed, self.spec['image'])
                checked('cp', self.seed + ':' + source.rstrip('/') + '/.', '-', timeout=300,
                        limit=2 * self.spec['filesystems'][self.workdir]['bytes'] + (16 << 20),
                        output_path=self.directory / 'seed.tar')
            plan = {'workdir': self.workdir, 'seed': bool(source), 'staging': '/run/harnessgrad-inputs',
                    'env_dirs': list(container.writable_env_dirs(self.workdir)),
                    'immutable': [dst[len(self.workdir)+1:] for _, dst in self.mounts if dst.startswith(self.workdir + '/')]}
            (self.directory / 'plan.json').write_text(json.dumps(plan))
            argv = ['create', '--pull=never', '--name', self.name, '--network=none', '--read-only',
                    '--cpus', str(self.spec.get('cpus', 2)), '--memory', str(self.policy['resources']['memory_bytes']),
                    '--memory-swap', str(self.policy['resources']['memory_bytes']),
                    # Explicit -1 also overrides a Docker daemon default PID cap.
                    '--pids-limit', str(self.policy['resources']['pids'] or -1), '--cap-drop=ALL',
                    '--cap-add=CHOWN', '--cap-add=KILL', '--cap-add=DAC_OVERRIDE', '--security-opt=no-new-privileges',
                    '--user=0:0', '--entrypoint=' + self.python]
            for path, limits in self.filesystems.items():
                options = 'rw,nosuid,' + ('dev,mode=755' if path == '/dev' else 'nodev,exec,mode=1777')
                argv += ['--tmpfs', f'{path}:{options},size={limits["bytes"]},nr_inodes={limits["inodes"]}']
            binds = [(HELPER, RUNTIME + '/helper.py'), (self.directory, RUNTIME + '/control'), *self.mounts]
            expected_mounts = {dst: str(Path(src).resolve()) for src, dst in binds}
            if len(expected_mounts) != len(binds): raise ValueError('duplicate container mount')
            for src, dst in binds:
                if ',' in str(src) or ',' in str(dst): raise ValueError('commas are unsupported in mount paths')
                argv += ['--mount', f'type=bind,src={Path(src).resolve()},dst={dst},readonly']
            self.created.append(self.name)
            checked(*argv, self.spec['image'], '-I', '-S', RUNTIME + '/helper.py', 'idle')
            checked('start', self.name)
            self.started = True
            self.receipt['channels'] = {dst: (['send', 'receive'] if dst == '/run/harnessgrad-tool/tool.sock'
                else ['POST chat/completions']) for _, dst in self.mounts if dst.endswith('.sock')}
            for _, dst in self.mounts:
                if dst.endswith('.sock') and dst not in ('/run/harnessgrad-tool/tool.sock', '/run/harnessgrad-model/model.sock'):
                    raise ValueError('unauthorized container socket channel')
            self.receipt.update(observe(self.name, self.binding, self.policy, supervisor={
                'pid': os.getpid(), 'timeout_ms': int(self.timeout_s * 1000),
                'action': 'docker exec deadline kills container; normal completion kills actor UID processes; finally force-remove container'},
                python=self.python, expected_mounts=expected_mounts))
            actual = self.receipt['evidence']['image_id']
            if actual != image['Id']: raise RuntimeError('container image changed at launch')
            actor = json.loads(checked('exec', '--user=65534:65534', self.name, self.python, '-I', '-S',
                                       RUNTIME + '/helper.py', 'identity'))
            if actor['uid'] != 65534 or actor['effective_caps'] != 0 or actor['no_new_privileges'] != 1:
                raise RuntimeError('actor privilege separation not enforced')
            self.receipt['actor_identity'] = actor
            # Observe before initialization too: extraction/copying is bounded by
            # the very same kernel filesystem and resource limits.
            checked('exec', self.name, self.python, '-I', '-S', RUNTIME + '/helper.py', 'init', timeout=300)
            return self
        except BaseException as exc:
            if getattr(exc, 'receipt', None): self.receipt['admission'] = exc.receipt
            self.close()
            raise

    def run(self, argv, *, env=None, cwd=None):
        command = ['docker', 'exec', '--user=65534:65534', '-w', cwd or self.workdir]
        for key, value in sorted({**self.env, **(env or {})}.items()):
            command += ['-e', key + '=' + str(value)]
        command += [self.name, *argv]
        started = time.monotonic()
        try:
            try:
                proc = bounded(command, timeout=self.timeout_s, limit=8 << 20, truncate=True)
                self.receipt['console_logs'] = proc.output_retention
            except subprocess.TimeoutExpired:
                # Do not wait for an in-container helper when actor PIDs may be
                # exhausted. Kill the whole container at the host deadline.
                checked('kill', self.name, timeout=10)
                self.started = False
                self.receipt['timeout_action'] = 'container killed'
                raise
            cgroup = Path(self.receipt['evidence']['cgroup'])
            events = dict(line.split() for line in (cgroup / 'memory.events').read_text().splitlines())
            self.receipt['memory_events'] = {key: int(value) for key, value in events.items()}
            if int(events.get('oom_kill', 0)):
                raise RuntimeError('worker cgroup reported an OOM kill')
            return subprocess.CompletedProcess(argv, proc.returncode,
                proc.stdout.decode(errors='replace'), proc.stderr.decode(errors='replace'))
        finally:
            self.receipt['execution'] = {'elapsed_s': time.monotonic() - started,
                                         'timeout_s': self.timeout_s}
            # Actor has a different UID, no capabilities, no writable executable
            # runtime. Root helper can stop even setsid/forked descendants safely.
            if self.started:
                checked('exec', self.name, self.python, '-I', '-S', RUNTIME + '/helper.py', 'quiesce', timeout=10)

    def clear(self, paths):
        checked('exec', self.name, self.python, '-I', '-S', RUNTIME + '/helper.py',
                'clear', self.workdir, json.dumps(paths))

    def collect(self, paths, *, max_bytes=16 << 20, truncate=False):
        return json.loads(checked('exec', self.name, self.python, '-I', '-S',
            RUNTIME + '/helper.py', 'collect', self.workdir, json.dumps(paths), json.dumps(max_bytes),
            *(['truncate'] if truncate else []),
            limit=None if max_bytes is None else 2 * max_bytes + 65536))

    def close(self):
        errors = []
        for name in reversed(self.created):
            try:
                checked('rm', '--force', '--volumes', name, timeout=120)
                p = bounded(['docker', 'inspect', name], timeout=30)
                if p.returncode == 0 or b'No such' not in p.stderr:
                    errors.append('container absence not confirmed: ' + name)
            except Exception as exc:
                errors.append(str(exc))
        self.created.clear()
        try: self.temp.cleanup()
        except OSError as exc: errors.append(str(exc))
        self.receipt['cleanup'] = {'complete': not errors, 'errors': errors}

    def __exit__(self, *args):
        self.close()
