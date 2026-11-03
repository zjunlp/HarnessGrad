"""Shared tool result type for workspace-provided tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Result returned by a custom workspace tool.

    ``content`` is the string shown to the main LLM. ``metadata`` is kept in
    runtime history and traces for middleware/evaluation; it is not injected
    into the LLM context by the kernel.
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
