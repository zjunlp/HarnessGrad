#!/usr/bin/env python3
"""Explicit, offline-first preparation of pinned upstream native research records.

JSON --config is the native benchmark configuration. A data_path is required unless
--fetch is supplied. Source records/gold never enter task.json or actor SETUP.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.scaffolds.native_research.adapter import canonical
from data.scaffolds.native_research.native import activate, benchmark
from data.scaffolds.native_research.profiles import COMMIT, PROFILES


def prepare(name, output, config, *, fetch=False, judge_model=None):
    if not isinstance(config, dict):
        raise ValueError('native configuration must be an object')
    if not fetch and not config.get('data_path'):
        raise ValueError('provide native data_path; network acquisition requires explicit --fetch')
    if any(k.startswith('evolution') or k in ('split', 'split_counts', 'limit', '_prepared_splits') for k in config):
        raise ValueError('preparation preserves the complete native source split; select tasks in driver instead')
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('use a fresh output directory; never replace prepared evidence')
    native = benchmark(name, config)
    original_source = dict(type(native).metadata)
    wide_gold = {}
    if name == 'widesearch':
        # Capture the original CSV after the native loader has validated its ID
        # and read it. Reuse this iteration (including an explicit fetch), rather
        # than downloading twice or serializing the parsed DataFrame lossily.
        native_records = native._records
        def records_with_gold():
            for split, row, gold_root in native_records():
                yield split, row, gold_root
                task_id = row['instance_id']
                raw = (gold_root / (task_id + '.csv')).read_bytes()
                if hashlib.sha256(raw).hexdigest() != native._rows_by_id[task_id]['_gold_sha256']:
                    raise ValueError('WideSearch native gold CSV changed during preparation')
                wide_gold[task_id] = raw
        native._records = records_with_gold
    tasks = list(native._iter_tasks())
    if not tasks:
        raise ValueError('native source contains no eligible tasks')
    ids = [t.task_id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError('native source has duplicate task IDs')
    # Validate supplemental worlds once and record their bytes before publishing.
    extra = {}
    if name == 'travelplanner':
        from flashrsi.integrations._travelplanner_grade import check_database
        database = Path(config.get('database_path', ''))
        if not config.get('database_path') or not database.is_dir():
            raise ValueError('TravelPlanner requires database_path with all six native database files')
        extra['database_checksum'] = check_database(database)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    rows = []
    files = {}
    if name == 'workbench':
        source_root = native._upstream_root
        for directory in ('src', 'data'):
            origin = source_root / directory if directory == 'src' else native._data_root
            for path in sorted(origin.rglob('*')):
                if not path.is_file() or '__pycache__' in path.parts:
                    continue
                if path.is_symlink():
                    raise ValueError('native WorkBench worlds cannot contain linked files')
                relative = 'world/' + directory + '/' + path.relative_to(origin).as_posix()
                dest = output / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
                files[relative] = hashlib.sha256(dest.read_bytes()).hexdigest()
        extra['world_sha256'] = hashlib.sha256(canonical(files).encode()).hexdigest()
    for task in tasks:
        # Native IDs are data, not storage paths. Preserve them privately while
        # using a stable content name for platform progress/verdict artifacts.
        tid = name + '/' + hashlib.sha256(task.task_id.encode()).hexdigest()
        case = {'task_id': tid, 'native_task_id': task.task_id, 'question': task.question,
                'row': native._rows_by_id[task.task_id], 'metadata': dict(task.metadata),
                'upstream_commit': COMMIT, 'source': original_source, **extra}
        if name == 'harvey_lab':
            from data.scaffolds.native_research.harvey_native import prepare_resources
            case.update(prepare_resources(native, task, output, files))
        if name == 'widesearch':
            import base64
            q = native._queries_by_id[task.task_id]
            case['wide_query'] = {'schema': 'harnessgrad.widesearch-query.csv.v1',
                'instance_id': q.instance_id, 'query': q.query, 'evaluation': q.evaluation,
                'gold_csv_base64': base64.b64encode(wide_gold[task.task_id]).decode('ascii'),
                'language': q.language}
        rel = 'cases/' + hashlib.sha256(tid.encode()).hexdigest() + '.json'
        raw = canonical(case).encode()
        target = output / rel
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)
        files[rel] = hashlib.sha256(raw).hexdigest()
        rows.append({'task_id': tid, 'case': rel, 'source_split': task.metadata.get('source_split', 'unassigned')})
    if name == 'travelplanner':
        from flashrsi.integrations._travelplanner_grade import DATABASE_FILES
        for relative in DATABASE_FILES:
            dest = output / 'database' / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(config['database_path']) / relative, dest)
            files[str(dest.relative_to(output))] = hashlib.sha256(dest.read_bytes()).hexdigest()
    manifest = {'schema': 'harnessgrad.native-research.v1', 'dataset': name,
        'upstream_commit': COMMIT, 'source': original_source, 'source_projection': dict(native.metadata),
        'input_origin': {'mode': 'local-declared' if config.get('data_path') else 'pinned-source-fetch',
                         'authenticated_release': not bool(config.get('data_path')),
                         'note': 'Local file hashes bind actual inputs; they do not authenticate an upstream release.'},
        'configuration': {k: v for k, v in config.items() if k not in ('judge',)},
        'judge_model': judge_model, 'records': rows, 'files': files}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', choices=PROFILES)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--judge-model')
    args = parser.parse_args()
    value = prepare(args.dataset, args.output, json.loads(args.config.read_text()),
                    fetch=args.fetch, judge_model=args.judge_model)
    print(json.dumps({'dataset': args.dataset, 'tasks': len(value['records']), 'output': str(args.output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
