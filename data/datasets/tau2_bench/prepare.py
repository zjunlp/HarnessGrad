#!/usr/bin/env python3
"""Prepare the pinned τ² v0.1.0 **telecom** payload; never execute a model request.

    python3 data/datasets/tau2_bench/prepare.py --root /mnt/20t/xubuqiang/datasets/tau2-bench

Why a prepared payload at all, when v0.1.0 ships plain JSON: because the payload is what separates
**what the actor may see** from **what the verifier sees**. `public.json` is copied into the task
workspace; `private.json` (the user's scenario, the ticket and every evaluation criterion) is
handed only to the trusted runtime and the verifier. Preparing once, with digests recorded, is also
what makes a run reproducible after the upstream checkout is gone.

The split is **declared here, not upstream**: v0.1.0 predates `split_tasks.json` (that file arrives
with the τ³ release), so it has no official train/test division for these 114 tasks. The rule below
is deterministic and salted, so it cannot be cherry-picked: `dev` is 10 %, `eval` is 30 %, and the
rest is `train`. Re-running this script on the same bytes produces the same split.

This script is deliberately stdlib-only and runs on the platform's Python 3.12: it copies source and
reads `tasks.json`, but it does not import `tau2`, which needs 3.13 and the isolated runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

SCHEMA = 'hg-tau2-payload.v1'
REVISION = 'v0.1.0'
ARCHIVE_SHA256 = 'ddee4dc5655912c8a7c7306107714c1936c86a5c4884ff4c50f00d6c828c3377'
DOMAIN = 'telecom'
EXPECTED_TASKS = 114
EXPECTED_TASKS = 114


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def prepare(source: Path, target: Path) -> int:
    archive = source.parent / '_src' / 'tau2-bench-v0.1.0.tar.gz'
    if not archive.is_file():
        raise SystemExit(f'no pinned archive at {archive}')
    if digest(archive) != ARCHIVE_SHA256:
        raise SystemExit('the archive is not the pinned τ² v0.1.0 tarball')
    tree = source / 'data' / 'tau2' / 'domains' / DOMAIN
    tasks_path = tree / 'tasks.json'
    if not tasks_path.is_file():
        raise SystemExit(f'no {tasks_path}')
    rows = json.loads(tasks_path.read_text(encoding='utf-8'))
    if len(rows) != EXPECTED_TASKS:
        raise SystemExit(f'{DOMAIN} has {len(rows)} tasks, expected {EXPECTED_TASKS}')
    ids = [row['id'] for row in rows]
    if len(set(ids)) != len(ids):
        raise SystemExit('duplicate task ids in the pinned base split')

    if (target / 'manifest.json').is_file():
        raise SystemExit(f'{target} already holds a payload; use a fresh root')
    target.mkdir(parents=True, exist_ok=True)
    bundle = target / 'bundle'
    if bundle.exists():
        raise SystemExit(f'{bundle} exists; use a fresh root')
    shutil.copytree(source / 'src', bundle / 'src',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(source / 'LICENSE', bundle / 'LICENSE')
    shutil.copytree(source / 'data' / 'tau2' / 'user_simulator',
                    bundle / 'data' / 'tau2' / 'user_simulator',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # The domain's databases and policy are runtime inputs, not answers: `tasks*` and any result
    # files stay out, so a private scenario cannot be read from the bundle.
    shutil.copytree(tree, bundle / 'data' / 'tau2' / 'domains' / DOMAIN,
                    ignore=shutil.ignore_patterns('tasks*', 'results*', '__pycache__', '*.pyc'))

    # **No official split, so every task is study material.** v0.1.0's base split is all 278 tasks
    # with no train/test division (only v1.0.0 and later ship `split_tasks.json`), and a hash cut of
    # a benchmark's evaluation set is not a held-out exam. The platform therefore treats telecom as
    # 114 train tasks; if the official 278-task number is ever wanted, that is an explicit
    # full-set evaluation, not a split this payload invents.
    counts = {'train': 0, 'dev': 0, 'eval': 0}
    tasks = []
    for index, row in enumerate(rows):
        task_id = f'tau2_bench/{DOMAIN}/{index}'
        side = 'train'
        counts[side] += 1
        public = {'task_id': task_id, 'domain': DOMAIN, 'upstream_id': row['id']}
        criteria = row.get('evaluation_criteria') or {}
        private = dict(row)
        path = target / 'cases' / str(index)
        path.mkdir(parents=True, exist_ok=True)
        (path / 'public.json').write_text(
            json.dumps(public, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
        (path / 'private.json').write_text(
            json.dumps(private, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
        tasks.append({
            'task_id': task_id, 'upstream_id': row['id'], 'domain': DOMAIN, 'split': side,
            'path': f'cases/{index}',
            'public_sha256': digest(path / 'public.json'),
            'private_sha256': digest(path / 'private.json'),
            # What the metric needs, counted here so the adapter does not have to trust a docstring.
            'n_actions': len(criteria.get('actions') or []),
            'n_env_assertions': len(criteria.get('env_assertions') or []),
            'n_nl_assertions': len(criteria.get('nl_assertions') or []),
            'n_communicate_info': len(criteria.get('communicate_info') or []),
            'reward_basis': list(criteria.get('reward_basis') or []),
            'needs_judge': bool(criteria.get('nl_assertions')),
        })
    bundle_files = {str(p.relative_to(bundle)): digest(p)
                    for p in sorted(bundle.rglob('*')) if p.is_file()}
    manifest = {
        'schema': SCHEMA, 'revision': REVISION, 'domain': DOMAIN,
        'archive_sha256': ARCHIVE_SHA256,
        'tasks_json_sha256': digest(tasks_path),
        'bundle_sha256': text_digest(json.dumps(bundle_files, sort_keys=True)),
        'bundle_files': len(bundle_files),
        'split_rule': {'kind': 'none', 'reason': 'v0.1.0 publishes no train/test division'},
        'counts': counts, 'tasks': tasks,
    }
    (target / 'manifest.json').write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'tasks': len(tasks), 'counts': counts,
                      'manifest_sha256': digest(target / 'manifest.json')}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', default=os.environ.get('HG_TAU2_DATA'))
    parser.add_argument('--source', default=None,
                        help='extracted tau2-bench-0.1.0 tree (default: <root>/tau2-bench-0.1.0)')
    args = parser.parse_args()
    if not args.root:
        raise SystemExit('pass --root or set HG_TAU2_DATA')
    root = Path(args.root)
    source = Path(args.source) if args.source else root / 'tau2-bench-0.1.0'
    return prepare(source, root)


if __name__ == '__main__':
    sys.exit(main())
