#!/usr/bin/env python3
"""HTTP client for the task-bound tau service, staged as ``/app/tau``.

The service owns both database and episode recording. Local db.json and
tau_calls.jsonl files are never grading inputs. Says and termination count only
when accepted by the service, which rejects actions after the first termination.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

USAGE = """tau -- act on the tau-bench service
  ./tau list                       list tools and argument schemas
  ./tau call <tool_name> '<json>'  call a tool and print its observation
  ./tau say '<text>'               tell the user something (no reply)
  ./tau done                       finish the episode
  ./tau policy                     print the policy
  ./tau show                       print the current service database
"""


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    command, rest = argv[0], argv[1:]
    try:
        base = os.environ["HG_SVC_TAU_URL"].rstrip("/")
        if command in ("list", "policy", "show"):
            request = urllib.request.Request(base + "/" + command)
        else:
            payload = {"command": command}
            if command == "call":
                if len(rest) < 2:
                    raise ValueError("usage: ./tau call <tool_name> '<json>'")
                raw = " ".join(rest[1:])
                payload.update(name=rest[0], arguments=json.loads(
                    sys.stdin.read() if raw.strip() == "-" else raw))
            elif command == "say":
                payload["content"] = " ".join(rest).strip()
            elif command != "done":
                raise ValueError(f"unknown command {command!r}")
            request = urllib.request.Request(
                base + "/act", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            output = json.load(response)["output"]
        print(output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"Error: service refused request: {exc.read().decode()}", file=sys.stderr)
    except (KeyError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
