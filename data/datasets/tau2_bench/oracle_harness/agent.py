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
    from tau2_transport import call
    script = work / '.oracle/actions.json'
    sock = '/run/harnessgrad-tool/tool.sock'
    def checked(op, arguments):
        result = call(sock, op, arguments, timeout_s=None, max_line_bytes=None)
        if result.get('error'):
            raise RuntimeError(f'{op}: {result["error"]}')
        return result

    checked('start', {})
    if script.exists():
        for action in json.loads(script.read_text())['actions']:
            # USER actions belong to the official simulator, never the actor.
            if action.get('requestor', 'assistant') != 'assistant':
                raise ValueError('oracle actor cannot execute USER gold actions')
            checked('call', {'tool_name': action['name'], 'arguments': action['arguments']})
    checked('say', {'message': '###STOP###'})
    (work / 'trace.jsonl').write_text(json.dumps({
        'harness_identity': {'harness': 'tau2-control', 'agent_model': 'oracle'},
        'usage': {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}}) + '\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
