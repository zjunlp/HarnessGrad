"""Console settings persistence and credential-safe presentation.

Paths and environment are caller inputs. Keep the historical file encoding,
merge order, write/chmod order and exception behavior; this is not an atomic-write
migration. No service starts and no filesystem root is created on import/read.
"""
from __future__ import annotations
import json
import urllib.request
from urllib.parse import urlparse
if __package__:
    from .record_reader import read_json
else:
    from record_reader import read_json

SETTINGS_SIDES = ('task', 'meta', 'assistant')
SETTINGS_ALIASES = {'chat': 'meta'}


class SettingsStore:
    def __init__(self, path, legacy_path, environment):
        self.path = path
        self.legacy_path = legacy_path
        self.environment = environment

    def read(self) -> dict:
        """Load endpoint roles using saved, legacy-meta and task-environment precedence.

        Callers producing UI data must use redacted rather than serialize credentials.
        """
        data = read_json(self.path)
        chat_fallback = read_json(self.legacy_path)
        out = {}
        for side in SETTINGS_SIDES:
            block = dict(data.get(side) or {})
            for alias, canonical in SETTINGS_ALIASES.items():
                if canonical == side and not block.get("base_url"):
                    block = {**block, **(data.get(alias) or {})}
            if side == "meta" and not block.get("base_url"):
                block = {**chat_fallback, **{k: v for k, v in block.items() if v}}
            out[side] = {
                "base_url": str(block.get("base_url") or "").strip(),
                "api_key": str(block.get("api_key") or "").strip(),
                "model": str(block.get("model") or "").strip(),
            }
        out["task"]["base_url"] = out["task"]["base_url"] or self.environment.get(
            "HG_CONSOLE_TASK_BASE_URL", "").strip()
        out["task"]["api_key"] = out["task"]["api_key"] or self.environment.get(
            "HG_CONSOLE_TASK_API_KEY", "").strip()
        return out


    def write(self, payload: dict, *, current: dict) -> dict:
        """Write what the form sent, keeping a value the form left blank.

        A blank field means "do not change it" -- a UI that echoed the redacted key back would
        otherwise erase the key every time the operator changed only the model name.
        """
        merged = {}
        for side in SETTINGS_SIDES:
            # Accept legacy aliases while persisting only canonical endpoint roles.
            incoming = {key: value for alias, canonical in SETTINGS_ALIASES.items()
                        if canonical == side
                        for key, value in (payload.get(alias) or {}).items()}
            incoming.update(payload.get(side) or {})
            new_url = str(incoming.get('base_url') or '').strip()
            if (new_url and new_url != current[side]['base_url'] and current[side]['api_key']
                    and not str(incoming.get('api_key') or '').strip()):
                raise ValueError(f'{side}: changing base_url requires an explicit API key')
            block = dict(current[side])
            for key in ("base_url", "api_key", "model"):
                value = str(incoming.get(key) or "").strip()
                if value:
                    block[key] = value
            merged[side] = block
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(merged, indent=1) + "\n", encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return merged


def display_base_url(value: str) -> str:
    """Only display conventional endpoints; arbitrary paths can themselves be secrets."""
    try:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            return ""
        parsed = urlparse(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment or "?" in value or "#" in value or
                parsed.path not in ("", "/", "/v1", "/v1/")):
            return ""
        return value
    except ValueError:
        return ""


def redacted(data) -> dict:
    """Never serialize credentials or potentially secret endpoint components."""
    return {side: {"base_url": display_base_url(data[side]["base_url"]),
                   "base_url_set": bool(data[side]["base_url"]),
                   "base_url_redacted": bool(data[side]["base_url"] and not display_base_url(data[side]["base_url"])),
                   "model": data[side]["model"], "api_key_set": bool(data[side]["api_key"])}
            for side in SETTINGS_SIDES}


def probe_endpoint(base_url: str, api_key: str) -> dict:
    """`GET {base_url}/models` -- the cheapest question that distinguishes "wrong URL" from "no key"."""
    if not base_url:
        return {"ok": False, "detail": "base_url 为空"}
    url = base_url.rstrip("/") + "/models"
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {api_key or 'none'}",
            "User-Agent": "codex_cli_rs/0.1 (harnessgrad-console)"})
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:  # Transport exceptions may contain URLs, headers, or credentials.
        return {"ok": False, "detail": "Endpoint probe failed; check the URL and server configuration."}
    names = [row.get("id") for row in (body.get("data") or []) if isinstance(row, dict)]
    # Provider-controlled IDs can echo credentials; the UI only needs the count.
    return {"ok": True, "models": [], "count": len(names)}
