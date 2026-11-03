"""SWE Verified: local pinned images, sealed patch export, fresh native grading.

The harness talks to an offline repository through a narrow exec relay. It does
not submit a resolved verdict. Scoring replays its exported patch against the
baseline in a separate observed container and invokes FlashRSI's unchanged
official SWE-bench helper in an isolated, explicitly installed interpreter.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

from harnessgrad.config.areas import ConfigMissing
from eval.containers.observed import Runtime, checked

ASSETS = Path(__file__).resolve().parent
HELPER = ASSETS / 'vendor/flashrsi/integrations/_swe_grade.py'
REVISION = '02e7a74ffd0b707aab73d203fe87bdc7c76afc8e'
IMAGE = re.compile(r'sha256:[a-f0-9]{64}')


def python():
    value = Path(os.environ.get('HG_SWE_PYTHON', sys.executable))
    if not value.is_absolute() or not value.is_file():
        raise ConfigMissing('swe_bench: HG_SWE_PYTHON must name an installed absolute interpreter')
    return str(value)


def official(action, root=None, *, timeout_s=30):
    argv = [python(), '-I', '-X', 'utf8', str(ASSETS / 'swe_official.py'), action]
    if root is not None:
        argv.append(str(root))
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHON_DOTENV_DISABLED': '1'}
    if 'HG_SWE_SOURCE' in os.environ:
        environment['HG_SWE_SOURCE'] = os.environ['HG_SWE_SOURCE']
    result = subprocess.run(argv, cwd=str(root) if root else '/', stdin=subprocess.DEVNULL,
        capture_output=True, text=True, errors='replace', timeout=timeout_s,
        env=environment)
    if result.returncode:
        raise ConfigMissing('swe_bench: pinned official evaluator unavailable or failed; install swebench at ' + REVISION)
    return {} if action == 'check' else json.loads(result.stdout)


def configuration():
    from data.scaffolds import roots
    path = Path(os.environ.get(
        'HG_SWE_BENCH_RUNTIME',
        roots.dataset_root('swe_bench') / 'native-runtime.json'))
    if not path.is_file() or path.is_symlink():
        raise ConfigMissing('swe_bench: prepare local images with tools/prepare_flashrsi_swe.py and set HG_SWE_BENCH_RUNTIME')
    config = json.loads(path.read_text())
    if (config.get('schema') != 'harnessgrad.swe-native-runtime.v1'
            or config.get('evaluator_revision') != REVISION or not isinstance(config.get('instances'), dict)):
        raise ValueError('invalid SWE native runtime declaration')
    return config


def runtime_spec(entry, *, actor):
    return {'kind': 'exec', 'backend': 'observed-v1',
        'image': entry['actor_image_id'] if actor else entry['image_id'],
        'image_workspace': '/testbed', 'workdir': '/testbed', 'network': 'none',
        'python': entry.get('python', '/usr/local/bin/python3'),
        'cpus': entry.get('cpus', 2), 'memory_mb': entry.get('memory_mb', 4096),
        'pids': entry.get('pids', 512),
        'filesystems': {'/testbed': {'bytes': entry.get('workspace_bytes', 4 << 30),
                                   'inodes': entry.get('workspace_inodes', 262144)}}}


def selected(case, case_sha256, config=None, *, deadline=None):
    config = configuration() if config is None else config
    entry = config['instances'].get(case['native_task_id'])
    if not isinstance(entry, dict):
        raise ConfigMissing('swe_bench: local per-instance images have not been prepared for selected task')
    if (entry.get('base_commit') != case['row']['base_commit'] or entry.get('case_sha256') != case_sha256
            or not IMAGE.fullmatch(entry.get('image_id', '')) or not IMAGE.fullmatch(entry.get('actor_image_id', ''))
            or entry.get('sanitization') != 'flashrsi-baseline-prune-v1'):
        raise ValueError('SWE runtime baseline/case/image binding mismatch')
    for key in ('image_id', 'actor_image_id'):
        remaining = 60 if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('native image admission timed out')
        image = json.loads(checked('image', 'inspect', entry[key], timeout=min(60, remaining)))[0]
        if image['Id'] != entry[key]:
            raise ValueError('SWE local image changed')
    return entry


def readiness(adapter, manifest, *, task_id=None):
    official('check')
    config = configuration()
    for record in manifest['records']:
        if task_id is not None and record['task_id'] != task_id:
            continue
        case = adapter.case(record, manifest)
        if case['row'].get('image_assets'):
            raise ConfigMissing('swe_bench: FlashRSI Verified route does not support image_assets')
        selected(case, manifest['files'][record['case']], config)
    return manifest


def reset_script(base, *, sanitize_root=False):
    if not re.fullmatch(r'[a-f0-9]{40}', base):
        raise ValueError('invalid baseline commit')
    script = ("set -e\ncd /testbed\n"
        f"git reset --hard {base}\ngit clean -fd\ngit checkout --detach\n"
        "git for-each-ref --format='delete %(refname)' | git update-ref --stdin\n"
        "git config --local --remove-section remote.origin || true\n"
        "git reflog expire --expire=now --all\ngit gc --prune=now\n")
    if sanitize_root:
        script += ('rm -f -- /root/setup_repo.sh /root/setup_env.sh /root/install.sh '
                   '/eval.sh /patch.diff /test.patch /tmp/patch.diff\n')
    return script


def prepare_image(case, case_sha256, *, image=None, image_python='/usr/local/bin/python3'):
    """Explicit offline preparation only; never pulls or modifies an existing image."""
    original = json.loads(checked('image', 'inspect', image or case['row']['image']))[0]['Id']
    name = 'hg-swe-prepare-' + uuid.uuid4().hex
    created = False
    try:
        checked('create', '--pull=never', '--name', name, '--network=none', '--memory=4g',
            '--cpus=2', '--pids-limit=512', '--security-opt=no-new-privileges',
            '--entrypoint=/bin/sh', original, '-c', 'while :; do sleep 3600; done')
        created = True
        checked('start', name)
        checked('exec', name, '/bin/bash', '-c', reset_script(case['row']['base_commit'], sanitize_root=True), timeout=300)
        actor_id = checked('commit', name, timeout=300).decode().strip()
        if not IMAGE.fullmatch(actor_id):
            raise ValueError('Docker did not return a local immutable image id')
        return {'image_id': original, 'actor_image_id': actor_id,
            'case_sha256': case_sha256, 'base_commit': case['row']['base_commit'],
            'python': image_python, 'sanitization': 'flashrsi-baseline-prune-v1'}
    finally:
        if created:
            checked('rm', '--force', '--volumes', name, timeout=30)


class Session:
    def __init__(self, case, case_sha256, context):
        self.case, self.case_sha256 = case, case_sha256
        self.entry = selected(case, case_sha256)
        self.runtime = Runtime(runtime_spec(self.entry, actor=True),
            binding={'adapter_id': case['task_id'], 'run_id': context['run_id'],
                     'trial_id': context['trial_id'], 'manifest_sha256': case_sha256}, timeout_s=300)
        self._closed = False
        self._close_error = None
        cleanup = os.environ.get('HG_SWE_CLEANUP_PATH')
        if cleanup:
            # Publish exact owned names before the first Docker operation. The
            # relay supervisor can reap a failed/interrupted startup as well.
            Path(cleanup).write_text(json.dumps({'schema': 'harnessgrad.swe-cleanup.v1',
                'containers': [self.runtime.name, self.runtime.seed]}))
        self.runtime.__enter__()

    def execute(self, command):
        if not isinstance(command, str) or not command.strip() or len(command) > 1 << 20:
            raise ValueError('exec requires a nonempty shell command')
        result = self.runtime.run(['/bin/bash', '-lc', command])
        return {'stdout': result.stdout, 'stderr': result.stderr, 'exit_code': result.returncode}

    def finish(self):
        self.runtime.timeout_s = min(self.runtime.timeout_s, 3.0)
        # No score or private test script is visible in this actor environment.
        result = self.runtime.run(['/bin/bash', '-c',
            'set -e\ncd /testbed\ngit add -N -- .\ngit -c core.quotePath=true diff '
            '--no-ext-diff --no-textconv --binary --full-index '
            + self.entry['base_commit'] + ' -- .'])
        if result.returncode or self.runtime.receipt.get('console_logs', {}).get('stdout', {}).get('truncated'):
            raise ValueError('native patch export failed or exceeded retention limit')
        return {'task_id': self.case['task_id'], 'case_sha256': self.case_sha256,
            'base_commit': self.entry['base_commit'], 'image_id': self.entry['image_id'],
            'actor_image_id': self.entry['actor_image_id'], 'model_patch': result.stdout}

    def close(self):
        # Preserve the first cleanup failure; a second call must not overwrite
        # failed evidence with an empty created-container list.
        if self._closed:
            if self._close_error:
                raise RuntimeError(self._close_error)
            return
        self._closed = True
        self.runtime.close()
        if not self.runtime.receipt.get('cleanup', {}).get('complete'):
            self._close_error = 'native SWE actor cleanup failed'
            print(json.dumps({'schema': 'harnessgrad.swe-cleanup-error.v1',
                'runtime': self.runtime.receipt}), file=sys.stderr, flush=True)
            raise RuntimeError(self._close_error)


def run(spec, artifact_root, *, task_id, run_id, trial_id, timeout_s):
    deadline = time.monotonic() + timeout_s
    evidence = {'measured': False, 'evaluator_revision': REVISION}
    result = {'kind': 'command', 'score': None, 'passed': False, 'outcome': 'unmeasured', 'evidence': evidence}
    runtime = None
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('native SWE verification timed out')
        return value
    try:
        case_raw = (artifact_root / spec['scorer_input']['path']).read_bytes()
        case = json.loads(case_raw)
        case_sha256 = hashlib.sha256(case_raw).hexdigest()
        entry = selected(case, case_sha256, deadline=deadline)
        sealed = json.loads((artifact_root / 'swe_episode/episode.json').read_text())
        payload = sealed['payload']
        expected = {'task_id': task_id, 'case_sha256': case_sha256,
            'base_commit': entry['base_commit'], 'image_id': entry['image_id'], 'actor_image_id': entry['actor_image_id']}
        if any(payload.get(k) != v for k, v in expected.items()) or payload.get('failure'):
            raise ValueError('native SWE patch export binding mismatch')
        patch = payload['model_patch']
        if not isinstance(patch, str):
            raise ValueError('native patch must be text')
        fields = ('instance_id', 'image', 'repo', 'version', 'eval_script', 'log_parser', 'eval_type', 'FAIL_TO_PASS', 'PASS_TO_PASS')
        with tempfile.TemporaryDirectory(prefix='hg-swe-grade-') as temp:
            root = Path(temp)
            # Runtime executes candidate tests as uid65534. The private mount is
            # visible only in the fresh verifier, but must be readable there.
            root.chmod(0o755)
            (root / 'request.json').write_text(json.dumps({'row': {k: case['row'][k] for k in fields}}))
            (root / 'model.patch').write_text(patch, encoding='utf-8', newline='\n')
            prepared = official('prepare', root, timeout_s=remaining())
            runtime = Runtime(runtime_spec(entry, actor=False), binding={
                'adapter_id': task_id, 'run_id': run_id or 'direct', 'trial_id': trial_id or 'trial-1', 'manifest_sha256': case_sha256},
                timeout_s=remaining(), deadline=deadline, mounts=[(root, '/run/hg-swe-grade')])
            with runtime:
                reset = runtime.run(['/bin/bash', '-c', reset_script(entry['base_commit'])])
                if reset.returncode:
                    raise ValueError('native pristine baseline reset failed')
                applied = False
                commands = prepared['apply_commands'] if patch.strip() else ()
                for index, command in enumerate(commands):
                    runtime.timeout_s = remaining()
                    if index:
                        reset = runtime.run(['/bin/bash', '-c', 'git checkout -- . && git clean -fd'])
                        if reset.returncode:
                            raise ValueError('native patch retry reset failed')
                    proc = runtime.run(['/bin/bash', '-c', command + ' /run/hg-swe-grade/model.patch'])
                    if proc.returncode == 0:
                        applied = True
                        break
                if commands and not applied:
                    proc = runtime.run(['git', 'apply', '--check', '--reverse', '/run/hg-swe-grade/model.patch'])
                    applied = proc.returncode == 0
                if applied:
                    runtime.timeout_s = remaining()
                    runtime.run(['/bin/bash', '-c', 'ulimit -f 32768; /bin/bash /run/hg-swe-grade/eval.sh > /tmp/hg-swe-test-output 2>&1'])
                    output = checked('exec', runtime.name, 'cat', '/tmp/hg-swe-test-output', timeout=remaining(), limit=32 << 20).decode('utf-8', errors='replace')
                else:
                    output = prepared['apply_patch_fail']
            evidence['runtime'] = runtime.receipt
            if not runtime.receipt.get('cleanup', {}).get('complete'):
                raise RuntimeError('native SWE verifier cleanup failed')
            (root / 'test_output.txt').write_text(output, encoding='utf-8')
            verdict = official('grade', root, timeout_s=remaining())
        if (type(verdict.get('resolved')) is not bool or type(verdict.get('patch_successfully_applied')) is not bool
                or verdict['resolved'] and not (applied and verdict['patch_successfully_applied'])):
            raise ValueError('invalid native official verdict')
        if applied and not verdict['patch_successfully_applied']:
            # The official helper uses this flag for finding native test output.
            # A successfully applied patch with no completed test protocol is an
            # unavailable measurement, not proof that the repair failed tests.
            raise RuntimeError('native test execution produced no parseable protocol')
        value = float(verdict['resolved'])
        evidence.update(measured=True, metrics={'resolved': value}, native=verdict,
            official_evaluation_performed=True, patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
            image_id=entry['image_id'], actor_image_id=entry['actor_image_id'], base_commit=entry['base_commit'])
        result.update(score=value, passed=bool(value), outcome='measured')
    except Exception as exc:
        if runtime is not None:
            evidence['runtime'] = runtime.receipt
        evidence.update(error=type(exc).__name__)
        result['unmeasured'] = 'native SWE evaluator failed: ' + type(exc).__name__
    return result
