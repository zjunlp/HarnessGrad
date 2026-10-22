"""Pin the official τ² v0.1.0 package and disable ambient dotenv/provider configuration.

Same shape as the τ³ bootstrap, with two deliberate differences: the interpreter check is 3.13
(τ² v0.1.0 declares `requires-python>=3.13`), and the digest covers the payload's own manifest
rather than a bundle manifest inside the archive, because this payload is built here by
`prepare.py` rather than extracted from a release tarball.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

SCHEMA = 'hg-tau2-payload.v1'


def bootstrap(bundle: Path, runtime: Path | None = None,
              manifest: Path | None = None) -> dict:
    """Verify the payload manifest, then put the pinned sources on `sys.path`.

    `manifest` is explicit when the caller mounts the bundle and the manifest separately (the
    verifier does: it copies both as inputs), and defaults to the payload layout
    (`<root>/bundle` next to `<root>/manifest.json`) otherwise.
    """
    bundle = Path(bundle).resolve()
    manifest_path = Path(manifest) if manifest is not None else bundle.parent / 'manifest.json'
    if not manifest_path.is_file():
        raise ValueError(f'no τ² payload manifest at {manifest_path}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema') != SCHEMA:
        raise ValueError('τ² payload schema mismatch')
    if sys.version_info[:2] != (3, 13):
        raise ValueError('τ² v0.1.0 requires Python 3.13 before loading its pinned dependencies')
    # The sources are read-only material, not an installed package: never write bytecode into the
    # payload, and never let an ambient `.env` reach the user simulator's model call.
    sys.dont_write_bytecode = True
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['HF_HUB_OFFLINE'] = '1'
    if runtime is not None:
        sites = list(Path(runtime).glob('lib/python3.13/site-packages'))
        if len(sites) != 1:
            raise ValueError('τ² requires its prepared Python 3.13 runtime')
        sys.path.insert(0, str(sites[0]))
    sys.path.insert(0, str(bundle / 'src'))
    return manifest
