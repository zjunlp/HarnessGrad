"""Authored offline controls; no benchmark data or platform workbook dependency."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from harnessgrad import dataset_runtime

ROOT = Path(__file__).resolve().parents[2]


def adapter():
    path = ROOT / 'data/datasets/spreadsheetbench.py'
    spec = importlib.util.spec_from_file_location('spreadsheet_runtime_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(tmp_path):
    golden = tmp_path / 'golden.xlsx'
    golden.write_bytes(b'authored input identity')
    return {'task_id': 'control', 'golden_path': golden, 'answer_position': 'Sheet!A1',
            'instruction_type': '', 'instruction': 'Set a value.'}


def runtime():
    path = '/synthetic/store/instances/control/venv'
    return {'dataset': 'spreadsheetbench', 'profile': 'execution', 'runtime_id': 'a' * 64,
            'manifest_path': '/synthetic/store/instances/control/artifact.json',
            'path': path, 'python': path + '/bin/python', 'runtime_paths': [path]}


def test_declaration_and_lazy_binding(tmp_path, monkeypatch):
    module = adapter()
    row = record(tmp_path)
    monkeypatch.setattr(module, '_ensure', lambda: [row])
    monkeypatch.setattr(module, '_LOAD_ERROR', None)
    tasks, scorable = module.load()
    assert tasks[0]['task_id'] == 'control' and scorable == {'control': 'result.xlsx'}
    assert 'golden' not in json.dumps(tasks)
    def missing(name):
        raise dataset_runtime.DatasetRuntimeUnavailable('explicit prepare required')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', missing)
    mapping = module.VERIFY
    assert list(mapping) == ['control']
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='prepare'):
        mapping['control']
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: runtime())
    from data.scaffolds.spreadsheet_runtime import native
    monkeypatch.setattr(native, 'require_runtime', lambda: {'schema': 1, 'files': {}, 'deployment': 'identity-only-control'})
    verifier = mapping['control']
    assert verifier['argv'][:3] == [runtime()['python'], '-I', '-B']
    assert verifier['python_runtime']['role'] == 'verifier'
    assert set(verifier['scorer_input']['files']) == {'task.json', 'golden.xlsx', 'native-runtime.json'}
    assert not any(row['kind'] == 'pip' for row in module.downloads())
    declaration = dataset_runtime.execution_descriptor('spreadsheetbench')
    assert declaration['pins'] == {'openpyxl': '3.1.2', 'et-xmlfile': '2.0.0'}
    assert 'which is installed' not in module._goal(row)


def test_trusted_helper_ignores_actor_cwd_and_bytecode(tmp_path):
    stage = tmp_path / 'trusted'
    stage.mkdir()
    helper = stage / 'data/scaffolds/scorer_input.py'
    helper.parent.mkdir(parents=True)
    helper.write_bytes((ROOT / 'data/scaffolds/scorer_input.py').read_bytes())
    # A sourceless actor import must never execute.
    actor = tmp_path / 'actor'
    (actor / 'data/scaffolds').mkdir(parents=True)
    (actor / 'data/__init__.py').write_text('raise RuntimeError("actor import")')
    entry = stage / 'check.py'
    entry.write_bytes((ROOT / 'data/datasets/spreadsheetbench/verify/check.py').read_bytes())
    helper.with_suffix('.pyc').write_bytes(b'unchecked malicious bytecode')
    (stage / 'native.py').write_bytes((ROOT / 'data/scaffolds/spreadsheet_runtime/native.py').read_bytes())
    (stage / 'native.pyc').write_bytes(b'unchecked native bytecode')
    proc = subprocess.run([sys.executable, '-I', '-B', str(entry), '--help'], cwd=actor,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert '--scorer-input-sha256' in proc.stdout


def xlsx(path, value):
    """Minimal OOXML authored with stdlib only; no writer imported on platform."""
    import zipfile
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in {
            '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
            '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
            'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet" sheetId="1" r:id="rId1"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
            'xl/worksheets/sheet1.xml': f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="n"><v>{value}</v></c></row></sheetData></worksheet>',
        }.items():
            archive.writestr(name, content)


def test_external_artifact_scores_authored_xlsx(tmp_path):
    import os
    if not os.environ.get('HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT') or not os.environ.get('HG_SPREADSHEET_NATIVE_MANIFEST'):
        pytest.skip('explicit external execution artifact required')
    selected = dataset_runtime.resolve_execution('spreadsheetbench', root=os.environ['HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT'])
    module = adapter()
    row = record(tmp_path)
    xlsx(row['golden_path'], 42)
    from unittest.mock import patch
    with patch.object(dataset_runtime, 'resolve_execution', return_value=selected):
        verifier = module._verifier(row)
    stage = tmp_path / 'stage'
    stage.mkdir()
    for entry in verifier['inputs']:
        target = stage / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(entry['from']).read_bytes() if 'from' in entry else entry['content'].encode())
    xlsx(stage / 'result.xlsx', 42)
    (stage / 'answer.txt').write_text('result.xlsx')
    (stage / 'openpyxl.py').write_text('raise RuntimeError("actor shadow")')
    proc = subprocess.run(verifier['argv'], cwd=stage, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    evidence = json.loads((stage / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is True and evidence['reward'] == 1
    assert evidence['scorer_input']['verified'] is True
    assert (stage / 'verifier/reward.txt').read_text() == '1\n'


def test_source_identity_uses_current_bytes_without_git(tmp_path, monkeypatch):
    import shutil
    from harnessgrad import scoring_identity
    path = ROOT / 'data/datasets/spreadsheetbench/scoring_profile.py'
    spec = importlib.util.spec_from_file_location('spreadsheet_profile_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative in (*module.SOURCES, 'data/datasets/spreadsheetbench/scoring_profile.py'):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    first = scoring_identity.resolve('spreadsheetbench')
    scorer = tmp_path / 'data/datasets/spreadsheetbench/verify/check.py'
    scorer.write_bytes(scorer.read_bytes() + b'\n# source identity control\n')
    second = scoring_identity.resolve('spreadsheetbench')
    assert first != second


def external_spec(tmp_path):
    import os
    from unittest.mock import patch
    if not os.environ.get('HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT') or not os.environ.get('HG_SPREADSHEET_NATIVE_MANIFEST'):
        pytest.skip('explicit external Python artifact and prepared native manifest required')
    selected = dataset_runtime.resolve_execution('spreadsheetbench', root=os.environ['HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT'])
    row = record(tmp_path)
    xlsx(row['golden_path'], 5)
    with patch.object(dataset_runtime, 'resolve_execution', return_value=selected):
        return adapter()._verifier(row)


def formula_xlsx(path, formula='SUM(2,3)'):
    import zipfile
    xlsx(path, 999)
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries['xl/worksheets/sheet1.xml'] = entries['xl/worksheets/sheet1.xml'].replace(
        b'<v>999</v>', ('<f>' + formula + '</f><v>999</v>').encode())
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


@pytest.mark.parametrize('formula, score', [('SUM(2,3)', 1), ('SUM(2,4)', 0)])
def test_native_formula_sandbox_correct_and_wrong(tmp_path, monkeypatch, formula, score):
    import hashlib
    from eval.verification import verifier as platform_verifier
    spec = external_spec(tmp_path)
    actor = tmp_path / 'actor'
    actor.mkdir()
    formula_xlsx(actor / 'result.xlsx', formula)
    before = hashlib.sha256((actor / 'result.xlsx').read_bytes()).hexdigest()
    (actor / 'answer.txt').write_text('result.xlsx')
    # Neither Python nor the native child may import actor-controlled modules.
    (actor / 'data/scaffolds').mkdir(parents=True)
    (actor / 'data/__init__.py').write_text('raise RuntimeError("actor package")')
    for name in ('openpyxl.py', 'native.py', 'sitecustomize.py'):
        (actor / name).write_text('raise RuntimeError("actor source")')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY', 'authored-secret-must-not-reach-native')
    # Capture the manifest in the spec, then remove environment configuration:
    # workers must consume the bound private bytes rather than ambient HG_*.
    monkeypatch.delenv('HG_SPREADSHEET_NATIVE_MANIFEST')
    result = platform_verifier._verify(actor, spec, '', sandbox=True, timeout_s=90,
                                      env={'kind': 'files'}, task_id='control')
    assert result.get('score') == score, json.dumps(result, indent=2)
    evidence = result['evidence']
    assert evidence['measured'] is True and evidence['recalculated'] is True
    assert evidence['native_runtime']['verified'] is True
    assert evidence['native_runtime']['deployment'] == 'verified-host-installation'
    assert evidence['native_runtime']['manifest_sha256'] == spec['scorer_input']['files']['native-runtime.json']
    assert hashlib.sha256((actor / 'result.xlsx').read_bytes()).hexdigest() == before


@pytest.mark.parametrize('active', ['xl/vbaProject.bin', 'xl/externalLinks/externalLink1.xml'])
def test_native_active_content_is_unknown_without_reward(tmp_path, monkeypatch, active):
    import zipfile
    from eval.verification import verifier as platform_verifier
    spec = external_spec(tmp_path)
    actor = tmp_path / 'actor'
    actor.mkdir()
    formula_xlsx(actor / 'result.xlsx')
    with zipfile.ZipFile(actor / 'result.xlsx', 'a') as archive:
        archive.writestr(active, b'authored rejected payload')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    result = platform_verifier._verify(actor, spec, '', sandbox=True, timeout_s=90,
                                      env={'kind': 'files'}, task_id='control')
    assert result.get('score') is None, result
    assert result.get('outcome') == 'unmeasured'
    assert 'macros or external workbook links' in result.get('detail', ''), result
    assert not (actor / 'verifier/reward.txt').exists()


def test_missing_native_configuration_fails_explicitly(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: runtime())
    monkeypatch.delenv('HG_SPREADSHEET_NATIVE_MANIFEST', raising=False)
    module = adapter()
    with pytest.raises(RuntimeError, match='prepare native LibreOffice'):
        module._verifier(record(tmp_path))
    assert module._native_status()['status'] == 'unknown'
    assert module._soffice() is None


def load_check():
    path = ROOT / 'data/datasets/spreadsheetbench/verify/check.py'
    spec = importlib.util.spec_from_file_location('spreadsheet_v1_check_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_changed_native_runtime_is_unknown_even_without_submission(tmp_path):
    import hashlib
    tracked = tmp_path / 'identity-control'
    tracked.write_bytes(b'changed deployment')
    manifest = {'schema': 1, 'deployment': 'verified-host-installation',
                'soffice_binary': str(tracked), 'python': str(tracked),
                'program_dir': str(tmp_path),
                'files': {str(tracked): hashlib.sha256(b'original deployment').hexdigest()}}
    (tmp_path / 'native-runtime.json').write_text(json.dumps(manifest))
    (tmp_path / 'task.json').write_text(json.dumps({'task_id': 'control', 'answer_position': 'Sheet!A1'}))
    xlsx(tmp_path / 'golden.xlsx', 5)
    reward, evidence = tmp_path / 'reward.txt', tmp_path / 'evidence.json'
    reward.write_text('1\n')  # An unknown recheck must remove a stale reward.
    check = load_check()
    status = check.main(['--workdir', str(tmp_path), '--native-manifest', 'native-runtime.json',
                         '--reward-file', str(reward), '--evidence-file', str(evidence)])
    assert status == 3 and not reward.exists()
    result = json.loads(evidence.read_text())
    assert result['measured'] is False and 'native runtime identity changed' in result['error']


def test_v1_conversion_environment_never_forwards_credentials(tmp_path, monkeypatch):
    check = load_check()
    formula_xlsx(tmp_path / 'result.xlsx')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-secret')
    monkeypatch.setenv('HG_SPREADSHEET_NATIVE_MANIFEST', '/actor/manifest')
    monkeypatch.setenv('PYTHONPATH', '/actor/modules')
    observed = {}
    def run(argv, **kwargs):
        observed.update(argv=argv, env=kwargs['env'])
        return subprocess.CompletedProcess(argv, 1, '', 'authored conversion failure')
    monkeypatch.setattr(check.subprocess, 'run', run)
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    ok, _ = check.recalculate(tmp_path / 'result.xlsx', scratch=scratch,
                             soffice=['trusted-python', '-I', '-B', 'trusted-native.py',
                                      'launch', '--manifest', 'private-manifest', '--'])
    assert ok is False
    assert observed['argv'][:3] == ['trusted-python', '-I', '-B']
    assert not any(key in observed['env'] for key in
                   ('OPENAI_API_KEY', 'HG_SPREADSHEET_NATIVE_MANIFEST', 'PYTHONPATH', 'HOME'))


def test_answer_cannot_name_reserved_private_golden(tmp_path):
    from data.scaffolds.scorer_input import inventory_digest
    check = load_check()
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'task.json').write_text(json.dumps({'task_id': 'control', 'answer_position': 'Sheet!A1'}))
    xlsx(tmp_path / 'golden.xlsx', 5)
    (tmp_path / 'answer.txt').write_text('sub/../golden.xlsx')
    import hashlib
    files = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
             for name in ('task.json', 'golden.xlsx')}
    reward, evidence = tmp_path / 'reward.txt', tmp_path / 'evidence.json'
    status = check.main(['--workdir', str(tmp_path), '--task-id', 'control',
                         '--scorer-input-sha256', inventory_digest(files),
                         '--scorer-input-files', json.dumps(files), '--reward-file', str(reward),
                         '--evidence-file', str(evidence)])
    assert status == 3 and not reward.exists()
    assert 'reserved private scorer input' in json.loads(evidence.read_text())['error']
