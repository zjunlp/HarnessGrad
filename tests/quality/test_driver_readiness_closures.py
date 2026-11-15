"""Offline contracts for run configuration and boundary readiness ownership."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from harnessgrad import env_file
from harnessgrad.orchestration import boundary_readiness as boundary
from harnessgrad.orchestration import improver_readiness as improver


class Reporter:
    def __init__(self):
        self.events = []

    def note(self, message, **kwargs):
        self.events.append((message, kwargs))


@pytest.mark.parametrize('name', ['none', 'noop', 'null', ' NONE '])
def test_disabled_improver_clears_only_name_and_never_resolves(name):
    request = improver.ImproverRequest(name, None, None)
    assert request.environment_patch() == {'HG_IMPROVER_NAME': None}
    reporter = Reporter()
    def forbidden(*args, **kwargs):
        raise AssertionError('disabled improver attempted resolution or probe')
    observed = improver.resolve_readiness(request, reporter=reporter,
        load_config=forbidden, resolve_config=forbidden, provider_url=forbidden,
        probe=forbidden)
    assert observed == improver.ImproverReadiness({}, None, '')
    assert len(reporter.events) == 1


def test_resolution_error_is_soft_but_provider_error_propagates():
    reporter = Reporter()
    request = improver.ImproverRequest('cli', 'override', 'high')
    assert request.environment_patch() == {'HG_IMPROVER_NAME': 'cli',
        'HG_IMPROVER_MODEL': 'override', 'HG_IMPROVER_EFFORT': 'high'}
    def broken():
        raise RuntimeError('unreadable')
    result = improver.resolve_readiness(request, reporter=reporter,
        load_config=broken, resolve_config=lambda *a, **kw: None)
    assert result.declaration == {} and not result.refused
    assert 'unreadable' in reporter.events[0][0]
    declaration = dict(ok=True, name='cli', version='', sha256='f'*64, kind='cli')
    def broken_provider(_):
        raise RuntimeError('provider')
    with pytest.raises(RuntimeError, match='provider'):
        improver.resolve_readiness(request, reporter=reporter,
            load_config=lambda: {}, resolve_config=lambda *a, **kw: declaration,
            provider_url=broken_provider)


@pytest.mark.parametrize('answer', [True, False])
def test_tool_gate_preserves_tri_state_and_probe_identity(monkeypatch, answer):
    monkeypatch.setattr(improver.platform_config, 'current', lambda: SimpleNamespace(
        improver=SimpleNamespace(skip_tool_gate='')))
    declaration = dict(ok=True, name='cli', version='', sha256='f'*64,
                       kind='cli', model='declared')
    calls = []
    def probe(*args):
        calls.append(args)
        return answer, 'offline result'
    result = improver.resolve_readiness(improver.ImproverRequest('cli', None, None),
        reporter=Reporter(), load_config=lambda: {},
        resolve_config=lambda *a, **kw: declaration,
        provider_url=lambda _: 'http://offline.invalid', probe=probe)
    assert calls == [('http://offline.invalid', 'declared')]
    assert result.tool_calls is answer and result.refused is (not answer)
    assert result.declaration is declaration


def test_container_checks_selected_images_in_order_and_stop_first(monkeypatch):
    calls = []
    monkeypatch.setattr(boundary.container, 'available', lambda: 'offline-docker')
    def check(root, image):
        calls.append((root, image))
        return {'ok': image != 'b', 'detail': image}
    monkeypatch.setattr(boundary.container, 'self_check', check)
    envs = {key: {'kind': 'exec', 'image': image} for key, image in
            [('one', 'c'), ('two', 'a'), ('three', 'b'), ('four', 'a')]}
    assert boundary.check_container_boundaries(envs, reporter=Reporter()) is False
    assert calls == [(boundary.PLATFORM_ROOT, 'a'), (boundary.PLATFORM_ROOT, 'b')]


def test_container_failure_and_files_only_never_probe_images(monkeypatch):
    def forbidden(*args):
        raise AssertionError('unexpected image probe')
    monkeypatch.setattr(boundary.container, 'self_check', forbidden)
    monkeypatch.setattr(boundary.container, 'available', lambda: None)
    assert boundary.check_container_boundaries({}, reporter=Reporter())
    assert not boundary.check_container_boundaries(
        {'a': {'kind': 'exec', 'image': 'a'}}, reporter=Reporter())


def test_sandbox_failure_does_not_observe_envelope(monkeypatch, tmp_path):
    calls = []
    def check(*args):
        calls.append(args)
        return {'ok': False, 'detail': 'offline refusal'}
    monkeypatch.setattr(boundary.sandbox, 'self_check', check)
    def forbidden():
        raise AssertionError('envelope after refusal')
    monkeypatch.setattr(boundary.sandbox, 'envelope', forbidden)
    assert boundary.check_sandbox_boundary(True, tmp_path, {}, reporter=Reporter()) is None
    assert calls == [(boundary.PLATFORM_ROOT, tmp_path)]


def test_exec_description_keeps_host_boundary_state(monkeypatch, tmp_path):
    def forbidden(*args):
        raise AssertionError('no sandbox probe when disabled')
    monkeypatch.setattr(boundary.sandbox, 'self_check', forbidden)
    result = boundary.check_sandbox_boundary(False, tmp_path,
        {'a': {'kind': 'exec', 'image_digest': 'sha256:abc',
               'services': [{'name': 'mock-api'}]}}, reporter=Reporter())
    assert result.state == {'requested': False, 'active': False,
                            'plan_version': boundary.sandbox.PLAN_VERSION}
    assert result.line == 'exec — sha256:abc + services[mock-api] (host-side bwrap OFF for the method)'


def test_dotenv_install_retains_explicit_values_and_resets_snapshot(monkeypatch, tmp_path):
    from harnessgrad import config
    path = tmp_path / '.env'
    path.write_text('HG_AGENT_MODEL=file\nHG_AGENT_BACKEND=mock\n')
    monkeypatch.delenv('HG_REVIEWED_ENVIRONMENT_ONLY', raising=False)
    monkeypatch.setenv('HG_AGENT_MODEL', 'explicit')
    monkeypatch.delenv('HG_AGENT_BACKEND', raising=False)
    calls = []
    monkeypatch.setattr(config, 'reset', lambda: calls.append('reset'))
    assert env_file.install_env_file(path) == {'HG_AGENT_MODEL': 'file', 'HG_AGENT_BACKEND': 'mock'}
    import os
    assert os.environ['HG_AGENT_MODEL'] == 'explicit'
    assert os.environ['HG_AGENT_BACKEND'] == 'mock'
    assert calls == ['reset']
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setattr(env_file, 'read_env_file', lambda _: pytest.fail('reviewed environment read file'))
    assert env_file.install_env_file(path) == {}
    assert calls == ['reset', 'reset']


def test_scenario_updates_preserve_partial_assignment_before_conflict():
    from harnessgrad.orchestration import scenario_config
    current = scenario_config.ScenarioOptions('old', None, 'train', 'all', 0, 1, None, 1)
    spec = dict(dataset='new', split='declared', side='eval', sampling='all',
                rounds=0, trials=1, budget={'jobs': 1})
    updates = scenario_config.scenario_option_updates(current, spec, {'side'},
                                                      platform_root=Path('/tmp'))
    assert next(updates) == ('dataset', 'new')
    assert next(updates) == ('split', 'declared')
    with pytest.raises(ValueError, match='scenario side conflicts'):
        next(updates)


@pytest.mark.parametrize('backend,model,key,live', [
    ('mock', '', '', False), ('mock', 'm', 'k', False),
    ('openai', 'm', '', False), ('openai', 'm', 'k', True)])
def test_agent_description_and_gate_use_declared_configuration(backend, model, key, live):
    from harnessgrad.orchestration import run_configuration
    facts = run_configuration.describe_agent(SimpleNamespace(backend=backend, model=model, api_key=key))
    assert facts == dict(backend=backend, model=model, credentials=bool(key), live=live)
    calls = []
    def load():
        calls.append('load')
        return 'registry'
    def banned(registry, name):
        calls.append((registry, name))
        return ''
    reason = run_configuration.agent_refusal(facts, load_improvers=load, forbidden_reason=banned)
    assert calls == ['load', ('registry', model)]
    assert bool(reason) is (backend != 'mock' and not live)


def test_builtin_registry_admits_deepseek_v4_pro_as_agent():
    from harnessgrad.orchestration import run_configuration
    from tools import improver as registry

    agent = SimpleNamespace(backend="openai", model="deepseek-v4-pro", api_key="fixture-key")
    facts = run_configuration.describe_agent(agent)
    assert run_configuration.agent_refusal(
        facts,
        load_improvers=lambda: registry.load_config(registry.DEFAULT_CONFIG),
        forbidden_reason=registry.forbidden_reason,
    ) is None


def test_disabled_scenario_resume_never_reads_run_fields():
    import driver
    # Legacy callers may ask the disabled gate without constructing run options.
    driver._check_scenario_resume(SimpleNamespace(resume=False), {'name': 'ignored'})
