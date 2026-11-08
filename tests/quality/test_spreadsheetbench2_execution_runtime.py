"""Authored v2 controls; native tests require explicit external prepared runtimes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from harnessgrad import dataset_runtime

ROOT = Path(__file__).resolve().parents[2]


def adapter():
    spec=importlib.util.spec_from_file_location('v2_control_adapter', ROOT/'data/datasets/spreadsheetbench2.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def row(tmp_path):
    source=tmp_path/'input.xlsx';gold=tmp_path/'golden.xlsx'
    source.write_bytes(b'authored identity only');gold.write_bytes(b'authored golden identity only')
    return {'task_id':'spreadsheetbench2/Template/control','category':'Template',
            'row':{'id':'control','instruction':'Set A1 to five.', 'spreadsheet_path':'input.xlsx',
                   'golden_response_path':'golden.xlsx','answer_position':'Sheet!A1:B1'},
            'input':source,'golden':gold,'output':'control_output.xlsx'}


def test_v2_loading_is_lazy_and_declares_own_closure(tmp_path, monkeypatch):
    module=adapter();record=row(tmp_path)
    monkeypatch.setattr(module,'records',lambda:[record])
    monkeypatch.setattr(dataset_runtime,'resolve_execution',lambda name: (_ for _ in ()).throw(
        dataset_runtime.DatasetRuntimeUnavailable('prepare execution first')))
    tasks,_=module.load()
    assert 'golden' not in json.dumps(tasks)
    assert list(module.VERIFY)==[record['task_id']]
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable,match='prepare'):
        module.VERIFY[record['task_id']]
    descriptor=dataset_runtime.execution_descriptor('spreadsheetbench2')
    assert descriptor['pins']=={'openpyxl':'3.1.2','et-xmlfile':'2.0.0','tqdm':'4.66.4'}
    assert not any(item['kind']=='pip' for item in module.downloads())


def test_v2_readonly_status_does_not_accept_python_only(tmp_path,monkeypatch):
    module=adapter();monkeypatch.setattr(module,'records',lambda:[row(tmp_path)])
    monkeypatch.delenv('HG_SPREADSHEET_NATIVE_MANIFEST',raising=False)
    monkeypatch.setattr(dataset_runtime,'check_execution',lambda name:{'present':True,'status':'ready'})
    status=module.provision_status()
    assert status['present'] is False
    assert status['formula_runtime']['status']=='unknown'
    assert 'prepare' in status['formula_runtime']


def prepared_spec(tmp_path):
    root=os.environ.get('HG_SPREADSHEETBENCH2_TEST_RUNTIME_ROOT')
    if not root or not os.environ.get('HG_SPREADSHEET_NATIVE_MANIFEST'):
        pytest.skip('explicit managed v2 artifact and native manifest required')
    selected=dataset_runtime.resolve_execution('spreadsheetbench2',root=root)
    record=row(tmp_path)
    # Use only the isolated artifact's package for creating these synthetic workbooks.
    code='''import openpyxl,sys
for name,a in [(sys.argv[1],0),(sys.argv[2],5)]:
 w=openpyxl.Workbook();w.active['A1']=a;w.active['B1']=7;w.save(name)
'''
    subprocess.run([selected['python'],'-I','-B','-c',code,str(record['input']),str(record['golden'])],check=True)
    with patch.object(dataset_runtime,'resolve_execution',return_value=selected):
        spec=adapter()._verify(record)
    return spec,selected


@pytest.mark.parametrize('formula,score',[('SUM(2,3)',1),('SUM(2,4)',0)])
def test_v2_actual_platform_undoes_actor_imports_and_scores_native_formula(tmp_path,monkeypatch,formula,score):
    from eval.verification import verifier
    spec,selected=prepared_spec(tmp_path)
    actor=tmp_path/'actor';actor.mkdir();output=actor/'control_output.xlsx'
    code="import openpyxl,sys;w=openpyxl.Workbook();w.active['A1']='='+sys.argv[2];w.active['B1']=7;w.save(sys.argv[1])"
    subprocess.run([selected['python'],'-I','-B','-c',code,str(output),formula],check=True)
    before=hashlib.sha256(output.read_bytes()).hexdigest()
    (actor/'data/scaffolds').mkdir(parents=True)
    (actor/'data/__init__.py').write_text('raise RuntimeError("actor import")')
    for name in ('openpyxl.py','sitecustomize.py','tqdm.py'):
        (actor/name).write_text('raise RuntimeError("actor import")')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT',str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-private-secret')
    monkeypatch.delenv('HG_SPREADSHEET_NATIVE_MANIFEST')
    result=verifier._verify(actor,spec,'',sandbox=True,timeout_s=120,
                            env={'kind':'files'},task_id=spec['scorer_input']['task_id'])
    assert result.get('score')==score,json.dumps(result,indent=2)
    evidence=result['evidence'];assert evidence['measured'] is True
    assert evidence['scorer_input']['verified'] is True
    assert evidence['runtime_context']['native_manifest_sha256']==spec['scorer_input']['files']['native-runtime.json']
    assert evidence['runtime_context']['python_runtime_id']==selected['runtime_id']
    assert hashlib.sha256(output.read_bytes()).hexdigest()==before


@pytest.mark.parametrize('refusal',['symlink','hardlink','macro','external-link','manifest-tamper'])
def test_v2_rejects_untrusted_links_active_content_and_manifest_tampering(tmp_path,monkeypatch,refusal):
    from eval.verification import verifier
    spec,selected=prepared_spec(tmp_path)
    actor=tmp_path/'actor';actor.mkdir();output=actor/'control_output.xlsx'
    code="import openpyxl,sys;w=openpyxl.Workbook();w.active['A1']='=SUM(2,3)';w.active['B1']=7;w.save(sys.argv[1])"
    subprocess.run([selected['python'],'-I','-B','-c',code,str(output)],check=True)
    if refusal=='symlink':
        output.unlink();output.symlink_to('golden.xlsx')
    elif refusal=='hardlink':
        os.link(output,actor/'another-link.xlsx')
    elif refusal in ('macro','external-link'):
        with zipfile.ZipFile(output,'a') as archive:
            archive.writestr('xl/vbaProject.bin' if refusal=='macro' else
                             'xl/externalLinks/externalLink1.xml',b'authored unsupported content')
    else:
        entry=next(item for item in spec['inputs'] if item['path']=='native-runtime.json')
        entry['content']+='\n'
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT',str(tmp_path))
    monkeypatch.delenv('HG_SPREADSHEET_NATIVE_MANIFEST')
    result=verifier._verify(actor,spec,'',sandbox=True,timeout_s=120,
                            env={'kind':'files'},task_id=spec['scorer_input']['task_id'])
    assert result.get('score') is None,json.dumps(result,indent=2)
    assert result['outcome']=='unmeasured'
    # require_exit_zero short-circuits the public verdict; private evidence persists.
    evidence=result.get('evidence') or json.loads((actor/'verifier/evidence.json').read_text())
    assert evidence['measured'] is False
    assert not (actor/'verifier/reward.txt').exists()
