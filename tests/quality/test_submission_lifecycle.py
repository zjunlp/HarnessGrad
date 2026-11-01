"""Cancellation, public retention, permanent receipts and crash recovery."""
from datetime import datetime,timezone,timedelta
import json
import os
from pathlib import Path

import pytest

from console import submissions as backend
from test_submission_queue import service_factory,seed,start,wait_until

IDENTITY='sub-20261030T000000Z-1234abcd'


def test_queued_delete_never_launches_and_repeat_does_not_extend_ttl(service_factory,monkeypatch):
    instance=service_factory(retention_seconds=60)
    start(instance)
    fd=backend._lease(instance.root/'.control/execute.lock')
    calls=[]
    monkeypatch.setattr(instance,'_run',lambda *a:calls.append(a))
    try:
        seed(instance,IDENTITY,'queued')
        with instance._lock:
            instance._queue.append(IDENTITY)
        code,body=instance.delete(IDENTITY)
        assert code==202 and body['status']=='cancelled'
        first=instance._load(IDENTITY)
        assert instance.delete(IDENTITY)[0]==202
        assert instance._load(IDENTITY)['expires_at']==first['expires_at']
        assert instance.get(IDENTITY)['status']=='cancelled'
    finally:
        os.close(fd)
    assert not calls and not instance._queue


def queue_pair(instance):
    identities=(IDENTITY,'sub-20261101T000001Z-2345bcde')
    with instance._condition:
        for seq,identity in enumerate(identities,1):
            seed(instance,identity,'queued',queue_seq=seq,
                 environment=instance._environment({}),scenario_receipt={},
                 tree_sha256='fixture',harness_dir=str(instance.root/identity),argv=[])
            instance._envs[identity]={}
            instance._queue.append(identity)
        instance._condition.notify_all()
    return identities


@pytest.mark.parametrize('published',[False,True])
def test_delete_persistence_error_respects_published_cancellation(service_factory,monkeypatch,published):
    instance=service_factory(sweep_seconds=3600)
    start(instance)
    fd=backend._lease(instance.root/'.control/execute.lock')
    assert fd is not None
    real=instance._update
    calls=[]
    def publish(identity,**changes):
        if changes.get('status')=='cancelled':
            if published:
                real(identity,**changes)
            raise OSError('cancellation persistence failure')
        return real(identity,**changes)
    def run(plan,env,lease_fd):
        calls.append(plan['submission_id'])
        real(plan['submission_id'],status='failed',error={'code':'internal-error','detail':'fixture; no driver'})
    monkeypatch.setattr(instance,'_update',publish)
    monkeypatch.setattr(instance,'_run',run)
    try:
        first,second=queue_pair(instance)
        with pytest.raises(OSError,match='cancellation persistence failure'):
            instance.delete(first)
        assert instance.get(first)['status']==('cancelled' if published else 'queued')
        assert (first in instance._queue) is not published
        assert (first in instance._envs) is not published
    finally:
        os.close(fd)
    wait_until(lambda:instance._load(second)['status']=='failed')
    assert calls==([second] if published else [first,second])
    if published:
        assert instance.get(first)['status']=='cancelled'
    assert not instance._queue


def test_dispatch_skips_cancelled_receipt_left_in_queue(service_factory,monkeypatch):
    instance=service_factory(sweep_seconds=3600)
    start(instance)
    fd=backend._lease(instance.root/'.control/execute.lock')
    assert fd is not None
    calls=[]
    def run(plan,env,lease_fd):
        calls.append(plan['submission_id'])
        instance._update(plan['submission_id'],status='failed',error={
            'code':'internal-error','detail':'fixture; no driver'})
    monkeypatch.setattr(instance,'_run',run)
    try:
        first,second=queue_pair(instance)
        instance._update(first,status='cancelled')
    finally:
        os.close(fd)
    wait_until(lambda:instance._load(second)['status']=='failed')
    assert instance.get(first)['status']=='cancelled'
    assert first not in instance._envs and not instance._queue
    assert calls==[second]


@pytest.mark.parametrize('terminal_write_failures',[0,1])
def test_running_publication_error_terminalizes_without_launch(service_factory,monkeypatch,terminal_write_failures):
    instance=service_factory(sweep_seconds=3600)
    start(instance)
    fd=backend._lease(instance.root/'.control/execute.lock')
    assert fd is not None
    real=instance._update
    calls=[]
    remaining=terminal_write_failures
    def publish(identity,**changes):
        nonlocal remaining
        if identity==first and changes.get('status')=='failed' and remaining:
            remaining-=1
            raise OSError('terminal receipt not yet writable')
        data=real(identity,**changes)
        if identity==first and changes.get('status')=='running':
            raise OSError('running published before directory fsync failed')
        return data
    def run(plan,env,lease_fd):
        calls.append(plan['submission_id'])
        real(plan['submission_id'],status='failed',error={'code':'internal-error','detail':'fixture; no driver'})
    try:
        first,second=queue_pair(instance)
        monkeypatch.setattr(instance,'_update',publish)
        monkeypatch.setattr(instance,'_run',run)
    finally:
        os.close(fd)
    wait_until(lambda:instance._load(second)['status']=='failed')
    data=instance.get(first)
    assert data['status']=='failed' and data['error']['code']=='internal-error'
    assert 'result' not in data
    assert calls==[second] and not instance._queue
    assert remaining==0


