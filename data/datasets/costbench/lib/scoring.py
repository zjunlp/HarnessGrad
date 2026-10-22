"""Pinned CostBench static path exact match and separately masked official metrics.

The primary reward is evaluate_single_query()['tool_path_exact_match'], with all
measured episodes in its denominator. It is deliberately not called the paper's
filtered EMR: upstream eval() excludes histories of at most three messages.
Tool prices are simulated task costs, never the actor's model-token ledger.
"""
from copy import deepcopy
import math
import re

REVISION = '6424069f3f773ade076bcedbed302ed47cfb1837'
METRIC = 'costbench.static.sequence5.per_query_path_exact_match.v1'
TRAIN_COMPAT_METRIC = 'costbench.train.seven_digit_candidate_tcr.v1'


def extract_train_candidate_id(answer_text):
    """Train-only seven-digit counterpart of upstream extract_candidate_id.

    The released train IDs have seven digits. Upstream's loose five-digit
    expression silently truncates them; preserve the original scorer and expose
    this repair as a separately named training metric. Never apply it to test.
    """
    if '<think>' in answer_text and '</think>' in answer_text:
        answer_text = answer_text.split('</think>')[1]
    scope = re.search(r'<answer>([\s\S]*?)</answer>', answer_text, re.IGNORECASE)
    if scope is None:
        return None
    domain = r'(Transportation|Location|Accommodation|Attraction|Dining|Shopping)'
    patterns = (
        rf'<\s*{domain}Candidate\s*\{{\s*Candidate_ID\s*:\s*(\d{{7}})\s*\}}\s*>',
        rf'<\s*{domain}Candidate\s*\{{\s*(\d{{7}})\s*\}}\s*>',
        rf'<\s*{domain}Candidate\s*(\d{{7}})\s*>',
        rf'{domain}Candidate\s*(\d{{7}})(?!\d)',
    )
    for pattern in patterns:
        match = re.search(pattern, scope.group(1), re.IGNORECASE)
        if match:
            return match.group(2)
    return None


def train_answer(query):
    """Same last-assistant-message selection as the upstream answer helper."""
    for message in reversed(query['conversation_history']):
        if message.get('role') == 'assistant':
            content = message.get('content', '')
            if content is None:
                return None
            if '<answer>' in content or 'Candidate' in content:
                return extract_train_candidate_id(content)
    return None


def validate_query(query):
    """Reject broken/private-runtime records rather than manufacture task zeros."""
    if not isinstance(query, dict) or not isinstance(query.get('query_id'), str):
        raise ValueError('missing official query identity')
    if type(query.get('is_goal_state')) is not bool:
        raise ValueError('goal-state evidence must be an explicit boolean')
    history = query.get('conversation_history')
    if not isinstance(history, list) or any(not isinstance(m, dict) for m in history):
        raise ValueError('missing trusted conversation history')
    blocks = query.get('block_stats', {})
    if (blocks.get('expected_block_count', 0) != 0
            or blocks.get('block_count_agent', blocks.get('block_count', 0)) != 0
            or blocks.get('block_count', 0) != 0 or blocks.get('block_step', [])):
        raise ValueError('dynamic episodes cannot be scored as static CostBench')
    scenarios = query.get('groundtruth', {}).get('scenarios')
    if not isinstance(scenarios, list) or len(scenarios) != 1:
        raise ValueError('static CostBench requires one trusted ground-truth scenario')
    tools = scenarios[0].get('tools')
    if not isinstance(tools, list) or not tools:
        raise ValueError('ground-truth path is missing or empty')
    for tool in tools:
        if not isinstance(tool, dict) or len(tool) != 1:
            raise ValueError('invalid ground-truth tool cost')
        value = next(iter(tool.values()))
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('ground-truth tool costs must be positive and finite')
    return query


def eligibility(result):
    """Exact denominator predicates from upstream eval.py:796-817, 892-919, 981."""
    measured = not result.get('gt_incomplete', False) and result.get('is_valid', False)
    long_history = result.get('conversation_length', 0) > 3
    return {
        'accuracy': bool(measured and long_history),
        'path': bool(not result.get('gt_incomplete', False)
                     and result.get('is_block_valid', False) and long_history),
        'cost': bool(measured and not result.get('no_goal_state', False)
                     and result.get('is_block_valid', False) and long_history),
        'tool_usage': bool(measured),
    }


