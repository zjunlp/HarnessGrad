"""searchqa: pinned native FlashRSI QA protocol through the HarnessGrad verifier.

See data/scaffolds/flashrsi_qa/README.md for source, split and fidelity boundaries.
Imports never fetch data. Prepare the external data root explicitly before load().
"""
from data.scaffolds import roots
from data.scaffolds.flashrsi_qa import Dataset

KIND = "dataset"
ORACLE_SOURCE = "released"
METRIC = 'text_em_f1'
DATASET_ROOT = roots.dataset_root('searchqa', legacy='/mnt/20t/xubuqiang/datasets/searchqa')
_spec = Dataset("searchqa", DATASET_ROOT)
DEFAULT_ENV = {"kind": "files"}

def load():
    return _spec.load()

def downloads():
    return _spec.downloads()

def score(prediction, gold):
    from data.scaffolds.flashrsi_qa.scoring import score as native_score
    return native_score("searchqa", prediction, gold)

def provision_status():
    try:
        tasks, _ = _spec.load()
        return {'root': str(DATASET_ROOT), 'present': True, 'tasks': len(tasks), 'metric': METRIC,
                'scope': 'prepared native source; real-data diagnostic is a separate gate'}
    except (OSError, ValueError) as exc:
        return {'root': str(DATASET_ROOT), 'present': False, 'tasks': None, 'detail': str(exc)}

def __getattr__(name):
    return _spec.getattr(name)
