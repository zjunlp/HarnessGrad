#!/usr/bin/env python3
"""Run the trusted native QA scorer on one released reference or empty answer.

Only uses the provisioned selected task. This is an oracle/control, not a model run
and never authorizes a benchmark pilot. Math uses the mandatory MathVerify runtime.
"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.scaffolds.flashrsi_qa import CATALOG, Dataset
from data.scaffolds.flashrsi_qa.scoring import score


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', choices=CATALOG, required=True)
    p.add_argument('--task-id')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--oracle', action='store_true')
    g.add_argument('--noop', action='store_true')
    a = p.parse_args()
    dataset = Dataset(a.dataset)
    rows = dataset.records()
    row = next((r for r in rows if r['task_id'] == a.task_id), None) if a.task_id else rows[0]
    if row is None: p.error('task ID is not in the prepared native source')
    answer = row['gold']['answer'] if a.oracle else ''
    result = score(a.dataset, answer, row['gold'])
    print(result['reward'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
