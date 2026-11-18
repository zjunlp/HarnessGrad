"""Personal B1 budgets and configuration; scripted replies, never a real model."""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from harnessgrad.config import MethodConfig
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.fixtures import ScriptedModelClient
from harnessgrad.personal.model import ModelSession
from harnessgrad.personal.profile import ModelLimits, load_model_settings
from harnessgrad.personal.service import PersonalService

ROOT = Path(__file__).resolve().parents[2]
KEY = 'synthetic-model-credential'


def settings(**extra):
    return load_model_settings({'HG_METHOD_BASE_URL': 'http://127.0.0.1:1/v1',
                                'HG_METHOD_MODEL': 'fixture-model', 'HG_METHOD_API_KEY': KEY, **extra})


@pytest.fixture
def selected(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    service = PersonalService(tmp_path / 'state')
    return service, service.add_project(project)['id']


def session(selected, replies=({'fixture': True},), **options):
    service, project = selected
    client = ScriptedModelClient(replies)
    return ModelSession(service.store, project, settings(), fixture=True, client=client, **options), client


def test_config_is_explicit_frozen_and_uses_existing_parser(tmp_path, monkeypatch):
    env_file = tmp_path / 'selected.env'
    env_file.write_text('HG_METHOD_MODEL=file-model\nHG_METHOD_BASE_URL=http://127.0.0.1:1/v1\n'
                        f'HG_METHOD_API_KEY={KEY}\nHG_METHOD_TIMEOUT_S=9\n', encoding='utf-8')
    value = load_model_settings({'HG_METHOD_MODEL': 'selected-model', 'HG_AGENT_MODEL': 'not-selected',
                                 'HG_AGENT_BASE_URL': 'private', 'HG_AGENT_TIMEOUT_S': 'invalid'}, env_file=env_file)
    monkeypatch.setenv('HG_METHOD_MODEL', 'changed-after-snapshot')
    assert isinstance(value.method, MethodConfig) and value.method.model == 'selected-model'
    assert value.method.timeout_s == 9 and value.endpoint.endswith('/v1/chat/completions')
    assert KEY not in repr(value) + json.dumps(value.identity())
    with pytest.raises(ValueError, match='needs HG_METHOD'):
        load_model_settings({'HG_AGENT_MODEL': 'actor', 'HG_IMPROVER_MODEL': 'not-a-default'})
    with pytest.raises(ValueError, match='needs HG_METHOD'):
        load_model_settings({'HG_METHOD_MODEL': ''}, env_file=env_file)


@pytest.mark.parametrize('change', [
    {'HG_METHOD_TIMEOUT_S': KEY}, {'HG_METHOD_BASE_URL': 'http://user:private@localhost/v1'},
    {'HG_METHOD_BASE_URL': 'http://localhost/v1?key=private'}, {'HG_METHOD_API_KEY': 'bad\nprivate'},
    {'HG_METHOD_MODEL': KEY},
])
def test_config_errors_do_not_echo_credentials(change):
    with pytest.raises((ValueError, RuntimeError)) as exc:
        settings(**change)
    assert KEY not in str(exc.value) and 'private' not in str(exc.value)


def test_two_stages_resume_one_budget_and_never_store_input_or_credentials(selected):
    first, client = session(selected, [{'private': 'PRIVATE REPLY'}, {'decision': 'no_change'}],
                            limits=ModelLimits(max_calls=2))
    with first:
        assert first.for_stage('plan', system_prompt='Plan only.')('PRIVATE PROMPT')['private'] == 'PRIVATE REPLY'
    service, project = selected
    rotated = settings(HG_METHOD_API_KEY='rotated-synthetic-credential')
    second = ModelSession(EventStore(service.store.path), project, rotated, job_id=first.job_id,
                          limits=ModelLimits(max_calls=2), fixture=True, client=client)
    with second:
        assert second.for_stage('propose', system_prompt='Propose only.')('Second stage.') == {'decision': 'no_change'}
        with pytest.raises(ValueError, match='logical model call limit'):
            second('A third stage must not dispatch.')
    usage = second.usage()
    assert usage['logical_calls'] == 2 and len(client.requests) == 2
    assert [r['messages'][0]['content'] for r in client.requests] == ['Plan only.', 'Propose only.']
    assert usage['prompt_tokens'] is None and usage['transport_attempts'] == 0
    with service.store.connection() as db:
        dump = '\n'.join(db.iterdump())
    for private in (KEY, rotated.method.api_key, 'PRIVATE PROMPT', 'PRIVATE REPLY'):
        assert private not in dump
    assert service.snapshot()['model_calls'] == 0
    assert service.snapshot()['model_usage']['fixture_calls'] == 2


@pytest.mark.parametrize('change', ['project', 'profile', 'model', 'limit', 'fixture'])
def test_resumption_cannot_change_the_frozen_scope(selected, tmp_path, change):
    first, _ = session(selected)
    service, project = selected
    options = {'profile_id': 'default', 'fixture': True, 'job_id': first.job_id,
               'limits': ModelLimits(), 'client': ScriptedModelClient([])}
    config = settings()
    if change == 'project':
        other = tmp_path / 'other'; other.mkdir(); project = service.add_project(other)['id']
    elif change == 'profile': options['profile_id'] = 'other'
    elif change == 'model': config = settings(HG_METHOD_MODEL='other-model')
    elif change == 'limit': options['limits'] = ModelLimits(max_calls=20)
    else: options.update(fixture=False, client=None)
    with pytest.raises(ValueError, match='snapshot'):
        ModelSession(service.store, project, config, **options)


def test_live_executor_is_not_stolen_and_revisions_are_bounded(selected):
    first, client = session(selected, [{}, {}], limits=ModelLimits(max_revisions=1))
    service, project = selected
    other = ModelSession(service.store, project, settings(), job_id=first.job_id,
                         limits=first.limits, fixture=True, client=client)
    with first:
        with pytest.raises(ValueError, match='executor|lock'):
            with other: pass
        assert first.reserve_revision() == 1
        with pytest.raises(ValueError, match='revision limit'):
            first.reserve_revision()
        first('One request.')
    with other:
        other('Another request.')
    assert other.usage()['logical_calls'] == 2 and other.usage()['revisions'] == 1


def test_invalid_or_secret_input_does_not_reserve_or_dispatch(selected):
    value, client = session(selected, limits=ModelLimits(max_input_bytes=240))
    with value:
        for prompt in ('', KEY, 'x' * 241):
            with pytest.raises(ValueError): value(prompt)
    assert value.usage()['logical_calls'] == 0 and not client.requests


def test_waiting_outside_execution_does_not_reset_or_spend_active_time(selected):
    now = [100.0]
    clock = lambda: now[0]
    wall = lambda: 1000 + now[0]
    first, client = session(selected, [{}], limits=ModelLimits(execution_seconds=1), clock=clock, wall_clock=wall)
    with first:
        first('First request.'); now[0] += .3
    now[0] += 1000
    service, project = selected
    resumed = ModelSession(service.store, project, settings(), job_id=first.job_id,
                           limits=first.limits, fixture=True, client=client, clock=clock, wall_clock=wall)
    with resumed:
        now[0] += .8
        with pytest.raises(ValueError, match='execution budget'): resumed('Too late.')
    assert resumed.usage()['execution_seconds'] == pytest.approx(1.1)
    assert len(client.requests) == 1
    with pytest.raises(ValueError, match='execution budget'):
        with resumed: pass


def test_process_exit_preserves_reservation_and_marks_unknown_usage(selected):
    first, _ = session(selected, limits=ModelLimits(max_calls=2, execution_seconds=30))
    service, project = selected
    code = '''
import os,sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from harnessgrad.personal.event_store import EventStore
from harnessgrad.personal.model import ModelSession
from harnessgrad.personal.profile import ModelLimits,load_model_settings
settings=load_model_settings({'HG_METHOD_BASE_URL':'http://127.0.0.1:1/v1',
 'HG_METHOD_MODEL':'fixture-model','HG_METHOD_API_KEY':'synthetic-model-credential'})
store=EventStore(Path(sys.argv[2]))
job=ModelSession(store,sys.argv[3],settings,job_id=sys.argv[4],fixture=True,client=object(),
 limits=ModelLimits(max_calls=2,execution_seconds=30))
job.__enter__()
store.models.reserve(job.job_id,job._lease,'plan','0'*64,7,job._wall_clock())
os._exit(0)
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT),
                             str(service.store.path), project, first.job_id],
                            cwd=service.root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    client = ScriptedModelClient([{}])
    resumed = ModelSession(service.store, project, settings(), job_id=first.job_id,
                           limits=first.limits, fixture=True, client=client)
    with resumed:
        resumed('Continue conservatively.')
        with pytest.raises(ValueError, match='logical model call limit'): resumed('No reset.')
    usage = resumed.usage()
    assert usage['logical_calls'] == 2 and usage['recovered_executions'] == 1
    assert usage['calls'][0]['status'] == 'unknown'
    assert usage['transport_attempts'] is None and usage['prompt_tokens'] is None
    assert any(event['type'] == 'model.recovered' for event in service.store.job_events(first.job_id))


def test_offline_cli_demo_ignores_real_model_environment(tmp_path):
    project=tmp_path/'project';project.mkdir();state=tmp_path/'state'
    env={k:v for k,v in os.environ.items() if not k.startswith(('HG_METHOD_','PYTHONPATH'))}
    env.update(PYTHONDONTWRITEBYTECODE='1',HG_METHOD_API_KEY='unused-real-looking-secret',
               HG_METHOD_BASE_URL='https://unused.invalid',HG_METHOD_MODEL='must-not-call')
    result=subprocess.run([sys.executable,'-m','harnessgrad.cli','personal','model-demo',
                           '--state-root',str(state),'--project',str(project)],
                          cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode == 0,result.stderr
    output=json.loads(result.stdout)
    assert output['fixture'] and output['budget_exhausted'] and not output['applied']
    assert output['scripted_calls'] == 2 and output['real_model_calls'] == 0
    assert output['usage']['logical_calls'] == 2 and output['usage']['status'] == 'completed'
    assert 'unused-real-looking-secret' not in result.stdout+result.stderr


def test_config_view_does_not_construct_client_or_create_state(tmp_path, monkeypatch, capsys):
    from harnessgrad.entrypoints.personal import main
    from methods.endpoint_client import Client
    monkeypatch.setattr(Client,'__init__',lambda *a,**k:(_ for _ in ()).throw(AssertionError('client created')))
    for key,value in {'HG_METHOD_MODEL':'fixture-model','HG_METHOD_BASE_URL':'http://127.0.0.1:1/v1',
                      'HG_METHOD_API_KEY':KEY}.items():monkeypatch.setenv(key,value)
    state=tmp_path/'must-not-exist'
    assert main(['model-config','--state-root',str(state)]) == 0
    output=capsys.readouterr()
    assert KEY not in output.out+output.err and json.loads(output.out)['credential_present']
    assert not state.exists()


def test_legacy_constructor_and_usage_contract_are_unchanged():
    from harnessgrad.customization.model import ProposalModel
    assert list(inspect.signature(ProposalModel).parameters) == ['base_url','api_key','model','max_calls','timeout']
    old=ProposalModel(base_url='http://127.0.0.1:1/v1',api_key=KEY,model='fixture',max_calls=1)
    client=ScriptedModelClient([{'no_change':'fixture'}]);old._client=client
    assert old('fixture prompt') == {'no_change':'fixture'}
    assert client.requests[0]['messages'][0]['content'] == (
        'You are improving a harness from the supplied feedback. Return only one '
        'JSON object that follows the requested schema; do not use Markdown fences.')
    with pytest.raises(ValueError,match='logical model call limit'):old('not sent')
    assert old.usage() == {'logical_calls':1,'transport_attempts':0,'calls':[
        {'prompt_tokens':None,'completion_tokens':None,'transport_attempts':0,'unknown_transport_attempts':0}]}
