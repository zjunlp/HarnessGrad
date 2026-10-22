"""Real pinned blocker transitions, visibility and train reference compatibility."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from data.datasets import costbench as dataset


@pytest.fixture
def make_runtime(tmp_path, monkeypatch):
    root = Path(os.environ.get('HG_COSTBENCH_DATA', dataset.DATASET_ROOT))
    if not (root / 'prepared-v1/manifest.json').is_file():
        pytest.skip('CostBench prepared static data required')
    monkeypatch.syspath_prepend(str(dataset.ASSETS / 'lib'))
    monkeypatch.chdir(tmp_path)
    from bootstrap import bootstrap
    from dynamic_runtime import DynamicCostBenchRuntime

    def create(mode, split='test'):
        cases = []
        for path in sorted((root / 'prepared-v1/tasks' / split).glob('*/private.json')):
            case = json.loads(path.read_text())
            if case['query']['task'] == 'location' and case['query']['groundtruth']['scenarios'][0]['path_length'] == 2:
                cases.append((path, case))
                if len(cases) == 2:
                    break
        assert len(cases) == 2
        path, config = cases[0]
        public = json.loads(path.with_name('public.json').read_text())
        bootstrap(root / 'bundle', root / 'runtime', split=split,
                  search_space=root / config['search_space'], config_path=tmp_path / 'runtime.yaml')
        from env.core.base_types import create_tool_from_dict
        from env.settings import load_config
        from env.utils.blocker import create_blocker
        from env.utils.solver import GroundtruthSolver
        pool = tmp_path / 'query-pool.json'
        pool.write_text(json.dumps([c['query'] for _, c in cases]))
        protocol = config['protocol']
        protocol.update(use_blocker=True, block_mode=mode, block_num=1)
        config['query']['tool_creation_seed'] = config['tool_seed']
        tools = {name: create_tool_from_dict({**spec, 'name': name}) for name, spec in config['tools'].items()}
        defaults = load_config()
        planner = create_blocker(mode, 1, str(pool), defaults.random.seed_range, 2, str(tmp_path))
        planner.set_solver(GroundtruthSolver(tools))
        planner.filter_options = {name: protocol.get(name, getattr(defaults.tool_defaults, name))
                                  for name in ('control_tool_length', 'max_tool_length', 'ban_longest_tool',
                                               'min_atomic_cost', 'max_atomic_cost', 'noise_std')}
        planner.initialize_blocking_plan(config['query'])
        config.update(seed='42', case_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return DynamicCostBenchRuntime(config, public), config
    return create


@pytest.mark.parametrize('split', ['train', 'test'])
@pytest.mark.parametrize('mode', ['cost_change', 'steplen_change', 'preference_change', 'ban_tool'])
def test_event_timing_visibility_and_reference_recalculation(make_runtime, mode, split):
    runtime, config = make_runtime(mode, split)
    before = runtime.show()
    initial_state = set(runtime.state)
    first = config['oracle_actions'][0]
    first_cost = runtime.tools[first['name']].cost
    first_output_type = runtime.tools[first['name']].output_type
    assert config['query']['block_stats']['block_step'] == [1]
    result = runtime.turn(calls=[first])
    assert runtime.blocker.blocked_count == 1
    assert len(runtime.query['groundtruth']['scenarios']) == 2
    assert runtime.query['groundtruth']['scenarios'][1]['gt_id']
    assert 'Cost: ' + str(first_cost) in result['results'][0]['content']
    assert runtime.steps == 1
    if mode == 'ban_tool':
        assert runtime.state == initial_state
        assert result['results'][0]['content'].startswith('[ERROR]')
        assert first['name'] in runtime.visible
        assert before == runtime.show()
        from env.utils.eval import extract_full_agent_steps
        steps, total, valid, _ = extract_full_agent_steps(runtime.history)
        assert total == valid == 1 and steps[0]['cost'] == first_cost
    elif mode == 'preference_change':
        assert runtime.state == initial_state
        assert result['messages'][-1] == runtime.history[-1]
        assert runtime.show()['query'] == result['messages'][-1]['content']
        assert runtime.query['preferences'] == config['query']['preferences']
    elif mode == 'cost_change':
        assert first_output_type in runtime.state
        assert result['tools'] == runtime.show()['tools']
        assert before['tools'] != result['tools']
    else:
        assert first_output_type in runtime.state
        length = config['query']['blocking_plan'][0]['parameters']['target_range'][0]
        removed = runtime.blocker.removed_tools
        assert removed
        assert all(runtime.tools[name].to_dict()['component_count'] == length for name in removed)
        assert not removed.intersection(runtime.visible)
        assert result['tools'] == runtime.show()['tools']
    public_text = json.dumps(result) + json.dumps(runtime.show())
    assert all(key not in public_text for key in ('blocking_plan', 'groundtruth', 'new_random_seed', 'trigger_step'))
    # Calling episode and inspecting the schemas do not modify the episode.
    first_episode = runtime.episode()
    assert first_episode == runtime.episode()
    runtime.show()
    assert first_episode == runtime.episode()


def test_post_event_effectiveness_and_repeated_ban(make_runtime):
    runtime, config = make_runtime('cost_change')
    runtime.turn(calls=[config['oracle_actions'][0]])
    assert runtime.episode()['query_result']['block_stats']['block_count_agent'] == 0
    runtime.turn(calls=[config['oracle_actions'][1]])
    assert runtime.episode()['query_result']['block_stats']['block_count_agent'] == 0
    runtime.turn(content='<answer> ' + config['oracle_answer'] + ' </answer>')
    assert runtime.episode()['query_result']['block_stats']['block_count_agent'] == 1

    runtime, config = make_runtime('ban_tool')
    first = config['oracle_actions'][0]
    runtime.turn(calls=[first])
    assert runtime.episode()['query_result']['block_stats']['block_count_agent'] == 1
    state = set(runtime.state)
    repeated = runtime.turn(calls=[first])
    assert repeated['results'][0]['content'].startswith('[ERROR]')
    assert runtime.state == state and runtime.blocker.blocked_count == 1


@pytest.mark.parametrize('mode', ['cost_change', 'steplen_change', 'preference_change', 'ban_tool'])
def test_answer_first_and_noop_do_not_invent_events(make_runtime, mode):
    runtime, config = make_runtime(mode)
    assert runtime.episode()['query_result']['block_stats']['block_count_agent'] == 0
    result = runtime.turn(content='<answer> ' + config['oracle_answer'] + ' </answer>',
                          calls=[config['oracle_actions'][0]])
    assert result['results'] == [] and result['termination'] == 'final_answer'
    assert runtime.blocker.blocked_count == 0
    assert runtime.episode()['query_result']['block_stats']['block_count_agent'] == 0


def test_first_call_only_malformed_payloads_and_step_limit(make_runtime):
    runtime, config = make_runtime('ban_tool')
    for malformed in ({}, False, ''):
        assert 'error' in runtime.turn(calls=malformed)
    assert runtime.steps == 0 and runtime.blocker.blocked_count == 0
    result = runtime.turn(calls=config['oracle_actions'])
    assert 'Multiple tool calls' in result['results'][1]['content']
    assert runtime.blocker.blocked_count == 1
    for _ in range(19):
        runtime.turn(calls=[config['oracle_actions'][0]])
    assert runtime.status()['termination'] == 'max_steps_reached'
    before = runtime.episode()
    assert runtime.turn(calls=[config['oracle_actions'][0]])['error'] == 'episode_ended'
    assert runtime.episode() == before


def test_reference_failure_does_not_become_task_zero(make_runtime, monkeypatch):
    runtime, config = make_runtime('cost_change')
    from env.utils import solver
    def broken(**kwargs):
        raise RuntimeError('reference lookup failed')
    monkeypatch.setattr(solver, 'calculate_groundtruth_id', broken)
    with pytest.raises(RuntimeError, match='reference recalculation failed'):
        runtime.turn(calls=[config['oracle_actions'][0]])


def test_empty_ban_name_is_upstream_task_failure_not_service_failure(make_runtime):
    runtime, config = make_runtime('ban_tool')
    before = set(runtime.state)
    response = runtime.turn(calls=[{'name': '', 'arguments': {}}])
    assert response['results'][0]['content'].startswith('[ERROR]')
    assert 'Cost:' not in response['results'][0]['content']
    assert runtime.steps == 1 and runtime.blocker.blocked_count == 0
    assert runtime.state == before
    episode = runtime.episode()
    assert len(episode['query_result']['groundtruth']['scenarios']) == 1
    from env.utils.eval import evaluate_single_query
    judged = evaluate_single_query(episode['query_result'])
    assert not judged['is_valid'] and judged['actual_block_count'] == 0
    # The pending event is still available for the next nonempty request.
    runtime.turn(calls=[config['oracle_actions'][0]])
    assert runtime.blocker.blocked_count == 1
