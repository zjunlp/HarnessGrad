"""No-model review boundary: actual dotenv readers cannot undo sanitized launch env."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import driver
from harnessgrad import config, paths
from harnessgrad.env_file import REVIEWED_ENVIRONMENT_ONLY as MARKER
from tools import launch_appworld_harness_baselines as launcher

ROOT=Path(__file__).resolve().parents[2]
INJECTED={'HG_AGENT_CODEX_COMMAND':'unreviewed-codex-wrapper',
          'HG_AGENT_DSH_COMMAND':'unreviewed-dsh-wrapper',
          'HG_AGENT_MAX_STEPS':'1','HG_MINISWE_STEP_LIMIT':'1'}


@pytest.fixture
def checkouts(tmp_path,monkeypatch):
    main=tmp_path/'main';linked=tmp_path/'linked'
    gitdir=main/'.git/worktrees/linked';gitdir.mkdir(parents=True);linked.mkdir()
    (gitdir/'commondir').write_text('../..')
    (linked/'.git').write_text('gitdir: '+str(gitdir))
    (main/'.env').write_text('HG_DATA_ROOT='+str(tmp_path/'ignored-data')+'\nHG_OVERLAY_ROOT='+str(tmp_path/'ignored-overlay')+'\nHG_AGENT_CODEX_COMMAND=unreviewed-codex-wrapper\n')
    (linked/'.env').write_text('\n'.join(k+'='+v for k,v in INJECTED.items())+'\n')
    monkeypatch.setattr(paths,'CHECKOUT',linked)
    for key in (*INJECTED,'HG_DATA_ROOT','HG_DATA_APPWORLD','HG_OVERLAY_ROOT',MARKER):
        monkeypatch.setenv(key,'tracked-before-clear');monkeypatch.delenv(key)
    config.reset()
    yield main,linked
    config.reset()


def test_reviewed_readers_skip_both_files_and_preserve_explicit_roots(checkouts,tmp_path,monkeypatch):
    main,linked=checkouts
    assert paths.main_checkout()==main
    monkeypatch.setenv(MARKER,'1')
    monkeypatch.setenv('HG_DATA_APPWORLD',str(tmp_path/'reviewed-data'))
    monkeypatch.setenv('HG_OVERLAY_ROOT',str(tmp_path/'reviewed-overlay'))
    monkeypatch.setenv('HG_AGENT_DSH_COMMAND','explicit-reviewed-wrapper')
    def no_read(path):raise AssertionError('reviewed mode must not read dotenv: '+str(path))
    monkeypatch.setattr(paths,'read_env_file',no_read)
    import harnessgrad.env_file as env_file
    monkeypatch.setattr(env_file,'read_env_file',no_read)
    assert driver.load_env(main/'.env')=={}
    assert driver.load_env(linked/'.env')=={}
    assert paths.dataset_root('appworld')==tmp_path/'reviewed-data'
    assert paths.datasets_root(required=False) is None
    assert config.current().runtime.overlay_root==str(tmp_path/'reviewed-overlay')
    assert os.environ['HG_AGENT_DSH_COMMAND']=='explicit-reviewed-wrapper'
    assert 'HG_AGENT_CODEX_COMMAND' not in os.environ
    assert 'HG_AGENT_MAX_STEPS' not in os.environ


def test_absent_marker_keeps_legacy_file_loading_and_process_precedence(checkouts,monkeypatch):
    main,linked=checkouts
    assert paths.datasets_root()==main.parent/'ignored-data'
    monkeypatch.setenv('HG_AGENT_DSH_COMMAND','explicit-wins')
    loaded=driver.load_env(linked/'.env')
    assert loaded==INJECTED
    assert os.environ['HG_AGENT_DSH_COMMAND']=='explicit-wins'
    assert os.environ['HG_AGENT_CODEX_COMMAND']==INJECTED['HG_AGENT_CODEX_COMMAND']
    assert os.environ['HG_AGENT_MAX_STEPS']=='1'
    assert config.current().storage.data_root==str(main.parent/'ignored-data')


def test_mode_transition_invalidates_prior_dotenv_dataset_snapshot(checkouts,monkeypatch):
    main,linked=checkouts
    before=config.current()
    assert before.storage.data_root==str(main.parent/'ignored-data')
    # No declared config variable changed: the marker alone must invalidate cache.
    monkeypatch.setenv(MARKER,'1')
    after=config.current()
    assert after is not before
    assert after.storage.data_root is None
    assert paths.datasets_root(required=False) is None
    monkeypatch.delenv(MARKER)
    assert config.current().storage.data_root==str(main.parent/'ignored-data')


def test_launcher_marker_precedes_real_driver_import_and_all_file_loads(checkouts,tmp_path,monkeypatch):
    main,linked=checkouts
    runtime={'environment':{'HG_OVERLAY_ROOT':str(tmp_path/'reviewed-overlay')}}
    monkeypatch.setattr(launcher,'runtime_binding',lambda *a:runtime)
    monkeypatch.setattr(launcher,'validate_appworld_overlay',lambda *a:None)
    monkeypatch.setattr(launcher,'dataset_environment',lambda:{'HG_DATA_APPWORLD':str(tmp_path/'reviewed-data')})
    model={'backend':'openai','name':'synthetic-no-call','endpoint':'http://127.0.0.1:1/v1'}
    identity={'harness':'miniagent','pool':'train','runtime_sha256':launcher.digest(runtime),'model':model,
              'budget':{'output_tokens':10,'deadline_s':60,'request_timeout_s':10,'native_settings':{}}}
    for key,value in INJECTED.items():monkeypatch.setenv(key,value)
    monkeypatch.setenv(MARKER,'0')
    env=launcher.execution_environment(identity,{'harness_runtimes':{'miniagent':{}}})
    assert env[MARKER]=='1'
    assert not set(INJECTED)&set(env)
    # Only the subprocess module search path is test-provided, never production.
    script='''import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import driver
from harnessgrad import paths,config
paths.CHECKOUT=Path(sys.argv[2])
assert driver.load_env(Path(sys.argv[3]))=={}
assert driver.load_env(Path(sys.argv[2])/'.env')=={}
assert all(k not in os.environ for k in json.loads(sys.argv[4]))
assert paths.datasets_root(required=False) is None
assert str(paths.dataset_root('appworld'))==os.environ['HG_DATA_APPWORLD']
assert config.current().runtime.overlay_root==os.environ['HG_OVERLAY_ROOT']
print('reviewed-environment-only-ok')
'''
    result=subprocess.run([sys.executable,'-c',script,str(ROOT),str(linked),str(main/'.env'),json.dumps(list(INJECTED))],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='reviewed-environment-only-ok'
