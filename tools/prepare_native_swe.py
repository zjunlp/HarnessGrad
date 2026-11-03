#!/usr/bin/env python3
"""Explicitly prepare local SWE images; never pull or evaluate benchmark tasks."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.scaffolds.native_research.adapter import Adapter
from data.scaffolds.native_research.swe_native import REVISION, official, prepare_image


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--task-id', action='append', help='native instance id; defaults to all prepared records')
    p.add_argument('--image-python', default='/usr/local/bin/python3')
    args = p.parse_args()
    if args.output.exists():
        p.error('use a fresh runtime declaration; old evidence is never overwritten')
    official('check')
    adapter = Adapter('swe_bench')
    manifest = adapter.manifest()
    instances = {}
    for record in manifest['records']:
        case = adapter.case(record, manifest)
        native_id = case['native_task_id']
        if args.task_id and native_id not in args.task_id:
            continue
        instances[native_id] = prepare_image(case, manifest['files'][record['case']], image_python=args.image_python)
    if not instances or args.task_id and set(args.task_id) != set(instances):
        p.error('one or more selected instance ids were not prepared')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'schema': 'harnessgrad.swe-native-runtime.v1',
        'evaluator_revision': REVISION, 'instances': instances}, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'instances': len(instances), 'network_acquisition': False}))


if __name__ == '__main__':
    main()
