"""Create and check frozen experiment splits without launching an experiment."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from data.scaffolds import registry
from data.scaffolds import custom_split


def main(argv=None):
    parser = argparse.ArgumentParser(prog='harnessgrad split', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='freeze a seeded ratio/count partition')
    create.add_argument('--dataset', required=True)
    create.add_argument('--source', choices=('all', 'official-eval'), default='all')
    sizes = create.add_mutually_exclusive_group(required=True)
    sizes.add_argument('--train-ratio', type=float)
    sizes.add_argument('--train-count', type=int)
    create.add_argument('--seed', type=int, default=0)
    create.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('check', help='validate a saved split against the current dataset pool')
    check.add_argument('--dataset', required=True)
    check.add_argument('--file', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        tasks, _ = registry.load(args.dataset)
        if args.command == 'create':
            manifest = custom_split.create(args.dataset, tasks, train_ratio=args.train_ratio,
                train_count=args.train_count, seed=args.seed, source=args.source)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Never silently replace the protocol another experiment used.
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            path = args.output
        else:
            path = args.file
        split, receipt = custom_split.read(args.dataset, tasks, path)
        print(json.dumps({'dataset': args.dataset, 'file': str(path.resolve()),
            'split_sha': registry.split_sha(split), 'file_sha256': receipt['file_sha256'],
            **receipt['counts'], 'official_eval_reused_for_train': receipt['official_eval_reused_for_train'],
            'claim': receipt['kind'], 'official_score_comparable': False}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, ImportError) as exc:
        print('split refused: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
