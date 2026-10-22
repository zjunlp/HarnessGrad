"""Question-weighted LoCoMo report from explicitly selected verifier evidence.

Platform task means are not this metric. A missing/unmeasured selected task
suppresses the complete-set micro average instead of shrinking its denominator.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.datasets import locomo

aggregate_scores = locomo._load_asset("lib/scoring.py").aggregate_scores


def summarize(evidences, selected_task_ids):
    selected = list(selected_task_ids)
    if not selected or len(set(selected)) != len(selected):
        raise ValueError('select a nonempty unique task list')
    pool = locomo.rows()
    if set(selected) - set(pool):
        raise ValueError('unknown selected task')
    indexed = {}
    for record in evidences:
        tid = record['task_id']
        if tid not in selected or tid in indexed:
            raise ValueError('unselected or duplicate evidence')
        if record['schema'] != 'hg-locomo-evidence-v1':
            raise ValueError('unexpected evidence schema')
        indexed[tid] = record
    scores = []
    categories = {}
    measured_tasks = 0
    for tid in selected:
        record = indexed.get(tid)
        if not record or record.get('measured') is not True:
            scores.append(None)
            continue
        gold = locomo.gold_record(tid)
        expected = {q['index']: q['category'] for q in gold['questions']}
        if (record.get('metric') != locomo.METRIC or record.get('source_sha256') != locomo.DATA_SHA256
                or record.get('conversation_sha256') != gold['conversation_sha256']
                or record.get('num_questions') != len(expected)):
            raise ValueError('evidence source/task coverage mismatch')
        details = record['per_question']
        if len(details) != len(expected) or {q['index'] for q in details} != set(expected):
            raise ValueError('question coverage mismatch')
        for q in details:
            reward = q['reward']
            if (q['category'] != expected[q['index']] or isinstance(reward, bool)
                    or not isinstance(reward, (int, float)) or not math.isfinite(reward)
                    or not 0 <= reward <= 1):
                raise ValueError('invalid per-question score')
            bucket = categories.setdefault(str(q['category']), {'score_sum': 0.0, 'questions': 0})
            bucket['score_sum'] += reward
            bucket['questions'] += 1
        total = sum(q['reward'] for q in details)
        if not math.isclose(record['score_sum'], total, abs_tol=1e-10):
            raise ValueError('score sum mismatch')
        scores.append({'score_sum': total, 'num_questions': len(expected)})
        measured_tasks += 1
    return {'schema': 'hg-locomo-report-v1', 'metric': locomo.METRIC,
            'selected_task_ids': selected, 'expected_tasks': len(selected),
            'measured_tasks': measured_tasks, 'unmeasured_tasks': len(selected) - measured_tasks,
            'expected_questions': sum(len(pool[t]['qa']) for t in selected),
            'per_category_measured': {c: {**v, 'mean': v['score_sum'] / v['questions']}
                                      for c, v in categories.items()},
            **aggregate_scores(scores)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', action='append', required=True)
    parser.add_argument('--evidence', action='append', type=Path, default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = summarize([json.loads(p.read_text()) for p in args.evidence], args.task_id)
    with args.output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
