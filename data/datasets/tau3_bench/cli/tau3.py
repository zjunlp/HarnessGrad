#!/usr/bin/env python3
"""Actor CLI: policy/tool schemas and the four permitted conversation operations."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tau3_transport import call


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['show', 'start', 'say', 'call', 'calls', 'status'])
    parser.add_argument('payload', nargs='?')
    parser.add_argument('arguments', nargs='?', default='{}')
    args = parser.parse_args()
    if args.operation == 'show':
        print(Path('tau3_public.json').read_text())
        return
    payload = {'message': args.payload} if args.operation == 'say' else (
        {'tool_name': args.payload, 'arguments': json.loads(args.arguments)} if args.operation == 'call' else {})
    if args.operation == 'calls':
        payload = {'tool_calls_json': args.payload}
    print(json.dumps(call('/run/harnessgrad-tool/tool.sock', args.operation, payload), ensure_ascii=False))

if __name__ == '__main__':
    main()
