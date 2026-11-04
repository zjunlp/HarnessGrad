"""Pin the official package and disable ambient dotenv/provider configuration."""
import hashlib
import json
import os
from pathlib import Path
import sys

RELEASE = 'v0.1.0'
#: The prepared bundle's own manifest digest, computed by `prepare.py` from the pinned
#: tarball. The bundle is source and domain data only -- no tasks, no trajectories.
BUNDLE_SHA256 = '82546c17152d18f67331e71852ba528ab2a2434dc401cab9949dc007b86be189'

def bootstrap(bundle, runtime=None):
    """Load pinned sources, optionally supplying dependencies for in-process callers."""
    if sys.version_info[:2] != (3, 13):
        # v0.1.0's pyproject declares `requires-python = ">=3.13"`, unlike the τ³ release
        # that runs on 3.12 -- the two adapters never share an interpreter.
        raise ValueError('tau2 v0.1.0 requires Python 3.13 before loading its pinned dependencies')
    bundle = Path(bundle).resolve()
    raw = (bundle / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != BUNDLE_SHA256:
        raise ValueError('tau2 bundle manifest digest mismatch')
    manifest = json.loads(raw)
    if manifest.get('release') != RELEASE:
        raise ValueError('tau2 bundle release mismatch')
    for relative, expected in manifest['files'].items():
        path = bundle / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('tau2 runtime asset digest mismatch: ' + relative)
    sys.dont_write_bytecode = True
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['TAU2_DATA_DIR'] = str(bundle / 'data')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['HF_HUB_OFFLINE'] = '1'
    if runtime:
        sites = list(Path(runtime).glob('lib/python3.13/site-packages'))
        if len(sites) != 1:
            raise ValueError('tau2 requires its prepared Python 3.13 runtime')
        sys.path.insert(0, str(sites[0]))
    sys.path.insert(0, str(bundle / 'src'))
    from loguru import logger
    logger.remove()
    return manifest
