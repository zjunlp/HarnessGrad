#!/usr/bin/env python3
"""CostBench tools: inspect schemas, invoke one tool, submit an assistant turn."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from costbench_transport import call


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['show', 'call', 'turn', 'submit', 'status'])
    parser.add_argument('payload', nargs='?')
    parser.add_argument('arguments', nargs='?', default='{}')
    args = parser.parse_args()
    if args.operation == 'show':
        print(Path('costbench_public.json').read_text())
        return
    if args.operation == 'call':
        payload = {'content': None, 'calls': [{'name': args.payload, 'arguments': args.arguments}]}
    elif args.operation == 'submit':
        payload = {'content': args.payload, 'calls': []}
    elif args.operation == 'turn':
        payload = json.loads(args.payload)
    else:
        payload = {}
    op = 'status' if args.operation == 'status' else 'turn'
    print(json.dumps(call('/run/harnessgrad-tool/tool.sock', op, payload,
                          timeout_s=None, max_line_bytes=None), ensure_ascii=False))


if __name__ == '__main__':
    main()
