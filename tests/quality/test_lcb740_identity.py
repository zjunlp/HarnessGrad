"""Public-only /740 admission; approved records below are fabricated test fixtures."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import lcb740_identity as gate


@pytest.fixture
def statement():
    return (ROOT / "data/datasets/livecodebench/abc360_a.public.md").read_bytes()


def record_bytes(**changes):
    record = {
        "schema": gate.INDEX_SCHEMA, "task_id": gate.TASK_ID,
        "row_index": gate.ROW_INDEX, "question_id": gate.QUESTION_ID,
        "revision": gate.REVISION, "pool_sha256": gate.POOL_SHA256,
        "split_sha256": gate.SPLIT_SHA256, "side": "train",
        "public_statement_sha256": gate.STATEMENT_SHA256,
        "source_scope": "metadata_only", "binding": "POOL_ROW_ATTESTED",
        "custodian": "SYNTHETIC_TEST_CUSTODIAN",
    }
    record.update(changes)
    return json.dumps(record, sort_keys=True).encode()


def approve_fixture(statement, record):
    # This digest simulates an independent operator trust anchor, not real evidence.
    return gate.identity(statement, record, hashlib.sha256(record).hexdigest())


def test_existing_public_statement_does_not_attest_row(statement):
    result = gate.identity(statement)
    assert result["row_attested"] is False
    assert result["pool_freshly_verified_here"] is False
    assert result["binding"] == "FILENAME_INFERRED_NOT_POOL_ATTESTED"
    assert "independently approved index record" in result["blocker"]
    unsigned = dict(result)
    digest = unsigned.pop("identity_sha256")
    assert digest == hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_statement_mutation_is_rejected(statement):
    with pytest.raises(ValueError, match="statement hash"):
        gate.identity(statement + b"\n")


def test_both_record_and_independent_approval_are_required(statement):
    record = record_bytes()
    with pytest.raises(ValueError, match="both metadata record"):
        gate.identity(statement, record)
    with pytest.raises(ValueError, match="both metadata record"):
        gate.identity(statement, approved_metadata_sha256=hashlib.sha256(record).hexdigest())
    with pytest.raises(ValueError, match="approval hash"):
        gate.identity(statement, record, "0" * 64)


def test_synthetic_approved_row_preserves_full_split_and_pool_claims(statement):
    result = approve_fixture(statement, record_bytes())
    assert result["row_attested"] is True and result["blocker"] is None
    assert result["task_id"] == "livecodebench/740" and result["row_index"] == 740
    assert result["original_imposed_split_sha256"] == gate.SPLIT_SHA256
    assert result["declared_pool_sha256"] == gate.POOL_SHA256
    assert result["pool_freshly_verified_here"] is False
    assert hashlib.sha256(b"livecodebench/740").digest()[0] % 3 != 0
    unsigned = dict(result)
    digest = unsigned.pop("identity_sha256")
    assert digest == hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("change", [
    {"schema": "other"}, {"task_id": "livecodebench/0"},
    {"row_index": 0}, {"row_index": 740.0}, {"row_index": True}, {"question_id": "other"},
    {"revision": "other"}, {"pool_sha256": "0" * 64},
    {"split_sha256": "0" * 64}, {"side": "eval"},
    {"public_statement_sha256": "0" * 64}, {"source_scope": "mixed_pool"},
    {"binding": "FILENAME_INFERRED_NOT_POOL_ATTESTED"},
    {"custodian": " "}, {"custodian": 42}, {"private_tests": []},
    {"approved_metadata_sha256": "0" * 64},
])
def test_approved_digest_cannot_override_row_schema(statement, change):
    with pytest.raises(ValueError):
        approve_fixture(statement, record_bytes(**change))


@pytest.mark.parametrize("record", [b"null", b"[]", b"{}", b"{", b"\xff",
                                        b"[" * 2000 + b"0" + b"]" * 2000])
def test_malformed_approved_records_are_rejected(statement, record):
    with pytest.raises(ValueError):
        approve_fixture(statement, record)


def test_duplicate_keys_cannot_hide_conflicting_row_identity(statement):
    record = b'{"row_index": 0, ' + record_bytes()[1:]
    with pytest.raises(ValueError, match="duplicate"):
        approve_fixture(statement, record)


@pytest.mark.parametrize("field", list(json.loads(record_bytes())))
def test_every_attestation_field_is_required(statement, field):
    record = json.loads(record_bytes())
    del record[field]
    with pytest.raises(ValueError, match="schema mismatch"):
        approve_fixture(statement, json.dumps(record).encode())


@pytest.mark.parametrize("metadata", ["{}", bytearray(b"{}"), memoryview(b"{}"), b""])
def test_metadata_must_be_nonempty_bytes(statement, metadata):
    with pytest.raises(ValueError, match="1..4096 bytes"):
        gate.identity(statement, metadata, "0" * 64)


@pytest.mark.parametrize("approval", [
    "", "0" * 63, "0" * 65, "A" * 64, "g" * 64, "0" * 64 + "\n",
    b"0" * 64, 42, True,
])
def test_approval_requires_canonical_digest(statement, approval):
    with pytest.raises(ValueError, match="lowercase SHA-256 hex"):
        gate.identity(statement, record_bytes(), approval)


def test_size_bound_precedes_json_parsing(statement, monkeypatch):
    record = record_bytes()
    padded = record + b" " * (gate.MAX_INDEX_BYTES - len(record))
    assert approve_fixture(statement, padded)["row_attested"] is True

    def forbidden_parse(*args, **kwargs):
        raise AssertionError("oversized record must be rejected before JSON parsing")

    monkeypatch.setattr(gate.json, "loads", forbidden_parse)
    with pytest.raises(ValueError, match="1..4096 bytes"):
        approve_fixture(statement, padded + b" ")


def test_approval_binds_exact_bytes_before_json_parsing(statement, monkeypatch):
    record = record_bytes()
    approval = hashlib.sha256(record).hexdigest()

    def forbidden_parse(*args, **kwargs):
        raise AssertionError("unapproved record must be rejected before JSON parsing")

    monkeypatch.setattr(gate.json, "loads", forbidden_parse)
    with pytest.raises(ValueError, match="approval hash mismatch"):
        gate.identity(statement, record + b"\n", approval)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "utf-8-sig"])
def test_record_requires_utf8_without_bom(statement, encoding):
    record = record_bytes().decode().encode(encoding)
    with pytest.raises(ValueError):
        approve_fixture(statement, record)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_json_values_are_rejected(statement, value):
    with pytest.raises(ValueError, match="non-finite index record value"):
        approve_fixture(statement, record_bytes(row_index=value))


@pytest.mark.parametrize("custodian", [
    "", " padded", "padded ", "line\nbreak", "tab\tname", "null\x00name",
    "invisible\u200bname", "\ud800", "x" * (gate.MAX_CUSTODIAN_CHARS + 1),
])
def test_custodian_requires_bounded_printable_identity(statement, custodian):
    with pytest.raises(ValueError, match="custodian must be"):
        approve_fixture(statement, record_bytes(custodian=custodian))


@pytest.mark.parametrize("custodian", [
    "x" * gate.MAX_CUSTODIAN_CHARS, "SYNTHETIC_测试_CUSTODIAN",
])
def test_printable_unicode_and_maximum_custodian_are_valid(statement, custodian):
    record = json.loads(record_bytes(custodian=custodian))
    result = approve_fixture(statement, json.dumps(record, ensure_ascii=False).encode("utf-8"))
    assert result["custodian"] == custodian
    assert result["pool_freshly_verified_here"] is False


def test_launcher_cannot_accept_caller_attestation_receipt(statement, tmp_path):
    from tools.launch_lcb740_candidate import launch

    with pytest.raises(TypeError, match="row_identity"):
        launch(agent=b"# offline fixture", statement=statement, tests=b"{}",
               work_root=tmp_path, model="SYNTHETIC_TEST_MODEL",
               row_identity={"row_attested": True,
                             "public_statement_sha256": gate.STATEMENT_SHA256})


@pytest.mark.parametrize("metadata,approval", [
    (record_bytes(), None), (None, "0" * 64), (record_bytes(), "0" * 64),
    (record_bytes(), "A" * 64),
    (b" " * (gate.MAX_INDEX_BYTES + 1), "0" * 64),
    (record_bytes(custodian=" padded"),
     hashlib.sha256(record_bytes(custodian=" padded")).hexdigest()),
    (record_bytes(private_tests=[]), hashlib.sha256(record_bytes(private_tests=[])).hexdigest()),
])
def test_launcher_rejects_invalid_attestation_before_relay(
        statement, tmp_path, monkeypatch, metadata, approval):
    from tools import launch_lcb740_candidate as launcher

    def forbidden_relay(*args, **kwargs):
        raise AssertionError("identity failure must precede relay/model access")

    monkeypatch.setattr(launcher, "Relay", forbidden_relay)
    with pytest.raises(ValueError):
        launcher.launch(agent=b"# offline fixture", statement=statement, tests=b"{}",
                        work_root=tmp_path, model="SYNTHETIC_TEST_MODEL",
                        index_record=metadata, approved_index_sha256=approval)
