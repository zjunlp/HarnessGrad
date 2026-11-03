"""SpreadsheetBench-2 native workbooks and unchanged official accuracy scorer.

This is the v2 Debugging/Financial_Model/Template collection. It is distinct
from SpreadsheetBench v1 verified_400; Visualization requires another evaluator.
Public input workbooks are staged for actors; golden workbooks and comparison
ranges only arrive as trusted verifier inputs after actors exit.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

from data.scaffolds import roots
import re
import shutil

from data.scaffolds.native_harbor import safe_file
from data.scaffolds.scorer_input import bind as bind_scorer_input

KIND = 'dataset'
ORACLE_SOURCE = 'released'
OFFICIAL_EVAL = None
SPLIT = None
SPLIT_SOURCE = 'none'
DEFAULT_ENV = {'kind': 'files'}
DATASET_ROOT = roots.dataset_root('spreadsheetbench2',
                              legacy=os.environ.get('HG_SPREADSHEETBENCH2_DATA', '/mnt/20t/xubuqiang/datasets/spreadsheetbench2'))
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
                       'The platform recalculates the workbook and applies the native regression/modification accuracy evaluator after execution.'),
              'submission': {'kind': 'files', 'paths': [r['output']]}} for r in rows]
    return tasks, {r['task_id']: METRIC for r in rows}


def _setup(record):
    return {'files': [{'path': 'input.xlsx', 'from': str(record['input'])}]}


def _verify(record):
    # Preserve the original filename: native Debugging modes inspect Color /
    # Embedded in spreadsheet_path. The original/golden are trusted private copies.
    native_input = 'original/' + Path(record['row']['spreadsheet_path']).name
    row = {**record['row'], 'spreadsheet_path': native_input, 'golden_response_path': 'golden.xlsx'}
    spec = {'kind': 'command', 'argv': ['python3', 'spreadsheetbench2-check/check.py'],
            'inputs': [{'path': 'spreadsheetbench2-check/check.py', 'from': str(ASSETS / 'verify/check.py')},
                       {'path': 'spreadsheetbench2-check/evaluation.py', 'from': str(ASSETS / 'verify/evaluation.py')},
                       {'path': 'spreadsheetbench2-check/open_spreadsheet.py', 'from': str(ASSETS / 'verify/open_spreadsheet.py')},
                       {'path': 'spreadsheetbench2-check/PROVENANCE.json', 'from': str(ASSETS / 'PROVENANCE.json')},
                       {'path': native_input, 'from': str(record['input'])},
                       {'path': 'golden.xlsx', 'from': str(record['golden'])},
                       {'path': 'record.json', 'content': json.dumps({'task_id': record['task_id'], 'category': record['category'], 'row': row})}],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'timeout_s': 180, 'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
    return bind_scorer_input(spec, record['task_id'], [native_input, 'golden.xlsx', 'record.json'])


def provision_status():
    status = {'root': str(DATASET_ROOT), 'present': False, 'tasks': None,
              'libreoffice': shutil.which('soffice') or shutil.which('libreoffice')}
    try:
        rows = records()
        packages = {name: importlib.metadata.version(name) for name in ('openpyxl', 'tqdm')}
        exact_packages = packages == {'openpyxl': '3.1.2', 'tqdm': '4.66.4'}
        status.update(tasks=len(rows), present=bool(status['libreoffice']) and exact_packages,
                      source_prepared=True, metric=METRIC,
                      python_packages=packages,
                      detail='Native workbooks present; UNO must be installed for /usr/bin/python3. Exact Python dependencies are checked again by the verifier.')
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
        status['detail'] = str(exc)
    return status


def downloads():
    return [{'kind': 'hf-dataset', 'name': 'SpreadsheetBench-2 native workbook collection',
             'repo': 'KAKA22/SpreadsheetBench-v2', 'version': REVISION,
             'files': [ARCHIVE], 'dst': '_src', 'size': 'upstream archive size',
             'note': 'Pinned v2 workbook bytes. Run prepare.py to extract only supported categories; no Visualization proxy is loaded.'},
            {'kind': 'pip', 'name': 'official workbook evaluator openpyxl', 'package': 'openpyxl',
             'version': '3.1.2', 'size': '~250 KB', 'note': 'Workbook reader used by the unchanged official v2 evaluator; pinned to the control-tested runtime.'},
            {'kind': 'pip', 'name': 'official workbook evaluator tqdm', 'package': 'tqdm',
             'version': '4.66.4', 'size': '~80 KB', 'note': 'Imported by the unchanged official evaluator and native LibreOffice helper; pinned to the control-tested runtime.'}]


def __getattr__(name):
    if name == 'SETUP':
        return {r['task_id']: _setup(r) for r in records()}
    if name == 'VERIFY':
        return {r['task_id']: _verify(r) for r in records()}
    if name == 'ENV':
        return {r['task_id']: dict(DEFAULT_ENV) for r in records()}
    raise AttributeError(name)
