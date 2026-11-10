"""Native benchmark scoring using public source and locally prepared dependencies.

Only scoring source is authenticated against the selected repository revision.
The interpreter and installed packages belong to this machine's prepared runtime;
no bytes from an earlier machine are required. Local identities remain recorded.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import tempfile

from data.scaffolds.public_native import DATASETS, PROFILE, ROOT, identity
from eval.containers import sandbox
from harnessgrad import dataset_runtime


class PublicRuntime:
    def __init__(self, binding, runtime, code):
        self.binding, self.runtime, self.code = binding, runtime, code
        self.receipt = {'dataset': binding['dataset'], 'sha256': binding['sha256'],
                        'verified': True, 'mechanism': PROFILE,
                        'files': len(binding['sources']),
                        'identity': {'public_logic_sha256': binding['sha256'],
                                     'python_runtime': runtime,
                                     'dependency_versions': dataset_runtime.execution_descriptor(binding['dataset'])['pins']},
                        'host': {'system': platform.system(), 'machine': platform.machine(),
                                 'kernel': platform.release(), 'admission_policy': 'local-public-runtime-v1'}}

    def run(self, argv, *, workdir, reward_dir, timeout_s, immutable_runtimes=()):
        dataset_runtime.admit_binding(self.runtime, 'verifier')
        task = Path(workdir).resolve()
        for value in self.runtime['runtime_paths']:
            path = Path(value).resolve()
            if path == task or path.is_relative_to(task) or task.is_relative_to(path):
                raise ValueError('public scorer dependencies overlap task storage')
        if argv[1] == '-c':
            bootstrap = argv[2]
            arguments = []
        else:
            script = PurePosixPath(argv[1])
            if str(script) not in self.binding['sources']:
                raise ValueError('entry point is outside public scorer sources')
            bootstrap = ('import runpy; args=sys.argv[1:]; sys.argv=args; '
                         'runpy.run_path(args[0],run_name="__main__")')
            arguments = [str(self.code / script), *argv[2:]]
        # Avoid task-local module shadows and package startup hooks. All scorer
        # imports resolve from the trusted code snapshot or the local prepared site.
        site = Path(self.runtime['path']) / 'lib/python3.12/site-packages'
        prefix = 'import sys; sys.path[:0]=' + repr([str(self.code), str(site)]) + '; '
        command = [self.runtime['python'], '-I', '-S', '-B', '-c', prefix + bootstrap, *arguments]
        env = {'PATH': os.defpath, 'HOME': '/tmp', 'TMPDIR': '/tmp', 'LANG': 'C.UTF-8',
               'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        return sandbox.run(command, platform=ROOT, work_root=Path(workdir), task_dir=Path(workdir),
                           repo=self.code, env=env, cwd=Path(workdir), timeout_s=timeout_s,
                           task_dir_writable=False, runtime_paths=tuple(Path(p) for p in self.runtime['runtime_paths']),
                           writable_paths=(Path(reward_dir),), network='none',
                           immutable_runtimes=immutable_runtimes, platform_interpreter=False)


@contextmanager
def admit(binding):
    if (not isinstance(binding, dict) or set(binding) != {'dataset','profile','sources','sha256'}
            or binding['dataset'] not in DATASETS or binding['profile'] != PROFILE
            or binding['sha256'] != identity({k:v for k,v in binding.items() if k != 'sha256'})):
        raise ValueError('invalid public native scoring contract')
    selected = dataset_runtime.bind(dataset_runtime.resolve_execution(binding['dataset']), 'verifier')
    with tempfile.TemporaryDirectory(prefix='hg-public-scorer-') as temporary:
        code = Path(temporary)
        for name, entry in binding['sources'].items():
            relative = PurePosixPath(name)
            if not name.endswith('.py') or relative.is_absolute() or '..' in relative.parts or str(relative) != name:
                raise ValueError('invalid public scorer source path')
            if 'source' in entry:
                source = (ROOT / entry['source']).resolve(strict=True)
                if not source.is_relative_to(ROOT) or not source.is_file():
                    raise ValueError('public scorer source escapes the platform')
                raw = source.read_bytes()
            else:
                raw = entry['content'].encode('utf-8')
            if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('public scoring code changed during selection')
            target = code / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        runtime = PublicRuntime(binding, selected, code)
        work = code / '.probe-task'; work.mkdir()
        reward = work / 'verifier'; reward.mkdir()
        probe_code = 'import json; facts={"python":sys.version,"isolated":sys.flags.isolated,"no_site":sys.flags.no_site}; '
        if binding['dataset'] == 'bird_minidev':
            probe_code += 'import sqlite3; facts["sqlite"]=sqlite3.sqlite_version; '
        probe_code += 'print(json.dumps(facts))'
        probe = runtime.run(['python3', '-c', probe_code],
                            workdir=work, reward_dir=reward, timeout_s=30)
        if probe.returncode:
            raise ValueError('public scorer runtime startup failed: ' + probe.stderr[-1500:])
        facts = json.loads(probe.stdout)
        if not facts.get('isolated') or not facts.get('no_site'):
            raise ValueError('public scorer Python isolation unavailable')
        runtime.receipt['identity']['execution'] = facts
        yield runtime
