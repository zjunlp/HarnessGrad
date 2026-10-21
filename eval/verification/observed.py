"""Fresh observed container for an artifact-only combined command verifier."""
import base64
from pathlib import Path

from eval.containers.observed import Runtime, digest
from eval.policy.runtime_policy import phase_timeout


def verify(workdir, spec, answer, **options):
    from eval.verification.verifier import _verify_existing
    from eval.verification.isolation import declared_class
    receipt = {}
    try:
        if (not options.get('sandbox') or spec.get('kind') != 'command'
                or spec.get('judge') or spec.get('native_runtime') or not spec.get('result_contract')
                or declared_class(spec) not in ('unknown', 'actor-workdir')
                or (options.get('env') or {}).get('kind', 'files') != 'files'):
            raise ValueError('container_runtime requires sandboxed files command, result_contract, and no other scorer runtime')
        # Keep the established relative scorer file contract. Only these bounded
        # output files return; no container filesystem snapshot is shared.
        outputs = [spec['reward_file'].lstrip('/')]
        if spec.get('evidence_file'): outputs.append(spec['evidence_file'].lstrip('/'))
        if any(Path(p).is_absolute() or '..' in Path(p).parts or not Path(p).parts for p in outputs):
            raise ValueError('unsafe verifier result path')
        runtime_spec = spec['container_runtime']
        root = runtime_spec.get('workdir', '/workspace')
        binding = {'adapter_id': options.get('task_id') or 'direct-verifier',
                   'run_id': options.get('run_id') or 'direct', 'trial_id': options.get('trial_id') or 'trial-1',
                   'manifest_sha256': digest(spec)}
        class Scorer:
            def run(self, argv, *, workdir, reward_dir, timeout_s, immutable_runtimes=()):
                mounts = [(workdir, '/run/harnessgrad-inputs'),
                          *((src, root + '/' + rel) for src, rel in immutable_runtimes)]
                runtime = Runtime(runtime_spec, binding=binding,
                    timeout_s=phase_timeout(timeout_s, runtime_spec.get('verify_timeout_s')),
                    mounts=mounts, env={'HOME': '/tmp', 'TMPDIR': '/tmp'})
                try:
                    with runtime:
                        runtime.clear(outputs)
                        proc = runtime.run(argv, cwd=root + ('/' + spec['cwd'] if spec.get('cwd') else ''))
                        files = runtime.collect(outputs, max_bytes=spec.get('result_max_bytes', 4 << 20))
                        for name, item in files.items():
                            if item['status'] == 'submitted':
                                target = workdir / name; target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(base64.b64decode(item['base64']))
                        return proc
                finally:
                    receipt.update(runtime.receipt)
        verdict = _verify_existing(workdir, spec, answer, native_runtime=Scorer(), **options)
        if not receipt.get('cleanup', {}).get('complete'):
            verdict.update(score=None, passed=False, outcome='unmeasured', unmeasured='verifier cleanup incomplete')
    except Exception as exc:
        verdict = {'kind': 'command', 'passed': False, 'score': None, 'outcome': 'unmeasured', 'unmeasured': str(exc)}
    verdict['container_runtime'] = receipt
    return verdict
