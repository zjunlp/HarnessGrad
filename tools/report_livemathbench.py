#!/usr/bin/env python3
"""Native G-Pass@k report from COMPLETE HG per-task trial labels; never pass^k.

The ordinary curve reward remains explicitly labelled single-sample accuracy.
This separate report requires the declared number of successful measured trials
for every task. Missing/invalid labels do not become zero or shrink denominators.
"""
import argparse
import importlib.util
import json
from pathlib import Path


def aggregate(point, *, n=48, ks=(16,)):
    if type(n) is not int or n < 1 or not ks or any(type(k) is not int or not 1 <= k <= n for k in ks):
        raise ValueError('G-Pass requires explicit positive n and k <= n')
    tasks = point.get('per_task')
    labels = point.get('per_task_trials')
    problems = []
    if not isinstance(tasks, dict) or not tasks:
        problems.append('no measured tasks')
    if not isinstance(labels, dict) or set(labels) != set(tasks or {}):
        problems.append('trial labels do not cover exactly the measured task manifest')
    if point.get('n_invalid') is None or point['n_invalid'] != 0:
        problems.append('invalid count is missing or nonzero')
    if not problems:
        for tid, values in labels.items():
            if not isinstance(values, list) or len(values) != n or any(type(v) not in (int, float) or v not in (0, 1) for v in values):
                problems.append(f'{tid}: expected {n} complete binary labels')
    result = {'schema': 'harnessgrad.livemathbench.gpass.v1', 'n': n, 'ks': list(ks),
              'complete': not problems, 'problems': problems, 'metrics': None, 'per_task': None}
    if problems:
        return result
    source = Path(__file__).resolve().parents[1] / 'data/scaffolds/native_research/vendor/flashrsi/benchmarks/livemathbench/_metrics.py'
    spec = importlib.util.spec_from_file_location('_hg_native_gpass', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    per_task = {tid: module.metrics(values, list(ks)) for tid, values in labels.items()}
    result.update(per_task=per_task, metrics={key: sum(value[key] for value in per_task.values()) / len(per_task)
                                            for key in next(iter(per_task.values()))})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('curve', type=Path)
    p.add_argument('--n', type=int, default=48)
    p.add_argument('--k', type=int, nargs='+', default=[16])
    a = p.parse_args()
    points = [json.loads(line) for line in a.curve.read_text().splitlines() if line.strip()]
    if not points:
        p.error('curve is empty')
    report = aggregate(points[-1], n=a.n, ks=a.k)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
