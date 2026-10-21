"""One-event CostBench scoring with the pinned upstream dynamic eligibility rules.

An actor that stops before its scheduled intervention is a measured failure, not
a broken scorer. Such records are omitted by upstream aggregate metrics; the
HarnessGrad primary mean nevertheless includes every measured episode.
"""
from copy import deepcopy
import math

from scoring import REVISION, eligibility, train_answer

MODES = ('ban_tool', 'cost_change', 'preference_change', 'steplen_change')
TRAIN_COMPAT_METRIC = 'costbench.dynamic.train.seven_digit_candidate_tcr.v1'


def metric(mode):
    if mode not in MODES:
        raise ValueError('unknown CostBench dynamic mode')
    return f'costbench.dynamic.sequence5.{mode}.block1.per_query_path_exact_match.v1'


def validate_query(query, mode):
    metric(mode)
    if not isinstance(query, dict) or not isinstance(query.get('query_id'), str):
        raise ValueError('missing official query identity')
    if type(query.get('is_goal_state')) is not bool:
        raise ValueError('goal-state evidence must be an explicit boolean')
    history = query.get('conversation_history')
    if not isinstance(history, list) or any(not isinstance(m, dict) for m in history):
        raise ValueError('missing trusted conversation history')
    protocol = query.get('run_args', {})
    if (protocol.get('use_blocker') is not True or protocol.get('block_mode') != mode
            or type(protocol.get('block_num')) is not int or protocol['block_num'] != 1
            or protocol.get('refinement_level') != 2):
        raise ValueError('dynamic CostBench protocol mismatch')
    blocks = query.get('block_stats', {})
    if type(blocks.get('expected_block_count')) is not int or blocks['expected_block_count'] != 1:
        raise ValueError('dynamic CostBench requires exactly one planned intervention')
    for field in ('block_count_agent', 'block_count'):
        if type(blocks.get(field)) is not int or blocks[field] not in (0, 1):
            raise ValueError('invalid dynamic event count: ' + field)
    if 'block_count_gt' in blocks and (type(blocks['block_count_gt']) is not int
                                     or blocks['block_count_gt'] != blocks['block_count']):
        raise ValueError('dynamic GT event counters disagree')
    if blocks['block_count_agent'] > blocks['block_count']:
        raise ValueError('agent event count exceeds trusted GT event count')
    steps, types, metadata = (blocks.get(k) for k in ('block_step', 'block_type', 'metadata'))
    if (not isinstance(steps, list) or len(steps) != 1 or type(steps[0]) is not int
            or steps[0] < 1 or types != [mode]
            or not isinstance(metadata, list) or len(metadata) != 1):
        raise ValueError('missing complete dynamic intervention metadata')
    event = metadata[0]
    if (not isinstance(event, dict) or event.get('block_index') != 0
            or event.get('block_type') != mode or event.get('trigger_step') != steps[0]
            or not isinstance(event.get('parameters'), dict)):
        raise ValueError('dynamic event metadata disagrees with schedule')
    plan = query.get('blocking_plan')
    if plan != [{'type': mode, 'parameters': event['parameters']}]:
        raise ValueError('dynamic blocking plan disagrees with event metadata')
    scenarios = query.get('groundtruth', {}).get('scenarios')
    if not isinstance(scenarios, list) or len(scenarios) not in (1, 2):
        raise ValueError('dynamic CostBench requires initial and optional intervened scenarios')
    if blocks['block_count'] == 1 and len(scenarios) != 2:
        raise ValueError('applied dynamic event has no recomputed reference')
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict) or scenario.get('scenario_index') != index:
            raise ValueError('dynamic reference scenario indices disagree')
        tools = scenario.get('tools')
        if not isinstance(tools, list) or not tools:
            raise ValueError('dynamic reference path is missing or empty')
        if not isinstance(scenario.get('gt_id'), str) or not scenario['gt_id']:
            raise ValueError('dynamic reference answer is missing')
        for tool in tools:
            if not isinstance(tool, dict) or len(tool) != 1:
                raise ValueError('invalid dynamic reference tool cost')
            value = next(iter(tool.values()))
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('dynamic reference costs must be positive and finite')
        if index and scenario.get('block_info') != {
                'type': mode, 'trigger_step': steps[0], 'parameters': event['parameters']}:
            raise ValueError('dynamic scenario intervention disagrees with schedule')
    return query


