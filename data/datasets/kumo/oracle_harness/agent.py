#!/usr/bin/env python3
"""Control only: query the live KUMO relay, then submit the released answer."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task')
    parser.add_argument('--workdir', required=True)
    args = parser.parse_args()
    work = Path(args.workdir).resolve()
    oracle = work / '.oracle' / 'answer.txt'
    if oracle.is_file():
        public = json.loads((work / 'kumo_public.json').read_text())
        subprocess.run([sys.executable, 'kumo.py', 'do', public['actions'][0]],
                       cwd=work, check=True)
        subprocess.run([sys.executable, 'kumo.py', 'submit', oracle.read_text().strip()],
                       cwd=work, check=True)
    (work / 'trace.jsonl').write_text(json.dumps({
        'harness_identity': {'harness': 'kumo-oracle', 'agent_model': 'oracle'},
        'usage': {'input_tokens': 0, 'output_tokens': 0, 'calls': 0},
    }) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
