#!/usr/bin/env python3
"""Control only: execute released assistant actions through the real private API."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task')
    parser.add_argument('--workdir', required=True)
    args = parser.parse_args()
    work = Path(args.workdir)
    sys.path.insert(0, str(work))
    from tau3_transport import call
    script = work / '.oracle/actions.json'
    sock = '/run/harnessgrad-tool/tool.sock'
    call(sock, 'start', {}, timeout_s=None, max_line_bytes=None)
    if script.exists():
        for action in json.loads(script.read_text())['actions']:
            result = call(sock, 'call', {'tool_name': action['name'], 'arguments': action['arguments']},
                          timeout_s=None, max_line_bytes=None)
            if result.get('error'):
                raise RuntimeError(result['error'])
    call(sock, 'say', {'message': '###STOP###'}, timeout_s=None, max_line_bytes=None)
    (work / 'trace.jsonl').write_text(json.dumps({
        'harness_identity': {'harness': 'tau3-control', 'agent_model': 'oracle'},
        'usage': {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}}) + '\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
