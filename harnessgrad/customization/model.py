"""Compatible customization entry point for the shared JSON callback."""
from __future__ import annotations

from harnessgrad.improvement.model import JsonModel

SYSTEM = ('You are improving a harness from the supplied feedback. Return only one '
          'JSON object that follows the requested schema; do not use Markdown fences.')


class ProposalModel(JsonModel):
    """Keep the original customization constructor and default system prompt."""

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 max_calls: int, timeout: float = 180):
        super().__init__(base_url=base_url, api_key=api_key, model=model,
                         max_calls=max_calls, timeout=timeout, system_prompt=SYSTEM)
