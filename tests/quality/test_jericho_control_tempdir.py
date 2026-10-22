"""The standalone check needs writable private temp storage in its real sandbox."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _control():
    spec = importlib.util.spec_from_file_location(
        "jericho_control_tempdir_test", ROOT / "data/datasets/jericho/control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap unavailable")
@pytest.mark.parametrize("inherited_root", [None, "/"])
def test_standalone_check_can_use_private_tmp(monkeypatch, inherited_root):
    control = _control()
    if inherited_root is None:
        monkeypatch.delenv("HARNESSGRAD_WORK_ROOT", raising=False)
    else:
        monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", inherited_root)
    # Match main()'s flat temp-directory layout rather than pytest's nested tmp_path.
    with tempfile.TemporaryDirectory(prefix="jericho-control-regression-") as directory:
        workdir = Path(directory)
        sentinel = workdir / "artifact.txt"
        sentinel.write_text("frozen task artifact")
        verifier = {
            "kind": "command",
            "argv": [sys.executable, "check.py"],
            "inputs": [{"path": "check.py", "content": '''
import errno
import json
import os
import tempfile
from pathlib import Path

assert os.environ['TMPDIR'] == '/tmp'
assert tempfile.gettempdir() == '/tmp'
with tempfile.TemporaryDirectory(prefix='jericho-verifier-') as directory:
    scratch = Path(directory) / 'scratch'
    scratch.write_bytes(b'blat')
    assert scratch.read_bytes() == b'blat'
assert not scratch.parent.exists()
try:
    Path('artifact.txt').write_text('changed')
except OSError as exc:
    assert exc.errno == errno.EROFS
else:
    raise AssertionError('task artifacts must remain read-only')
Path('verifier/evidence.json').write_text(json.dumps({'measured': True}))
Path('verifier/reward.txt').write_text('1\\n')
'''}],
            "reward_file": "/verifier/reward.txt",
            "evidence_file": "/verifier/evidence.json",
        }
        module = SimpleNamespace(VERIFY={"synthetic": verifier}, VERIFY_TIMEOUT_S=15,
                                 DEFAULT_ENV={"kind": "files", "network": "none"})
        reward, detail = control.run_check(module, {"task_id": "synthetic"}, workdir)
        assert reward == 1.0, detail
        assert json.loads(detail)["measured"] is True
        assert sentinel.read_text() == "frozen task artifact"
        assert os.environ.get("HARNESSGRAD_WORK_ROOT") == inherited_root


@pytest.mark.parametrize("inherited_root", [None, "/previous-work-root"])
def test_check_restores_work_root_after_verifier_exception(tmp_path, monkeypatch, inherited_root):
    control = _control()
    if inherited_root is None:
        monkeypatch.delenv("HARNESSGRAD_WORK_ROOT", raising=False)
    else:
        monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", inherited_root)
    module = SimpleNamespace(VERIFY={"synthetic": {}}, VERIFY_TIMEOUT_S=15,
                             DEFAULT_ENV={"kind": "files"})

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic verifier", 15)

    monkeypatch.setattr("eval.verifier._verify", timed_out)
    with pytest.raises(subprocess.TimeoutExpired):
        control.run_check(module, {"task_id": "synthetic"}, tmp_path)
    assert os.environ.get("HARNESSGRAD_WORK_ROOT") == inherited_root