def aggregate(queries, *, splits=None):
    """Run the upstream batch scorer, retaining masks and undefined-value meaning.

    This function takes full trusted query records, not a list of per-task mean
    costs. The raw upstream result is retained verbatim for differential checks;
    the presentation metrics use null when their denominator is zero.
    """
    from env.utils.eval import eval as official_aggregate, evaluate_single_query, check_answer_accuracy

    queries = [deepcopy(validate_query(q)) for q in queries]
    splits = ['test'] * len(queries) if splits is None else list(splits)
    if len(splits) != len(queries) or any(split not in ('train', 'test') for split in splits):
        raise ValueError('each CostBench episode requires an explicit train/test split')
    rows = [evaluate_single_query(q) for q in queries]
    if any(not r.get('is_valid') or r.get('gt_incomplete') for r in rows):
        raise ValueError('official scorer could not measure a trusted static episode')
    masks = [eligibility(r) for r in rows]
    counts = {name: sum(m[name] for m in masks)
              for name in ('accuracy', 'path', 'cost', 'tool_usage')}
    raw = official_aggregate(queries)
    tool_calls = sum(r['total_tool_calls'] for r, mask in zip(rows, masks) if mask['tool_usage'])
    valid_calls = sum(r['valid_tool_calls'] for r, mask in zip(rows, masks) if mask['tool_usage'])
    cost = raw['cost_metrics']
    # Retain both raw difference and the implementation's atomic-unit normalization.
    # These are explicitly different quantities, not interchangeable Cost Gap labels.
    metrics = {
        'official_emr': raw['tool_path_metrics']['tool_path_exact_match_ratio'] if counts['path'] else None,
        'official_aed': raw['tool_path_metrics']['avg_edit_distance'] if counts['path'] else None,
        'official_aned': raw['tool_path_metrics']['avg_normalized_edit_distance'] if counts['path'] else None,
        'official_tcr': raw['accuracy_metrics']['final_answer_accuracy'] if counts['accuracy'] else None,
        'official_avg_agent_cost': cost['avg_agent_cost'] if counts['cost'] else None,
        'official_avg_gt_cost': cost['avg_gt_cost'] if counts['cost'] else None,
        'cost_gap_raw_units': cost['avg_agent_cost'] - cost['avg_gt_cost'] if counts['cost'] else None,
        'official_avg_delta_atomic': cost.get('avg_delta_atomic') if counts['cost'] else None,
        'official_cost_ratio': cost['cost_ratio'] if counts['cost'] else None,
        'invalid_tool_use_ratio': (tool_calls - valid_calls) / tool_calls if tool_calls else None,
    }
    train_rows = []
    for query, mask, split in zip(queries, masks, splits):
        if split == 'train':
            answer = train_answer(query)
            train_rows.append({'query_id': query['query_id'], 'eligible': mask['accuracy'],
                               'answer_id': answer,
                               'correct': bool(check_answer_accuracy(answer, query['groundtruth']['scenarios'], 0))})
    train_denominator = sum(row['eligible'] for row in train_rows)
    metrics['train_compat_tcr'] = (sum(row['correct'] for row in train_rows if row['eligible'])
                                   / train_denominator if train_denominator else None)
    if any(value is not None and not math.isfinite(value) for value in metrics.values()):
        raise ValueError('official scorer produced a non-finite metric')
    return {
        'metric': METRIC,
        'primary_mean': sum(float(r['tool_path_exact_match']) for r in rows) / len(rows) if rows else None,
        'primary_denominator': len(rows),
        'official_raw': raw,
        'metrics': metrics,
        'denominators': {**counts, 'tool_calls': tool_calls},
        'eligibility': [{'query_id': r['query_id'], **mask} for r, mask in zip(rows, masks)],
        'per_query': rows,
        'train_compatibility': {'metric': TRAIN_COMPAT_METRIC, 'denominator': train_denominator,
                                'per_query': train_rows},
    }


def score(config, payload):
    if config.get('revision') != REVISION or payload.get('revision') != REVISION:
        raise ValueError('CostBench source revision mismatch')
    if payload.get('task_id') != config['task_id']:
        raise ValueError('CostBench episode task identity mismatch')
    if payload.get('failure'):
        raise RuntimeError('private CostBench runtime failed: ' + str(payload['failure']))
    query = payload['query_result']
    if query.get('query_id') != config['query']['query_id']:
        raise ValueError('CostBench episode query identity mismatch')
    if query.get('groundtruth') != config['query'].get('groundtruth'):
        raise ValueError('CostBench episode ground truth differs from the private case')
    for field in ('task', 'goal_type', 'preferences', 'TimeInfo', 'location_preference'):
        if field in config['query'] and query.get(field) != config['query'][field]:
            raise ValueError('CostBench episode private query field mismatch: ' + field)
    if 'protocol' in config and query.get('run_args') != config['protocol']:
        raise ValueError('CostBench episode protocol differs from the private case')
    result = aggregate([query], splits=[config['split']])
    return {'reward': result['primary_mean'], 'metric': METRIC,
            'official': result, 'query_result': query, 'split': config['split']}
