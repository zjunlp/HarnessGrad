#!/usr/bin/env python3
"""Control only: execute a private gold script supplied solely by control.py."""
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
    from costbench_transport import call
    script = work / '.oracle/actions.json'
    if script.exists():
        oracle = json.loads(script.read_text())
        for action in oracle['actions']:
            result = call('/run/harnessgrad-tool/tool.sock', 'turn',
                          {'content': None, 'calls': [action]}, timeout_s=None, max_line_bytes=None)
            if result.get('error'):
                raise RuntimeError(result['error'])
        call('/run/harnessgrad-tool/tool.sock', 'turn',
             {'content': '<answer> ' + oracle['answer'] + ' </answer>', 'calls': []},
             timeout_s=None, max_line_bytes=None)
    (work / 'trace.jsonl').write_text(json.dumps({
        'harness_identity': {'harness': 'costbench-control', 'agent_model': 'oracle'},
        'usage': {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}}) + '\n')


if __name__ == '__main__':
    main()
