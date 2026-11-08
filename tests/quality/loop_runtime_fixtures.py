"""Real locked Loop SDK preparation shared by offline integration controls.

Select a genuine uv wheel cache with UV_CACHE_DIR, or original wheels with
HG_HARNESS_WHEELHOUSE. No package doubles or automatic network acquisition.
"""
from pathlib import Path
import os
import shutil
import tempfile
from types import SimpleNamespace

import pytest
from harnessgrad import config, python_exec_runtime as runtime
from harnessgrad import python_harness_runtime as native

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def prepared_loop_runtime():
    if not shutil.which('uv') or not shutil.which('bwrap'):
        pytest.fail('Loop controls require uv and bubblewrap; no SDK control was executed')
    root = Path(tempfile.mkdtemp(prefix='hg-loop-test-', dir=Path.home() / '.cache'))
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
            patch.setenv('UV_OFFLINE', '1')
            patch.setenv('UV_PYTHON_DOWNLOADS', 'never')
            store = Path(os.environ.get('HG_HARNESS_RUNTIME_ROOT') or root / 'store').resolve()
            patch.setenv('HG_HARNESS_RUNTIME_ROOT', str(store))
            if not os.environ.get('UV_CACHE_DIR'):
                # Never rely on a developer's ambient uv cache. A configured
                # wheelhouse works with this initially empty owned cache.
                patch.setenv('UV_CACHE_DIR', str(root / 'wheel-cache'))
            config.reset()
            try:
                receipt = runtime.prepare(ROOT / 'base_harness/loop', build=True)
            except native.RuntimeUnavailable as exc:
                pytest.fail('Genuine Loop SDK wheels are unavailable offline. Select the complete '
                    'locked cache with UV_CACHE_DIR or original wheels with HG_HARNESS_WHEELHOUSE. '
                    'Prepare explicitly with tools/prepare_loop_test_runtime.py; tests never '
                    'download or fabricate SDK packages. Original error: ' + str(exc))
            yield SimpleNamespace(root=root, store=store, receipt=receipt)
    finally:
        config.reset()
        native.remove_instance(root)
