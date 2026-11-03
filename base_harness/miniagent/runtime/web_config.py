"""Explicit endpoint configuration shared by web tools and preflight."""

from urllib.parse import urlparse

_config: dict = {}


def configure(config: dict) -> None:
    global _config
    _config = config


class WebToolError(RuntimeError):
    """A configured network tool cannot safely complete its request."""


def endpoint(tool: str) -> str:
    name = {"web_search": "search_url", "read_url": "browse_url"}[tool]
    value = _config.get(name, "")
    if not value:
        raise WebToolError(
            f"{tool} requires an explicit web.{name} endpoint; configure a trusted "
            "search/browse service or remove this tool from agent.yaml"
        )
    parsed = urlparse(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise WebToolError(
            f"web.{name} must be a URL without credentials, query, or fragment"
        )
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise WebToolError(
            f"web.{name} requires HTTPS (HTTP is permitted only for loopback tests)"
        )
    return value


def validate_config(names) -> None:
    for name in names:
        if name in {"web_search", "read_url"}:
            endpoint(name)


def headers() -> dict:
    secret = _config.get("api_key")
    return {"Authorization": "Bearer " + secret} if secret else {}
