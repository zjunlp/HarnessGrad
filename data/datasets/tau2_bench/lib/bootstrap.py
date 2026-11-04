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
#: The original receipt accidentally included 66 interpreter-specific __pycache__ files.
#: Fresh preparation excludes bytecode. This second pin is the identical official source,
#: domain data and LICENSE file map, with only those cache entries removed. Keep accepting
#: the original receipt without rewriting a deployed payload.
SOURCE_BUNDLE_SHA256 = '344dd2017a2e273a9378c232784c5bb47642af80298ba68847851a7f33953419'

def validate_bundle(bundle):
    """Read-only pin and complete file-set validation; usable from Python 3.12 too."""
    bundle = Path(bundle)
    if bundle.is_symlink() or (bundle / 'manifest.json').is_symlink():
        raise ValueError('symlink in tau2 bundle manifest/root')
    raw = (bundle / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() not in (BUNDLE_SHA256, SOURCE_BUNDLE_SHA256):
        raise ValueError('tau2 bundle manifest digest mismatch')
    manifest = json.loads(raw)
    if manifest.get('release') != RELEASE:
        raise ValueError('tau2 bundle release mismatch')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files or 'LICENSE' not in files:
        raise ValueError('tau2 bundle files/license missing')
    for relative, expected in files.items():
        parts = Path(relative)
        if parts.is_absolute() or '..' in parts.parts or str(parts) != relative:
            raise ValueError('unsafe tau2 bundle path: ' + relative)
        path = bundle / relative
        if any((bundle / Path(*parts.parts[:i])).is_symlink()
               for i in range(len(parts.parts) + 1)):
            raise ValueError('symlink in tau2 runtime asset: ' + relative)
        if not path.is_file():
            raise ValueError('missing tau2 runtime asset: ' + relative)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('tau2 runtime asset digest mismatch: ' + relative)
    for path in bundle.rglob('*'):
        if path.is_symlink():
            raise ValueError('symlink in tau2 runtime asset: ' + path.relative_to(bundle).as_posix())
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*')
              if p.is_file() and p != bundle / 'manifest.json'
              and (p.relative_to(bundle).as_posix() in files
                   or '__pycache__' not in p.relative_to(bundle).parts)}
    if actual != set(files):
        raise ValueError('unlisted tau2 runtime assets: ' + ', '.join(sorted(actual - set(files))[:3]))
    return manifest


def bootstrap(bundle, runtime=None):
    """Load pinned sources, optionally supplying dependencies for in-process callers."""
    if sys.version_info[:2] != (3, 13):
        # v0.1.0's pyproject declares `requires-python = ">=3.13"`, unlike the τ³ release
        # that runs on 3.12 -- the two adapters never share an interpreter.
        raise ValueError('tau2 v0.1.0 requires Python 3.13 before loading its pinned dependencies')
    bundle = Path(bundle).resolve()
    manifest = validate_bundle(bundle)
    sys.dont_write_bytecode = True
    # Never execute unlisted/generated cache files from an otherwise pinned source tree.
    sys.pycache_prefix = str(bundle / '.bytecode-disabled')
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