def aggregate(queries, *, mode, splits=None):
    from env.utils.eval import eval as official_aggregate, evaluate_single_query, check_answer_accuracy

    queries = [deepcopy(validate_query(q, mode)) for q in queries]
    splits = ['test'] * len(queries) if splits is None else list(splits)
    if len(splits) != len(queries) or any(s not in ('train', 'test') for s in splits):
        raise ValueError('each CostBench episode requires an explicit train/test split')
    rows = [evaluate_single_query(q) for q in queries]
    for row in rows:
        if row.get('error') or row.get('gt_incomplete'):
            raise ValueError('official scorer could not measure a complete dynamic reference')
        if not row.get('is_valid') and row.get('is_block_valid') is not False:
            raise ValueError('unexplained official dynamic scoring failure')
    masks = [eligibility(row) for row in rows]
    counts = {name: sum(mask[name] for mask in masks)
              for name in ('accuracy', 'path', 'cost', 'tool_usage')}
    raw = official_aggregate(queries)
    tool_calls = sum(r.get('total_tool_calls', 0) for r, m in zip(rows, masks) if m['tool_usage'])
    valid_calls = sum(r.get('valid_tool_calls', 0) for r, m in zip(rows, masks) if m['tool_usage'])
    costs = raw['cost_metrics']
    metrics = {
        'official_emr': raw['tool_path_metrics']['tool_path_exact_match_ratio'] if counts['path'] else None,
        'official_aed': raw['tool_path_metrics']['avg_edit_distance'] if counts['path'] else None,
        'official_aned': raw['tool_path_metrics']['avg_normalized_edit_distance'] if counts['path'] else None,
        'official_tcr': raw['accuracy_metrics']['final_answer_accuracy'] if counts['accuracy'] else None,
        'official_avg_agent_cost': costs['avg_agent_cost'] if counts['cost'] else None,
        'official_avg_gt_cost': costs['avg_gt_cost'] if counts['cost'] else None,
        'cost_gap_raw_units': costs['avg_agent_cost'] - costs['avg_gt_cost'] if counts['cost'] else None,
        'official_avg_delta_atomic': costs.get('avg_delta_atomic') if counts['cost'] else None,
        'official_cost_ratio': costs['cost_ratio'] if counts['cost'] else None,
        'invalid_tool_use_ratio': (tool_calls - valid_calls) / tool_calls if tool_calls else None,
    }
    train_rows = []
    for query, row, mask, split in zip(queries, rows, masks, splits):
        if split == 'train':
            answer = train_answer(query)
            count = query['block_stats']['block_count_agent']
            train_rows.append({'query_id': query['query_id'], 'eligible': mask['accuracy'],
                               'answer_id': answer, 'scenario_index': count,
                               'correct': bool(check_answer_accuracy(answer, query['groundtruth']['scenarios'], count))})
    train_n = sum(r['eligible'] for r in train_rows)
    metrics['train_compat_tcr'] = (sum(r['correct'] for r in train_rows if r['eligible']) / train_n
                                   if train_n else None)
    if any(value is not None and not math.isfinite(value) for value in metrics.values()):
        raise ValueError('official scorer produced a non-finite dynamic metric')
    return {
        'metric': metric(mode), 'mode': mode,
        'primary_mean': sum(float(r.get('tool_path_exact_match', False)) for r in rows) / len(rows) if rows else None,
        'primary_denominator': len(rows), 'official_raw': raw, 'metrics': metrics,
        'denominators': {**counts, 'tool_calls': tool_calls},
        'event_incomplete_count': sum(not r['is_block_valid'] for r in rows),
        'eligibility': [{'query_id': r['query_id'], **mask} for r, mask in zip(rows, masks)],
        'per_query': rows,
        'train_compatibility': {'metric': TRAIN_COMPAT_METRIC, 'denominator': train_n,
                                'per_query': train_rows},
    }


def score(config, payload):
    mode = config.get('protocol', {}).get('block_mode')
    identity = metric(mode)
    if config.get('revision') != REVISION or payload.get('revision') != REVISION:
        raise ValueError('CostBench source revision mismatch')
    if payload.get('task_id') != config['task_id']:
        raise ValueError('CostBench episode task identity mismatch')
    if payload.get('failure'):
        raise RuntimeError('private CostBench runtime failed: ' + str(payload['failure']))
    expected_prefix = f"costbench_dynamic/{mode}/{config['split']}/"
    if not config['task_id'].startswith(expected_prefix):
        raise ValueError('dynamic task identity disagrees with mode and split')
    query = validate_query(payload['query_result'], mode)
    initial = config['query']
    if query.get('query_id') != initial.get('query_id'):
        raise ValueError('CostBench episode query identity mismatch')
    if query['groundtruth']['scenarios'][0] != initial['groundtruth']['scenarios'][0]:
        raise ValueError('dynamic episode initial ground truth differs from private case')
    if query.get('run_args') != config['protocol']:
        raise ValueError('CostBench episode protocol differs from private case')
    for field in ('task', 'goal_type', 'preferences', 'TimeInfo'):
        if field in initial and query.get(field) != initial[field]:
            raise ValueError('CostBench episode private query field mismatch: ' + field)
    if query.get('location_preference') != initial.get('location_preference'):
        location = query['block_stats']['metadata'][0]['parameters'].get('location')
        if (mode != 'preference_change' or not location
                or query.get('location_preference') != location
                or len(query['groundtruth']['scenarios']) != 2):
            raise ValueError('unexplained dynamic location preference mutation')
    # Prepared plans, when present, are authoritative; actor-provided episodes
    # cannot substitute a different intervention before the upstream scorer runs.
    if 'blocking_plan' in initial and query['blocking_plan'] != initial['blocking_plan']:
        raise ValueError('dynamic blocking plan differs from private case')
    result = aggregate([query], mode=mode, splits=[config['split']])
    return {'reward': result['primary_mean'], 'metric': identity,
            'official': result, 'query_result': query, 'split': config['split'], 'mode': mode}
