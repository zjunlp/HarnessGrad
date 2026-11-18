"""Explicit method-model snapshots and task limits; credentials stay in memory."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import os
from pathlib import Path
from typing import Mapping

from harnessgrad.config import MethodConfig, load, read_env_file
from harnessgrad.personal.contracts import MAX_BYTES

MODEL_ENV = ('HG_METHOD_BASE_URL', 'HG_METHOD_API_KEY', 'HG_METHOD_MODEL', 'HG_METHOD_TIMEOUT_S')


@dataclass(frozen=True)
class ModelLimits:
    max_calls: int = 12
    execution_seconds: float = 300
    max_revisions: int = 2
    max_input_bytes: int = MAX_BYTES

    def __post_init__(self):
        for value in (self.max_calls, self.max_revisions, self.max_input_bytes):
            if type(value) is not int or value < 1:
                raise ValueError('model limits must be positive integers')
        if (isinstance(self.execution_seconds, bool)
                or not isinstance(self.execution_seconds, (float, int))
                or not math.isfinite(self.execution_seconds) or self.execution_seconds <= 0):
            raise ValueError('model execution limit must be finite and positive')
        object.__setattr__(self, 'execution_seconds', float(self.execution_seconds))

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ModelSettings:
    method: MethodConfig = field(repr=False)
    endpoint: str = field(init=False)

    def __post_init__(self):
        method = self.method
        if (not isinstance(method.model, str) or not method.model.strip()
                or not isinstance(method.base_url, str) or not method.base_url
                or not isinstance(method.api_key, str) or not method.api_key):
            raise ValueError('model configuration needs HG_METHOD_BASE_URL, HG_METHOD_MODEL and HG_METHOD_API_KEY')
        from methods.endpoint_client import validate_config
        endpoint = validate_config(base_url=method.base_url, api_key=method.api_key,
                                   timeout=method.timeout_s)
        if method.api_key in endpoint or method.api_key in method.model:
            raise ValueError('model identity must not contain its credential')
        object.__setattr__(self, 'endpoint', endpoint)

    def identity(self) -> dict:
        return {'endpoint': self.endpoint, 'model': self.method.model,
                'request_timeout_seconds': self.method.timeout_s,
                'credential_ref': 'HG_METHOD_API_KEY',
                'credential_present': bool(self.method.api_key)}


def load_model_settings(env: Mapping[str, str] | None = None, *,
                        env_file: Path | None = None) -> ModelSettings:
    """Use the existing parser on selected fields, never the global run snapshot."""
    source = {}
    if env_file is not None:
        if (env_file.is_symlink() or not env_file.is_file()
                or env_file.stat().st_size > 16384):
            raise ValueError('select one ordinary model environment file up to 16 KiB')
        source.update(read_env_file(env_file))
    source.update(os.environ if env is None else env)
    try:
        method = load({key: source[key] for key in MODEL_ENV if key in source}).method
    except (ValueError, TypeError, OverflowError):
        raise ValueError('invalid selected method model configuration') from None
    return ModelSettings(method)
