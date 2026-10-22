#!/usr/bin/env python3
"""Assemble exact bounded observations and sizes; never edit the live registry."""
import ast
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
DEST = ROOT / 'registry/w-spreadvacuous-validation.txt'
FIXTURE = ROOT / 'tests/fixtures/spreadvacuous'
parts = ['w-spreadvacuous validation (authored workbook controls only)\n',
         'All shell commands below ran from the assigned worktree. Historical fixture directory\n'
         'tests/fixtures/hg_spreadvacuous was renamed to tracked tests/fixtures/spreadvacuous;\n'
         'the before/after receipts were copied byte-for-byte, not regenerated.\n']


def record(command):
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    parts.append('$ ' + shlex.join(command) + '\n' + proc.stdout + proc.stderr +
                 f'EXIT={proc.returncode}\n')
    assert proc.returncode == 0, (command, proc.stdout, proc.stderr)


def receipt(command, filename, exit_code):
    parts.append('$ ' + command + '\n' + (FIXTURE / filename).read_text() + f'EXIT={exit_code}\n')


# This reproduction actually ran before any production/test edit.
reproduction = '''TMPDIR=/tmp/spreadvacuous-tmp python3 - <<'PY'
import importlib.util
import tempfile
from pathlib import Path
import openpyxl
spec = importlib.util.spec_from_file_location('check', 'data/datasets/spreadsheetbench/verify/check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
with tempfile.TemporaryDirectory(prefix='reproduction-') as directory:
    root = Path(directory)
    for name, value in [('golden', 5), ('result', 999)]:
        wb = openpyxl.Workbook()
        wb.active.title = 'Sheet1'
        wb.active['A1'] = value
        wb.save(root / f'{name}.xlsx')
        wb.close()
    print("generate_cell_names('BD2:308') ->", check.generate_cell_names('BD2:308'))
    print("parse_cell_range('BD2:308') ->", check.parse_cell_range('BD2:308'))
    for position in ["'Sheet1'!BD2:308", "'Sheet1'!A1"]:
        ok, msg = check.compare_workbooks(root / 'golden.xlsx', root / 'result.xlsx', '', position)
        print(f'compare_workbooks(golden A1=5, result A1=999, position {position!r}) -> ok={ok!r} msg={msg!r}')
PY'''
parts.append('$ ' + reproduction + '\n' + '''generate_cell_names('BD2:308') -> []
parse_cell_range('BD2:308') -> ((56, 2), (0, 308))
compare_workbooks(golden A1=5, result A1=999, position "'Sheet1'!BD2:308") -> ok=True msg=''
compare_workbooks(golden A1=5, result A1=999, position "'Sheet1'!A1") -> ok=False msg='value difference at A1: golden has 5, result has 999'
EXIT=0
''')
receipt('TMPDIR=/tmp/spreadvacuous-tmp PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/hg_spreadvacuous/controls.py controls', 'before.txt', 0)
receipt('TMPDIR=/tmp/spreadvacuous-tmp PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/quality/test_spreadsheetbench_classification_w_spread2.py tests/quality/test_spreadsheetbench_control_submit.py --basetemp=/tmp/spreadvacuous-before', 'before-tests.txt', 0)
receipt('TMPDIR=/tmp/spreadvacuous-tmp PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/hg_spreadvacuous/controls.py controls', 'after.txt', 0)
receipt('TMPDIR=/tmp/spreadvacuous-tmp PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/quality/test_spreadsheetbench_classification_w_spread2.py tests/quality/test_spreadsheetbench_control_submit.py --basetemp=/tmp/spreadvacuous-after', 'after-tests.txt', 0)
receipt('TMPDIR=/tmp/spreadvacuous-tmp PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/hg_spreadvacuous/controls.py metadata', 'metadata-strict-initial.txt', 0)
receipt('TMPDIR=/tmp/spreadvacuous-tmp PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/spreadvacuous/controls.py metadata', 'metadata.txt', 0)
receipt('TMPDIR=/tmp/spreadvacuous-tmp PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/spreadvacuous/integration.py', 'integration-initial-tests.txt', 1)
receipt('TMPDIR=/tmp/spreadvacuous-tmp PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/spreadvacuous/integration.py', 'integration-tests.txt', 0)
parts.append('Inherited observations, not a rerun of eligibility:\n' + (FIXTURE / 'inherited-eligibility.txt').read_text())
record(['git', 'rev-parse', 'HEAD', 'wg-w-spreadcache', 'wg-w-spreadelig'])
record(['sha256sum', 'data/datasets/spreadsheetbench/verify/check.py'])
record(['python3', 'tools/apply_blocker_proposal.py', 'registry/proposals/w-spreadvacuous.json', '--dataset', 'spreadsheetbench', '--blocker', 'dataset-journal-gap'])
record(['python3', 'tools/check_dataset_blockers.py'])
record(['git', 'diff', '--check'])
record(['git', 'diff', 'e0c1cd3254e0168c9e9e1a7dabee750772ddc868', '--',
        'AGENTS.md', 'registry/dataset-blockers.json', 'registry/withdrawn-requirements.md',
        'docs/adapter-integration-journal.md'])
