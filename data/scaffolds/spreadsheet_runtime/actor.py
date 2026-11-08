"""Public spreadsheet editing tools from an explicitly prepared dependency artifact.

The artifact contains packages only: no scorer sources, gold or native manifests.
The platform admits and mounts it read-only for task shell tools independently of
whatever SDK interpreter the selected harness requires.
"""
from collections.abc import Mapping


def environment(dataset):
    from harnessgrad.dataset_runtime import bind, resolve_execution
    return {'kind': 'files', 'network': 'none',
            'task_python': bind(resolve_execution(dataset), 'actor')}


class Environments(Mapping):
    def __init__(self, dataset, records):
        self.dataset, self.records = dataset, records

    def __iter__(self):
        return (row['task_id'] for row in self.records())

    def __len__(self):
        return len(self.records())

    def __getitem__(self, task_id):
        if any(row['task_id'] == task_id for row in self.records()):
            return environment(self.dataset)
        raise KeyError(task_id)
