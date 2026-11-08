"""Console JSON and size display primitives; no server state."""
from __future__ import annotations
import math

def json_safe(value):
    """JSON 里不允许 NaN/Infinity；它们是"没有数"，转成 null，别让整个响应 500。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def human_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 2**20:
        return f"{n / 2**20:.1f} MB"
    if n >= 2**10:
        return f"{n / 2**10:.1f} KB"
    return f"{n} B"