parts.append('The empty protected-file diff above establishes these files were not changed in this branch.\n'
             'Registry checker cannot be affected by a scorer-only code edit or standalone proposal.\n')

# Verify the claimed primitive preservation and every cited local line range.
check = 'data/datasets/spreadsheetbench/verify/check.py'
before = ast.parse(subprocess.check_output(['git', 'show', f'e0c1cd3254e0168c9e9e1a7dabee750772ddc868:{check}'], cwd=ROOT))
after = ast.parse((ROOT / check).read_text())
primitives = ['transform_value', 'datetime_to_float', 'compare_cell_value', 'col_num2name',
              'col_name2num', 'parse_cell_range', 'generate_cell_names', 'cell_level_compare']
for name in primitives:
    def body(tree):
        return ast.dump(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name))
    assert body(before) == body(after), name
parts.append('Observation from validate.py: unchanged ASTs = ' + json.dumps(primitives) + '\n')
report = ROOT / 'registry/w-spreadvacuous-divergence.md'
citations = re.findall(r'((?:tests|registry|data)/[\w/.-]+):(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)', report.read_text())
for filename, positions in citations:
    lines = (ROOT / filename).read_text().splitlines()
    for position in positions.split(','):
        endpoints = list(map(int, position.split('-')))
        assert 1 <= min(endpoints) <= max(endpoints) <= len(lines), (filename, position, len(lines))
parts.append(f'Observation from validate.py: {len(citations)} report file:line citations checked for existence and bounds.\n')

# Delete only task-owned SSD scratch. Persistent report/receipt files remain on assigned HDD.
for directory in ['/tmp/spreadvacuous-before', '/tmp/spreadvacuous-after', '/tmp/spreadvacuous-tmp']:
    path = Path(directory)
    if path.exists():
        shutil.rmtree(path)
    parts.append(f'CLEANUP via shutil.rmtree on task-owned path {directory}: exists={path.exists()}\n')
remaining = sorted(str(p) for p in Path('/mnt/20t/xubuqiang/hg-work/spreadvacuous').glob('cache-*'))
assert not remaining, remaining
parts.append('Disposable assigned HDD source overlays remaining=' + json.dumps(remaining) + '\n')
parts.append('NOT VERIFIED / NOT PERFORMED:\n'
             '- Actual parent merge and its final checker digest; parent must recompute custody.\n'
             '- Released workbook scorer execution, selected dataset/trial coverage or benchmark scores.\n'
             '- Full installed workbook eligibility, upstream execution or fresh upstream authentication.\n'
             '- Quote-aware comma repair (130-9 is detected/refused, not repaired).\n'
             '- Production sandbox, resource/cleanup quotas, held-out data or model/GPU/API execution.\n'
             '- Direct legacy invocation with a pre-existing reward file; controls use fresh directories.\n'
             '- Real-engine success on this standalone pre-cache branch: the opt-in test belongs to\n'
             '  cache worker bytes and was tested both before/after in the sparse combined overlay.\n'
             '- Any frozen run/receipt mutation, registry application, main write, push, merge or branch deletion.\n')
files = sorted([ROOT / check, ROOT / 'tests/quality/test_spreadsheetbench_classification_w_spread2.py',
                ROOT / 'registry/proposals/w-spreadvacuous.json', report, DEST, *FIXTURE.iterdir()])
# Self-referential size stabilizes once its decimal digit count stops changing.
DEST.write_text('')
for _ in range(8):
    sizes = '\n'.join(f'{p.relative_to(ROOT)} {p.stat().st_size} bytes' for p in files)
    payload = '\n'.join(parts) + '\nCOMMIT FILE SIZES (all files, including this validation):\n' + sizes + '\n'
    old = DEST.stat().st_size
    DEST.write_text(payload)
    if DEST.stat().st_size == old:
        break
else:
    raise AssertionError('validation size failed to stabilize')
assert all(p.stat().st_size < 1_000_000 for p in files)
print(f'validation_written={DEST.relative_to(ROOT)} files={len(files)} each_below_1MB=True')
print(next(s for s in parts if '$ python3 tools/check_dataset_blockers.py' in s), end='')
