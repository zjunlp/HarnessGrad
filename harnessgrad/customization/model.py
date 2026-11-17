"""Explicit, bounded model callback for customization proposals.

The transport is shared with existing methods; no experiment history is read.
Prompts are sent only to the caller-selected endpoint and are not logged here.
"""
from __future__ import annotations

import json
from typing import Any

from methods.endpoint_client import Client, EndpointError

from harnessgrad.improvement.contracts import reject_json_constant, unique_json_object

SYSTEM = ('You are improving a harness from the supplied feedback. Return only one '
          'JSON object that follows the requested schema; do not use Markdown fences.')


class ProposalModel:
    """A JSON-object callback with a limit on logical calls, not HTTP attempts."""

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 max_calls: int, timeout: float = 180):
        if not isinstance(model, str) or not model.strip():
            raise ValueError('model name is required')
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError('max_calls must be a positive integer')
        self._client = Client(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = model
        self.max_calls = max_calls
        self.calls = 0
        self.attempts = 0
        self.observed_usage: list[dict[str, Any]] = []

    def __call__(self, prompt: str) -> dict:
        if self.calls >= self.max_calls:
            raise ValueError('logical model call limit reached')
        self.calls += 1
        try:
            result = self._client.complete(model=self.model, temperature=0.0, messages=[
                {'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': prompt}])
        except EndpointError as exc:
            self.attempts += exc.attempts
            raise
        self.attempts += result.transport_attempts
        usage = result.usage
        self.observed_usage.append({
            'prompt_tokens': getattr(usage, 'prompt_tokens', None),
            'completion_tokens': getattr(usage, 'completion_tokens', None),
            'transport_attempts': result.transport_attempts,
            'unknown_transport_attempts': result.unknown_transport_attempts,
        })
        try:
            value = json.loads(result.choices[0].message.content, object_pairs_hook=unique_json_object,
                               parse_constant=reject_json_constant)
        except (TypeError, ValueError, RecursionError):
            raise ValueError('model reply must be one JSON object') from None
        if not isinstance(value, dict):
            raise ValueError('model reply must be one JSON object')
        return value

    def usage(self) -> dict:
        """Return observed values without treating missing provider usage as zero."""
        return {'logical_calls': self.calls, 'transport_attempts': self.attempts,
                'calls': list(self.observed_usage)}
