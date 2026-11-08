"""Opt-in real artifact acceptance; missing dependencies remain missing evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_real_math_failure_boundaries_from_cold_platform(tmp_path):
    store = os.environ.get('HG_TEST_MATH_RUNTIME_ROOT')
    if not store:
        pytest.skip('set HG_TEST_MATH_RUNTIME_ROOT to an explicitly prepared Math execution store')
    from harnessgrad import dataset_runtime
    status = dataset_runtime.check_execution('math', root=Path(store))
    if not status['present'] and status['status'] == 'unavailable':
        pytest.skip('selected Math execution artifact is absent; prepare it explicitly outside this test')
    assert status['present'], 'selected Math execution artifact is invalid: ' + status.get('detail', '')
    # The native sandbox intentionally hides /tmp interpreter prefixes.
    cache = Path.home() / '.cache'
    cache.mkdir(exist_ok=True)
    private_platform = tempfile.TemporaryDirectory(prefix='hg-math-cold-platform-', dir=cache)
    platform = Path(private_platform.name) / 'venv'
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(platform)
    home = tmp_path / 'home'
    home.mkdir()
    output = tmp_path / 'private-control'
    output.mkdir(mode=0o700)
    environment = {'HOME': str(home), 'PATH': os.defpath, 'LANG': 'C.UTF-8',
                   'PYTHONDONTWRITEBYTECODE': '1', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    try:
        result = subprocess.run([str(platform / 'bin/python'), '-I', '-B',
            str(ROOT / 'tests/fixtures/math_native_failure_control.py'), str(ROOT), store, str(output)],
            cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=180)
    finally:
        private_platform.cleanup()
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['platform_math_verify_present'] is False
    assert len(report['controls']) == 16
    for name in ('math', 'math500'):
        cases = {row['control']: row for row in report['controls'] if row['dataset'] == name}
        assert cases['empty-prediction']['score'] == 0
        assert cases['malformed-final-box']['score'] == 0
        assert cases['last-box-overrides-correct']['score'] == 0
        assert cases['unparseable-private-reference']['score'] is None
        assert cases['native-timeout-with-old-reward']['outcome'] == 'unmeasured'
        assert cases['native-timeout-with-old-reward']['deadline_exhausted'] is True
    assert 'PRIVATE AUTHORED REASONING' not in json.dumps(report)
