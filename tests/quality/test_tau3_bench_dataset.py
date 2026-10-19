"""τ³ native relay, private episode, fixed seed and real sandbox controls."""
import importlib.util
import json
from pathlib import Path

import pytest
from data.datasets import tau3_bench as dataset
from test_tau3_bench_official_scorer import asset, official


def control_module():
    spec = importlib.util.spec_from_file_location('tau3_controls', dataset.ASSETS / 'control.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_all_four_domains_manifest_and_private_material(monkeypatch):
    monkeypatch.setenv('HG_ENV_MODEL', 'fixture-user')
    monkeypatch.setenv('HG_JUDGE_MODEL', 'fixture-judge')
    rows = dataset.roster()
    assert len(rows) == 375
    assert {d: sum(r['domain'] == d for r in rows.values()) for d in ('airline','retail','telecom','banking_knowledge')} == {
        'airline': 50, 'retail': 114, 'telecom': 114, 'banking_knowledge': 97}
    for tid, row in rows.items():
        setup = dataset.SETUP[tid]; verifier = dataset.VERIFY[tid]
        assert setup['tool_relay']['model']['seed_source'] == 'trial'
        assert setup['tool_relay']['record']['path'] == 'trusted_episode'
        assert not any('private' in str(f) or 'runtime' in f['path'] for f in setup['files'])
        assert verifier['trial_protocol']['trials'] == 3
        assert verifier['result_contract']['require_exit_zero'] is True
        assert ('judge' in verifier) == row['needs_judge']
    assert sum(r['needs_judge'] for r in rows.values()) == 113


@pytest.mark.parametrize('domain', ['airline', 'retail', 'telecom', 'banking_knowledge'])
def test_official_environment_and_user_simulator_available(official, domain, tmp_path):
    row = next(r for r in dataset.roster().values() if r['domain'] == domain)
    config = json.loads((dataset.DATASET_ROOT / row['path'] / 'private.json').read_text())
    config.update(user_model='fixture-user', seed=123)
    path = tmp_path / 'private.json'; path.write_text(json.dumps(config))
    state = asset('runtime').Tau3Runtime(path)
    from tau2.user.user_simulator import UserSimulator
    assert isinstance(state.user, UserSimulator)
    assert state.user.llm_args['seed'] == 123
    assert state.environment.get_tools()
    assert not state.bootstrap_complete


@pytest.mark.parametrize('oracle', [True, False])
@pytest.mark.parametrize('platform', [False, True])
def test_official_control_with_local_transport(tmp_path, monkeypatch, oracle, platform):
    control = control_module()
    with control.Endpoint() as endpoint:
        score, result = control.run(oracle, platform, tmp_path)
    assert score == float(oracle), result
    assert endpoint.requests
    assert endpoint.requests[0]['seed'] == dataset.TRIAL_PROTOCOL['seeds'][0]
    if platform:
        service = result['runtime_services']['tool_relay']
        assert service['cleanup']['complete']
        assert result['verdict']['interaction_record']['binding']['task_id'] == 'tau3_bench/airline/8'


def test_actor_cannot_reconfigure_or_export_episode(official, tmp_path):
    spec = importlib.util.spec_from_file_location('tau3_relay_test', dataset.ASSETS / 'relay.py')
    relay = importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)
    class State:
        start_conversation = submit_assistant_message = call_assistant_tool = submit_assistant_tool_calls = get_runtime_status = lambda *args: 'unused'
    service = relay.Service({}, State())
    for operation in ('configure_run', 'reset', 'record_termination', 'episode', 'score'):
        assert service.handle(operation, {})['error'] == 'unsupported_operation_or_fields'


@pytest.mark.parametrize('failed_process', [False, True])
def test_incomplete_episode_preserves_evidence_but_failed_scorer_cannot_publish(tmp_path, failed_process):
    from eval.verification.verifier import _verify
    tid = 'tau3_bench/airline/8'
    row = dataset.roster()[tid]
    spec = dataset.VERIFY[tid]
    if failed_process:
        spec['argv'] = ['python3', '-I', '-c',
            "from pathlib import Path; import sys; "
            "Path('verifier/reward.txt').write_text('1'); "
            "Path('verifier/evidence.json').write_text('{\"measured\":true}'); sys.exit(3)"]
    else:
        # Platform-injected control envelope; no actor may supply this input in a run.
        sealed = {'schema': 'harnessgrad.interaction-record.v1',
                  'binding': {'task_id': tid, 'seed': '123'},
                  'payload': {'task_id': '8', 'domain': 'airline', 'seed': 123,
                              'case_sha256': row['private_sha256'],
                              'termination_reason': None, 'messages': []}}
        spec['inputs'].append({'path': 'trusted_episode/episode.json', 'content': json.dumps(sealed)})
    result = _verify(tmp_path, spec, '', sandbox=True, timeout_s=30)
    assert result['score'] is None and result['outcome'] == 'unmeasured', result
    if failed_process:
        assert result['exit_code'] == 3 and 'evidence' not in result
        assert (tmp_path / 'verifier/reward.txt').read_text() == '1'
    else:
        assert result['evidence']['measured'] is False
        assert result['evidence']['error'] == 'IncompleteConversation'
        assert result['evidence']['detail'] == 'incomplete_conversation_without_stop_signal'


def test_actor_preseeded_verifier_packages_are_hidden(tmp_path):
    control = control_module()
    with control.Endpoint():
        score, result = control.run(False, True, tmp_path, attack=True)
    assert score == 0, result
    assert result['verdict']['evidence']['measured'] is True
    assert result['runtime_services']['tool_relay']['cleanup']['complete']


