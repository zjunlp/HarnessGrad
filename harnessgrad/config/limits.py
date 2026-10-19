"""Explicit operator limits, separate from dataset protocol and harness strategy.

No new numeric defaults live here. Dataset adapters opt in, preserving legacy
adapters. A JSON null removes an optional local cap; outer phase cancellation
and runtime isolation still apply. The parsed policy enters config snapshots.
"""
from __future__ import annotations

import copy
import json
import math
import re


FIELDS = {
    'task': {'agent_timeout_s', 'verify_timeout_s'},
    'relay': {'max_calls', 'deadline_s', 'max_line_bytes'},
    'record': {'max_bytes'},
    'submission': {'max_bytes'},
    'judge': {'max_calls', 'max_output_tokens', 'timeout_s', 'max_retries',
              'max_attempts', 'response_max_bytes', 'max_line_bytes'},
    'environment': {'max_calls', 'max_output_tokens', 'timeout_s', 'max_retries',
                    'max_attempts', 'response_max_bytes', 'max_line_bytes'},
    'cooperative': {'max_messages', 'max_message_bytes', 'max_calls', 'max_line_bytes'},
    'model_route': {'max_connections', 'request_max_bytes', 'response_max_bytes', 'header_timeout_s'},
    'container': {'cpus', 'memory_mb', 'pids', 'workspace_bytes', 'workspace_inodes'},
    'preparation': {'cpus', 'memory_mb'},
}
FLOATS = {'agent_timeout_s', 'verify_timeout_s', 'deadline_s', 'timeout_s', 'header_timeout_s', 'cpus'}


def _areas(value):
    if not isinstance(value, dict) or set(value) - FIELDS.keys():
        raise ValueError('HG_EVAL_LIMITS contains an unknown policy area')
    for area, settings in value.items():
        if not isinstance(settings, dict) or set(settings) - FIELDS[area]:
            raise ValueError(f'HG_EVAL_LIMITS invalid {area} fields')
        for key, number in settings.items():
            if number is None:
                if key == 'max_retries':
                    raise ValueError('max_retries must be a nonnegative integer')
                if area == 'container' and key != 'pids':
                    raise ValueError(f'container.{key} requires an explicit positive resource allocation')
                continue
            types = (int, float) if key in FLOATS else (int,)
            if (type(number) not in types or not math.isfinite(number)
                    or number < 0 or (number == 0 and key != 'max_retries')):
                raise ValueError(f'HG_EVAL_LIMITS {area}.{key} must be positive and finite')
            if key == 'workspace_bytes' and number % 4096:
                raise ValueError('workspace_bytes must be page aligned')


def parse(raw):
    if raw is None or raw == '':
        return None
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('HG_EVAL_LIMITS contains duplicate keys')
            value[key] = item
        return value
    try:
        policy = json.loads(raw, object_pairs_hook=unique)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError('HG_EVAL_LIMITS must be a JSON object') from exc
    if not isinstance(policy, dict):
        raise ValueError('HG_EVAL_LIMITS must be a JSON object')
    _areas({k: v for k, v in policy.items() if k != 'datasets'})
    datasets = policy.get('datasets', {})
    if not isinstance(datasets, dict):
        raise ValueError('HG_EVAL_LIMITS datasets must be an object')
    for name, settings in datasets.items():
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise ValueError('HG_EVAL_LIMITS dataset names must be identifiers')
        _areas(settings)
    return policy


def limits(dataset: str, area: str) -> dict:
    """Return a fresh global + dataset policy; candidates never supply this."""
    if area not in FIELDS:
        raise ValueError('unknown evaluation limit area')
    from harnessgrad.config import current
    policy = current().runtime.evaluation_limits or {}
    return copy.deepcopy({**policy.get(area, {}),
                          **policy.get('datasets', {}).get(dataset, {}).get(area, {})})
