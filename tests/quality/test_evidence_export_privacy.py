"""Privacy and snapshot checks using synthetic offline receipts."""
import json

import pytest

from harnessgrad import evidence
from harnessgrad.evidence_export import build_export
from harnessgrad.receipt_verification import ReceiptStore, canonical
from test_evidence_export import author, save_run


def test_untrusted_field_values_are_not_exported(tmp_path):
    root = tmp_path / 'runs'
    save_run(root, 'base')
    save_run(root, 'sub', 1, tree='b')
    path = root / 'sub' / 'run_meta.json'
    meta = json.loads(path.read_text())
    meta['config']['agent']['model'] = 'password huntertwo'
    meta['config']['agent']['max_tokens'] = 'private answer'
    path.write_text(json.dumps(meta))
    pack = build_export(author(), root)
    assert b'password huntertwo' not in canonical(pack)
    assert b'private answer' not in canonical(pack)
    config = next(x for x in pack['runs'] if x['run_id'] == 'sub')['projection']['run_meta']['config']
    assert config['agent']['model'] is None
    assert config['agent']['max_tokens'] is None


def test_missing_patch_parent_is_rechecked(tmp_path, monkeypatch):
    root = tmp_path / 'runs'
    save_run(root, 'base')
    save_run(root, 'sub', 1, tree='b')
    patch = root / 'sub' / 'diffs' / 'round-1.patch'
    patch.unlink()
    patch.parent.rmdir()
    original = ReceiptStore.recheck

    def create_patch(store):
        patch.parent.mkdir()
        patch.write_text('synthetic patch')
        original(store)

    monkeypatch.setattr(ReceiptStore, 'recheck', create_patch)
    with pytest.raises(evidence.EvidenceError, match='receipt_changed'):
        build_export(author(), root)
