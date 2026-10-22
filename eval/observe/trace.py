"""Compatibility adapter for the canonical trace reader in `eval.observe.tracing`.

The implementation lives in `eval.observe.tracing`: `first_object` parses model replies and
`loop_facts` summarizes JSON-lines traces. This module re-exports those functions and
the end-state constants for existing callers; it performs no parsing of its own.
New callers should import from `eval.observe.tracing`.

**Expiry: 2026-11-14, one month from the retirement of the previous shim batch**
(`4b99bf4` deleted three re-export shims that had outlived their callers). On that date
either delete this module -- updating whatever still imports it -- or record here why it
is still needed. It is listed so the next cleanup does not have to rediscover it: a shim
with no deadline outlives its reason, and the reason it exists is a caller that was never
migrated. `tests/quality/test_import_direction.py` is the place to add the assertion that
it is gone.
"""
from __future__ import annotations

from eval.observe.tracing import (
    ENDED_ANSWERED,
    ENDED_BUDGET,
    ENDED_CRASHED,
    ENDED_PARSE_ERROR,
    ENDED_UNKNOWN,
    _ended,
    first_object,
    loop_facts,
)

__all__ = ["first_object", "loop_facts", "ENDED_CRASHED", "ENDED_ANSWERED",
           "ENDED_PARSE_ERROR", "ENDED_BUDGET", "ENDED_UNKNOWN"]
