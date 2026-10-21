"""The WSL-generated resolver remains readable inside the real namespace."""
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.containers import sandbox


@pytest.mark.skipif(
    shutil.which("bwrap") is None or not Path("/mnt/wsl/resolv.conf").is_file(),
    reason="requires WSL with bubblewrap",
)
def test_wsl_resolver_is_visible_readonly_without_exposing_other_host_files(tmp_path):
    repo, task = tmp_path / "repo", tmp_path / "task"
    repo.mkdir()
    task.mkdir()
    code = (
        "from pathlib import Path; import os, json; "
        "p=Path('/etc/resolv.conf'); "
        "print(json.dumps({'content':p.read_text(),'writable':os.access(p,os.W_OK),"
        "'platform_visible':Path(" + repr(str(ROOT / "driver.py")) + ").exists()}))"
    )
    proc = sandbox.run(
        [sys.executable, "-c", code], platform=ROOT, work_root=repo, repo=repo,
        task_dir=task, env={}, timeout_s=30, cwd=task, network="none",
    )
    assert proc.returncode == 0, proc.stderr
    import json
    seen = json.loads(proc.stdout)
    assert seen["content"] == Path("/etc/resolv.conf").read_text()
    assert not seen["writable"]
    assert not seen["platform_visible"]
