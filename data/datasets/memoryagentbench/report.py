"""Aggregate verifier evidence against a selected prepared pool without rerunning scoring."""
import argparse
import json
from pathlib import Path

from .lib.reporting import summarize
from .lib.storage import load_pool
from .lib.scoring import JUDGE_SOURCES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-root', required=True, type=Path)
    parser.add_argument('--task-id', action='append')
    parser.add_argument('--evidence', action='append', type=Path, default=[])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    rows = [row for row in load_pool(args.prepared_root)['tasks'] if row['source'] not in JUDGE_SOURCES]
    if args.task_id:
        if len(set(args.task_id)) != len(args.task_id) or set(args.task_id) - {r['task_id'] for r in rows}:
            parser.error('selected task IDs must be known and unique')
        rows = [r for r in rows if r['task_id'] in args.task_id]
    result = summarize([json.loads(path.read_text()) for path in args.evidence], rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
