"""Agent configuration facts and launch refusal policy, with no process effects."""
from __future__ import annotations
from harnessgrad.config import AgentConfig


def describe_agent(agent: AgentConfig) -> dict:
    backend = agent.backend
    model = agent.model
    has_key = bool(agent.api_key)
    return {
        "backend": backend,
        "model": model,
        "credentials": has_key,
        "live": backend != "mock" and bool(model) and has_key,
    }


def agent_refusal(model: dict, *, load_improvers, forbidden_reason) -> str | None:
    banned = forbidden_reason(load_improvers(), model.get("model") or "")
    if banned:
        return f"refusing to run: {banned}"
    if model["backend"] != "mock" and not model["live"]:
        missing = [n for n, ok in (("HG_AGENT_MODEL", model["model"]),
                                   ("HG_AGENT_API_KEY", model["credentials"]))
                   if not ok]
        return (f"backend is {model['backend']!r} but {', '.join(missing)} "
              f"{'is' if len(missing) == 1 else 'are'} unset; every score would "
              f"come from a harness that cannot reach a model. See .env.example."
              )

    return None
