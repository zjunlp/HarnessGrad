"""Count heavyweight operations with authored pools; no benchmark data or models."""
import ast
import hashlib
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from data.scaffolds import declarations, registry, scorer_input
from harnessgrad import dataset_runtime as runtime
from test_dataset_execution_runtime import execution
from test_dataset_runtime import environment

ROOT = Path(__file__).resolve().parents[2]


def test_alfworld_import_does_not_resolve_runtime_for_any_of_134_tasks(monkeypatch):
    source = ROOT/'data/datasets/alfworld.py'
    tree = ast.parse(source.read_bytes())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ('_collect','_study_ids'):
            node.body = [ast.Return(ast.Name('_AUTHORED', ast.Load()) if node.name=='_collect' else ast.List([],ast.Load()))]
    ast.fix_missing_locations(tree)
    module = ModuleType('authored_alfworld_performance'); module.__file__=str(source)
    module._AUTHORED = [{'task_id':f'authored/{i}','stripped_game':'{}'} for i in range(134)]
    resolve=Mock(return_value={'python':'/authored/python'})
    monkeypatch.setattr(runtime,'resolve_execution',resolve)
    monkeypatch.setattr(runtime,'bind',lambda selected,role:{**selected,'role':role})
    monkeypatch.delenv('HG_ALFWORLD_POOL',raising=False)
    exec(compile(tree,str(source),'exec'),module.__dict__)
    assert module._LOAD_ERROR is None
    assert len(module.SETUP)==len(module.VERIFY)==134
    assert list(module.SETUP)==[row['task_id'] for row in module._AUTHORED]
    assert 'authored/1' in module.SETUP and module.SETUP.get('absent') is None
    assert resolve.call_count==0
    for key in ('authored/1','authored/5'):
        assert module.SETUP[key]['tool_relay']['python']=='/authored/python'
    assert resolve.call_count==2  # Only the two requested task values were constructed.


def test_lazy_map_lookup_is_fresh_and_keys_never_build():
    build=Mock(side_effect=lambda record:{'files':[]})
    selected=declarations.TaskDeclarations([{'task_id':'one'},{'task_id':'two'}],build)
    assert len(selected)==2 and list(selected)==['one','two'] and 'one' in selected
    assert selected.get('absent') is None and not build.called
    selected['one']['files'].append('caller mutation')
    assert selected['one']['files']==[]
    assert build.call_count==2


def test_execution_snapshot_checks_once_but_launch_admission_stays_fresh(execution,monkeypatch):
    _,_,store,_=execution
    artifact=runtime.prepare_execution('control',root=store);binding=runtime.bind(artifact,'service')
    verify=Mock(wraps=runtime._verify_manifest);monkeypatch.setattr(runtime,'_verify_manifest',verify)
    with runtime.execution_snapshot({'control':binding}):
        for _ in range(134):
            selected=runtime.resolve_execution('control')
            assert selected['descriptor_sha256']==artifact['descriptor_sha256']
            assert 'role' not in selected
            selected['runtime_paths'].append('/caller-only')
        assert verify.call_count==1
        assert '/caller-only' not in runtime.resolve_execution('control')['runtime_paths']
        marker=next(Path(artifact['path']).rglob('runtime_marker.py'))
        marker.chmod(0o644);marker.write_text('changed after metadata admission')
        with pytest.raises(runtime.DatasetRuntimeUnavailable,match='inventory changed'):
            runtime.admit_binding(binding,'service')
    with pytest.raises(runtime.DatasetRuntimeUnavailable):
        with runtime.execution_snapshot({'control':binding}): pass


def test_runtime_cache_is_scoped_and_not_a_process_wide_permission(execution,monkeypatch):
    _,_,store,_=execution;runtime.prepare_execution('control',root=store)
    verify=Mock(wraps=runtime._verify_manifest);monkeypatch.setattr(runtime,'_verify_manifest',verify)
    with runtime.execution_declarations():
        for _ in range(134): runtime.resolve_execution('control',root=store)
        assert verify.call_count==1
    with runtime.execution_declarations():runtime.resolve_execution('control',root=store)
    assert verify.call_count==2
    runtime.resolve_execution('control',root=store)
    assert verify.call_count==3


def test_native_manifest_and_record_index_reuse_is_metadata_scoped(tmp_path,monkeypatch):
    from data.scaffolds.native_research.adapter import Adapter,COMMIT
    folder=tmp_path/'native';folder.mkdir()
    path=folder/'manifest.json'
    path.write_text(json.dumps({'schema':'harnessgrad.native-research.v1','dataset':'browsecomp',
        'upstream_commit':COMMIT,'source':{},'records':[{'task_id':f'browsecomp/{i}'} for i in range(134)],'files':{}}))
    adapter=Adapter('browsecomp',root=folder)
    read=Mock(wraps=adapter._read_manifest);monkeypatch.setattr(adapter,'_read_manifest',read)
    with declarations.scope():
        for _ in range(134): assert len(adapter.records())==134
    assert read.call_count==1
    path.write_text('{}')
    with declarations.scope():
        with pytest.raises(ValueError,match='manifest'):adapter.records()
    assert read.call_count==2


def test_native_sheet_inventory_is_reused_only_for_metadata(monkeypatch):
    from data.scaffolds.spreadsheet_runtime import metadata,native
    inspect=Mock(return_value={'files':{},'deployment':'authored'})
    monkeypatch.setattr(native,'require_runtime',inspect)
    with declarations.scope():
        first=metadata.selected_native();first['files']['caller-only']='value'
        for _ in range(134):assert metadata.selected_native()['files']=={}
    assert inspect.call_count==1
    metadata.selected_native();assert inspect.call_count==2


