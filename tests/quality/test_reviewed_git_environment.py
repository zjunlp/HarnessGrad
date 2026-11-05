"""Real Git stages with synthetic HOME/config only; never user configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from tools import launch_appworld_harness_baselines as launcher

ROOT=Path(__file__).resolve().parents[2]


def executable(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);path.chmod(0o755)


def stage(source,work,env):
    script='import sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from eval.ckpt.git_state import stage_workspace;stage_workspace(Path(sys.argv[2]),Path(sys.argv[3]))'
    result=subprocess.run([sys.executable,'-B','-c',script,str(ROOT),str(source),str(work)],
                          env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


def reviewed_env(home,system,monkeypatch):
    monkeypatch.setenv('HOME',str(home));monkeypatch.setenv('GIT_CONFIG_SYSTEM',str(system))
    monkeypatch.setenv('GIT_TEMPLATE_DIR','/unreviewed/template')
    monkeypatch.setenv('GIT_CONFIG_COUNT','1');monkeypatch.setenv('GIT_CONFIG_KEY_0','core.hooksPath')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0','/unreviewed/hooks')
    runtime={'environment':{}, 'harness_filesystem':{
        'schema':'harnessgrad-owned-source-filesystem-v1','sha256':'1'*64,'entries':1},
        'harness_source':{'path':str((ROOT/'base_harness/miniagent').resolve()),
                          'submission_sha256':'2'*64,'manifest_sha256':'3'*64},
        'launch_entry':{'python':str(Path(sys.executable).resolve()),'python_sha256':'4'*64,
                        'driver':str((ROOT/'driver.py').resolve()),'driver_sha256':'5'*64}}
    monkeypatch.setattr(launcher,'harness_inputs',lambda *a:(ROOT/'base_harness/miniagent',{},
                                                            {'submission_sha256':'2'*64},'3'*64,runtime))
    monkeypatch.setattr(launcher,'validate_appworld_overlay',lambda *a:None)
    monkeypatch.setattr(launcher,'dataset_environment',lambda:{'HG_DATA_APPWORLD':'/synthetic/unused'})
    identity={'harness':'miniagent','pool':'train',
              'runtime_sha256':launcher.digest(launcher.runtime_commitment(runtime)),
              'model':{'backend':'openai','name':'synthetic','endpoint':'http://127.0.0.1:1/v1'},
              'budget':{'output_tokens':10,'deadline_s':60,'request_timeout_s':10,'native_settings':{}}}
    env=launcher.execution_environment(identity,{'harness_runtimes':{'miniagent':{}}})
    assert not any(key.startswith('GIT_') for key in env)
    return env


@pytest.mark.parametrize('kind',['global-hook','global-template','system-template','global-filter'])
def test_reviewed_stage_ignores_synthetic_executable_git_configuration(tmp_path,monkeypatch,kind):
    home=tmp_path/'home';home.mkdir();system=tmp_path/'system.gitconfig';system.write_text('')
    sentinel=tmp_path/'sentinel';hook=tmp_path/'hooks/pre-commit'
    executable(hook,'#!/bin/sh\nprintf HOOK >> '+str(sentinel)+'\n')
    template=tmp_path/'template';executable(template/'hooks/pre-commit',hook.read_text())
    filter_program=tmp_path/'filter.sh';executable(filter_program,'#!/bin/sh\nprintf FILTER >> '+str(sentinel)+'\ncat\n')
    source=tmp_path/'source';source.mkdir();(source/'payload.txt').write_text('synthetic payload')
    if kind=='global-hook':config='[core]\n hooksPath = '+json.dumps(str(hook.parent))+'\n'
    elif kind.endswith('template'):config='[init]\n templateDir = '+json.dumps(str(template))+'\n'
    else:
        config='[filter "probe"]\n clean = '+json.dumps(str(filter_program))+'\n'
        (source/'.gitattributes').write_text('payload.txt filter=probe\n')
    if kind=='system-template':system.write_text(config)
    else:(home/'.gitconfig').write_text(config)
    # Legacy control uses only synthetic global/system configuration. No real
    # HOME, XDG Git config, system file or inherited Git variable is consulted.
    legacy={'HOME':str(home),'XDG_CONFIG_HOME':str(home/'xdg'),'PATH':'/usr/local/bin:/usr/bin:/bin',
            'GIT_CONFIG_SYSTEM':str(system),'PYTHONDONTWRITEBYTECODE':'1'}
    if not kind.endswith('template'):
        legacy['GIT_TEMPLATE_DIR']=''
    stage(source,tmp_path/'legacy',legacy)
    assert sentinel.exists(),'legacy must demonstrate executable config was read'
    sentinel.unlink()
    env=reviewed_env(home,system,monkeypatch)
    stage(source,tmp_path/'reviewed',env)
    assert not sentinel.exists()
    assert not (tmp_path/'reviewed/.git/hooks/pre-commit').exists()
    assert (tmp_path/'reviewed/.git/HEAD').is_file()


def test_reviewed_stage_disables_default_home_attributes(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();system=tmp_path/'system.gitconfig';system.write_text('')
    # Git can discover attributes even without a global config file. Their
    # deterministic effect on index bytes must not enter a reviewed stage either.
    attrs=home/'.config/git/attributes';attrs.parent.mkdir(parents=True);attrs.write_text('payload.txt text eol=lf\n')
    source=tmp_path/'source';source.mkdir();(source/'payload.txt').write_bytes(b'a\r\nb\r\n')
    legacy={'HOME':str(home),'PATH':'/usr/local/bin:/usr/bin:/bin',
            'GIT_CONFIG_SYSTEM':str(system),'GIT_TEMPLATE_DIR':'','PYTHONDONTWRITEBYTECODE':'1'}
    stage(source,tmp_path/'legacy',legacy)
    baseline=subprocess.run(['git','-C',str(tmp_path/'legacy'),'show','HEAD:payload.txt'],
                            env=legacy,capture_output=True,check=True)
    assert baseline.stdout==b'a\nb\n'
    env=reviewed_env(home,system,monkeypatch);work=tmp_path/'reviewed';stage(source,work,env)
    result=subprocess.run(['git','-C',str(work),'show','HEAD:payload.txt'],env=env,capture_output=True,check=True)
    assert result.stdout==b'a\r\nb\r\n'


def test_framework_git_isolation_does_not_change_runner_native_git(tmp_path,monkeypatch):
    from eval.ckpt.git_state import stage_workspace
    from eval.run.runner import harness_env
    home=tmp_path/'home';home.mkdir();sentinel=tmp_path/'native-hook'
    hooks=tmp_path/'hooks';executable(hooks/'pre-commit','#!/bin/sh\nprintf NATIVE >> '+str(sentinel)+'\n')
    (home/'.gitconfig').write_text('[core]\n hooksPath = '+json.dumps(str(hooks))+'\n')
    source=tmp_path/'source';source.mkdir();(source/'payload').write_text('synthetic')
    system=tmp_path/'system';system.write_text('')
    env=reviewed_env(home,system,monkeypatch)
    # Install exactly the reviewed parent env; marker remains opt-in for framework
    # _git but runner.harness_env has no generated GIT_* isolation overrides.
    for key in list(os.environ):monkeypatch.delenv(key,raising=False)
    for key,value in env.items():monkeypatch.setenv(key,value)
    stage_workspace(source,tmp_path/'framework')
    assert not sentinel.exists()
    native=harness_env()
    assert not any(key.startswith('GIT_') for key in native)
    assert os.environ==env
    work=tmp_path/'native';work.mkdir();(work/'file').write_text('native')
    # Test command disables system config via explicit test-only -c identity,
    # not by adding framework isolation settings to the actual task environment.
    for args in [('init','-q'),('add','-A'),('-c','user.email=synthetic@local','-c','user.name=Synthetic','commit','-qm','native')]:
        ran=subprocess.run(['git','-C',str(work),*args],env=native,capture_output=True,text=True)
        assert ran.returncode==0,ran.stderr
    assert sentinel.read_text()=='NATIVE'
