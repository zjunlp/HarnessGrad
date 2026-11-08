"""Real subprocess protocol controls, without downloads or optional packages."""
from __future__ import annotations

from contextlib import nullcontext
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from harnessgrad import dataset_provider as client
from data.scaffolds import provider_worker as worker
from tools import dataset_lint


def test_inventory_does_not_materialize_every_verifier_or_return_private_answers(monkeypatch):
    from data.scaffolds import registry
    task = {'task_id':'authored/0', 'goal':'public question'}
    module = SimpleNamespace(KIND='dataset', SPLIT_SOURCE='none', SPLIT=None,
                             _SPEC=SimpleNamespace(VERIFIER_KIND='command'))
    selected=[]
    def declarations(name, *, task_ids):
        selected.append(task_ids)
        return [task], {task['task_id']:{'files':[]}}, {task['task_id']:{'kind':'command','secret':'private'}}
    monkeypatch.setattr(registry, '_local_provider', nullcontext)
    monkeypatch.setattr(worker.importlib, 'import_module', lambda name:module)
    monkeypatch.setattr(registry, 'load_split', lambda name:([task], {'authored/0':'private'}, None))
    monkeypatch.setattr(registry, 'load_envs', lambda name:{'authored/0':{'kind':'files'}})
    monkeypatch.setattr(registry, 'load_tasks', declarations)
    value=worker.dispatch({'dataset':'authored','operation':'inventory'})
    assert selected==[['authored/0']]
    assert value['tasks']==1 and value['sample']==task
    assert value['metadata']['SPLIT_SOURCE']=='none'
    assert value['verifier_kinds']==['command'] and value['env_kinds']==['files']
    assert 'private' not in json.dumps(value)


def test_control_snapshot_keeps_selected_reference_callbacks_in_the_provider(monkeypatch):
    from data.scaffolds import registry
    record={'task_id':'authored/one', 'gold':{'answer':'private answer','supporting_facts':[['private',0]]},
            'row':{'unused':'must not leave the worker'}}
    module=SimpleNamespace(__name__='authored', control_task=lambda tid:record,
        task_for=lambda rec:{'task_id':rec['task_id'],'goal':'public'},
        setup_for=lambda rec:{'files':[]}, verify_for=lambda rec:{'kind':'command'},
        DEFAULT_ENV={'kind':'files'}, EVIDENCE_AWARE=True,
        ORACLE_SUBMISSION=lambda gold:{'answer':gold['answer']}, NOOP_EXPECTED=lambda rec:.25)
    monkeypatch.setattr(registry, '_local_provider', nullcontext)
    monkeypatch.setattr(worker.importlib, 'import_module', lambda name:module)
    value=worker.dispatch({'dataset':'authored','operation':'control','task_ids':['authored/one']})
    assert value['record']=={key:record[key] for key in ('task_id','gold')}
    assert value['submission']=={'answer':'private answer'}
    assert value['prediction']['predicted_evidence']==[['private',0]]
    assert value['noop_expected']==.25
    assert 'private' not in json.dumps(value['task'])


@pytest.mark.parametrize('operation,selection', [('control',[]),('control',['one','two']),('inventory',['one'])])
def test_public_entry_operations_refuse_ambiguous_selections(subprocess_provider, operation, selection):
    _,_,data=subprocess_provider
    request={'schema':worker.SCHEMA,'dataset':'gsm8k','operation':operation,
        'descriptor_sha256':'descriptor-control','runtime_id':'runtime-control','runtime_path':sys.prefix,
        'configuration':{'HG_DATA_GSM8K':str(data)},'task_ids':selection}
    with pytest.raises(ValueError, match='selection|selected task'):
        worker._request(request)