def test_source_digest_cache_invalidates_when_mtime_is_restored(tmp_path,monkeypatch):
    source=tmp_path/'source';source.write_bytes(b'first')
    old=source.stat();first=scorer_input.source_digest(source)
    source.write_bytes(b'other');os.utime(source,ns=(old.st_atime_ns,old.st_mtime_ns))
    assert scorer_input.source_digest(source)==hashlib.sha256(b'other').hexdigest()!=first


def test_provider_inventory_uses_kind_contract_without_traversing_values(monkeypatch):
    from data.scaffolds import provider_worker as worker
    task={'task_id':'authored/one','goal':'public'}
    module=SimpleNamespace(KIND='dataset',VERIFIER_KIND='command')
    class NoTraversal:
        def values(self):raise AssertionError('inventory traversed the full verifier pool')
    module.VERIFY=NoTraversal()
    monkeypatch.setattr(worker.importlib,'import_module',lambda name:module)
    monkeypatch.setattr(registry,'load_split',lambda name:([task],{},None))
    monkeypatch.setattr(registry,'load_envs',lambda name:{task['task_id']:{'kind':'files'}})
    monkeypatch.setattr(registry,'load_tasks',lambda *args,**kwargs:([task],{},{task['task_id']:{'kind':'command'}}))
    result=worker._inventory('authored')
    assert result['verifier_kinds']==['command'] and result['tasks']==1


def test_provider_catalog_is_cached_only_in_a_frozen_loading_session(tmp_path,monkeypatch):
    from harnessgrad import dataset_provider as client
    monkeypatch.setattr(runtime,'descriptor',lambda name:{'provider':'adapter','descriptor_sha256':'authored'})
    monkeypatch.setenv('HG_DATA_AUTHORED',str(tmp_path/'first'))
    provider=client.DatasetProvider('authored',freeze_runtime=True)
    request=Mock(return_value={'tasks':[{'task_id':'one'}],'scorable':{},'metadata':{}})
    monkeypatch.setattr(provider,'_request',request)
    provider.catalog()['tasks'].clear()
    assert provider.catalog()['tasks']==[{'task_id':'one'}] and request.call_count==1
    monkeypatch.setenv('HG_DATA_AUTHORED',str(tmp_path/'second'))
    provider.catalog();assert request.call_count==2
    fresh=client.DatasetProvider('authored');monkeypatch.setattr(fresh,'_request',request)
    fresh.catalog();fresh.catalog();assert request.call_count==4


@pytest.mark.parametrize('dataset',['jericho','scienceworld'])
def test_interactive_mapping_keys_do_not_prepare_unselected_tasks(dataset,monkeypatch):
    import importlib
    module=importlib.import_module('data.datasets.'+dataset)
    records=[{'task_id':f'authored/{i}'} for i in range(134)]
    runtime_lookup=Mock(side_effect=AssertionError('mapping enumeration selected an engine'))
    monkeypatch.setattr(runtime,'resolve_execution',runtime_lookup)
    build=Mock(return_value={'files':[]})
    if dataset=='jericho':
        monkeypatch.setitem(module.__dict__,'_RECORDS',records)
        mapping=module._map(build)
    else:
        monkeypatch.setattr(module,'_ensure',lambda:records)
        monkeypatch.setattr(module,'_setup',build)
        mapping=module._Lazy().get('SETUP')
    assert len(mapping)==134 and list(mapping)==[row['task_id'] for row in records]
    assert 'authored/3' in mapping and not build.called and not runtime_lookup.called
    mapping['authored/3']
    build.assert_called_once_with(records[3])


def test_repeated_source_reads_are_bounded_by_declaration_scope(tmp_path,monkeypatch):
    source=tmp_path/'code.py';source.write_text('authored source')
    read=Mock(wraps=scorer_input._file_digest);monkeypatch.setattr(scorer_input,'_file_digest',read)
    with declarations.scope():
        for _ in range(134):scorer_input.source_digest(source)
    assert read.call_count==1
    with declarations.scope():scorer_input.source_digest(source)
    assert read.call_count==2


def test_travelplanner_checks_world_once_per_task_and_detects_mid_batch_change(tmp_path,monkeypatch):
    from data.scaffolds.native_research.adapter import Adapter,COMMIT
    from data.scaffolds.native_research import component_runtime
    folder=tmp_path/'native';(folder/'database').mkdir(parents=True)
    world=folder/'database/authored.txt';world.write_text('authored world')
    case=folder/'case.json';case.write_text(json.dumps({'task_id':'travelplanner/one','question':'authored'}))
    manifest={'schema':'harnessgrad.native-research.v1','dataset':'travelplanner','upstream_commit':COMMIT,
              'source':{},'judge_model':'authored-no-calls','records':[{'task_id':'travelplanner/one','case':'case.json'}],
              'files':{'case.json':hashlib.sha256(case.read_bytes()).hexdigest(),
                       'database/authored.txt':hashlib.sha256(world.read_bytes()).hexdigest()}}
    (folder/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(component_runtime,'binding',lambda *a:{'python':'/authored/python','runtime_paths':[]})
    adapter=Adapter('travelplanner',root=folder)
    check=Mock(wraps=adapter.checked_world);monkeypatch.setattr(adapter,'checked_world',check)
    with declarations.scope():
        spec=adapter.verifier('travelplanner/one')
        assert spec['kind']=='command' and check.call_count==1
        world.write_text('changed after metadata selection')
        with pytest.raises(ValueError,match='world/database'):
            adapter.verifier('travelplanner/one')
