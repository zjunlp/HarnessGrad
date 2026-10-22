"""Per-source question means with explicit selected coverage, never silent omission."""
from collections import defaultdict
import math

from .scoring import PRIMARY_METRICS, JUDGE_SOURCES
from .storage import question_id


def summarize(records, selected):
    expected = {item['task_id']: item for item in selected}
    if len(expected) != len(selected) or not expected:
        raise ValueError('selected manifest must be nonempty with unique tasks')
    received = {}
    for evidence in records:
        if not isinstance(evidence, dict):
            raise ValueError('invalid verifier evidence')
        tid = evidence.get('task_id')
        if tid not in expected or tid in received:
            raise ValueError('unexpected or duplicate task evidence')
        received[tid] = evidence
    groups = defaultdict(lambda: {'expected_tasks': 0, 'measured_tasks': 0, 'expected_questions': 0,
                                  'measured_questions': 0, 'scores': [], 'unmeasured': []})
    episode_scores = []
    for tid, row in expected.items():
        source, count = row['source'], row['question_count']
        group = groups[source]
        group['expected_tasks'] += 1
        group['expected_questions'] += count
        evidence = received.get(tid)
        if evidence is None or evidence.get('measured') is False:
            group['unmeasured'].append({'task_id': tid, 'reason': (
                'missing_evidence' if evidence is None else evidence.get('reason', 'unmeasured'))})
            continue
        if (evidence.get('schema') != 'hg-mab-verifier-v1' or evidence.get('measured') is not True
                or evidence.get('source') != source or evidence.get('metric') != PRIMARY_METRICS[source]
                or evidence.get('artifacts', {}).get('input_sha256') != row['input_sha256']):
            raise ValueError('verifier evidence identity mismatch')
        result = evidence.get('result', {})
        questions = result.get('questions')
        if (result.get('status') != 'measured' or result.get('source') != source
                or result.get('episode_id') != row['episode_id']
                or result.get('metric') != PRIMARY_METRICS[source]
                or result.get('aggregation') != 'question_mean_within_episode'
                or result.get('coverage') != dict(expected=count, submitted=count, missing=0,
                                                   measured=count, unmeasured=0)
                or not isinstance(questions, list) or len(questions) != count):
            raise ValueError('incomplete or inconsistent measured episode')
        if source in JUDGE_SOURCES:
            from .judge import profile
            declared = evidence.get('judge')
            if (not isinstance(declared, dict) or not isinstance(declared.get('model'), str)
                    or declared != profile(source, declared['model'],
                                           max_output_tokens=declared.get('max_output_tokens'),
                                           reasoning=declared.get('reasoning'))
                    or result.get('judge') != declared):
                raise ValueError('invalid judge profile')
            if 'judge' in group and group['judge'] != declared:
                raise ValueError('mixed judge profiles within one source')
            group['judge'] = declared
        scores = []
        for index, item in enumerate(questions):
            if (not isinstance(item, dict) or item.get('question_id') != question_id(tid, index)
                    or item.get('status') != 'measured' or item.get('metric') != PRIMARY_METRICS[source]
                    or type(item.get('score')) not in (int, float)
                    or not math.isfinite(item['score']) or not 0 <= item['score'] <= 1):
                raise ValueError('invalid question metric or ordering')
            if source in JUDGE_SOURCES and item.get('judge', {}).get('profile') != group['judge']:
                raise ValueError('question judge profile mismatch')
            scores.append(float(item['score']))
        score = result.get('score')
        if (type(score) not in (int, float) or not math.isfinite(score)
                or not math.isclose(score, sum(scores) / count, rel_tol=0, abs_tol=1e-12)):
            raise ValueError('episode score disagrees with questions')
        group['measured_tasks'] += 1
        group['measured_questions'] += count
        group['scores'].extend(scores)
        episode_scores.append(score)
    sources = {}
    for source, group in sorted(groups.items()):
        scores = group.pop('scores')
        group['unmeasured_tasks'] = group['expected_tasks'] - group['measured_tasks']
        group['unmeasured_questions'] = group['expected_questions'] - group['measured_questions']
        group['metric'] = PRIMARY_METRICS[source]
        group['status'] = 'complete' if group['unmeasured_tasks'] == 0 else 'incomplete'
        if scores:
            # A provided result is one measured question, never one context row.
            group['measured_question_mean'] = sum(scores) / len(scores)
            if group['status'] == 'complete':
                group['score'] = group['measured_question_mean']
        sources[source] = group
    result = {'schema': 'hg-mab-report-v1', 'scope': 'explicitly_selected_episodes',
              'aggregation': 'question_mean_per_source', 'sources': sources,
              'coverage': {'expected_tasks': len(expected), 'measured_tasks': len(episode_scores),
                           'unmeasured_tasks': len(expected) - len(episode_scores),
                           'expected_questions': sum(row['question_count'] for row in selected),
                           'measured_questions': sum(group['measured_questions'] for group in sources.values())}}
    result['coverage']['unmeasured_questions'] = (result['coverage']['expected_questions']
                                                 - result['coverage']['measured_questions'])
    if episode_scores:
        result['platform_measured_episode_mean'] = sum(episode_scores) / len(episode_scores)
    return result