@pytest.mark.parametrize('operation,body', [
    ('inventory', {}),
    ('inventory', {'metadata':{},'tasks':True,'setup':{},'sample':{},'env_kinds':[],'verifier_kinds':[]}),
    ('inventory', {'metadata':{},'tasks':1,'setup':{},'sample':None,'env_kinds':[],'verifier_kinds':[]}),
    ('control', {}), ('control', {'refused':True}),
])
def test_public_entry_clients_reject_malformed_responses(subprocess_provider, monkeypatch, operation, body):
    provider,_,_=subprocess_provider
    monkeypatch.setattr(provider, '_request', lambda *args,**kwargs:body)
    with pytest.raises(client.DatasetProviderError) as caught:
        getattr(provider,operation)()
    assert caught.value.error_type=='ProtocolError'


@pytest.mark.parametrize('field,value', [
    ('noop_expected',True),('noop_expected',float('inf')),('prediction',[]),
    ('task',{'task_id':'different'}),('record',{'task_id':'','gold':{}}),
])
def test_control_client_checks_reference_and_expectation_shapes(subprocess_provider, monkeypatch, field, value):
    provider,_,_=subprocess_provider
    body={'record':{'task_id':'one','gold':{}},'task':{'task_id':'one'},
          'setup':{},'verifier':{},'env':{},'prediction':None,'submission':None,'noop_expected':.25}
    monkeypatch.setattr(provider,'_request',lambda *args,**kwargs:body)
    assert provider.control('one')['noop_expected']==.25
    body[field]=value
    with pytest.raises(client.DatasetProviderError) as caught:
        provider.control('one')
    assert caught.value.error_type=='ProtocolError'


_RESPONSE = '''\
import json,os,sys
request=json.load(sys.stdin)
response={key:request[key] for key in ('schema','dataset','operation','descriptor_sha256','runtime_id')}
response['interpreter']={'python':sys.executable,'prefix':sys.prefix}
response['result']={'tasks':[], 'scorable':{}, 'metadata':{}, 'environment':dict(os.environ),
                    'isolated':sys.flags.isolated, 'bytecode':sys.dont_write_bytecode}
print(json.dumps(response))
'''


@pytest.fixture
def subprocess_provider(tmp_path, monkeypatch):
    source = tmp_path / "source"
    folder = source / "data/scaffolds"
    folder.mkdir(parents=True)
    (source / "data/__init__.py").write_text("")
    (folder / "__init__.py").write_text("")
    program = folder / "provider_worker.py"
    program.write_text(_RESPONSE)
    monkeypatch.setattr(client, "ROOT", source)
    declaration = {"provider": "qa", "descriptor_sha256": "descriptor-control"}
    runtime = {"python": sys.executable, "path": sys.prefix, "runtime_id": "runtime-control",
               "descriptor_sha256": declaration["descriptor_sha256"]}
    monkeypatch.setattr(client.dataset_runtime, "descriptor", lambda name: declaration)
    monkeypatch.setattr(client.dataset_runtime, "resolve", lambda name: runtime)
    monkeypatch.setattr(client.dataset_runtime, "check", lambda name: {"present": True})
    data = tmp_path / "data-root"
    monkeypatch.setenv("HG_DATA_GSM8K", str(data))
    return client.DatasetProvider("gsm8k"), program, data


def test_worker_gets_frozen_storage_and_no_ambient_python_or_model_environment(
        subprocess_provider, monkeypatch, tmp_path):
    provider, _, data = subprocess_provider
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('ambient hook entered')")
    monkeypatch.setenv("PYTHONPATH", str(poison))
    monkeypatch.setenv("HG_AGENT_API_KEY", "must-not-enter-worker")
    monkeypatch.setenv("HG_JUDGE_MODEL", "must-not-be-inherited")
    value = provider.catalog()
    environment = value["environment"]
    assert environment["HG_DATA_GSM8K"] == str(data)
    assert environment["HG_REVIEWED_ENVIRONMENT_ONLY"] == "1"
    assert environment["PATH"] == os.defpath
    assert "PYTHONPATH" not in environment
    assert "HG_AGENT_API_KEY" not in environment and "HG_JUDGE_MODEL" not in environment
    assert value["isolated"] and value["bytecode"]
    assert not Path(environment["HOME"]).exists(), "the private instance must be cleaned"