@pytest.mark.parametrize('response', ['{}', '{"results": []}', '{"results": [{"expectedOutcome": "unrelated", "metExpectation": true, "reasoning": "fixture"}]}'])
def test_real_nl_evaluator_http_missing_assertions_unmeasured(official, monkeypatch, response):
    from eval.service.judge import Judge
    from eval.service.tool_transport import call
    import tau2.evaluator.evaluator_nl_assertions as nl
    old = nl.generate
    config = json.loads((dataset.DATASET_ROOT / 'cases/retail/2/private.json').read_text())
    episode = {'task_id': config['task']['id'], 'domain': 'retail', 'seed': 123,
               'termination_reason': 'agent_stop', 'messages': [{'role': 'assistant', 'content': '###STOP###'}]}
    control = control_module()
    try:
        with control.Endpoint(judge_response=response) as endpoint:
            with Judge(dataset.model_spec('judge')) as broker:
                monkeypatch.setenv('HG_JUDGE_SOCKET', str(broker.path))
                asset('model').install('judge', call)
                with pytest.raises(ValueError, match='(malformed_nl|assertion_coverage)'):
                    asset('scoring').score(config, episode)
                assert len(endpoint.requests) == 1
                assert 'expectedOutcomes' in endpoint.requests[0]['messages'][1]['content']
    finally:
        nl.generate = old


def test_three_trials_emit_complete_domain_report(tmp_path, monkeypatch):
    import shutil
    import sys
    from eval.run.runner import evaluate
    from tools.tau3_report import report
    control = control_module()
    tid = 'tau3_bench/airline/8'
    harness = tmp_path / 'harness'
    shutil.copytree(dataset.ASSETS / 'oracle_harness', harness)
    manifest = json.loads((harness / 'harness.json').read_text())
    manifest['runtime_paths'] = list(dict.fromkeys([sys.prefix, sys.base_prefix,
        str(Path(sys._base_executable).parent.parent)]))
    (harness / 'harness.json').write_text(json.dumps(manifest))
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    with control.Endpoint() as endpoint:
        result = evaluate(harness, [dataset.control_task(tid)], {tid: dataset.METRIC},
                          setups={tid: dataset.SETUP[tid]}, verifiers={tid: dataset.VERIFY[tid]},
                          envs={tid: dataset.ENV[tid]}, sandbox=True, trials=3, jobs=1,
                          run_id='tau3-protocol-control', harness_sha='protocol-control',
                          progress_dir=tmp_path / 'progress', recordings_root=tmp_path / 'records')
    repeated = result['repeated_trials']
    assert repeated['complete'], result
    assert repeated['metrics'] == {'1': 0, '2': 0, '3': 0}
    assert [r['seed'] for r in endpoint.requests] == dataset.TRIAL_PROTOCOL['seeds']
    grouped = report(repeated)
    assert grouped['groups'] == {'airline': {'1': 0, '2': 0, '3': 0}}
    assert grouped['selected_tasks'] == 1 and not grouped['official_full_base']
    assert list((tmp_path / 'progress').rglob('repeated-trials.json'))


def test_official_user_tool_roundtrip_through_http_broker(official, monkeypatch, tmp_path):
    from eval.service.judge import Judge
    from eval.service.tool_transport import call
    import tau2.user.user_simulator as user
    old = user.generate
    row = next(r for r in dataset.roster().values() if r['domain'] == 'telecom')
    config = json.loads((dataset.DATASET_ROOT / row['path'] / 'private.json').read_text())
    action = next(a for a in config['task']['evaluation_criteria']['actions'] if a.get('requestor') == 'user')
    config.update(user_model='tau3-control-user', seed=123)
    path = tmp_path / 'private.json'; path.write_text(json.dumps(config))
    control = control_module()
    try:
        with control.Endpoint(user_tool=action) as endpoint:
            spec = dataset.model_spec('environment', row['user_tools'])
            spec.pop('seed_source'); spec['parameters']['seed'] = 123
            with Judge(spec, role='environment') as broker:
                monkeypatch.setenv('HG_ENV_MODEL_SOCKET', str(broker.path))
                asset('model').install('environment', call)
                state = asset('runtime').Tau3Runtime(path)
                state.start_conversation()
                assert len(endpoint.requests) == 2
                assert any(m['role'] == 'tool' and m.get('requestor') == 'user' for m in state.episode()['messages'])
                assert endpoint.requests[1]['messages'][-1]['role'] == 'tool'
    finally:
        user.generate = old


def test_real_nl_http_partial_assertion_list_is_unmeasured(official, monkeypatch):
    from eval.service.judge import Judge
    from eval.service.tool_transport import call
    import tau2.evaluator.evaluator_nl_assertions as nl
    old = nl.generate
    config = json.loads((dataset.DATASET_ROOT / 'cases/retail/62/private.json').read_text())
    expected = config['task']['evaluation_criteria']['nl_assertions']
    assert len(expected) == 2
    response = json.dumps({'results': [{'expectedOutcome': expected[0], 'metExpectation': True, 'reasoning': 'fixture'}]})
    episode = {'task_id': config['task']['id'], 'domain': 'retail', 'seed': 123,
               'termination_reason': 'agent_stop', 'messages': [{'role': 'assistant', 'content': '###STOP###'}]}
    try:
        with control_module().Endpoint(judge_response=response) as endpoint:
            with Judge(dataset.model_spec('judge')) as broker:
                monkeypatch.setenv('HG_JUDGE_SOCKET', str(broker.path))
                asset('model').install('judge', call)
                with pytest.raises(ValueError, match='assertion_coverage'):
                    asset('scoring').score(config, episode)
                assert len(endpoint.requests) == 1
    finally:
        nl.generate = old
