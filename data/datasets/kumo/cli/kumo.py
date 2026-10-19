#!/usr/bin/env python3
"""Public KUMO client: show the task, query an action, or write a final answer."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    commands.add_parser("stats")
    commands.add_parser("do").add_argument("action")
    commands.add_parser("submit").add_argument("answer")
    args = parser.parse_args()
    if args.command == "show":
        print(Path("kumo_public.json").read_text())
    elif args.command == "submit":
        Path("answer.txt").write_text(args.answer.strip() + "\n")
        print("Answer saved.")
    elif args.command == "do" or args.command == "stats":
        sys.path.insert(0, "/run/harnessgrad-tool")
        from tool_transport import call
        result = call("/run/harnessgrad-tool/tool.sock", args.command,
                      {"action": args.action} if args.command == "do" else {},
                      timeout_s=None, max_line_bytes=None)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
