"""One native actor world behind the existing private tool-relay boundary."""
from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import threading
import time

from data.scaffolds.native_harbor import _tree_identity
from eval.containers.observed import Runtime, digest


class Session:
    def __init__(self, case, binding, *, runtime_factory=None):
        self.case = case
        self.binding = binding
        self.spec = case['runtime']['actor']
        self.root = self.spec['workdir']
        self.lock = threading.Lock()
        self.timeout = case['actor_timeout_s']
        self.deadline = time.monotonic() + self.timeout
        self.commands = []
        self.artifacts = {}
        self.finished = False
        self.failure = None
        self.sealing = False
        if runtime_factory is None:
            if self.spec.get('backend') == 'native-public':
                from .public_runtime import PublicRuntime
                runtime_factory = PublicRuntime
            else:
                runtime_factory = Runtime
        self.runtime = runtime_factory(self.spec, binding=binding, timeout_s=self.timeout,
                                       deadline=self.deadline,
                                       env={'HOME': self.root + '/.home', 'TMPDIR': '/tmp'})
        from eval.run.native_supervisor import publish_runtime
        publish_runtime(self.runtime)
        if _tree_identity(Path(case['directory'])) != case['source_sha256']:
            raise ValueError('native source changed before actor launch')
        try:
            self.runtime.__enter__()
            # Original actor images must not contain the private native tests tree.
            probe = self.runtime.run([self.spec['python'], '-I', '-S', '-c',
                'import pathlib; p=pathlib.Path; '
                'assert not p("/tests").exists(), "private tests found in actor image"; '
                'p(".home").mkdir(exist_ok=True);p(".logs/artifacts").mkdir(parents=True,exist_ok=True);'
                'p(".logs/agent").mkdir(parents=True,exist_ok=True)'])
            if probe.returncode:
                raise ValueError('native actor image exposes private scorer or lacks prepared writable world')
        except BaseException:
            self.runtime.close()
            raise

    def handle(self, op, args):
        if op == 'describe':
            return {'workdir': self.root, 'tools': ['exec', 'finish'],
                    'instruction': 'Use exec to run shell commands in this persistent native world; finish exports only benchmark-declared artifacts.'}
        if op == 'finish':
            self.finish()
            return {'finished': True, 'artifacts': sorted(self.artifacts)}
        if op != 'exec' or not isinstance(args, dict) or set(args) != {'command'} or not isinstance(args['command'], str):
            raise ValueError('native actor accepts exec with one command string or finish')
        with self.lock:
            if self.finished or self.sealing or self.failure is not None:
                raise ValueError('native actor episode is already sealed')
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('native actor deadline reached')
            self.runtime.timeout_s = remaining
            try:
                process = self.runtime.run(['/bin/bash', '-lc', args['command']], cwd=self.root)
            except BaseException as exc:
                self.failure=type(exc).__name__
                self.runtime.close()
                raise
            self.commands.append({'command': args['command'], 'returncode': process.returncode})
            return {'returncode': process.returncode, 'output': process.stdout + process.stderr}

    def relative(self, path):
        if not isinstance(path, str) or not path.startswith('/'):
            raise ValueError('native artifact must use its declared absolute path')
        value = PurePosixPath(path)
        if '..' in value.parts:
            raise ValueError('native artifact path escapes')
        if path.startswith(self.root.rstrip('/') + '/'):
            return path[len(self.root.rstrip('/')) + 1:]
        if path.startswith('/logs/'):
            # Prepared images map /logs to the admitted writable world .logs.
            return '.logs/' + path[len('/logs/'):]
        raise ValueError('native artifact outside prepared world/log surface')

    def _export(self, name, relative):
        code = ('import json,pathlib,stat,os;root=pathlib.Path.cwd();p=root/'+repr(relative)+';out=[];count=0\n'
                'def visit(q,depth=0):\n'
                ' global count\n'
                ' count+=1\n'
                ' if depth>32 or count>2000: raise ValueError("native output tree budget exceeded")\n'
                ' s=q.lstat()\n'
                ' if stat.S_ISLNK(s.st_mode): raise ValueError("linked native output")\n'
                ' if stat.S_ISDIR(s.st_mode):\n'
                '  with os.scandir(q) as entries:\n'
                '   for child in entries: visit(pathlib.Path(child.path),depth+1)\n'
                ' elif stat.S_ISREG(s.st_mode) and s.st_nlink==1: out.append(q.relative_to(root).as_posix())\n'
                ' else: raise ValueError("nonregular native output")\n'
                'if p.exists(): visit(p)\n'
                'print(json.dumps(sorted(out)))')
        result = self.runtime.run([self.spec['python'], '-I', '-S', '-c', code])
        if result.returncode:
            raise RuntimeError('native output inventory failed')
        paths = json.loads(result.stdout)
        if not isinstance(paths, list) or len(paths) > 2000:
            raise ValueError('native output inventory exceeds 2000 files')
        exported = self.runtime.collect(paths, max_bytes=self.case['runtime'].get('artifact_max_bytes', 64 << 20))
        for path, record in exported.items():
            if record.get('status') != 'submitted':
                continue
            suffix = path[len(relative):].lstrip('/')
            target = name.rstrip('/') + ('/' + suffix if suffix else '')
            self.artifacts[target] = record

    def finish(self, *, cleanup=False):
        with self.lock:
            if self.finished:
                return
            if self.failure is not None:
                raise RuntimeError('native artifact sealing previously failed')
            self.sealing = True
            try:
                # Collect runs only trusted source commands after actor calls stop.
                # Parent ToolRelay gives SIGTERM cleanup 15 seconds; a longer native
                # collect must be requested explicitly with finish before actor exit.
                for item in self.case['collect']:
                    # Explicit finish gets the native collector budget. Signal
                    # teardown is bounded so ToolRelay's 15-second grace cannot
                    # turn into an orphaned Docker container.
                    native_limit = float(item.get('timeout_sec', 300))
                    self.runtime.timeout_s = min(native_limit, 3.0) if cleanup else native_limit
                    process = self.runtime.run(['/bin/bash', '-lc', item['command']], cwd=self.root)
                    if process.returncode:
                        raise RuntimeError('native collect command failed')
                for path in self.case['artifact_paths']:
                    self._export(path, self.relative(path))
                self.finished = True
            except BaseException as exc:
                self.failure = type(exc).__name__
                raise
            finally:
                self.runtime.close()
                self.sealing = False

    def record(self):
        return {'schema': 'harnessgrad.native-harbor-episode.v1',
                'binding': self.binding, 'task_id': self.case['task_id'],
                'case_sha256': hashlib.sha256(json.dumps(self.case,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'source_sha256': self.case['source_sha256'], 'dataset': self.case['dataset'],
                'finished': self.finished, 'failure': self.failure, 'artifacts': self.artifacts,
                'command_count': len(self.commands), 'actor_runtime': self.runtime.receipt}
