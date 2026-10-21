#!/usr/bin/env python3
"""Control only: replay an explicitly staged answer map through the live relay."""
import argparse
import json
from pathlib import Path
import sys
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task')
    parser.add_argument('--workdir', type=Path, required=True)
    args = parser.parse_args()
    work = args.workdir.resolve()
    sys.path.insert(0, str(work))
    from mab_transport import call
    # These paths must exist only after the actor stops. The harness can read
    # control answers solely because the trusted control explicitly stages them.
    assert not (work / 'trusted_episode').exists()
    assert not (work / 'mab_verify').exists()
    control = json.loads((work / '.control.json').read_text())
    for name, content in control.get('actor_preload', {}).items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            path.symlink_to(content['symlink'])
        else:
            path.write_text(content)
    if control['mode'] != 'noop':
        first = subprocess.run([sys.executable, str(work / 'mab'), 'next'], cwd=work,
                               capture_output=True, text=True, check=True)
        metadata = json.loads(first.stdout)
        assert len((work / metadata['path']).read_bytes()) == metadata['bytes']
        while True:
            item = call('/run/harnessgrad-tool/tool.sock', 'next')
            if item['kind'] == 'done':
                break
            if item['kind'] == 'chunk':
                op, fields = 'ack', {'chunk_id': item['chunk_id']}
                identity = item['chunk_id']
            else:
                op, fields = 'submit', {'question_id': item['question_id'],
                    'answer': control['answers'][item['question_id']]}
                identity = item['question_id']
            result = call('/run/harnessgrad-tool/tool.sock', op,
                          dict(fields, request_id=op + '-' + identity))
            assert result.get('accepted'), result
    (work / 'trace.jsonl').write_text(json.dumps({
        'harness_identity': {'harness': 'memoryagentbench-control', 'agent_model': 'oracle'},
        'usage': {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}}) + '\n')


if __name__ == '__main__':
    main()
