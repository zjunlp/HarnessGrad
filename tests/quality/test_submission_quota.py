"""Admission accounting controls; these do not claim a filesystem hard quota."""

import pytest

from console import submissions as backend
from test_submission_queue import service_factory
from test_submission_gate import archive, files


def test_one_mib_refuses_before_creating_upload_payload(service_factory):
    service=service_factory(quota_bytes=1024**2)
    with pytest.raises(backend.SubmissionRejected,match='quota'):
        service.reserve_upload(1024**2+1)
    assert not list(service.root.glob('sub-*'))
    assert not list(service.root.glob('.incoming/*'))
    assert not service.work.exists()


def test_unaccepted_payload_is_removed_and_other_runs_preserved(service_factory,tmp_path,monkeypatch):
    service=service_factory(quota_bytes=150000)
    monkeypatch.setattr(backend,'scenario_status',lambda *a,**kw:{'available':True})
    other=service.runs/'operator-run'
    other.mkdir()
    (other/'keep').write_text('untouched')
    payload=archive(tmp_path,{**files(),'large':b'x'*400000},kind='zip')
    with pytest.raises(backend.SubmissionRejected,match='quota'):
        service.submit(payload,'demo-smoke',driver_env={'HG_AGENT_BACKEND':'mock'})
    assert not service._tokens
    receipts=service._receipts()
    assert len(receipts)==1 and receipts[0]['status']=='rejected'
    directory=service.root/receipts[0]['submission_id']
    assert [p.name for p in directory.iterdir()]==['receipt.json']
    assert (other/'keep').read_text()=='untouched'


@pytest.mark.parametrize('filename',['receipt.json','request.json'])
def test_payload_cannot_hide_behind_control_metadata_basename(service_factory,filename):
    instance=service_factory(quota_bytes=1024**2)
    identity='sub-20261030T000000Z-1234abcd'
    payload=instance.root/identity/'staging/payload'
    payload.mkdir(parents=True)
    (payload/filename).write_bytes(b'x'*(2*1024**2))
    # Only the exact server receipt path is metadata, never nested user files.
    instance._save({'submission_id':identity,'run_id':identity,'status':'rejected',
                    'scenario':{'name':'demo-smoke'},'created_at':backend._utc()})
    assert instance.usage_bytes()>=2*1024**2
    with pytest.raises(backend.SubmissionRejected,match='quota'):
        instance.reserve_upload(1)
