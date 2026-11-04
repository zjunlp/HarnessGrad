"""Synthetic filesystem behavior and reviewed-source admission, never a model."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import driver
from tools import validate_harness as v


@pytest.fixture
def source(tmp_path):
    root = tmp_path/'source'; root.mkdir()
    (root/'harness.json').write_text(json.dumps({'name':'synthetic', 'version':'1',
        'entrypoint':'agent.py', 'backend':'cli', 'env_kinds':['files'], 'role':'harness'}))
    (root/'agent.py').write_text('print("synthetic")\n')
    (root/'impl').mkdir()
    (root/'helper.py').write_text('VALUE=1\n')
    (root/'impl/helper.py').write_text('VALUE=1\n')
    return root


def mutate(source, kind):
    if kind == 'mode': (source/'agent.py').chmod(0o755)
    elif kind == 'directory': (source/'empty-feature').mkdir()
    elif kind == 'link':
        (source/'helper.py').unlink(); (source/'helper.py').symlink_to('impl/helper.py')


def argv(tmp_path, source, content, filesystem):
    return ['driver.py', '--harness-path', str(source), '--expected-harness-sha256', content,
        '--expected-harness-filesystem-sha256', filesystem, '--dataset', 'demo', '--sampling', 'all',
        '--rounds', '0', '--jobs', '1', '--trials', '1', '--run-id', 'synthetic-review',
        '--runs-root', str(tmp_path/'runs'), '--work-root', str(tmp_path/'work')]


@pytest.mark.parametrize('kind', ['mode','directory','link'])
def test_metadata_changes_with_unchanged_submission_refuse_before_model(source, tmp_path, monkeypatch, capsys, kind):
    content = v.source_identity(source)['submission_sha256']
    filesystem = v.filesystem_identity(source)['sha256']
    mutate(source, kind)
    assert v.source_identity(source)['submission_sha256'] == content
    assert v.filesystem_identity(source)['sha256'] != filesystem
    monkeypatch.setattr(driver, 'describe_model', lambda: pytest.fail('unreviewed metadata reached model'))
    monkeypatch.setattr(driver.datasets, 'load_split', lambda *a, **k: pytest.fail('unreviewed metadata reached tasks'))
    monkeypatch.setattr(sys, 'argv', argv(tmp_path,source,content,filesystem))
    assert driver._main({}) == 2
    assert 'filesystem differs' in capsys.readouterr().err
    assert not (tmp_path/'runs').exists()


@pytest.mark.parametrize('kind', ['mode','directory','link'])
def test_transient_metadata_during_snapshot_refuses_before_model(source, tmp_path, monkeypatch, capsys, kind):
    content = v.source_identity(source)['submission_sha256']; filesystem = v.filesystem_identity(source)['sha256']
    original = driver._external_source_snapshot
    def raced(src, snapshot):
        mode = (src/'agent.py').stat().st_mode; helper=(src/'helper.py').read_bytes()
        mutate(src,kind)
        try: original(src,snapshot)
        finally:
            if kind=='mode': (src/'agent.py').chmod(stat.S_IMODE(mode))
            elif kind=='directory': (src/'empty-feature').rmdir()
            else: (src/'helper.py').unlink(); (src/'helper.py').write_bytes(helper)
    monkeypatch.setattr(driver,'_external_source_snapshot',raced)
    monkeypatch.setattr(driver,'describe_model',lambda:pytest.fail('raced snapshot reached model'))
    monkeypatch.setattr(sys,'argv',argv(tmp_path,source,content,filesystem))
    assert driver._main({}) == 2
    assert 'owned harness filesystem differs' in capsys.readouterr().err
    assert v.filesystem_identity(source)['sha256']==filesystem


@pytest.mark.parametrize('kind', ['absolute','relative','directory-alias','pyc-directory-alias'])
def test_normalized_safe_links_and_stripped_caches_match_owned_snapshot(source,tmp_path,kind):
    target=source/'impl/helper.py'
    alias=source/'alias'
    if kind=='absolute': alias.symlink_to(target)
    elif kind=='relative': alias.symlink_to('impl/helper.py')
    else:
        if kind=='pyc-directory-alias': alias=source/'alias.pyc'
        alias.symlink_to('impl',target_is_directory=True)
    (source/'__pycache__').mkdir();(source/'__pycache__/ignored.pyc').write_bytes(b'cache')
    (source/'leaf.pyc').write_bytes(b'cache')
    (source/'meaningful.pyc').mkdir();(source/'meaningful.pyc/file.txt').write_text('reviewed')
    expected=v.filesystem_identity(source)
    snapshot=tmp_path/'snapshot';driver._external_source_snapshot(source,snapshot);driver._strip_reviewed_bytecode(snapshot)
    assert v.filesystem_identity(snapshot)==expected
    assert not (snapshot/'__pycache__').exists() and not (snapshot/'leaf.pyc').exists()
    assert (snapshot/'meaningful.pyc/file.txt').is_file()
    if kind=='pyc-directory-alias': assert (snapshot/'alias.pyc').is_dir()


@pytest.mark.parametrize('kind',['escape','dangling','cycle','chain','aliased-parent','fifo','special-mode','into-cache'])
def test_unsafe_filesystems_refuse(source,tmp_path,kind):
    if kind=='escape': (source/'alias').symlink_to(tmp_path)
    elif kind=='dangling': (source/'alias').symlink_to('absent')
    elif kind=='cycle': (source/'impl/alias').symlink_to('..')
    elif kind=='chain':
        (source/'alias').symlink_to('helper.py'); (source/'second').symlink_to('alias')
    elif kind=='aliased-parent':
        (source/'alias').symlink_to('impl');(source/'second').symlink_to('alias/helper.py')
    elif kind=='fifo': os.mkfifo(source/'fifo')
    elif kind=='special-mode': (source/'agent.py').chmod(0o4755)
    else:
        (source/'__pycache__').mkdir();(source/'__pycache__/reviewed.txt').write_text('cache target')
        (source/'alias').symlink_to('__pycache__/reviewed.txt')
    with pytest.raises((OSError,ValueError,RuntimeError)): v.filesystem_identity(source)


@pytest.mark.parametrize('phase',['late-copy','staging','staged-work'])
def test_late_owned_metadata_tamper_refuses_before_execution(source,tmp_path,monkeypatch,capsys,phase):
    content=v.source_identity(source)['submission_sha256'];filesystem=v.filesystem_identity(source)['sha256']
    original=driver._external_source_snapshot;stage=driver.stage_workspace;copies=[]
    def snapshot(src,target):
        original(src,target);copies.append(target)
        if phase=='late-copy' and len(copies)==2: (target/'agent.py').chmod(0o755)
    def staging(src,work):
        stage(src,work)
        if phase=='staging': (src/'agent.py').chmod(0o755)
        if phase=='staged-work': (work/'agent.py').chmod(0o755)
    monkeypatch.setattr(driver,'_external_source_snapshot',snapshot)
    monkeypatch.setattr(driver,'stage_workspace',staging)
    monkeypatch.setattr(driver,'_run_mode_a',lambda *a,**k:pytest.fail('tampered metadata executed'))
    monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    monkeypatch.setattr(sys,'argv',argv(tmp_path,source,content,filesystem))
    assert driver._main({})==2
    assert 'filesystem' in capsys.readouterr().err and len(copies)==2


def test_modes_links_and_empty_directories_change_actual_synthetic_behavior(source,tmp_path):
    from eval.ckpt.git_state import stage_workspace,content_sha
    (source/'tool.sh').write_text('#!/bin/sh\nprintf SYNTHETIC\n');(source/'tool.sh').chmod(0o644)
    original=v.source_identity(source);fs=v.filesystem_identity(source);work=tmp_path/'noexec';stage_workspace(source,work)
    with pytest.raises(PermissionError): subprocess.run([str(work/'tool.sh')],check=True,capture_output=True)
    tree=content_sha(work);(source/'tool.sh').chmod(0o755)
    executable=tmp_path/'exec';stage_workspace(source,executable)
    assert subprocess.check_output([str(executable/'tool.sh')])==b'SYNTHETIC'
    assert v.source_identity(source)==original and v.filesystem_identity(source)!=fs
    assert content_sha(executable)!=tree
    code='from pathlib import Path\nVALUE=Path(__file__).is_symlink()\n'
    (source/'helper.py').write_text(code);(source/'impl/helper.py').write_text(code)
    before=v.source_identity(source);fs=v.filesystem_identity(source)
    def run():
        return subprocess.check_output([sys.executable,'-B','-c','import helper;print(helper.VALUE)'],cwd=source).strip()
    assert run()==b'False';(source/'helper.py').unlink();(source/'helper.py').symlink_to('impl/helper.py')
    assert run()==b'True' and v.source_identity(source)==before and v.filesystem_identity(source)!=fs
    before=v.source_identity(source);fs=v.filesystem_identity(source)
    directory_probe='from pathlib import Path;print(Path("empty-feature").is_dir())'
    assert subprocess.check_output([sys.executable,'-B','-c',directory_probe],cwd=source).strip()==b'False'
    (source/'empty-feature').mkdir()
    assert subprocess.check_output([sys.executable,'-B','-c',directory_probe],cwd=source).strip()==b'True'
    assert v.source_identity(source)==before and v.filesystem_identity(source)!=fs


@pytest.mark.parametrize('value',['','A'*64,'g'*64,'a'*63])
def test_filesystem_flag_validation_precedes_model(source,tmp_path,monkeypatch,value):
    monkeypatch.setattr(driver,'describe_model',lambda:pytest.fail('invalid flag reached model'))
    monkeypatch.setattr(sys,'argv',argv(tmp_path,source,v.source_identity(source)['submission_sha256'],value))
    assert driver._main({})==2


def test_paired_filesystem_flag_and_canonical_root_required(source,tmp_path,monkeypatch):
    monkeypatch.setattr(driver,'describe_model',lambda:pytest.fail('invalid source reached model'))
    content=v.source_identity(source)['submission_sha256'];filesystem=v.filesystem_identity(source)['sha256']
    arguments=argv(tmp_path,source,content,filesystem)
    position=arguments.index('--expected-harness-sha256');del arguments[position:position+2]
    monkeypatch.setattr(sys,'argv',arguments)
    assert driver._main({})==2
    alias=tmp_path/'source-alias';alias.symlink_to(source,target_is_directory=True)
    monkeypatch.setattr(sys,'argv',argv(tmp_path,alias,content,filesystem))
    assert driver._main({})==2


def test_staging_projection_cannot_hide_reviewed_root_entries(source,tmp_path):
    from eval.ckpt.git_state import stage_workspace
    (source/'.gitignore').write_text('# reviewed\n');(source/'.state').mkdir()
    snapshot=tmp_path/'snapshot';driver._external_source_snapshot(source,snapshot)
    expected=v.filesystem_identity(snapshot)
    work=tmp_path/'workspace';stage_workspace(snapshot,work)
    assert v.filesystem_identity(work,generated_root=('.git',))==expected
    (work/'.state').chmod(0o700)
    assert v.filesystem_identity(work,generated_root=('.git',))!=expected



def test_dotdot_cannot_erase_aliased_target_parent(source):
    (source/'nested/deeper').mkdir(parents=True)
    (source/'safe').write_text('lexical target')
    (source/'nested/safe').write_text('actual target')
    (source/'alias').symlink_to('nested/deeper',target_is_directory=True)
    link=source/'second';link.symlink_to('alias/../safe')
    assert link.resolve()==source/'nested/safe'
    with pytest.raises(ValueError,match='traversal differs'):
        v.filesystem_identity(source)


@pytest.mark.parametrize('absolute_link',[False,True])
def test_strict_driver_uses_normalized_snapshot_and_records_filesystem(source,tmp_path,absolute_link):
    if absolute_link:
        (source/'helper.py').unlink();(source/'helper.py').symlink_to(source/'impl/helper.py')
    (source/'agent.py').write_text('import argparse\nfrom pathlib import Path\np=argparse.ArgumentParser()\np.add_argument("--task")\np.add_argument("--workdir")\na=p.parse_args()\nPath(a.workdir,"answer.txt").write_text("alpha")\n')
    (source/'empty-feature').mkdir()
    (source/'__pycache__').mkdir();(source/'__pycache__/ignored.pyc').write_bytes(b'synthetic-cache')
    content=v.source_identity(source);filesystem=v.filesystem_identity(source)
    args=argv(tmp_path,source,content['submission_sha256'],filesystem['sha256'])
    run=subprocess.run([sys.executable,*args],env={**os.environ,'HG_AGENT_BACKEND':'mock',
        'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stderr[-2000:]
    meta=json.loads((tmp_path/'runs/synthetic-review/run_meta.json').read_text())
    assert meta['harness_source']['filesystem']==filesystem
    assert meta['harness_source']['submission_sha256']==content['submission_sha256']
    assert v.filesystem_identity(source)==filesystem
    assert (source/'__pycache__/ignored.pyc').read_bytes()==b'synthetic-cache'
    assert not (tmp_path/'work/synthetic-review/workspace/__pycache__').exists()


@pytest.mark.parametrize('extra',[
    ['--expected-harness-filesystem-sha='+'8'*64],
    ['--expected-harness-sha='+'8'*64], ['--harness-p=/synthetic/other'],
    ['--expected-harness-filesystem-sha256='+'8'*64], ['--harness-path=/synthetic/other'],
    ['--dataset=demo'], ['--sampling=all'], ['--rounds=0'], ['--trials=1'],
    ['--jobs=1'], ['--run-id=other'], ['--runs-root=/synthetic/runs'],
    ['--work-root=/synthetic/work'], ['--no-sandbox'], ['--job=1'],
])
def test_actual_reviewed_driver_parser_rejects_abbreviations_and_duplicates(source,tmp_path,monkeypatch,extra):
    monkeypatch.setattr(driver,'describe_model',lambda:pytest.fail('ambiguous argv reached model'))
    monkeypatch.setattr(driver.datasets,'load_split',lambda *a,**k:pytest.fail('ambiguous argv reached tasks'))
    arguments=argv(tmp_path,source,v.source_identity(source)['submission_sha256'],v.filesystem_identity(source)['sha256'])
    monkeypatch.setattr(sys,'argv',arguments+extra)
    with pytest.raises(SystemExit) as exc: driver._main({})
    assert exc.value.code==2


def test_actual_legacy_parser_preserves_abbreviation_behavior(tmp_path,monkeypatch):
    marker=RuntimeError('legacy parser reached model')
    monkeypatch.setattr(driver,'describe_model',lambda: (_ for _ in ()).throw(marker))
    monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    monkeypatch.setattr(sys,'argv',['driver.py','--harness','loop','--datase','demo','--sampl','all',
        '--rounds','0','--jobs','1','--runs-root',str(tmp_path/'runs'),'--work-root',str(tmp_path/'work')])
    with pytest.raises(RuntimeError,match='legacy parser reached model'): driver._main({})
