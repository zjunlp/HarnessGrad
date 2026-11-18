"""Select the explicit bubblewrap override without inheriting an ambient search path."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


def executable() -> str:
    override = os.environ.get('HG_BWRAP')
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError('HG_BWRAP is not an executable file; unset it or select a working bubblewrap')
        return str(path)
    selected = shutil.which('bwrap', path=os.defpath)
    if selected is None:
        raise ValueError('bubblewrap is required; install it or set HG_BWRAP to its executable path')
    return selected
