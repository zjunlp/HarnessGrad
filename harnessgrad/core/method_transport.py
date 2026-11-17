"""The legacy method JSON exchange, independent of process and workflow policy.

The executor owns isolation, credentials, paths, timeouts and staged evidence.
This module only serializes a supplied request and interprets a completed process;
it does not run a method, expose evidence, validate candidate paths or score work.
"""
from __future__ import annotations

import json
from typing import Any, Protocol


class CompletedMethod(Protocol):
    """The output fields supplied by an executor after a method exits."""

    returncode: int
    stdout: str
    stderr: str


def encode_request(request: dict[str, Any]) -> str:
    """Serialize the caller's request using the existing method wire format."""
    return json.dumps(request)


def decode_response(process: CompletedMethod) -> Any:
    """Interpret an exited method with the existing experiment error semantics.

    A zero exit accepts any JSON value, as the legacy method boundary has always
    done; downstream workflow-specific validation remains the caller's job.
    """
    if process.returncode != 0:
        return {"_error": f"exit {process.returncode}",
                "_stderr": process.stderr[-1500:]}
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        return {"_error": f"non-JSON stdout: {exc}",
                "_stdout": process.stdout[:500], "_stderr": process.stderr[-500:]}
