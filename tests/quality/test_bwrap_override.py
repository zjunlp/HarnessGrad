"""Explicit bwrap selection must reach preparation and trusted service launch plans."""
from pathlib import Path
import os
from unittest.mock import Mock

import pytest

from harnessgrad import bubblewrap, python_harness_runtime as runtime
from eval.containers import python_service


def plan(kind, tmp_path, monkeypatch):
    if kind == 'builder':
        instance = tmp_path / 'instance'; instance.mkdir()
        run = Mock(return_value='fixture')
        monkeypatch.setattr(runtime, '_run', run)
        runtime._builder(['/mock/uv'], instance=instance, uv=Path('/mock/uv'),
                         base={'base_prefix':'/usr', 'base_exec_prefix':'/usr'},
                         cache=tmp_path / 'cache', allow_downloads=False, timeout_s=1)
        return run.call_args.args[0]
    directory = tmp_path / 'service'; directory.mkdir()
    source = tmp_path / 'source'; source.mkdir()
    program = source / 'service.py'; program.write_text('')
    return python_service.command(['/usr/bin/python3','-I','-B',str(program)],
        directory=directory,runtime_paths=[],read_paths=[str(source)])


@pytest.mark.parametrize('kind', ['builder', 'service'])
def test_explicit_override_reaches_plan_with_no_path_lookup(kind,tmp_path,monkeypatch):
    exe = tmp_path / 'custom bwrap'; exe.write_text('fixture'); exe.chmod(0o755)
    monkeypatch.setenv('HG_BWRAP',str(exe))
    monkeypatch.setattr(bubblewrap.shutil,'which',Mock(side_effect=AssertionError('override ignored')))
    argv = plan(kind,tmp_path,monkeypatch)
    assert argv[0] == str(exe) and '--unshare-net' in argv


@pytest.mark.parametrize('kind', ['builder', 'service'])
@pytest.mark.parametrize('invalid', ['missing','not-executable','directory'])
def test_invalid_override_is_refused_before_launch(kind,invalid,tmp_path,monkeypatch):
    exe = tmp_path / 'override'
    if invalid == 'not-executable':exe.write_text('fixture');exe.chmod(0o644)
    if invalid == 'directory':exe.mkdir()
    monkeypatch.setenv('HG_BWRAP',str(exe))
    monkeypatch.setattr(bubblewrap.shutil,'which',Mock(side_effect=AssertionError('invalid override fell back')))
    with pytest.raises((runtime.RuntimeUnavailable,RuntimeError),match='HG_BWRAP'):
        plan(kind,tmp_path,monkeypatch)


def test_default_search_remains_system_only(monkeypatch):
    monkeypatch.delenv('HG_BWRAP',raising=False)
    which = Mock(return_value='/usr/bin/bwrap');monkeypatch.setattr(bubblewrap.shutil,'which',which)
    assert bubblewrap.executable() == '/usr/bin/bwrap'
    which.assert_called_once_with('bwrap',path=os.defpath)
