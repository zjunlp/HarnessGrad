#!/usr/bin/env python3
"""Create/verify first-five custody receipts; no scorer, model, network or pilot."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
PACKET = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
NAMES = ('fever', 'hotpotqa', 'physicsquestions', 'reasoning_gym',
         'sportsunderstanding', 'spreadsheetbench', 'strategyqa', 'triviaqa')
PRIOR = Path('/mnt/20t/xubuqiang/Study/HarnessGrad/tests/fixtures/hg_wjournalcov')
LIMIT = ('Bounded first-N selection in declared adapter order; NOT a random sample and '
         'NOT representative of the dataset. Establishes only identifiable, hash-bound '
         'selection -> export -> scoring-input custody for these five tasks, not scoring '
         'execution, eligibility, admission or claims about the rest of the pool.')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def receipt(path):
    path = Path(path)
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': sha(raw)}


def code_receipts(name):
    paths = [f'data/datasets/{name}.py', 'data/scaffolds/qa/base.py',
             'data/scaffolds/qa/read.py', 'data/scaffolds/scorer_input.py']
    return [{**receipt(ROOT / p), 'path': p} for p in paths]


def data_path(module, name):
    if name in ('physicsquestions', 'sportsunderstanding', 'strategyqa'):
        return module.DATASET_ROOT / module.CSV_PATH
    if name == 'spreadsheetbench':
        return module.DATASET_ROOT / module.TASKS_JSON
    if name == 'fever':
        return module.DATASET_ROOT / module.VALID
    if name == 'reasoning_gym':
        return module.DATASET_ROOT / module.POOL
    return module.DATASET_ROOT / module.VALIDATION


def prior_binding(name, records, source_rows):
    path = PRIOR / f'{name}-inventory.json'
    if not path.is_file():
        return None
    previous = json.loads(path.read_bytes())
    selected = previous['rows'][:5]
    for i, (old, record) in enumerate(zip(selected, records)):
        assert old['task_id'] == record['task_id'], (name, 'prior task order', i)
        assert old['parsed_row_sha256'] == sha(canonical(source_rows[i])), (name, 'prior row', i)
    return {**receipt(path), 'selected_order_and_row_digests_match': True,
            'whole_inventory_revalidated': False,
            'selected_rows': [{k: r[k] for k in ('task_id', 'loaded_position',
                               'parsed_row_sha256', 'declared_side')} for r in selected]}


def make(name, export_root):
    # Refuse environment overrides, which could silently change the declared source/side.
    overrides = [k for k in os.environ if k.startswith(f'HG_{name.upper()}_')]
    assert not overrides, ('environment override must be unset', overrides)
    module = importlib.import_module(f'data.datasets.{name}')
    source = data_path(module, name)
    source_before = receipt(source)
    spreadsheet = name == 'spreadsheetbench'
    records = module._ensure() if spreadsheet else module._SPEC.ensure()
    assert len(records) >= 5
    selected = records[:5]
    ids = [r['task_id'] for r in selected]
    assert len(set(ids)) == 5
    division = module.SPLIT
    assert set(division['train']).isdisjoint(division['eval'])
    payload = {}
    if spreadsheet:
        source_rows = json.loads(source.read_bytes())[:5]
        payload['rows.json'] = canonical(source_rows) + b'\n'
    elif source.suffix == '.csv':
        # Keep physical CSV row bytes, including embedded newlines and original header.
        raw_lines = source.read_bytes().splitlines(keepends=True)
        reader = csv.DictReader(io.StringIO(b''.join(raw_lines).decode('utf-8'), newline=''))
        reader.fieldnames
        header_end = reader.line_num
        blocks = [b''.join(raw_lines[:header_end])]
        source_rows = []
        while len(source_rows) < 5:
            start = reader.line_num
            row = next(reader)
            end = reader.line_num
            if name == 'physicsquestions' and not str(row.get('input') or '').strip():
                continue
            source_rows.append(row)
            blocks.append(b''.join(raw_lines[start:end]))
        assert source_rows == [r['row'] for r in selected]
        payload['rows.csv'] = b''.join(blocks)
    elif source.suffix == '.jsonl':
        lines = [line for line in source.read_bytes().splitlines(keepends=True) if line.strip()][:5]
        source_rows = [json.loads(line) for line in lines]
        assert source_rows == [r['row'] for r in selected]
        payload['rows.jsonl'] = b''.join(lines)
    else:
        # Parquet row encodings are not independent byte ranges. Export precisely the
        # decoded columns consumed by the adapter, retaining row order and all gold.
        source_rows = [r['row'] for r in selected]
        payload['rows.jsonl'] = b''.join(canonical(row) + b'\n' for row in source_rows)
    tasks = []
    for i, record in enumerate(selected):
        task_id = record['task_id']
        side = 'eval' if task_id in division['eval'] else 'train'
        assert task_id in division[side]
        task = {'task_id': task_id, 'adapter_position': i,
                'source_row_index': i if spreadsheet else record['index'],
                'source_split': 'verified_400' if spreadsheet else record['split'],
                'declared_side': side, 'parsed_row_sha256': sha(canonical(source_rows[i]))}
        if not spreadsheet:
            assert record['gold'] == module._SPEC._gold(source_rows[i])
            spec = module._SPEC.verifier(record)
            binding = spec['scorer_input']
            gold = next(e['content'].encode('utf-8') for e in spec['inputs'] if e['path'] == 'gold.json')
            assert binding['sha256'] == sha(gold)
            assert binding['task_id'] == task_id
            payload[f'inputs/{i}/gold.json'] = gold
            question = record['question'].encode('utf-8')
            goal = module._SPEC._goal(record['question'], record['gold']).encode('utf-8')
            payload[f'inputs/{i}/question.txt'] = question
            payload[f'inputs/{i}/goal.txt'] = goal
            task.update(scorer_input=binding, input_sha256=sha(gold),
                        question_sha256=sha(question), goal_sha256=sha(goal))
            if 'id' in source_rows[i]:
                task['native_row_id'] = str(source_rows[i]['id'])
            if name == 'reasoning_gym':
                task['family'] = source_rows[i]['family']
                task['family_index'] = source_rows[i]['index']
        else:
            assert str(source_rows[i]['id']) == task_id
            spec = module._verifier(record)
            binding = spec['scorer_input']
            assert set(binding['files']) == {'golden.xlsx', 'task.json'}
            for entry in spec['inputs']:
                if entry['path'] in binding['files']:
                    raw = (entry['content'].encode('utf-8') if 'content' in entry
                           else Path(entry['from']).read_bytes())
                    assert sha(raw) == binding['files'][entry['path']]
                    payload[f'inputs/{i}/' + entry['path']] = raw
            init = Path(record['init_path']).read_bytes()
            payload[f'inputs/{i}/workbook.xlsx'] = init
            goal = module._goal(record).encode('utf-8')
            payload[f'inputs/{i}/goal.txt'] = goal
            task.update(scorer_input=binding, input_sha256=binding['sha256'],
                        initial_workbook=receipt(record['init_path']),
                        golden_workbook=receipt(record['golden_path']), goal_sha256=sha(goal))
        tasks.append(task)
    prior = prior_binding(name, selected, source_rows)
    if prior:
        assert [r['declared_side'] for r in prior['selected_rows']] == [r['declared_side'] for r in tasks]
    # Whole source is hashed again after construction; no claim of original producer custody.
    assert receipt(source) == source_before, (name, 'source changed during read')
    digest_map = {p: sha(raw) for p, raw in payload.items()}
    row_export = next(p for p in payload if p.startswith('rows.'))
    result = {'schema_version': 1, 'dataset': name,
              'selection_id': export_root.name + '-' + name,
              'run_id': export_root.name + '-' + name,
              'run_state': 'reserved-selection-only; no execution',
              'selection_kind': 'operator-authorized-first-5-of-full-adapter-order',
              'runtime_run_id': None, 'execution_authorized': False,
              'claim_limit': LIMIT, 'source_revision': getattr(module, 'REVISION', None),
              'adapter_source': code_receipts(name), 'installed_source': source_before,
              'adapter_presented_count': len(records), 'selected_count': 5,
              'eligible_count': None, 'trials_executed': 0, 'measured_count': 0,
              'invalid_count': 0, 'unmeasured_execution_count': 0,
              'unexecuted_selected_count': 5,
              'split_mode': 'declared',
              'split_source': 'hash' if spreadsheet else module.SPLIT_SOURCE,
              'side': 'mixed' if len({r['declared_side'] for r in tasks}) > 1 else tasks[0]['declared_side'],
              'selected_sides': {side: [r['task_id'] for r in tasks if r['declared_side'] == side]
                                 for side in ('train', 'eval')},
              'order_rule': 'adapter full record order, slice [:5] BEFORE any side filter; no sorting/sampling/shuffling',
              'export_path': str(export_root / name), 'row_export': row_export,
              'export_files': digest_map,
              'export_inventory_sha256': sha(canonical(digest_map)),
              'prior_inventory': prior, 'tasks': tasks}
    if name in ('physicsquestions', 'sportsunderstanding', 'strategyqa'):
        result['source_revision'] = 'ReWOO@9cd0283043ff4be0c9d614fda2789d143ca6ffd1'
    if name == 'reasoning_gym':
        result['source_revision'] = 'reasoning-gym==0.1.25; seed=20261008; per_family=20; preserved pool, not regenerated'
        assert source_before['sha256'] == module.POOL_SHA256
    # Recheck independent adapter-declared file pins where present.
    if not spreadsheet:
        for dep in module.downloads():
            pins = dep.get('sha256')
            if isinstance(pins, dict):
                relative = str(source.relative_to(module.DATASET_ROOT))
                if relative in pins:
                    assert source_before['sha256'] == pins[relative]
    return result, payload


def verify_exports(export_root, expected):
    actual = {str(p.relative_to(export_root)) for p in export_root.rglob('*') if p.is_file()}
    assert actual == set(expected), ('extra/missing export file', actual ^ set(expected))
    for relative, digest in expected.items():
        assert not (export_root / relative).is_symlink(), ('symlink export', relative)
        assert sha((export_root / relative).read_bytes()) == digest, ('export mismatch', relative)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create', action='store_true')
    parser.add_argument('--export-root', type=Path)
    args = parser.parse_args()
    if args.create:
        assert args.export_root and args.export_root.is_absolute()
        export_root = args.export_root
        assert not export_root.exists(), 'never overwrite/resume an export'
        assert not export_root.resolve().is_relative_to(ROOT)
    else:
        assert not args.export_root, 'verification uses recorded original export paths'
        export_root = Path(json.loads((PACKET / 'fever.json').read_bytes())['export_path']).parent
    all_digests = {}
    for name in NAMES:
        manifest, payload = make(name, export_root)
        target = PACKET / f'{name}.json'
        if args.create:
            assert not target.exists(), 'never overwrite an old manifest'
            for rel, raw in payload.items():
                p = export_root / name / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open('xb') as stream:
                    stream.write(raw)
            target.write_bytes(json.dumps(manifest, indent=2, ensure_ascii=False).encode() + b'\n')
        else:
            assert json.loads(target.read_bytes()) == manifest, (name, 'recomputed manifest mismatch')
        all_digests.update({name + '/' + p: digest for p, digest in manifest['export_files'].items()})
        print(f"{name}: first5 exact; side={manifest['side']}; scorer-input hashes=5; "
              f"export_files={len(payload)}; prior_selected_match={manifest['prior_inventory'] is not None}")
    verify_exports(export_root, all_digests)
    print(f'PASS: 8 datasets; 40 ordered tasks; {len(all_digests)} exact export files; '
          'no extra files; 0 scoring executions; all blockers remain open')


if __name__ == '__main__':
    main()
