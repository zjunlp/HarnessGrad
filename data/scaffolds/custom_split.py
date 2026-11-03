"""Explicit experiment partitions; source benchmark declarations remain unchanged.

Only public task IDs and adapter-declared group digests enter these manifests.
Creating or checking a split never materializes SETUP/VERIFY or calls a model.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from data.scaffolds import registry

SCHEMA = 'harnessgrad.custom-split.v1'
CLAIM = 'custom-heldout; not an official benchmark split or leaderboard score'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def task_ids(tasks):
    ids = [t['task_id'] for t in tasks]
    if any(not isinstance(t, str) or not t for t in ids) or len(ids) != len(set(ids)):
        raise ValueError('dataset task IDs must be nonempty unique strings')
    return sorted(ids)


def task_content_sha(tasks):
    return digest(sorted(tasks, key=lambda t: t['task_id']))


def _members(value, label):
    if (not isinstance(value, list) or any(not isinstance(t, str) or not t for t in value)
            or len(value) != len(set(value))):
        raise ValueError(f'{label} must be a list of unique nonempty task IDs')
    return value


def _official(name, tasks):
    try:
        split = registry.official_split(name, tasks)
    except (ValueError, AttributeError):
        return None
    return set(split['eval'])


def _groups(name, tasks):
    return registry.load_split_groups(name, tasks)


def _check_groups(groups, sides):
    if groups is None:
        return
    assigned = {}
    for side, ids in sides.items():
        for tid in ids:
            previous = assigned.setdefault(groups[tid], side)
            if previous != side:
                raise ValueError('related SPLIT_GROUPS tasks cross ' + previous + '/' + side
                    + '; choose a source pool and partition that keep complete groups together')


def create(name, tasks, *, train_ratio=None, train_count=None, seed=0, source='all'):
    """Freeze an order-independent seeded split of all tasks or official eval IDs.

    Counts are exact. Ratios target task counts; indivisible groups use the closest
    nonempty prefix of the seeded group order and report the actual counts.
    """
    ids = task_ids(tasks)
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    if (train_ratio is None) == (train_count is None):
        raise ValueError('choose exactly one of train_ratio or train_count')
    official = _official(name, tasks)
    if source not in ('all', 'official-eval'):
        raise ValueError('source must be all or official-eval')
    if source == 'official-eval' and official is None:
        raise ValueError('dataset has not declared an official evaluation pool; use --source all')
    pool = ids if source == 'all' else [t for t in ids if t in official]
    if len(pool) < 2:
        raise ValueError('a custom train/eval partition needs at least two source tasks')
    if train_count is not None:
        if type(train_count) is not int or not 0 < train_count < len(pool):
            raise ValueError('train_count must be positive and smaller than the source pool')
        target = train_count
    else:
        if (type(train_ratio) not in (int, float) or not math.isfinite(train_ratio)
                or not 0 < train_ratio < 1):
            raise ValueError('train_ratio must be finite and between 0 and 1 exclusively')
        target = max(1, min(len(pool)-1, math.floor(len(pool)*train_ratio + .5)))
    groups = _groups(name, tasks)
    units = {}
    for tid in pool:
        units.setdefault(groups[tid] if groups else tid, []).append(tid)
    if len(units) < 2:
        raise ValueError('source pool has fewer than two independent groups; cannot make disjoint sides')
    order = sorted(units, key=lambda key: (digest(['seeded-group-order-v1', seed, key]), key))
    prefixes = []
    count = 0
    for index, key in enumerate(order[:-1], 1):
        count += len(units[key])
        prefixes.append((count, index))
    actual, boundary = min(prefixes, key=lambda p: (abs(p[0]-target), p[0]))
    if train_count is not None and actual != target:
        raise ValueError(f'exact train_count={target} cannot be met in the seeded whole-group order; '
                         f'nearest count is {actual}. Use --train-ratio or an explicit manifest')
    train = sorted(t for key in order[:boundary] for t in units[key])
    train_set, pool_set = set(train), set(pool)
    split = {'train': train, 'eval': [t for t in pool if t not in train_set]}
    excluded = [t for t in ids if t not in pool_set]
    _check_groups(groups, {**split, 'excluded': excluded})
    manifest = {'schema': SCHEMA, 'dataset': name, 'task_ids_sha256': digest(ids),
        'task_content_sha256': task_content_sha(tasks),
        'official_eval_sha256': digest(sorted(official)) if official is not None else None,
        'split_groups_sha256': digest(groups) if groups is not None else None,
        'source': source, 'recipe': {'algorithm': 'seeded-group-order-v1', 'seed': seed,
            'train_ratio': train_ratio, 'train_count': train_count},
        'split': split, 'excluded': excluded,
        'claim': CLAIM}
    validate(name, tasks, manifest)
    return manifest


def validate(name, tasks, manifest, *, require_nonempty=('train', 'eval')):
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA:
        raise ValueError('unsupported custom split schema')
    expected = {'schema', 'dataset', 'task_ids_sha256', 'task_content_sha256',
                'official_eval_sha256', 'split_groups_sha256', 'source',
                'recipe', 'split', 'excluded', 'claim'}
    if set(manifest) != expected:
        raise ValueError('custom split has missing or unknown manifest fields')
    ids = task_ids(tasks)
    if manifest['dataset'] != name or manifest['task_ids_sha256'] != digest(ids):
        raise ValueError('custom split dataset/task roster differs; regenerate explicitly for this pool')
    if manifest['task_content_sha256'] != task_content_sha(tasks):
        raise ValueError('custom split public task content changed; regenerate explicitly for this pool')
    official = _official(name, tasks)
    if manifest['official_eval_sha256'] != (digest(sorted(official)) if official is not None else None):
        raise ValueError('custom split official evaluation declaration changed; regenerate explicitly')
    groups = _groups(name, tasks)
    if manifest['split_groups_sha256'] != (digest(groups) if groups is not None else None):
        raise ValueError('custom split grouping declaration changed; regenerate explicitly')
    split = manifest['split']
    if not isinstance(split, dict) or set(split) != {'train', 'eval'}:
        raise ValueError('custom split requires train and eval lists')
    sides = {side: _members(split[side], side) for side in ('train', 'eval')}
    excluded = _members(manifest['excluded'], 'excluded')
    all_ids = [*sides['train'], *sides['eval'], *excluded]
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != set(ids):
        raise ValueError('train/eval/excluded must cover every task exactly once, without overlap')
    if manifest['source'] not in ('all', 'official-eval'):
        raise ValueError('custom split source must be all or official-eval')
    if manifest['source'] == 'all' and excluded:
        raise ValueError('source all cannot exclude tasks')
    if manifest['source'] == 'official-eval':
        official = _official(name, tasks)
        if official is None or set(sides['train'] + sides['eval']) != official:
            raise ValueError('custom source pool differs from the declared official evaluation IDs')
    if not isinstance(manifest['recipe'], dict) or manifest['claim'] != CLAIM:
        raise ValueError('custom split recipe/claim is malformed')
    _check_groups(groups, {**sides, 'excluded': excluded})
    selected = set(sides['train'] + sides['eval'])
    return registry.validate_split(name, [t for t in tasks if t['task_id'] in selected], sides,
        require_nonempty=require_nonempty,
        split_groups={t: groups[t] for t in selected} if groups is not None else None)


def read(name, tasks, path, *, side='train'):
    """Read one file once; return split and a self-contained provenance receipt.

    Legacy {train,eval} remains supported. Its metadata also records official
    eval material moved to study. Paths are convenience only; raw SHA and frozen
    manifest contents survive editing or moving the original file.
    """
    path = Path(path)
    raw = path.read_bytes()
    payload = json.loads(raw.decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('split file must be a JSON object')
    if 'schema' in payload:
        split = validate(name, tasks, payload, require_nonempty=(side,))
    else:
        for label, value in payload.items():
            _members(value, label)
        split = registry.validate_split(name, tasks, payload, require_nonempty=(side,),
                                        split_groups=_groups(name, tasks))
    official = _official(name, tasks)
    reused = None if official is None else len(set(split['train']) & official)
    return split, {'kind': 'custom-heldout', 'schema': payload.get('schema', 'legacy-train-eval'),
        'path': str(path.resolve()), 'file_sha256': hashlib.sha256(raw).hexdigest(),
        'task_ids_sha256': digest(task_ids(tasks)), 'manifest': payload,
        'task_content_sha256': task_content_sha(tasks),
        'counts': {**{s: len(v) for s, v in split.items()}, 'excluded': len(payload.get('excluded', []))},
        'official_eval_reused_for_train': reused,
        'official_score_comparable': False}


def resolve(name, tasks, declared, choice, *, side='train'):
    """Same split selection for preflight/preparation and a real driver run."""
    if choice in (None, 'declared'):
        return declared, 'declared' if declared is not None else None, None
    if choice not in ('default', 'official'):
        split, receipt = read(name, tasks, choice, side=side)
        return split, 'file:' + str(Path(choice).resolve()), receipt
    groups = _groups(name, tasks)
    split = registry.default_split(tasks, split_groups=groups) if choice == 'default' else registry.official_split(name, tasks)
    return registry.validate_split(name, tasks, split, require_nonempty=(side,), split_groups=groups), choice, None


def check_eval_pair(train_run, name, split, protocol):
    """Prevent a custom-trained checkpoint from being relabelled official eval.

    A donor run older than `run_meta.json` (the legacy state layout some archived runs
    still use) has no protocol to compare against, and refusing there would break a
    checkpoint evaluation that never used a custom split at all. Missing metadata is
    therefore "no recorded protocol", not a failure; a *recorded* protocol is enforced.
    """
    meta_path = Path(train_run) / 'run_meta.json'
    if not meta_path.is_file():
        if protocol is not None:
            raise ValueError('paired evaluation needs the training run\'s run_meta.json to '
                             'compare the custom split identity against; this donor has none')
        return
    meta = json.loads(meta_path.read_text())
    if meta.get('dataset') != name:
        return  # Cross-dataset transfer retains its existing protocol.
    previous = meta.get('split_protocol')
    if previous is None and protocol is None:
        return
    if (previous is None) != (protocol is None):
        raise ValueError('paired evaluation must reuse the training run split file and custom identity')
    if split is None or meta.get('split_sha') != registry.split_sha(split):
        raise ValueError('paired evaluation must reuse the training run split; '
                         'pass the same --split file (not --split official)')
    if previous is not None and protocol is not None:
        if previous.get('task_content_sha256') != protocol.get('task_content_sha256'):
            raise ValueError('paired evaluation public task content differs from the training run')
        old_manifest, new_manifest = previous['manifest'], protocol['manifest']
        if old_manifest.get('schema') == SCHEMA and new_manifest.get('schema') == SCHEMA:
            for field in ('task_ids_sha256', 'split_groups_sha256', 'official_eval_sha256', 'source', 'excluded'):
                if old_manifest[field] != new_manifest[field]:
                    raise ValueError('paired evaluation split source/group declaration differs: ' + field)
