#!/usr/bin/env python3
"""Check that a locally served model is usable by HarnessGrad.

Run it against a server started by tools/serve_local_model.sh:

    python3 tools/check_local_model.py --base-url http://127.0.0.1:8002/v1

Why a script rather than "curl /v1/models and call it up": a server that answers
`/v1/models` proves only that the HTTP layer is up. The failures that cost time here
happen later and look like something else:

  * **JSON mode crashes the server.** vLLM 0.6.6 with a newer xgrammar died on the
    first request carrying `response_format={"type":"json_object"}`, killing the whole
    process rather than failing the request. RRSI's search roles request JSON on every
    call, so this is on the critical path, and it is invisible until you send one.
  * **The prompt format the harness uses is not the one you tested.** The harness asks
    for a bare `{"tool": ...}` / `{"answer": ...}` object with no prose, which a
    chat-tuned model will happily decorate.
  * **Context and concurrency limits differ from the defaults** the platform assumes.

So the checks below are the ones the platform actually depends on, in the order it
would hit them.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from openai import OpenAI


DEFAULT_BASE_URL = "http://127.0.0.1:8002/v1"


def banner(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m")


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
    print(f"  {mark} {label}" + (f"  — {detail}" if detail else ""))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--model", default=None,
                    help="served model name; taken from /v1/models if omitted")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    client = OpenAI(base_url=args.base_url, api_key="local", timeout=args.timeout)
    passed = True

    # ---- 1. the server lists a model -----------------------------------------
    banner("1. Server is up")
    try:
        models = [m.id for m in client.models.list().data]
        ok = bool(models)
        passed &= check("GET /v1/models", ok, f"{models}")
    except Exception as exc:                                   # noqa: BLE001
        passed &= check("GET /v1/models", False, f"{type(exc).__name__}: {exc}")
        print("\nthe server is not reachable; nothing else can be tested")
        return 1

    model = args.model or models[0]

    # ---- 2. plain completion, and how fast ------------------------------------
    banner("2. Plain completion (latency is the point of local serving)")
    t0 = time.time()
    try:
        r = client.chat.completions.create(
            model=model, max_tokens=64,
            messages=[{"role": "user", "content": "What is 17*23? Reply with the number only."}])
        dt = time.time() - t0
        text = (r.choices[0].message.content or "").strip()
        passed &= check("answers a question", "391" in text, f"{text[:40]!r} in {dt:.2f}s")
        usage = r.usage
        if usage:
            print(f"      tokens in={usage.prompt_tokens} out={usage.completion_tokens}"
                  f"  ({usage.completion_tokens/max(dt,1e-6):.0f} tok/s)")
    except Exception as exc:                                   # noqa: BLE001
        passed &= check("answers a question", False, f"{type(exc).__name__}: {exc}")

    # ---- 3. JSON mode, which is what RRSI's roles need ------------------------
    banner("3. JSON mode (guided decoding)")
    try:
        r = client.chat.completions.create(
            model=model, max_tokens=128,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content":
                       'Reply with exactly {"verdict": "accept", "reasons": []}'}])
        text = (r.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(text)
            good = isinstance(parsed, dict)
        except json.JSONDecodeError:
            good = False
        passed &= check("response_format=json_object returns parseable JSON", good,
                        f"{text[:60]!r}")
    except Exception as exc:                                   # noqa: BLE001
        passed &= check("response_format=json_object", False,
                        f"{type(exc).__name__}: {exc}")
        print("      NOTE: on some vLLM/xgrammar combinations this kills the whole")
        print("      server, so re-check /v1/models before continuing.")

    # ---- 4. the exact protocol the reference harness speaks -------------------
    banner("4. Harness protocol (the format base_harness/loop asks for)")
    system = ('You are an agent solving a task in a working directory.\n\n'
              'You have one tool: run a shell command.\n\n'
              'Reply with EXACTLY one of:\n'
              '  {"tool": "bash", "command": "<shell command>"}\n'
              '  {"answer": "<your final answer>"}\n\n'
              'Rules:\n- One action per reply. No prose outside the JSON.\n'
              '- To inspect files use shell commands (ls, cat, grep, ...).\n'
              '- Give the final answer only when you are confident.')
    try:
        r = client.chat.completions.create(
            model=model, max_tokens=256, temperature=0.0,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": json.dumps(
                          {"goal": "Answer with the word alpha."})}])
        text = (r.choices[0].message.content or "").strip()
        try:
            action = json.loads(text)
            good = isinstance(action, dict) and ("tool" in action or "answer" in action)
            detail = f"{str(action)[:70]}"
        except json.JSONDecodeError:
            good = False
            detail = f"not JSON: {text[:60]!r} (a chat model often adds prose)"
        passed &= check("returns a single bare action object", good, detail)
    except Exception as exc:                                   # noqa: BLE001
        passed &= check("harness protocol", False, f"{type(exc).__name__}: {exc}")

    # ---- 5. long context, since agent traces grow -----------------------------
    banner("5. Long input (agent conversations get long)")
    filler = "The quick brown fox jumps over the lazy dog. " * 400   # ~4k tokens
    try:
        r = client.chat.completions.create(
            model=model, max_tokens=32,
            messages=[{"role": "user", "content":
                       filler + "\n\nReply with the single word: ok"}])
        text = (r.choices[0].message.content or "").strip().lower()
        passed &= check("handles a ~4k-token prompt", "ok" in text,
                        f"replied {text[:30]!r}, prompt_tokens={r.usage.prompt_tokens}")
    except Exception as exc:                                   # noqa: BLE001
        passed &= check("handles a ~4k-token prompt", False, f"{type(exc).__name__}: {exc}")

    banner("Result")
    if passed:
        print("  all checks passed. Point the platform at this server:")
        print(f"      HG_AGENT_BACKEND=openai")
        print(f"      HG_AGENT_MODEL={model}")
        print(f"      HG_AGENT_BASE_URL={args.base_url}")
        print(f"      HG_AGENT_API_KEY=local")
        return 0
    print("  some checks failed; see above. Do not run a paid experiment until they pass.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
