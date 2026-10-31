"""Checkpoint identity comes from bytes even when Git's stat cache looks clean."""
from __future__ import annotations

import os
import subprocess

from eval.ckpt.git_state import commit_state, content_sha, init_repo


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True)


def repo(tmp_path):
    work = tmp_path / 'harness'
    work.mkdir()
    (work / 'agent.py').write_text("print('round 0')\n")
    return work


def test_commit_rehashes_bytes_when_preserved_metadata_hides_a_change(tmp_path):
    work = repo(tmp_path)
    path = work / 'agent.py'
    # An old timestamp avoids Git's same-second index-write protection. Ignoring
    # ctime is a supported Git configuration; copying saved files preserves mtime.
    old = 1_700_000_000_000_000_000
    os.utime(path, ns=(old, old))
    init_repo(work)
    git(work, 'config', 'core.trustctime', 'false')
    before = content_sha(work)
    path.write_text("print('round 1')\n")  # equal-size bytes, same inode
    os.utime(path, ns=(old, old))

    after = commit_state(work, 'restored round 1')

    assert git(work, 'show', f'{after}:agent.py') == "print('round 1')\n"
    assert after != before


def test_refresh_preserves_tracked_ignored_files_and_excludes_new_scratch(tmp_path):
    work = repo(tmp_path)
    (work / 'tracked.txt').write_text('before')
    init_repo(work)
    with (work / '.gitignore').open('a') as handle:
        handle.write('tracked.txt\nscratch.txt\n')
    (work / 'tracked.txt').write_text('after')
    (work / 'scratch.txt').write_text('private scratch')
    (work / '.state' / 'cache').write_text('runtime scratch')

    state = commit_state(work, 'tracked code and scratch')

    assert git(work, 'show', f'{state}:tracked.txt') == 'after'
    names = git(work, 'ls-tree', '-r', '--name-only', state).splitlines()
    assert 'scratch.txt' not in names
    assert '.state/cache' not in names


def test_refresh_keeps_noop_identity_and_records_add_delete_and_symlink(tmp_path):
    work = repo(tmp_path)
    init_repo(work)
    before = content_sha(work)
    assert commit_state(work, 'unchanged') == before
    (work / 'agent.py').unlink()
    (work / 'new.py').write_text('new source')
    (work / 'alias.py').symlink_to('new.py')

    state = commit_state(work, 'replace source')

    names = git(work, 'ls-tree', '-r', '--name-only', state).splitlines()
    assert 'agent.py' not in names
    assert 'new.py' in names
    assert '120000 blob' in git(work, 'ls-tree', state, 'alias.py')
    assert git(work, 'show', f'{state}:alias.py') == 'new.py'
