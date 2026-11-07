"""math500: pinned native upstream QA protocol through the HarnessGrad verifier.

See data/scaffolds/native_qa/README.md for source, split and fidelity boundaries.
Imports never fetch data. Prepare the external data root explicitly before load().
"""
from harnessgrad.paths import dataset_root
from data.scaffolds.native_qa import Dataset

KIND = "dataset"
MANAGED_EXECUTION_RUNTIME = "math"
ORACLE_SOURCE = "released"
METRIC = 'math-verify-0.9.0-last-boxed'
DATASET_ROOT = dataset_root('math500')
_spec = Dataset("math500", DATASET_ROOT)
DEFAULT_ENV = {"kind": "files"}

def load():
    return _spec.load()

def downloads():
    return _spec.downloads()

def score(prediction, gold):
    from data.scaffolds.native_qa.scoring import score as native_score
    return native_score("math500", prediction, gold)

def provision_status():
    return _spec.provision_status()

def __getattr__(name):
    return _spec.getattr(name)
