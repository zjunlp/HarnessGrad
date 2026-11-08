"""Task temp ownership and failed materialization retain operator/checkpoint sentinels."""
import json
from pathlib import Path

import pytest

from eval.run.task_workspace import TaskWorkspace


def test_owned_directories_are_removed_but_checkpoint_archive_and_operator_roots_survive(tmp_path):
    retained = [tmp_path / name for name in ('.state', 'private-archive', 'operator')]
    for root in retained:
        root.mkdir()
        (root / 'sentinel').write_text('retained')
    workspace = TaskWorkspace()
    task = workspace.path
    workspace.ensure_state(retained[0])
    home = workspace.home()
    stepwise = workspace.allocate_stepwise()
    overlay = workspace.stage_runtime({'public': True}, stage=lambda declaration, target: target)
    written = workspace.write_public_task({'task_id': 'authored', 'goal': 'public'})
    assert json.loads(written.read_text()) == {'task_id': 'authored', 'goal': 'public'}
    assert home.is_relative_to(task) and overlay == workspace.runtime_dir
    workspace.remove_stepwise()
    workspace.remove_task()
    workspace.remove_runtime()
    assert not any(path.exists() for path in (task, stepwise, overlay))
    assert all((root / 'sentinel').read_text() == 'retained' for root in retained)


def test_partial_runtime_stage_and_materialize_failures_keep_owned_paths_for_cleanup(tmp_path):
    workspace = TaskWorkspace()
    failure = KeyboardInterrupt('authored stage interruption')

    def stage(declaration, path):
        (path / 'partial').write_text('owned')
        raise failure

    try:
        with pytest.raises(KeyboardInterrupt) as raised:
            workspace.stage_runtime({}, stage=stage)
        assert raised.value is failure and (workspace.runtime_dir / 'partial').exists()
        with pytest.raises(OSError, match='authored write failure'):
            workspace.materialize([], platform=tmp_path,
                materialize=lambda *args: (_ for _ in ()).throw(OSError('authored write failure')))
    finally:
        workspace.remove_task()
        workspace.remove_runtime()
    assert not workspace.path.exists() and not workspace.runtime_dir.exists()


def test_cleanup_never_follows_a_replaced_task_directory_symlink(tmp_path):
    workspace = TaskWorkspace()
    workspace.remove_task()
    operator = tmp_path / 'operator'
    operator.mkdir()
    (operator / 'sentinel').write_text('private')
    workspace.path.symlink_to(operator, target_is_directory=True)
    workspace.remove_task()
    assert (operator / 'sentinel').read_text() == 'private'
    workspace.path.unlink()
