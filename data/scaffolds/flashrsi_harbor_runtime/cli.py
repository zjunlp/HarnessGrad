#!/usr/bin/env python3
"""Public native-world tools: one persistent task; no scoring operation."""
import argparse
import json
import os
from native_transport import call


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('describe', 'exec', 'finish'))
    parser.add_argument('command', nargs='?')
    args = parser.parse_args()
    if args.operation == 'exec' and args.command is None:
        parser.error('exec requires a shell command')
    value = call(os.environ.get('HG_TOOL_SOCKET', '/run/harnessgrad-tool/tool.sock'),
                 args.operation, {'command': args.command} if args.operation == 'exec' else {},
                 timeout_s=None, max_line_bytes=None)
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
