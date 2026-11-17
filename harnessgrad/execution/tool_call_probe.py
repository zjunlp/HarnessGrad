"""Explicit OpenAI-compatible tool-call capability probes; no I/O on import."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

#: One tool, one obvious call. `get_weather` is the shape every provider's docs use, so a
#: failure here is not "the prompt was unusual".
TOOL = [{
    "type": "function",
    "function": {
        "name": "write_note",
        "description": "Write a short note to a file.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "the note"}},
            "required": ["text"],
        },
    },
}]
PROMPT = "Use the write_note tool to write the note 'nine'. Call the tool; do not answer in prose."


def _post(url: str, payload: dict, api_key: str, timeout: float) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:400]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def probe_chat(base_url: str, model: str, key: str, timeout: float) -> tuple[bool, str]:
    code, data = _post(f"{base_url.rstrip('/')}/chat/completions", {
        "model": model, "messages": [{"role": "user", "content": PROMPT}],
        "tools": TOOL, "temperature": 0.0,
    }, key, timeout)
    if code != 200 or not isinstance(data, dict):
        return False, f"chat/completions returned {code}: {str(data)[:200]}"
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    calls = msg.get("tool_calls") or []
    if calls:
        name = ((calls[0].get("function") or {}).get("name")) or "?"
        return True, f"tool_calls[0].function.name={name}"
    return False, ("the reply carried no `tool_calls`; content was "
                   f"{str(msg.get('content'))[:160]!r}")


def probe_responses(base_url: str, model: str, key: str, timeout: float) -> tuple[bool, str]:
    code, data = _post(f"{base_url.rstrip('/')}/responses", {
        "model": model,
        "input": PROMPT,
        "tools": [{"type": "function", **TOOL[0]["function"]}],
        "temperature": 0.0,
    }, key, timeout)
    if code != 200 or not isinstance(data, dict):
        return False, f"responses returned {code}: {str(data)[:200]}"
    items = data.get("output") or []
    for item in items:
        if item.get("type") == "function_call":
            return True, f"function_call name={item.get('name')}"
    kinds = [i.get("type") for i in items if isinstance(i, dict)]
    return False, f"no `function_call` in output (types: {kinds})"
