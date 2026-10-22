#!/usr/bin/env python3
"""Can this endpoint emit **standard** tool_calls?

    python tools/probe_tool_calls.py --base-url http://127.0.0.1:8002/v1 \
        --model qwen3.5-9b-local --api-key local

Exit 0 and `{"tool_calls": true, ...}` when the model answers a tool definition with a
structured `tool_calls` array; exit 1 with a named reason when it answers with text.

**Why this exists as a gate rather than a convention.** Measured (2026-10-04, and it had
been raised before that): codex pointed at the local 9B *connected*, used 8,699 tokens and
did nothing -- the model wrote its call as `<tool_call><function=exec_command>...` text and
codex, which requires structured calls on `wire_api = "responses"`, executed nothing. The
requirement "pick a model that emits standard tool_calls" was never written down anywhere
(`grep -rn tool_call docs/ INTERFACE.md` returned nothing), so each time it was
rediscovered instead of being enforced.

A model that fails this probe is not a worse model. It is a model that **cannot drive an
agent shell**, and the only thing that distinguishes the two is a check like this one.

Two paths, because they are different code in vLLM and only one of them is what codex
uses: `/chat/completions` (what our own harnesses call) and `/responses` (what codex
0.160 requires). Both are probed; `--wire` picks which one decides the exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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


def _calls_chat(base_url: str, model: str, key: str, timeout: float) -> tuple[bool, str]:
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


def _calls_responses(base_url: str, model: str, key: str, timeout: float) -> tuple[bool, str]:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("HG_AGENT_BASE_URL", ""))
    ap.add_argument("--model", default=os.environ.get("HG_AGENT_MODEL", ""))
    ap.add_argument("--api-key", default=os.environ.get("HG_AGENT_API_KEY", "local"))
    ap.add_argument("--wire", choices=("responses", "chat", "either"), default="either",
                    help="which path decides the exit code (codex needs `responses`)")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()
    if not args.base_url or not args.model:
        print("need --base-url and --model (or HG_AGENT_BASE_URL / HG_AGENT_MODEL)",
              file=sys.stderr)
        return 2

    results = {}
    for name, fn in (("chat", _calls_chat), ("responses", _calls_responses)):
        ok, why = fn(args.base_url, args.model, args.api_key, args.timeout)
        results[name] = {"tool_calls": ok, "detail": why}
    if args.wire == "either":
        verdict = results["chat"]["tool_calls"] or results["responses"]["tool_calls"]
    else:
        verdict = results[args.wire]["tool_calls"]
    print(json.dumps({"model": args.model, "base_url": args.base_url,
                      "tool_calls": verdict, "wire_decides": args.wire,
                      "paths": results}, indent=1, ensure_ascii=False))
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