@pytest.mark.parametrize('restart',[False,True])
def test_incoming_cleanup_retries_one_failure_and_preserves_audit(service_factory,monkeypatch,restart):
    instance=service_factory(sweep_seconds=3600)
    token=instance.reserve_upload(65536)
    archive=token['directory']/'archive.bin'
    archive.write_bytes(b'x'*65536)
    real_unlink=Path.unlink
    with monkeypatch.context() as context:
        def fail_once(path,*args,**kwargs):
            if path==archive:
                raise PermissionError('one transient incoming cleanup failure')
            return real_unlink(path,*args,**kwargs)
        context.setattr(Path,'unlink',fail_once)
        instance.release_upload(token)
    audit_path=token['directory']/'request.json'
    closed=json.loads(audit_path.read_text())['closed_at']
    assert archive.exists() and not instance._tokens
    if restart:
        instance.close()
        instance=service_factory(sweep_seconds=3600)
        start(instance)
    else:
        instance.sweep()
    assert not archive.exists()
    audit=json.loads(audit_path.read_text())
    assert audit['closed_at']==closed and audit['cleanup_done'] is True
    assert audit['cleanup_pending'] is False


def test_incoming_retry_preserves_unknown_and_active_uploads(service_factory):
    instance=service_factory(sweep_seconds=3600)
    token=instance.reserve_upload(32)
    active=token['directory']/'archive.bin'
    active.write_bytes(b'active')
    instance._request_audit(token,closed_at=backend._utc())
    unknown=instance.root/'.incoming'/('f'*32)
    unknown.mkdir()
    (unknown/'request.json').write_text(json.dumps({'owner':{'pid':1},'closed_at':backend._utc()}))
    (unknown/'archive.bin').write_bytes(b'unknown')
    try:
        instance.sweep()
        assert active.read_bytes()==b'active'
        assert (unknown/'archive.bin').read_bytes()==b'unknown'
    finally:
        instance.release_upload(token)


@pytest.mark.parametrize('status,code',[('received','service-not-ready'),('validating','service-not-ready'),('running','running')])
def test_delete_does_not_touch_active_payload(service_factory,status,code):
    instance=service_factory()
    seed(instance,IDENTITY,status)
    payload=instance.root/IDENTITY/'keep'
    payload.write_text('active')
    with pytest.raises(backend.SubmissionRejected) as caught:
        instance.delete(IDENTITY)
    assert caught.value.problems[0]['code']==code
    assert payload.read_text()=='active' and instance._load(IDENTITY)['status']==status


def terminal(instance,**fields):
    seed(instance,IDENTITY,'running',archive_sha256='same',**fields)
    return instance._update(IDENTITY,status='failed',error={'code':'internal-error','detail':'fixture'})


def test_expiry_hides_reads_before_sweep_and_preserves_receipt_and_dedupe(service_factory):
    instance=service_factory(retention_seconds=60)
    data=terminal(instance)
    expires=backend._time(data['expires_at'])
    assert expires-backend._time(data['finished_at'])==timedelta(seconds=60)
    assert instance._visible(data,expires-timedelta(microseconds=1))
    assert not instance._visible(data,expires)
    # Move just the receipt deadline into the past; GET performs no deletion.
    instance._update(IDENTITY,expires_at='2020-01-01T00:00:00Z')
    bulk=instance.runs/IDENTITY
    bulk.mkdir()
    (bulk/'artifact').write_text('preserved until sweep')
    for read in (lambda:instance.get(IDENTITY),lambda:instance.log(IDENTITY)):
        with pytest.raises(backend.SubmissionRejected) as caught:
            read()
        assert caught.value.problems[0]['code']=='submission-not-found'
    assert instance.list()['items']==[] and (bulk/'artifact').exists()
    instance.sweep()
    assert not bulk.exists()
    receipt=instance.root/IDENTITY/'receipt.json'
    assert receipt.exists() and json.loads(receipt.read_text())['cleanup_done']
    with pytest.raises(backend.SubmissionRejected) as caught:
        instance._duplicate('same','demo-smoke')
    assert caught.value.problems[0]['code']=='duplicate-submission'


def test_terminal_delete_keeps_status_and_readable_until_expiry(service_factory):
    instance=service_factory(retention_seconds=60)
    data=terminal(instance)
    assert instance.delete(IDENTITY)[0]==202
    assert instance.get(IDENTITY)['status']=='failed'
    assert instance._load(IDENTITY)['expires_at']==data['expires_at']
    instance.sweep(now=backend._time(data['expires_at'])-timedelta(seconds=1))
    assert not instance._load(IDENTITY).get('cleanup_done')


def test_cleanup_retries_and_does_not_follow_symlink(service_factory,tmp_path,monkeypatch):
    instance=service_factory()
    terminal(instance)
    instance._update(IDENTITY,expires_at='2020-01-01T00:00:00Z')
    outside=tmp_path/'operator'
    outside.mkdir()
    (outside/'keep').write_text('private')
    (instance.runs/IDENTITY).symlink_to(outside,target_is_directory=True)
    real=instance._remove_payload
    monkeypatch.setattr(instance,'_remove_payload',lambda identity:(_ for _ in ()).throw(OSError('fixture busy')))
    instance.sweep()
    assert instance._load(IDENTITY)['cleanup_pending'] and not instance._load(IDENTITY).get('cleanup_done')
    monkeypatch.setattr(instance,'_remove_payload',real)
    instance.sweep()
    assert instance._load(IDENTITY)['cleanup_done']
    assert not (instance.runs/IDENTITY).is_symlink() and (outside/'keep').read_text()=='private'


def test_incomplete_live_owner_identity_is_quarantined(service_factory):
    instance=service_factory()
    directory=instance.root/'.incoming/incomplete-owner'
    directory.mkdir(parents=True)
    (directory/'request.json').write_text(json.dumps({'owner':{'pid':os.getpid()}}))
    (directory/'archive.bin').write_bytes(b'unknown, potentially live owner')
    start(instance)
    assert (directory/'archive.bin').exists()
    assert not json.loads((directory/'request.json').read_text()).get('interrupted_at')
