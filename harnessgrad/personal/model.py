"""Thin task-budget assembly around the existing JSON callback and transport."""
from __future__ import annotations

from contextlib import contextmanager
import os
import threading
import time
import uuid

from harnessgrad.personal.contracts import digest, identifier
from harnessgrad.personal.profile import ModelLimits, ModelSettings

SYSTEM = ('Help improve a personal harness using only the supplied, selected evidence. '
          'Return one JSON object matching the requested schema. Distinguish a proposal '
          'from an applied or verified change; ask for missing evidence when needed.')


@contextmanager
def _job_lock(path):
    """Nonblocking OS lock; closing the handle, including on process exit, releases it."""
    if path.is_symlink():
        raise ValueError('model execution lock must be an ordinary file')
    stream = path.open('a+b')
    try:
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError('model execution lock must be single-link')
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('model job already has an executor or its lock is unavailable') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


class ModelSession:
    """One durable job; each context is active execution, with idle time excluded.

    Stage callbacks share the same reservation ledger. Callers exit the context
    while waiting for user input. Recovery charges abandoned active time
    conservatively; it never refunds a request whose outcome is unknown.
    """

    def __init__(self, store, project_id: str, settings: ModelSettings, *,
                 limits: ModelLimits | None = None, profile_id: str = 'default',
                 job_id: str | None = None, fixture: bool = False, client=None,
                 clock=None, wall_clock=None):
        if client is not None and not fixture:
            raise ValueError('an injected model client requires explicit fixture mode')
        self.store = store
        self.settings = settings
        self.limits = limits if limits is not None else ModelLimits()
        self.job_id = store.models.prepare(project_id, profile_id, settings.identity(), self.limits,
                                           job_id=job_id, fixture=fixture)
        self._fixture_client = client
        self._clock = clock if clock is not None else time.monotonic
        self._wall_clock = wall_clock if wall_clock is not None else time.time
        self._owner = None

    def __enter__(self):
        if self._owner is not None:
            raise ValueError('model execution is already active')
        self._lock = _job_lock(self.store.path.with_name('model-' + digest(self.job_id) + '.lock'))
        self._lock.__enter__()
        started = False
        try:
            self._lease = uuid.uuid4().hex
            remaining = self.store.models.start(self.job_id, self._lease, self._wall_clock())
            self._started = self._clock()
            started = True
            self._deadline = self._started + remaining
            from harnessgrad.improvement.model import JsonModel
            method = self.settings.method
            self._callback = JsonModel(base_url=method.base_url, api_key=method.api_key,
                                       model=method.model, timeout=method.timeout_s,
                                       max_calls=self.limits.max_calls, system_prompt=SYSTEM,
                                       deadline=self._deadline, client=self._fixture_client)
            self._owner = threading.get_ident()
            return self
        except BaseException:
            try:
                if started:
                    self.store.models.pause(self.job_id, self._lease,
                                            self._clock() - self._started, failed=True)
            finally:
                self._lock.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.store.models.pause(self.job_id, self._lease, self._clock() - self._started,
                                    failed=exc_type is not None)
        finally:
            self._owner = None
            self._lock.__exit__(exc_type, exc, traceback)

    def _require_execution(self):
        if self._owner != threading.get_ident():
            raise ValueError('enter model execution in the calling thread first')
        if self._clock() >= self._deadline:
            raise ValueError('model execution budget exhausted')

    def __call__(self, prompt: str) -> dict:
        return self._call(prompt, stage='model', system_prompt=SYSTEM)

    def for_stage(self, stage: str, *, system_prompt: str = SYSTEM):
        identifier(stage)
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError('system prompt is required')
        def ask(prompt):
            return self._call(prompt, stage=stage, system_prompt=system_prompt)
        return ask

    def _call(self, prompt: str, *, stage: str, system_prompt: str) -> dict:
        self._require_execution()
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('model prompt must contain text')
        if self.settings.method.api_key in prompt or self.settings.method.api_key in system_prompt:
            raise ValueError('model input must not contain the endpoint credential')
        size = len(prompt.encode('utf-8')) + len(system_prompt.encode('utf-8'))
        if size > self.limits.max_input_bytes:
            raise ValueError('model input exceeds the task byte limit')
        seq = self.store.models.reserve(self.job_id, self._lease, stage,
                                        digest({'stage': stage, 'system': system_prompt, 'prompt': prompt}),
                                        size, self._wall_clock())
        callback = self._callback
        callback.system_prompt = system_prompt
        prior = len(callback.observed_usage)
        from methods.endpoint_client import EndpointError
        try:
            value = callback(prompt)
        except BaseException as exc:
            usage = self._observation(prior)
            kind, status = 'interrupted', 'unknown'
            if isinstance(exc, EndpointError):
                usage = {'prompt_tokens': None, 'completion_tokens': None,
                         'transport_attempts': exc.attempts, 'unknown_transport_attempts': exc.attempts}
                kind, status = 'transport', 'failed'
            elif isinstance(exc, ValueError) and len(callback.observed_usage) > prior:
                kind, status = 'json', 'failed'
            self.store.models.finish(self.job_id, self._lease, seq, usage,
                                     status=status, failure_kind=kind)
            raise
        usage = self._observation(prior)
        expired = self._clock() >= self._deadline
        self.store.models.finish(self.job_id, self._lease, seq, usage,
                                 status='failed' if expired else 'completed',
                                 failure_kind='deadline' if expired else None)
        if expired:
            raise ValueError('model execution budget exhausted')
        return value

    def _observation(self, prior):
        if len(self._callback.observed_usage) > prior:
            return dict(self._callback.observed_usage[-1])
        return {key: None for key in ('prompt_tokens', 'completion_tokens',
                                     'transport_attempts', 'unknown_transport_attempts')}

    def reserve_revision(self) -> int:
        self._require_execution()
        return self.store.models.reserve_revision(self.job_id, self._lease)

    def usage(self) -> dict:
        return self.store.models.usage(self.job_id)
