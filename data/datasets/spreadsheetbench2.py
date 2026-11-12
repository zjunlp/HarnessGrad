"""SpreadsheetBench-2 native workbooks and unchanged official accuracy scorer.

This is the v2 Debugging/Financial_Model/Template collection. It is distinct
from SpreadsheetBench v1 verified_400; Visualization requires another evaluator.
Public input workbooks are staged for actors; golden workbooks and comparison
ranges only arrive as trusted verifier inputs after actors exit.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
import json
import os
from pathlib import Path

from harnessgrad.paths import dataset_root
import re

from data.scaffolds.native_harbor import safe_file
from data.scaffolds.scorer_input import bind as bind_scorer_input

KIND = 'dataset'
VERIFIER_KIND = 'command'
ORACLE_SOURCE = 'released'
OFFICIAL_EVAL = None
SPLIT = None
SPLIT_SOURCE = 'none'
DEFAULT_ENV = {'kind': 'files'}
DATASET_ROOT = dataset_root('spreadsheetbench2')
REVISION = '5a2215ed4121945ab09d8723df2995602090b042'
EVALUATOR_REVISION = 'a7bdc2d358e052d35a94768e1f487c0d9e9c0011'
METRIC = 'spreadsheetbench2.official.accuracy'
CATEGORIES = ('Debugging', 'Financial_Model', 'Template')
ASSETS = Path(__file__).with_suffix('')
ARCHIVE = 'spreadsheetbench-v2-09-10.zip'


def records():
    requested = os.environ.get('HG_SPREADSHEETBENCH2_CATEGORY', 'all')
    if requested not in ('all', *CATEGORIES):
        raise ValueError('SpreadsheetBench2 category must be all/Debugging/Financial_Model/Template; Visualization is unsupported')
    root = DATASET_ROOT / 'spreadsheetbench-v2' if (DATASET_ROOT / 'spreadsheetbench-v2').is_dir() else DATASET_ROOT
    rows = []
    seen = set()
    for category in CATEGORIES if requested == 'all' else (requested,):
        base = root / category
        path = base / 'dataset.json'
        if not path.is_file():
            raise FileNotFoundError(f'spreadsheetbench2: prepare native {path}; run python3 data/datasets/spreadsheetbench2/prepare.py --root {DATASET_ROOT}')
        native = json.loads(safe_file(base, 'dataset.json').read_text(encoding='utf-8'))
        if not isinstance(native, list) or not native:
            raise ValueError(f'spreadsheetbench2: {path} must contain a nonempty native record array')
        for row in native:
            fields = ('id', 'instruction', 'spreadsheet_path', 'golden_response_path', 'answer_position')
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in fields):
                raise ValueError(f'spreadsheetbench2: native {category} record missing {fields}')
            if not re.fullmatch(r'[A-Za-z0-9_-]+', row['id']):
                raise ValueError('spreadsheetbench2: invalid native task id')
            tid = f'spreadsheetbench2/{category}/{row["id"]}'
            if tid in seen:
                raise ValueError(f'duplicate spreadsheetbench2 task id: {tid}')
            seen.add(tid)
            rows.append({'task_id': tid, 'category': category, 'row': dict(row),
                         'input': safe_file(base, row['spreadsheet_path']),
                         'golden': safe_file(base, row['golden_response_path']),
                         'output': f'{row["id"]}_output.xlsx'})
    return rows


def load():
    rows = records()
    tasks = [{'task_id': r['task_id'],
              'goal': (r['row']['instruction'] + '\n\nInput workbook: input.xlsx. '
                       f'Save the completed workbook as {r["output"]} in the working directory. '
                       'Use python or python3 with the task-owned openpyxl 3.1.2 to edit it. '
                       'The platform recalculates the workbook and applies the native regression/modification accuracy evaluator after execution.'),
              'submission': {'kind': 'files', 'paths': [r['output']]}} for r in rows]
    return tasks, {r['task_id']: METRIC for r in rows}


def _setup(record):
    return {'files': [{'path': 'input.xlsx', 'from': str(record['input'])}]}


def _verify(record):
    from harnessgrad.dataset_runtime import bind, resolve_execution
    from data.scaffolds.spreadsheet_runtime.metadata import selected_native
    binding = bind(resolve_execution('spreadsheetbench2'), 'verifier')
    native = selected_native()
    native.pop('soffice', None)  # Never execute an external, unbound launcher.
    native_content = json.dumps(native, sort_keys=True, separators=(',', ':'))
    sources = {
        'check.py': ASSETS / 'verify/check.py',
        'evaluation.py': ASSETS / 'verify/evaluation.py',
        'open_spreadsheet.py': ASSETS / 'verify/open_spreadsheet.py',
        'PROVENANCE.json': ASSETS / 'PROVENANCE.json',
        'native.py': Path(__file__).resolve().parents[1] / 'scaffolds/spreadsheet_runtime/native.py',
        'data/scaffolds/scorer_input.py': Path(__file__).resolve().parents[1] / 'scaffolds/scorer_input.py',
    }
    context = {'schema': 'spreadsheetbench2.trusted-context.v1',
               'task_id': record['task_id'], 'python_runtime': binding,
               'native_manifest_sha256': hashlib.sha256(native_content.encode()).hexdigest(),
               'native_identity': {
                   'python': native['python'],
                   'python_sha256': native['files'][native['python']],
                   'soffice_binary': native['soffice_binary'],
                   'soffice_sha256': native['files'][native['soffice_binary']],
                   'uno_bindings': {name: sha for name, sha in native['files'].items()
                                    if Path(name).name in ('uno.py', 'pyuno.so', 'pyuno.pyd')}},
               'sources': {name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for name, path in sources.items()},
               'metric': METRIC}
    native_input = 'original/' + Path(record['row']['spreadsheet_path']).name
    row = {**record['row'], 'spreadsheet_path': native_input, 'golden_response_path': 'golden.xlsx'}
    spec = {'kind': 'command',
            'python_runtime': binding, 'runtime_paths': list(binding['runtime_paths']),
            'argv': [binding['python'], '-I', '-B', 'check.py'],
            'inputs': [{'path': name, 'from': str(path)} for name, path in sources.items()
                       if name != 'data/scaffolds/scorer_input.py'] + [
                       {'path': 'native-runtime.json', 'content': native_content},
                       {'path': 'runtime-context.json', 'content': json.dumps(context, sort_keys=True)},
                       {'path': native_input, 'from': str(record['input'])},
                       {'path': 'golden.xlsx', 'from': str(record['golden'])},
                       {'path': 'record.json', 'content': json.dumps({'task_id': record['task_id'], 'category': record['category'], 'row': row})}],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'timeout_s': 300, 'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
    # bind() stages scorer_input.py itself; bind its exact source too.
    spec['inputs'].append({'path': 'data/scaffolds/scorer_input.py',
                           'from': str(sources['data/scaffolds/scorer_input.py'])})
    bound = bind_scorer_input(spec, record['task_id'],
                              [entry['path'] for entry in spec['inputs']])
    # bind() supplies the same trusted helper; retain one declaration per path.
    bound['inputs'] = list({entry['path']: entry for entry in bound['inputs']}.values())
    return bound


def provision_status():
    from harnessgrad.dataset_runtime import check_execution
    from data.scaffolds.spreadsheet_runtime.native import require_runtime
    execution = check_execution('spreadsheetbench2')
    try:
        root = str(DATASET_ROOT)
    except ValueError:
        root = None
    status = {'root': root, 'present': False, 'tasks': None,
              'execution_runtime': execution}
    try:
        native = require_runtime()
        status['formula_runtime'] = {'present': True, 'status': 'ready',
            'version': native['version'], 'deployment': native['deployment'],
            'limitations': native['limitations']}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        status['formula_runtime'] = {'present': False, 'status': 'unknown',
            'detail': type(exc).__name__,
            'prepare': 'python -m data.scaffolds.spreadsheet_runtime.native prepare --soffice /usr/bin/soffice --python /usr/bin/python3 --directory /absolute/private/cache/spreadsheet-native; set HG_SPREADSHEET_NATIVE_MANIFEST'}
    try:
        rows = records()
        status.update(tasks=len(rows), source_prepared=True, metric=METRIC)
    except (OSError, ValueError) as exc:
        status.update(source_prepared=False, detail=str(exc))
    status['present'] = bool(status.get('source_prepared') and execution['present']
                             and status['formula_runtime']['present'])
    status['prepare_execution'] = 'python tools/prepare_dataset.py --dataset spreadsheetbench2 --runtime execution --python 3.12'
    return status


def downloads():
    return [{'kind': 'hf-dataset', 'name': 'SpreadsheetBench-2 native workbook collection',
             'repo': 'KAKA22/SpreadsheetBench-v2', 'version': REVISION,
             'files': [ARCHIVE], 'dst': '_src', 'size': 'upstream archive size',
             'note': 'Pinned v2 workbook bytes. Run prepare.py to extract only supported categories; no Visualization proxy is loaded.'}]


class _Verifiers(Mapping):
    def __iter__(self):
        return (record['task_id'] for record in records())

    def __len__(self):
        return len(records())

    def __getitem__(self, task_id):
        for record in records():
            if record['task_id'] == task_id:
                return _verify(record)
        raise KeyError(task_id)


def __getattr__(name):
    if name == 'SETUP':
        return {r['task_id']: _setup(r) for r in records()}
    if name == 'VERIFY':
        return _Verifiers()
    if name == 'ENV':
        from data.scaffolds.spreadsheet_runtime.actor import Environments
        return Environments('spreadsheetbench2', records)
    raise AttributeError(name)
