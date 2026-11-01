"""Admission smoke gets private task state without changing the uploaded tree."""
import stat
import subprocess

import pytest

from console import submissions as backend
from eval.containers import sandbox
from tools import validate_harness as validator
from test_submission_gate import ACTOR, ROOT, files, gate, harness


STATE_ACTOR = ACTOR + '''
import os
state = Path(os.environ['HARNESSGRAD_STATE'])
assert state == Path(__file__).resolve().parent / '.state'
assert not (state / 'uploaded-cache').exists()
(state / 'env-cache').write_text('environment')
(Path(__file__).parent / '.state' / 'relative-cache').write_text('relative')
assert (state / 'env-cache').read_text() == 'environment'
try:
    Path(__file__).write_text('source must remain read-only')
except OSError:
    pass
else:
    raise AssertionError('source was writable')
'''


def snapshot(root):
    return {p.relative_to(root).as_posix():
            (stat.S_IMODE(p.stat().st_mode), None if p.is_dir() else p.read_bytes())
            for p in [root, *root.rglob('*')]}


def scratch_usage(parent, source, work):
    """Only allocations this smoke owns; the work root already existed."""
    total = 0
    for path in parent.rglob('*'):
        if path == source or source in path.parents or path == work:
            continue
        info = path.stat()
        total += max(info.st_size, getattr(info, 'st_blocks', 0) * 512)
    return total


@pytest.mark.parametrize('existing_state', [False, True])
def test_uploaded_smoke_state_is_private_and_source_is_unchanged(tmp_path, existing_state):
    root = harness(tmp_path, {**files(), 'agent.py': STATE_ACTOR.encode()})
    if existing_state:
        (root / '.state').mkdir()
        (root / '.state' / 'uploaded-cache').write_text('preserve original cache')
    (root / 'empty-directory').mkdir()
    (root / 'agent.py').chmod(0o750)
    work = tmp_path / 'work'
    work.mkdir()
    before = snapshot(root)

    assert gate.inspect_harness(root, work_root=work)['version'] == '1'

    assert snapshot(root) == before
    assert (root / '.state').exists() is existing_state
    assert set(tmp_path.iterdir()) == {root, work}
    assert not list(work.iterdir())


@pytest.mark.parametrize('limit', [0, 200000])
def test_smoke_quota_refuses_before_allocation_and_cleans_partial_copy(
        tmp_path, monkeypatch, limit):
    root = harness(tmp_path, {**files(), 'payload.bin': b'x' * 524288})
    work = tmp_path / 'work'
    work.mkdir()
    before = snapshot(root)
    balance = 0
    refused = False
    problem = backend.SubmissionRejected([
        {'code': 'service-not-ready', 'detail': 'quota smoke fixture limit'}])

    def budget(delta):
        nonlocal balance, refused
        remaining = balance + (delta if delta < 0 else 0)
        assert scratch_usage(tmp_path, root, work) <= remaining
        if delta > 0 and balance + delta > limit:
            refused = True
            raise problem
        balance += delta
        assert balance >= 0

    def readiness_only(argv, **kwargs):
        assert argv == ['/bin/true'], 'quota-refused source must never execute'
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(sandbox, 'run', readiness_only)
    with pytest.raises(backend.SubmissionRejected) as caught:
        validator.smoke(root, platform=ROOT, work_root=work, io_budget=budget)

    assert caught.value is problem and refused and balance == 0
    assert snapshot(root) == before
    assert set(tmp_path.iterdir()) == {root, work}
    assert not list(work.iterdir())


def test_smoke_cleanup_failure_does_not_release_surviving_copy(tmp_path,monkeypatch):
    root=harness(tmp_path)
    work=tmp_path/'work'
    work.mkdir()
    balance=0
    def budget(delta):
        nonlocal balance
        remaining=balance+(delta if delta<0 else 0)
        assert scratch_usage(tmp_path,root,work)<=remaining
        balance+=delta
    real_remove=validator.shutil.rmtree
    with monkeypatch.context() as context:
        def unavailable(path,*args,**kwargs):
            if work in path.parents:
                raise PermissionError('private smoke copy cleanup unavailable')
            return real_remove(path,*args,**kwargs)
        context.setattr(validator.shutil,'rmtree',unavailable)
        problems=validator.smoke(root,platform=ROOT,work_root=work,io_budget=budget)
    try:
        assert problems[0]['code']=='service-not-ready'
        assert balance>0 and list(work.iterdir())
    finally:
        for path in work.iterdir():
            real_remove(path)


def test_implicit_smoke_work_directory_is_reserved_before_creation(tmp_path,monkeypatch):
    root=harness(tmp_path)
    scratch=tmp_path/'scratch'
    scratch.mkdir()
    monkeypatch.setattr(validator.tempfile,'tempdir',str(scratch))
    balance=0
    def budget(delta):
        nonlocal balance
        usage=sum(max(p.stat().st_size,getattr(p.stat(),'st_blocks',0)*512)
                  for p in scratch.rglob('*'))
        assert usage<=balance+(delta if delta<0 else 0)
        balance+=delta
    assert gate.inspect_harness(root,io_budget=budget)['version']=='1'
    assert balance==0 and not list(scratch.iterdir())
