"""jobbench: pinned native task world, artifact export and fresh Harbor verification.

Loading is conditional on prepared local immutable images, source-bound runtime
manifest, fixed Harbor interpreter and independent local judge where required.
"""
from pathlib import Path

from harnessgrad.paths import dataset_root
import os
from data.scaffolds import native_harbor as native

KIND = 'dataset'
ORACLE_SOURCE = 'released'
OFFICIAL_EVAL = None
SPLIT = None
SPLIT_SOURCE = 'none'
DEFAULT_ENV = {'kind': 'files', 'network': 'none'}
DATASET_ROOT = dataset_root('jobbench')
REVISION = 'd43a66575635da0d3f6cb9a20976dc1a60f3ffc9'
SOURCE = 'https://huggingface.co/datasets/JobBench/job-bench'
METRIC = 'reward'


def inventory():
    return native.inventory(DATASET_ROOT, 'jobbench', collection=os.environ.get('HG_JOBBENCH_COLLECTION', 'main'))


def load():
    rows = inventory()
    return [native.public_task(r) for r in rows], {r.task_id: METRIC for r in rows}


def provision_status():
    return native.status(DATASET_ROOT, 'jobbench', collection=os.environ.get('HG_JOBBENCH_COLLECTION', 'main'))


def downloads():
    return [{'kind': 'http', 'name': 'JobBench official native renderer',
             'version': '660b33a86ed032559df0b2fdc1ad38cf7ceb83f3',
             'url': 'https://codeload.github.com/Job-Bench/job-bench-eval/tar.gz/660b33a86ed032559df0b2fdc1ad38cf7ceb83f3',
             'dst': '_src/native-renderer.tar.gz', 'size': 'upstream source archive',
             'note': 'Pinned native renderer. Explicit prepare.py --render fetches JobBench/job-bench at d43a66575635da0d3f6cb9a20976dc1a60f3ffc9 and preserves its generated private rubric verifier.'}]


from collections.abc import Mapping


class _Selected(Mapping):
    def __iter__(self):
        return iter(r.task_id for r in inventory())
    def __len__(self):
        return len(inventory())
    def __init__(self, build):
        self.build = build
    def __getitem__(self, task_id):
        row = next((r for r in inventory() if r.task_id == task_id), None)
        if row is None:
            raise KeyError(task_id)
        return self.build(row)


def __getattr__(name):
    if name == 'SETUP':
        return _Selected(native.setup)
    if name == 'VERIFY':
        return _Selected(native.verifier)
    if name == 'ENV':
        return _Selected(lambda r: dict(DEFAULT_ENV))
    raise AttributeError(name)
