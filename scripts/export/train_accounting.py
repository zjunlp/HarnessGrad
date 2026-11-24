"""Original Train cost overrides keyed by execution ID and round."""
import json
from pathlib import Path

REVIEW = json.loads((Path(__file__).parent / 'reviewed-results/original-train-costs.json').read_text())
SOURCE = REVIEW['source']


def original_cost(run, number):
    return REVIEW['runs'].get(run, {}).get(str(number))


def apply_task_cost(entry, reviewed):
    totals = [reviewed['input'], reviewed['output']]
    if None in totals:
        raise ValueError('Cannot replace a round total with a subtotal')
    entry['task'] = {'tokens': sum(totals), 'calls': None}
    entry['task_split'] = {'input': totals[0], 'output': totals[1],
                           'tasks': reviewed['tasks'], 'original_train_accounting': True}
    entry.pop('task_unrecorded', None)