def test_explicit_storage_snapshot_wins_without_changing_parent_environment(
        subprocess_provider, tmp_path):
    provider, _, original = subprocess_provider
    selected = tmp_path / "selected-root"
    value = provider.catalog(config_snapshot={"HG_DATA_GSM8K": str(selected)})
    assert value["environment"]["HG_DATA_GSM8K"] == str(selected)
    assert os.environ["HG_DATA_GSM8K"] == str(original)


@pytest.mark.parametrize("snapshot", [
    {"HG_AGENT_API_KEY": "secret"}, {"HG_DATA_GSM8K": "relative"}, {"HG_DATA_GSM8K": 3},
])
def test_configuration_does_not_accept_credentials_or_ambiguous_paths(subprocess_provider, snapshot):
    provider, _, _ = subprocess_provider
    with pytest.raises(ValueError):
        provider.catalog(config_snapshot=snapshot)


@pytest.mark.parametrize("change", [
    "response['runtime_id']='different'",
    "response['descriptor_sha256']='different'",
    "response['interpreter']['prefix']='/different-runtime'",
    "response['interpreter']['python']='/different-python'",
])
def test_response_must_bind_the_selected_runtime_and_interpreter(subprocess_provider, change):
    provider, program, _ = subprocess_provider
    program.write_text(_RESPONSE.replace("print(json.dumps(response))", change + "\nprint(json.dumps(response))"))
    with pytest.raises(client.DatasetProviderError) as caught:
        provider.catalog()
    assert caught.value.error_type == "IdentityMismatch"


def test_worker_timeout_is_not_a_score_and_reaps_the_process(subprocess_provider):
    provider, program, data = subprocess_provider
    data.mkdir()
    marker = data / "worker.pid"
    program.write_text(f"import os,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n")
    provider.timeout_s = 0.5
    with pytest.raises(client.DatasetProviderError) as caught:
        provider.catalog()
    assert caught.value.error_type == "TimeoutExpired"
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("allow_unprovisioned", [False, True])
@pytest.mark.parametrize("fault,error_type,status", [
    ("identity", "IdentityMismatch", "invalid"),
    ("malformed_json", "ProtocolError", "invalid"),
    ("crash", "ProtocolError", "invalid"),
    ("nonzero_exit", "ProtocolError", "invalid"),
    ("worker_exception", "NameError", "invalid"),
    ("timeout", "TimeoutExpired", "unavailable"),
    ("missing_python", "FileNotFoundError", "unavailable"),
])
def test_client_failures_from_real_subprocesses_fail_lint(
        subprocess_provider, monkeypatch, capsys, allow_unprovisioned, fault, error_type, status):
    provider, program, data = subprocess_provider
    programs = {
        "identity": _RESPONSE.replace("print(json.dumps(response))",
                                      "response['runtime_id']='wrong'\nprint(json.dumps(response))"),
        "malformed_json": "print('not JSON')\n",
        "crash": "raise RuntimeError('authored startup failure')\n",
        "nonzero_exit": _RESPONSE + "sys.exit(1)\n",
        "worker_exception": _RESPONSE.replace("print(json.dumps(response))",
            "response.pop('result')\n"
            "response['error']={'type':'NameError','detail':'missing_worker_handler'}\n"
            "print(json.dumps(response))\nsys.exit(1)"),
        "timeout": "import time\ntime.sleep(30)\n",
    }
    if fault == "missing_python":
        monkeypatch.setattr(client.dataset_runtime, "resolve", lambda name: {
            "python": str(data / "missing-python"), "path": str(data),
            "runtime_id": "runtime-control", "descriptor_sha256": "descriptor-control"})
    else:
        program.write_text(programs[fault])
    provider.timeout_s = 0.5
    monkeypatch.setattr(dataset_lint, "DatasetProvider", lambda name: provider)
    argv = ["--dataset", "gsm8k", "--json"]
    if allow_unprovisioned:
        argv.append("--allow-unprovisioned")

    assert dataset_lint.main(argv) == 1
    result, = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["errors"]
    assert result["status"] == "invalid" and result["unprovisioned"] is None
    snapshot = result["provision_status"]
    assert snapshot["present"] is False and snapshot["status"] == status
    assert snapshot["error_type"] == error_type
    assert snapshot["scope"] == "dataset-provider-load"


