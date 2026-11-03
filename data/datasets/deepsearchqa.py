"""deepsearchqa native source, tasks and platform-owned post-exit scoring.

Pinned upstream adaptation; see data/scaffolds/native_research/PROVENANCE.json.
Raw native data stays outside the repository. Importing this declaration performs no
acquisition and makes no claim that its external runtime is provisioned.
"""
from data.scaffolds import roots
from data.scaffolds.native_research.adapter import Adapter, TaskMapping, downloads as source_downloads

KIND = 'dataset'
ORACLE_SOURCE = 'released'
DATASET_ROOT = roots.dataset_root('deepsearchqa', legacy='/mnt/20t/xubuqiang/datasets/deepsearchqa/native-prepared')
ADAPTER = Adapter('deepsearchqa', root=DATASET_ROOT)
METRIC = 'deepsearchqa.native-adapted'
load = ADAPTER.load
provision_status = ADAPTER.provision_status
SETUP = TaskMapping(ADAPTER, ADAPTER.setup)
VERIFY = TaskMapping(ADAPTER, ADAPTER.verifier)
ENV = TaskMapping(ADAPTER, ADAPTER.environment)


def downloads():
    return source_downloads('deepsearchqa')


def __getattr__(name):
    if name not in ('SPLIT', 'OFFICIAL_EVAL', 'NATIVE_SPLITS'):
        raise AttributeError(name)
    try:
        rows = ADAPTER.records()
    except (OSError, ValueError) as exc:
        raise AttributeError(str(exc)) from exc
    if name == 'SPLIT':
        result = ADAPTER.split()
        if result is None:
            raise AttributeError('this native release declares no training split')
        return result
    if name == 'OFFICIAL_EVAL':
        return [tid for tid, row in rows.items() if row.get('source_split') != 'train']
    return {key: [tid for tid, row in rows.items() if row.get('source_split') == key]
            for key in sorted({row.get('source_split', 'unassigned') for row in rows.values()})}
