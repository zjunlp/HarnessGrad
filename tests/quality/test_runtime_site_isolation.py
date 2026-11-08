"""Filesystem controls use only task-owned fake global packages."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from eval.containers import sandbox


@pytest.mark.parametrize("grant", ["base", "parent", "bin", "alias"])
@pytest.mark.parametrize("site_kind", ["site-packages", "dist-packages"])
@pytest.mark.parametrize("platform_interpreter", [True, False])
def test_base_sites_hidden_but_independent_venv_and_stdlib_survive(tmp_path, grant, site_kind, platform_interpreter):
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap unavailable")
    # Outside /tmp: private-tmp hiding must not supply the isolation under test.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="hg-site-control-", dir=Path.home()/".cache") as directory:
        root = Path(directory)
        base = root/"store"/"base"
        (base/"bin").mkdir(parents=True)
        (base/"bin/python").symlink_to(sys.executable)
        stdlib = base/"lib/python3.99"
        stdlib.mkdir(parents=True)
        (stdlib/"os.py").write_text("# synthetic stdlib marker")
        site = stdlib/site_kind
        site.mkdir()
        (site/"hg_global_sentinel.py").write_text("VALUE = 'private-global'")
        venv = root/"venv"
        deps = venv/"lib/python3.99/site-packages"
        deps.mkdir(parents=True)
        (venv/"pyvenv.cfg").write_text("home = synthetic")
        (deps/"hg_namespace_sdk").mkdir()
        (deps/"hg_namespace_sdk/kernel.py").write_text("VALUE = 'declared-sdk'")
        alias = root/"alias"
        alias.symlink_to(base, target_is_directory=True)
        task = tmp_path/"task"; task.mkdir()
        work = tmp_path/"work"; work.mkdir()
        grants = {"base": base, "parent":base.parent, "bin":base/"bin", "alias":alias}
        code = f"""import sys,json,pathlib,sqlite3,math
sys.path.insert(0,{str(site)!r})
import importlib.util
hidden=importlib.util.find_spec('hg_global_sentinel') is None
sys.path.insert(0,{str(deps)!r})
from hg_namespace_sdk import kernel
try:pathlib.Path(kernel.__file__).write_text("changed")
except OSError:readonly=True
else:readonly=False
print(json.dumps([hidden,not pathlib.Path({str(site/'hg_global_sentinel.py')!r}).exists(),kernel.VALUE,readonly,math.sqrt(9),sqlite3.connect(':memory:').execute('select 1').fetchone()[0]]))
"""
        cmd=sandbox.command([sys.executable,"-I","-c",code],platform=Path.cwd(),
            work_root=work,task_dir=task,repo=task,env={},runtime_paths=(grants[grant],venv,Path(sys.base_prefix)),
            platform_interpreter=platform_interpreter)
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == [True,True,"declared-sdk",True,3.0,1]


def test_global_site_cannot_be_explicitly_reopened(tmp_path):
    site=Path(sys.base_prefix)/f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    if not site.is_dir():
        pytest.skip("base has no global site")
    task=tmp_path/"task";task.mkdir()
    work=tmp_path/"work";work.mkdir()
    with pytest.raises(sandbox.SandboxUnavailable, match="global Python site"):
        sandbox.command(["/bin/true"],platform=Path.cwd(),work_root=work,task_dir=task,
                        repo=task,env={},runtime_paths=(site,))


def test_public_projection_survives_global_mask(tmp_path):
    if not shutil.which('bwrap'):
        pytest.skip('bubblewrap unavailable')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='hg-site-projection-', dir=Path.home()/'.cache') as directory:
        root = Path(directory)
        base = root/'base'
        (base/'bin').mkdir(parents=True)
        (base/'bin/python').symlink_to(sys.executable)
        stdlib = base/'lib/python3.99'; stdlib.mkdir(parents=True)
        (stdlib/'os.py').write_text('# synthetic stdlib')
        target = stdlib/'site-packages/hg_public_sdk'; target.mkdir(parents=True)
        (target/'private_sentinel.py').write_text('# synthetic private sentinel')
        public = root/'public'; public.mkdir()
        (public/'__init__.py').write_text("VALUE = 'public'")
        task=tmp_path/'task';task.mkdir()
        work=tmp_path/'work';work.mkdir()
        code = (f'import sys,pathlib;sys.path.insert(0,{str(target.parent)!r});'
                'import hg_public_sdk;print(hg_public_sdk.VALUE);'
                f'assert not pathlib.Path({str(target/"private_sentinel.py")!r}).exists()')
        cmd=sandbox.command([sys.executable,'-I','-c',code],platform=Path.cwd(),
            work_root=work,task_dir=task,repo=task,env={},runtime_paths=(base,),
            package_overlays=((public,target),),private_paths=(target/'private_sentinel.py',))
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'public'
