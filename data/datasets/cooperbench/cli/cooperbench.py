#!/usr/bin/env python3
"""Fixed-peer CooperBench mailbox and explicit patch submission."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from cooperbench_transport import call as transport_call


def call(op, args):
    return transport_call('/run/harnessgrad-tool/tool.sock', op, args, timeout_s=None, max_line_bytes=None)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    send = commands.add_parser('send_message')
    send.add_argument('recipient', choices=['agent1', 'agent2'])
    send.add_argument('message')
    commands.add_parser('check_messages')
    commands.add_parser('submit')
    args = parser.parse_args()
    if args.command == 'send_message':
        print(json.dumps(call('send', {'to': args.recipient, 'message': args.message})))
    elif args.command == 'check_messages':
        print(json.dumps(call('receive', {})))
    else:
        root = Path(__file__).resolve().parent
        base = json.loads((root / 'public.json').read_text())['base_commit']
        subprocess.run(['git', 'add', '-A'], cwd=root / 'repo', check=True)
        patch = subprocess.check_output(['git', 'diff', '--cached', '--binary', base], cwd=root / 'repo')
        (root / 'agent.patch').write_bytes(patch)
        print(f'Submitted {len(patch)} bytes for {os.environ["HG_WORKER_ID"]}')


if __name__ == '__main__':
    main()
