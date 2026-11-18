"""Explicit JSON-object callback shared by improvement workflows.

The caller owns prompts, endpoint selection and persistent task budgets. Importing
the public improvement package or a pure proposal stage does not load this module.
"""
from __future__ import annotations

import json
from typing import Any

from methods.endpoint_client import Client, EndpointError

from harnessgrad.improvement.contracts import reject_json_constant, unique_json_object


class JsonModel:
    """A JSON-object callback with a limit on logical calls, not HTTP attempts."""

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 max_calls: int, system_prompt: str, timeout: float = 180,
                 client=None, deadline: float | None = None):
        if not isinstance(model, str) or not model.strip():
            raise ValueError('model name is required')
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError('max_calls must be a positive integer')
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError('system prompt is required')
        self._client = client if client is not None else Client(
            base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = model
        self.system_prompt = system_prompt
        self.deadline = deadline
        self.max_calls = max_calls
        self.calls = 0
        self.attempts = 0
        self.observed_usage: list[dict[str, Any]] = []

    def __call__(self, prompt: str) -> dict:
        if self.calls >= self.max_calls:
            raise ValueError('logical model call limit reached')
        self.calls += 1
        options = {'deadline': self.deadline} if self.deadline is not None else {}
        try:
            result = self._client.complete(model=self.model, temperature=0.0, messages=[
                {'role': 'system', 'content': self.system_prompt},
                {'role': 'user', 'content': prompt}], **options)
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
