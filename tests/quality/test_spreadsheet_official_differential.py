"""Real official-source/platform differentials on authored workbooks only.

Explicit retained artifacts are required. No released task/workbook or model is
used. The independent reference copies use the native UNO helper; v1's platform
path instead uses CLI conversion. This tests selected LibreOffice behavior, not
Excel equivalence or a hermetic deployment.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from harnessgrad import dataset_runtime
from data.scaffolds.spreadsheet_runtime import native

ROOT = Path(__file__).resolve().parents[2]
V1_REVISION = '49b73a94775fb489063f60ca1865e3a650079a79'
V1_SOURCE_SHA256 = '4ae77cee8df01d1f34684fceab972810d696886533d33be2e89373de6b4d3de3'
V2 = ROOT / 'data/datasets/spreadsheetbench2'


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def engines():
    required = ('HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT',
                'HG_SPREADSHEETBENCH2_TEST_RUNTIME_ROOT', 'HG_SPREADSHEET_NATIVE_MANIFEST')
    if any(not os.environ.get(name) for name in required):
        pytest.skip('explicit v1/v2 managed artifacts and native deployment required')
    # Reference execution uses genuine installed dependencies, not replacement
    # modules. Match the production workbook reader and upstream v2 progress lib.
    assert importlib.metadata.version('openpyxl') == '3.1.2'
    assert importlib.metadata.version('tqdm') == '4.66.4'
    selected = {version: dataset_runtime.resolve_execution(dataset, root=os.environ[env])
                for version, dataset, env in (
                    (1, 'spreadsheetbench', required[0]),
                    (2, 'spreadsheetbench2', required[1]))}
    provenance = json.loads((V2 / 'PROVENANCE.json').read_text())
    for name, digest in provenance['files'].items():
        assert hashlib.sha256((V2 / 'verify' / name).read_bytes()).hexdigest() == digest
    return selected, native.snapshot_native_python(native.require_runtime())


@pytest.fixture(scope='module')
def official_v1():
    source = os.environ.get('HG_SPREADSHEETBENCH_OFFICIAL_EVALUATOR')
    if not source:
        pytest.skip('explicit independent official v1 evaluator snapshot required')
    source = Path(source)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == V1_SOURCE_SHA256
    return load_source('spreadsheet_v1_independent_reference', source)


# Each workbook includes a preserved B1 cell so v2 has a genuine regression
# class as well as a modification class. Expected v1/v2 scores deliberately
# differ for the upstream tolerance and degenerate-class rules.
CASES = [
    ('types-and-empty', {'A1': 0, 'B1': 7},
     {'A1': 5, 'B1': 7, 'C1': None, 'D1': True,
      'E1': datetime.datetime(2024, 1, 2, 3), 'F1': datetime.time(9, 30, 15)},
     {'A1': '5', 'B1': 7, 'C1': '', 'D1': 1,
      'E1': datetime.datetime(2024, 1, 2, 10), 'F1': datetime.time(9, 30, 45)},
     'Totals!A1:F1', (1, 1)),
    ('relative-tolerance', {'A1': 0, 'B1': 7}, {'A1': 100, 'B1': 7},
     {'A1': 100.5, 'B1': 7}, 'Totals!A1:B1', (0, 1)),
    ('wrong-regression', {'A1': 0, 'B1': 7}, {'A1': 5, 'B1': 7},
     {'A1': 5, 'B1': 8}, 'Totals!A1:B1', (0, 0)),
    ('cross-sheet-formula', {'A1': 0, 'B1': 7}, {'A1': 5, 'B1': 7},
     {'A1': "=SUM('Data Sheet'!A1:A2)", 'B1': 7},
     "Totals!A1:B1,'Data Sheet'!A1:A2", (1, 1)),
    ('formula-empty', {'A1': 'before', 'B1': 7}, {'A1': None, 'B1': 7},
     {'A1': '=IF(1=1,"",1)', 'B1': 7}, 'Totals!A1:B1', (1, 1)),
    ('empty-versus-zero', {'A1': 'before', 'B1': 7}, {'A1': None, 'B1': 7},
     {'A1': 0, 'B1': 7}, 'Totals!A1:B1', (0, 1)),
    ('excel-error-fallback', {'A1': 0, 'B1': 7}, {'A1': '#DIV/0!', 'B1': 7},
     {'A1': 'N/A', 'B1': 7}, 'Totals!A1:B1', (0, 0)),
    ('financial-display-text', {'A1': 0, 'B1': 7}, {'A1': 'N/M', 'B1': 7},
     {'A1': 'N/A', 'B1': 7}, 'Totals!A1:B1', (0, 1)),
    ('all-regression', {'A1': 5, 'B1': 7}, {'A1': 5, 'B1': 7},
     {'A1': 5, 'B1': 7}, 'Totals!A1:B1', (1, 0)),
]


def workbook(path, cells, *, extra_sheet='Data Sheet'):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Totals'
    for name, value in cells.items():
        ws[name] = value
    extra = wb.create_sheet(extra_sheet)
    extra['A1'], extra['A2'] = 2, 3
    wb.save(path)
    wb.close()


def platform_score(tmp_path, monkeypatch, engines, version, input_file, golden,
                   output, position, *, category='Template'):
    from eval.verification import verifier
    selected, _ = engines
    dataset = 'spreadsheetbench' if version == 1 else 'spreadsheetbench2'
    adapter = load_source('spreadsheet_differential_adapter_' + str(version),
                          ROOT / 'data/datasets' / (dataset + '.py'))
    if version == 1:
        record = {'task_id': 'authored-differential', 'golden_path': golden,
                  'answer_position': position, 'instruction_type': ''}
        with patch.object(dataset_runtime, 'resolve_execution', return_value=selected[version]):
            spec = adapter._verifier(record)
        output_name = 'result.xlsx'
    else:
        record = {'task_id': 'spreadsheetbench2/' + category + '/authored',
                  'category': category, 'input': input_file, 'golden': golden,
                  'row': {'id': 'authored', 'spreadsheet_path': input_file.name,
                          'golden_response_path': golden.name, 'answer_position': position}}
        with patch.object(dataset_runtime, 'resolve_execution', return_value=selected[version]):
            spec = adapter._verify(record)
        output_name = 'authored_output.xlsx'
    actor = tmp_path / 'actor'
    actor.mkdir()
    submission = actor / output_name
    shutil.copyfile(output, submission)
    if version == 1:
        (actor / 'answer.txt').write_text(output_name)
    before = submission.read_bytes()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    verdict = verifier._verify(actor, spec, '', sandbox=True, timeout_s=180,
                               env={'kind': 'files'}, task_id=record['task_id'])
    assert verdict['evidence']['measured'] is True, verdict
    assert verdict['evidence']['scorer_input']['verified'] is True
    if version == 1:
        assert verdict['evidence']['native_runtime']['verified'] is True
        assert verdict['evidence']['native_runtime']['manifest_sha256'] == spec['scorer_input']['files']['native-runtime.json']
    else:
        context = verdict['evidence']['runtime_context']
        assert context['native_manifest_sha256'] == spec['scorer_input']['files']['native-runtime.json']
        assert context['python_runtime_id'] == selected[version]['runtime_id']
    assert submission.read_bytes() == before
    return verdict


def reference_copies(tmp_path, engines, paths):
    reference = tmp_path / 'reference'
    reference.mkdir()
    copies = []
    for path in paths:
        target = reference / path.name
        shutil.copyfile(path, target)
        copies.append(target)
    # An independently recalculated copy; never rewrite platform/actor inputs.
    native.isolated_uno_recalculate(engines[1], V2 / 'verify/open_spreadsheet.py', copies)
    return copies


@pytest.mark.parametrize('case', CASES, ids=[case[0] for case in CASES])
@pytest.mark.parametrize('version', [1, 2])
def test_official_source_and_platform_agree(tmp_path, monkeypatch, engines, request, case, version):
    name, initial, expected, submitted, position, scores = case
    original, golden, output = (tmp_path / file for file in ('input.xlsx', 'golden.xlsx', 'authored_output.xlsx'))
    for path, cells in ((original, initial), (golden, expected), (output, submitted)):
        workbook(path, cells)
    before = {path: path.read_bytes() for path in (original, golden, output)}
    official = (request.getfixturevalue('official_v1') if version == 1 else
                load_source('spreadsheet_v2_independent_reference', V2 / 'verify/evaluation.py'))
    ref_input, ref_golden, ref_output = reference_copies(tmp_path, engines, before)
    if version == 1:
        passed, _ = official.compare_workbooks(str(ref_golden), str(ref_output), '', position)
        reference = {'accuracy': int(passed)}
    else:
        reference, missing = official.process_single_item(
            {'id': 'authored', 'spreadsheet_path': ref_input.name,
             'golden_response_path': ref_golden.name, 'answer_position': position},
            str(ref_input.parent), str(ref_output.parent), {}, 'Template')
        assert missing is False
    verdict = platform_score(tmp_path, monkeypatch, engines, version, original, golden, output, position)
    assert verdict['score'] == reference['accuracy']
    assert reference['accuracy'] == scores[version - 1]
    if version == 1 and name in ('cross-sheet-formula', 'formula-empty'):
        assert verdict['evidence']['recalculated'] is True
    if version == 2:
        for key in ('regression_accuracy', 'modification_accuracy'):
            assert verdict['evidence'][key] == reference[key]
        if name in ('all-regression', 'excel-error-fallback'):
            assert (reference['regression_accuracy'], reference['modification_accuracy']) == (1, 0)
        elif name == 'wrong-regression':
            assert (reference['regression_accuracy'], reference['modification_accuracy']) == (0, 1)
    assert all(path.read_bytes() == content for path, content in before.items())
    (tmp_path / 'differential-receipt.json').write_text(json.dumps({
        'case': name, 'version': version, 'reference': reference['accuracy'],
        'platform': verdict['score'], 'native_version': engines[1]['version'],
        'native_manifest_sha256': hashlib.sha256(
            Path(os.environ['HG_SPREADSHEET_NATIVE_MANIFEST']).read_bytes()).hexdigest(),
        'managed_python_runtime_id': engines[0][version]['runtime_id'],
        'reference_packages': {name: importlib.metadata.version(name)
                               for name in (('openpyxl', 'tqdm', 'numpy') if version == 1 else
                                            ('openpyxl', 'tqdm'))},
        'reference_revision': (V1_REVISION if version == 1 else
            json.loads((V2 / 'PROVENANCE.json').read_text())['revision']),
        'reference_source_sha256': (V1_SOURCE_SHA256 if version == 1 else
            hashlib.sha256((V2 / 'verify/evaluation.py').read_bytes()).hexdigest())}) + '\n')


@pytest.mark.parametrize('mode', ['quoted-comma-sheet', 'Embedded', 'Color'])
def test_v2_category_specific_comparisons(tmp_path, monkeypatch, engines, mode):
    original = tmp_path / (mode + '_input.xlsx')
    golden, output = tmp_path / 'golden.xlsx', tmp_path / 'authored_output.xlsx'
    for path, value in ((original, 0), (golden, 5), (output, 5)):
        workbook(path, {'A1': value, 'B1': 7}, extra_sheet='Data, Sheet')
    position = "Totals!A1:B1,'Data, Sheet'!A1:A2" if mode == 'quoted-comma-sheet' else 'Totals!A1:B1'
    category = 'Template' if mode == 'quoted-comma-sheet' else 'Debugging'
    if mode == 'Embedded':
        import openpyxl
        for path, value in ((golden, '=SUM(2,3)'), (output, '=2+3')):
            wb = openpyxl.load_workbook(path)
            wb['Totals']['A1'] = value
            wb.save(path)
            wb.close()
    elif mode == 'Color':
        import openpyxl
        from openpyxl.styles import Font
        for path, color in ((golden, '00FF00'), (output, 'FF0000')):
            wb = openpyxl.load_workbook(path)
            wb['Totals']['A1'].font = Font(color=color)
            wb.save(path)
            wb.close()
    before = {path: path.read_bytes() for path in (original, golden, output)}
    ref_input, ref_golden, ref_output = reference_copies(tmp_path, engines, before)
    official = load_source('spreadsheet_v2_category_reference', V2 / 'verify/evaluation.py')
    reference, missing = official.process_single_item(
        {'id': 'authored', 'spreadsheet_path': ref_input.name,
         'golden_response_path': ref_golden.name, 'answer_position': position},
        str(ref_input.parent), str(ref_output.parent), {}, category)
    verdict = platform_score(tmp_path, monkeypatch, engines, 2, original, golden, output,
                             position, category=category)
    assert not missing
    assert verdict['score'] == reference['accuracy'] == int(mode == 'quoted-comma-sheet')
    for key in ('regression_accuracy', 'modification_accuracy'):
        assert verdict['evidence'][key] == reference[key]
    assert all(path.read_bytes() == content for path, content in before.items())
