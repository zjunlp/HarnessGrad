#!/usr/bin/env python3
"""Command-line and Python client for the local PTC-Bench tools."""
import argparse
import json
from ptc_transport import call as _request

_SOCKET = "/run/harnessgrad-tool/tool.sock"


def call(name, /, **arguments):
    return _request(_SOCKET, "call", {"name": name, "arguments": arguments})


def tools():
    return _request(_SOCKET, "tools", {})


def status():
    return _request(_SOCKET, "status", {})


def answer(text):
    return _request(_SOCKET, "answer", {"text": text})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tools")
    commands.add_parser("status")
    response = commands.add_parser("answer")
    response.add_argument("text")
    tool = commands.add_parser("call")
    tool.add_argument("name")
    tool.add_argument("arguments", nargs="?", default="{}")
    args = parser.parse_args()
    if args.command == "call":
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            parser.error("call arguments must be a JSON object")
        result = call(args.name, **arguments)
    elif args.command == "answer":
        result = answer(args.text)
    else:
        result = tools() if args.command == "tools" else status()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
