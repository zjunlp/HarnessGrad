"""Run a local action policy in a replaceable Ray Core actor."""
from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import secrets
import tempfile
import time

from eval.service.action_policy import digest


@contextmanager
def local_ray(*, num_gpus):
    os.environ.update(RAY_AUTH_MODE='token', RAY_AUTH_TOKEN=secrets.token_hex(32),
                      RAY_USAGE_STATS_ENABLED='0')
    import ray

    with tempfile.TemporaryDirectory(prefix='hg-ray-') as directory:
        try:
            ray.init(address='local', include_dashboard=False, num_cpus=1, num_gpus=num_gpus,
                     object_store_memory=128 << 20, _temp_dir=directory, _node_ip_address='127.0.0.1')
            yield ray
        finally:
            ray.shutdown()


class RayPolicyService:
    """Dispatch at most one prediction at a time through a serial Relay."""

    def __init__(self, ray, worker, args, *, num_gpus, inference_timeout_s, recovery_timeout_s):
        self.ray = ray
        self.worker_type = ray.remote(num_cpus=1, num_gpus=num_gpus, max_task_retries=0)(worker)
        self.worker_args = args
        self.inference_timeout_s = inference_timeout_s
        self.recovery_timeout_s = recovery_timeout_s
        self.failure = None
        self._start_worker()
        self.worker_identity = ray.get(self.ready_ref, timeout=recovery_timeout_s)
        self.ready_ref = None
        self.identity = {
            **self.worker_identity,
            'serving_runtime': 'ray-core',
            'serving_version': ray.__version__,
            'serving_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'inference_timeout_s': inference_timeout_s,
            'predict_timeout_s': inference_timeout_s + 5.0,
            'recovery_timeout_s': recovery_timeout_s,
        }
        self.signature = digest(self.identity)

    def _start_worker(self):
        self.worker = self.worker_type.remote(self.worker_args)
        self.ready_ref = self.worker.describe.remote()
        self.ready_deadline = time.monotonic() + self.recovery_timeout_s

    def _replace_worker(self):
        # Submit the next worker only after requesting termination of the old actor.
        self.ray.kill(self.worker)
        self._start_worker()

    def _ready(self):
        if self.failure:
            raise RuntimeError(self.failure)
        if self.ready_ref is None:
            return True
        ready, _ = self.ray.wait([self.ready_ref], timeout=0)
        if not ready and time.monotonic() < self.ready_deadline:
            return False
        try:
            if not ready:
                raise TimeoutError('action policy worker recovery deadline reached')
            if self.ray.get(self.ready_ref) != self.worker_identity:
                raise ValueError('action policy worker identity changed during recovery')
        except Exception:
            self.failure = 'action policy worker failed to recover'
            self.ray.kill(self.worker)
            raise
        self.ready_ref = None
        return True

    def handle(self, op, args):
        if op == 'describe' and not args:
            if self.failure:
                raise RuntimeError(self.failure)
            return self.identity
        if op == 'predict_ready' and not args:
            return {'ready': self._ready()}
        if (op != 'predict' or set(args) != {'identity', 'instruction', 'images', 'state', 'seed'}
                or args['identity'] != self.signature):
            raise ValueError('invalid request or action policy identity changed')
        if not self._ready():
            return {'status': 'recovering'}
        result = self.worker.predict.remote(args)
        try:
            return self.ray.get(result, timeout=self.inference_timeout_s)
        except self.ray.exceptions.GetTimeoutError:
            self._replace_worker()
            return {'status': 'timeout'}
        except self.ray.exceptions.RayActorError:
            self._replace_worker()
            raise
