"""Observed Docker peer execution; the candidate remains the acting policy."""
import base64
import json
import os
from pathlib import Path
import subprocess

from eval.containers.observed import Runtime, digest
from eval.containers import container, harness_runtime
from eval.containers.immutable_runtime import projections, targets
from eval.observe.tracing import _materialize
from eval.policy.runtime_policy import phase_timeout
from eval.service.worker_model_route import ModelRoute


def run(repo, task, *, worker, private, channel, timeout_s, run_id, trial_id, run_seed):
    from eval.run import runner
    spec = worker['env']
    work = spec.get('workdir', '/workspace')
    stage = private / 'inputs'; stage.mkdir()
    _materialize(stage, worker.get('files'), runner.PLATFORM_ROOT)
    (stage / 'task.json').write_text(json.dumps(task))
    manifest = json.loads((repo / 'harness.json').read_text())
    entry = Path(manifest['entrypoint'])
    if entry.is_absolute() or '..' in entry.parts or (repo / entry).is_symlink():
        return runner._invalid(task, 'unsafe candidate entrypoint', stage='worker admission', service=worker['id'])
    bindings = projections(worker.get('files'))
    targets(stage, bindings)
    mounts = [(stage, '/run/harnessgrad-inputs'), (repo, str(repo)),
              (channel, '/run/harnessgrad-tool/tool.sock'),
              *((src, work + '/' + rel) for src, rel in bindings)]
    # The socket's parent is never mounted or discoverable by a peer. UID 65534
    # needs socket connect permission; isolation comes from the per-peer mount.
    os.chmod(channel, 0o666)
    timeout_s = phase_timeout(timeout_s, spec.get('agent_timeout_s'))
    env = container.container_env(work, deadline_s=timeout_s)
    env.update(HG_WORKER_ID=worker['id'], HARNESSGRAD_STATE=work + '/.harnessgrad-state')
    route = None; runtime = None
    result = None
    try:
        overlay = harness_runtime.lookup(repo, spec.get('python', '/usr/local/bin/python3'), image=spec['image'], image_digest=spec['image'])
        if overlay:
            mounts.extend((p, str(p)) for p in harness_runtime.extra_paths(overlay))
            env.update(harness_runtime.env_for(overlay))
        if manifest.get('runtime_paths'):
            raise ValueError('observed workers require image dependencies or a configured harness overlay, not host runtime_paths')
        argv = [spec.get('python', '/usr/local/bin/python3'), str(repo / entry), '--task', work + '/task.json', '--workdir', work]
        if os.environ.get('HG_AGENT_BACKEND', 'mock') != 'mock':
            route = ModelRoute(env.get('HG_AGENT_BASE_URL', ''), timeout_s=timeout_s,
                               **worker.get('model_route', {})).start()
            mounts += [(route.path, '/run/harnessgrad-model/model.sock'),
                       (Path(__file__).parents[1] / 'service/native_model_forwarder.py', '/run/harnessgrad-model/forwarder.py')]
            argv = [spec.get('python', '/usr/local/bin/python3'), '-I', '-S', '/run/harnessgrad-model/forwarder.py',
                    '/run/harnessgrad-model/model.sock', route.url_path, '--', *argv]
        binding = {'adapter_id': task['task_id'], 'run_id': run_id or 'direct', 'trial_id': trial_id or 'trial-1',
                   'manifest_sha256': digest({'worker': worker, 'task': task, 'seed': run_seed})}
        runtime = Runtime(spec, binding=binding, timeout_s=timeout_s, mounts=mounts, env=env)
        with runtime:
            proc = runtime.run(argv)
            artifacts = runtime.collect(worker['artifacts'], max_bytes=worker.get('artifact_max_bytes', 16 << 20))
            logs = runtime.collect(['answer.txt', 'trace.jsonl'], max_bytes=8 << 20, truncate=True)
            runtime.receipt['log_files'] = {name: {k: v for k, v in row.items() if k != 'base64'}
                                             for name, row in logs.items()}
            def content(name):
                return base64.b64decode(logs[name].get('base64', '')).decode(errors='replace')
            result = dict(task_id=task['task_id'], answer=content('answer.txt'), trace=content('trace.jsonl'),
                          exit_code=proc.returncode, stderr=proc.stderr, artifacts=artifacts,
                          env_usage={}, identity=None)
    except (Exception, SystemExit) as exc:
        stage_name = 'worker timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'worker container'
        result = runner._invalid(task, exc, stage=stage_name, service=worker['id'])
    finally:
        if route is not None: route.stop()
        if result is not None:
            result['runtime_services'] = {'container': runtime.receipt if runtime else {},
                                          'model': route.report() if route else None}
            if runtime and not runtime.receipt.get('cleanup', {}).get('complete'):
                result['invalid'] = {'stage': 'worker cleanup', 'detail': 'container cleanup incomplete'}
            if route and (route.faults or not route.report()['cleanup']['complete']):
                result['invalid'] = {'stage': 'worker model', 'detail': 'fixed model route failed'}
    return result
