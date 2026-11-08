"""Evaluation usage coverage projections; canonical arithmetic stays in tracing."""
from __future__ import annotations

from dataclasses import dataclass, field

from eval.observe.tracing import _last_usage, harness_usage_totals, environment_usage_totals


@dataclass
class EnvironmentUsage:
    by_task: dict = field(default_factory=dict)

    def observe(self, tid, outcome):
        if 'usage_records' in outcome:
            self.by_task[tid] = [record['env_usage'] for record in outcome['usage_records']]

    def totals(self):
        return environment_usage_totals(report for reports in self.by_task.values() for report in reports)


def harness_reports(tasks, outcomes, trial_traces, traces):
    # Preserve the historical fallback for outcomes that predate explicit attempt
    # usage, including stored cached traces; coverage arithmetic stays canonical.
    return {task['task_id']:
        ([record['usage'] for record in outcomes[task['task_id']]['usage_records']]
         if 'usage_records' in outcomes[task['task_id']] else
         [_last_usage(trace) for trace in trial_traces.get(task['task_id'],
          [traces[task['task_id']]] if task['task_id'] in traces else [])]) for task in tasks}


def harness_totals(by_task, *, trials):
    return harness_usage_totals(by_task, trials=trials)
