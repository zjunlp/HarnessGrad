#!/usr/bin/env python3
"""Can this endpoint emit **standard** tool_calls?

    python tools/diagnostics/probe_tool_calls.py --base-url http://127.0.0.1:8002/v1 \
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
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessgrad.execution import tool_call_probe as probe  # noqa: E402

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
    for name, fn in (("chat", probe.probe_chat), ("responses", probe.probe_responses)):
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
