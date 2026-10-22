#!/usr/bin/env python3
"""Authored workbook controls; metadata mode inspects references, never released workbooks."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile

import openpyxl

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
CHECK = ROOT / 'data/datasets/spreadsheetbench/verify/check.py'
META = Path('/mnt/20t/xubuqiang/datasets/spreadsheetbench/spreadsheetbench_verified_400/dataset.json')
spec = importlib.util.spec_from_file_location('spreadvacuous_check', CHECK)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def emit(**record):
    print(json.dumps(record, sort_keys=True))


def workbook(path, sheet, values):
    wb = openpyxl.Workbook()
    wb.active.title = sheet
    for cell, value in values.items():
        wb.active[cell] = value
    wb.save(path)
    wb.close()


def controls():
    emit(checker_sha256=hashlib.sha256(CHECK.read_bytes()).hexdigest())
    emit(generate_cell_names=check.generate_cell_names('BD2:308'),
         parse_cell_range=check.parse_cell_range('BD2:308'))
    cases = [
        ('73-45', "'Sheet1'!BD2:308", 'Sheet1', 'Sheet1', {'A1': 999}),
        ('single-cell', "'Sheet1'!A1", 'Sheet1', 'Sheet1', {'A1': 999}),
        ('130-9', "'b2b, sez, de'!A5:V10", 'b2b, sez, de', 'b2b, sez, de', {'A1': 999}),
        ('283-32', "Sheet3'!A:G,'Sheet4'!A:G", 'Sheet3', 'Sheet3', {'A1': 999}),
        ('missing-result-sheet', 'Sheet1!A1', 'Sheet1', 'Other', {'A1': 999}),
        ('missing-golden-sheet', 'Absent!A1', 'Sheet1', 'Sheet1', {'A1': 999}),
        ('mixed-empty-last', 'Sheet1!A1,Sheet1!BD2:308', 'Sheet1', 'Sheet1', {'A1': 999}),
    ]
    golden_values = {'A1': 5, 'A2': 6, 'B1': 7, 'B2': 8}
    cases.append(('normal-range-match', 'Sheet1!A1:B2', 'Sheet1', 'Sheet1', golden_values))
    for coordinate in golden_values:
        cases.append((f'normal-range-diff-{coordinate}', 'Sheet1!A1:B2', 'Sheet1',
                      'Sheet1', dict(golden_values, **{coordinate: 999})))
    for task_id, position, golden_sheet, result_sheet, values in cases:
        with tempfile.TemporaryDirectory(prefix='spreadvacuous-control-') as directory:
            work = Path(directory)
            # The defect reproduction has exactly one materialized cell per workbook.
            golden = {'A1': 5} if not task_id.startswith('normal-range') else golden_values
            workbook(work / 'golden.xlsx', golden_sheet, golden)
            workbook(work / 'result.xlsx', result_sheet, values)
            (work / 'task.json').write_text(json.dumps({
                'task_id': task_id, 'answer_position': position, 'instruction_type': ''}))
            try:
                ok, message = check.compare_workbooks(work / 'golden.xlsx', work / 'result.xlsx', '', position)
                emit(task_id=task_id, direct_ok=ok, direct_message=message)
            except Exception as exc:
                emit(task_id=task_id, direct_exception=f'{type(exc).__name__}: {exc}')
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = check.main(['--workdir', str(work), '--reward-file', str(work / 'reward.txt'),
                                   '--evidence-file', str(work / 'evidence.json')])
            evidence = json.loads((work / 'evidence.json').read_text())
            emit(task_id=task_id, position=position, exit=code, measured=evidence['measured'],
                 score=evidence.get('score'), reason=evidence.get('error', evidence.get('why')),
                 reward_file=(work / 'reward.txt').read_text() if (work / 'reward.txt').exists() else None,
                 stdout=stdout.getvalue(), stderr=stderr.getvalue())


def metadata():
    """Address expansion only: no comparison/scoring or installed workbook access."""
    payload = META.read_bytes()
    rows = json.loads(payload)
    counts = {}
    exceptions = []
    wb = openpyxl.Workbook()
    for row in rows:
        status = 'addressable'
        details = []
        for component in row['answer_position'].split(','):
            probe = component.split('!')[-1].strip().strip("'")
            try:
                if ':' in probe:
                    check.parse_cell_range(probe)
            except ValueError as exc:
                status = 'range_zero_policy_only'
                details.append(str(exc))
                break
            cell_range = component.split('!')[-1].strip("'")
            try:
                cells = check.generate_cell_names(cell_range)
                if not cells:
                    status = 'empty_expansion'
                    details.append(repr(cells))
                    break
                for name in cells:
                    wb.active[name]
            except (ValueError, KeyError, AttributeError) as exc:
                status = 'unaddressable_component'
                details.append(f'{type(exc).__name__}: {exc}')
                break
        counts[status] = counts.get(status, 0) + 1
        if status != 'addressable':
            exceptions.append(dict(task_id=str(row['id']), answer_position=row['answer_position'],
                                   reference_class=status, details=details))
    wb.close()
    emit(scope=str(META), metadata_sha256=hashlib.sha256(payload).hexdigest(), rows=len(rows),
         reference_classes=counts, exceptions=exceptions,
         limit='reference expansion/worksheet lookup only; tuple-valued lookup is not rejected; installed workbook loadability and measured coverage UNKNOWN')


if __name__ == '__main__':
    {'controls': controls, 'metadata': metadata}[sys.argv[1]]()
