"""Pin the official package and disable ambient dotenv/provider configuration."""
import hashlib
import json
import os
from pathlib import Path
import sys

REVISION = 'fc0055dc4e0a316c3f83133267fbd6faaa770992'

def bootstrap(bundle, runtime=None):
    if sys.version_info[:2] != (3, 12):
        raise ValueError('tau3 requires Python 3.12 before loading its pinned dependencies')
    bundle = Path(bundle).resolve()
    raw = (bundle / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != 'b534b3c0c068bdb647c130c05431b73566a1c94ff54eaa6e414c27a20ba44b4a':
        raise ValueError('tau3 bundle manifest digest mismatch')
    manifest = json.loads(raw)
    if manifest['revision'] != REVISION:
        raise ValueError('tau3 source revision mismatch')
    for relative, expected in manifest['files'].items():
        path = bundle / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('tau3 runtime asset digest mismatch: ' + relative)
    sys.dont_write_bytecode = True
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['TAU2_DATA_DIR'] = str(bundle / 'data')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['HF_HUB_OFFLINE'] = '1'
    if runtime:
        sites = list(Path(runtime).glob('lib/python3.12/site-packages'))
        if len(sites) != 1:
            raise ValueError('tau3 requires its prepared Python 3.12 runtime')
        sys.path.insert(0, str(sites[0]))
    sys.path.insert(0, str(bundle / 'src'))
    from loguru import logger
    logger.remove()
    return manifest
