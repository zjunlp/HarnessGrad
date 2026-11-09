"""Agent identity checks shared by H0 preparation and recipient runs."""
from __future__ import annotations

import json
import os
from pathlib import Path


def agent_problems(run: Path, point: dict, *, model: str | None,
                   backend: str, endpoint: str, env=None) -> list[str]:
    """Unknown donor facts are not evidence that two agents are comparable."""
    if point.get("shared_from") is not None:
        # The consumer's endpoint does not establish where an inherited score was measured.
        return ["donor point reuses an earlier measurement; specify the original donor "
                "to verify its endpoint identity"]
    try:
        metadata = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["donor run_meta.json is missing or unreadable; agent identity is unknown"]
    config = metadata.get("config") if isinstance(metadata, dict) else None
    agent = config.get("agent") if isinstance(config, dict) else None
    if not isinstance(agent, dict):
        agent = {}
    problems = []
    expected = {"agent_model": model, "agent_backend": backend, "endpoint": endpoint}
    for key, field in (("agent_model", "model"), ("agent_backend", "backend"),
                       ("endpoint", "base_url")):
        if field not in agent or agent[field] != expected[key]:
            problems.append(f"measured identity {key} differs from requested configuration or is missing")
    if point.get("measured_by_platform") is not True:
        problems.append("curve does not verify a platform measurement")
    measured = point.get("identity")
    if not isinstance(measured, dict):
        measured = {}
    if "agent_model" not in measured or measured["agent_model"] != model:
        problems.append("curve identity agent_model differs from requested configuration or is missing")
    if ("agent_backend" not in measured or
            (measured["agent_backend"] != backend and not _opencode_backend_matches(measured, backend, env))):
        problems.append("curve identity agent_backend differs from requested configuration or is missing")
    return problems


def _opencode_backend_matches(measured: dict, backend: str, env) -> bool:
    # OpenCode's npm adapter reports its CLI name as the backend in the trace.
    if (backend != "openai" or measured.get("harness_name") != "opencode"
            or measured.get("agent_backend") != "opencode"):
        return False
    trace = measured.get("harness_identity")
    if (not isinstance(trace, dict) or trace.get("harness") != "opencode-agent"
            or trace.get("agent_backend") != "opencode"):
        return False
    runtime = trace.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("schema") != 3:
        return False
    wire = (os.environ if env is None else env).get("HG_AGENT_OPENCODE_WIRE", "chat")
    return wire in ("chat", "responses") and trace.get("wire") == wire
