#!/usr/bin/env python3
"""Add official per-domain pass^k to a platform complete-manifest τ³ report."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.datasets import tau3_bench
from eval.run.repeated_trials import aggregate


def report(raw):
    protocol = tau3_bench.TRIAL_PROTOCOL
    tasks = {row['task_id'] for row in raw['manifest']}
    if not tasks <= tau3_bench.roster().keys():
        raise ValueError('report contains tasks outside pinned tau3 base split')
    for task in tasks:
        planned = [r for r in raw['manifest'] if r['task_id'] == task]
        if len(planned) != protocol['trials'] or [str(r['seed']) for r in planned] != [str(s) for s in protocol['seeds']]:
            raise ValueError('report does not use the declared three-trial seed protocol')
    groups = {task: tau3_bench.roster()[task]['domain'] for task in tasks}
    result = aggregate(raw['manifest'], raw['results'], protocol['ks'], groups=groups)
    result.update(source_revision=tau3_bench.REVISION, selected_tasks=len(tasks),
                  eligible_tasks=len(tau3_bench.roster()), official_full_base=len(tasks) == 375)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform_report', type=Path)
    args = parser.parse_args()
    print(json.dumps(report(json.loads(args.platform_report.read_text())), ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