@pytest.mark.parametrize("status", ["unavailable", "invalid"])
def test_runtime_changes_after_initial_check_keep_their_status(subprocess_provider, monkeypatch, status):
    provider, _, _ = subprocess_provider

    def changed_runtime(name):
        raise client.dataset_runtime.DatasetRuntimeUnavailable("runtime changed", status=status)

    monkeypatch.setattr(client.dataset_runtime, "resolve", changed_runtime)
    assert provider.check() == {
        "present": False, "status": status, "scope": "dataset-provider-runtime",
        "detail": "runtime changed", "tasks": 0}


def test_catalog_keeps_answers_separate_without_touching_execution_declarations(monkeypatch):
    from data.scaffolds import registry

    task = {"task_id": "fixture/test/0", "goal": "answer the question", "inputs": []}

    class Adapter:
        KIND = "dataset"
        SPLIT = None
        OFFICIAL_EVAL = None

        def __getattr__(self, name):
            if name in {"SETUP", "VERIFY", "ENV"}:
                raise AssertionError("catalog evaluated execution declarations")
            raise AttributeError(name)

    adapter = Adapter()
    monkeypatch.setattr(registry, "_local_provider", nullcontext)
    monkeypatch.setattr(registry, "load_split", lambda name: ([task], {task["task_id"]: "private-answer"}, None))
    monkeypatch.setattr(importlib, "import_module", lambda name: adapter)
    result = worker.dispatch({"dataset": "fixture", "operation": "catalog"})
    assert result["tasks"] == [task]
    assert "private-answer" not in json.dumps(result["tasks"])
    assert result["scorable"] == {task["task_id"]: "private-answer"}
    assert result["metadata"]["SPLIT"] is None
    assert "SPLIT_GROUPS" not in result["metadata"]


@pytest.mark.parametrize("selection", [None, [], ["chosen"]])
def test_resolve_preserves_selection_semantics_for_both_existing_registry_paths(monkeypatch, selection):
    from data.scaffolds import registry

    calls = []

    def declarations(name, *, task_ids):
        calls.append(("declarations", task_ids))
        return [], {}, {}

    def environments(name, *, task_ids):
        calls.append(("environments", task_ids))
        return {}

    monkeypatch.setattr(registry, "_local_provider", nullcontext)
    monkeypatch.setattr(registry, "load_tasks", declarations)
    monkeypatch.setattr(registry, "load_envs", environments)
    result = worker.dispatch({"dataset": "fixture", "operation": "resolve", "task_ids": selection})
    assert calls == [("declarations", selection), ("environments", selection)]
    assert result == {"tasks": [], "setups": {}, "verifiers": {}, "envs": {}}


@pytest.mark.parametrize("status", ["unavailable", "invalid"])
def test_missing_or_invalid_runtime_is_reported_without_launching_worker(subprocess_provider, monkeypatch, status):
    provider, _, _ = subprocess_provider
    monkeypatch.setattr(client.dataset_runtime, "check", lambda name: {
        "present": False, "status": status, "scope": "runtime", "detail": "prepare first"})
    monkeypatch.setattr(provider, "_request", lambda *args, **kwargs: pytest.fail("must not launch"))
    assert provider.check() == {"present": False, "status": status,
                                "scope": "dataset-provider-runtime", "detail": "prepare first", "tasks": 0}


def test_duplicate_selection_is_rejected_before_worker_execution(subprocess_provider):
    provider, _, _ = subprocess_provider
    with pytest.raises(ValueError, match="duplicates"):
        provider.resolve(["chosen", "chosen"])
