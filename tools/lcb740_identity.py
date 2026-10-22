"""Public /740 identity admission without importing the mixed-pool adapter.

An approved digest is an operator/custodian trust anchor, independent of the record.
A content hash or filename ordering alone is never trusted row attestation.
"""
from __future__ import annotations

import hashlib
import json
import re

TASK_ID, ROW_INDEX, QUESTION_ID = "livecodebench/740", 740, "abc360_a"
REVISION = "0fe84c3912ea0c4d4a78037083943e8f0c4dd505"
POOL_SHA256 = "bbf2bf826856156656568f568e4026f7702f90b5da22a880b733b755a69f4256"
SPLIT_SHA256 = "af97cfb379d5af4ae2437e6fda50d4a15199f0d001b0dda64625304a85c9f187"
STATEMENT_SHA256 = "072b9614bc27ae5fb47d3a7fc90de200c15208722a145d6291985967cb50c14f"
PUBLIC_URL = "https://atcoder.jp/contests/abc360/tasks/abc360_a?lang=en"
INDEX_SCHEMA = "harnessgrad.trusted-public-index-row.v1"
MAX_INDEX_BYTES = 4096
MAX_CUSTODIAN_CHARS = 256


def _unique_object(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"duplicate index record key: {key}")
        record[key] = value
    return record


def _reject_constant(value):
    raise ValueError("non-finite index record value")


def identity(statement: bytes, metadata: bytes | None = None,
             approved_metadata_sha256: str | None = None) -> dict:
    """Validate a record against a digest supplied through trusted external custody.

    This function cannot establish who approved a digest. The caller must obtain
    it independently of the record; computing a self-hash is not an approval.
    """
    if hashlib.sha256(statement).hexdigest() != STATEMENT_SHA256:
        raise ValueError("public statement hash mismatch")
    result = {"task_id": TASK_ID, "row_index": ROW_INDEX, "question_id": QUESTION_ID,
              "revision": REVISION, "declared_pool_sha256": POOL_SHA256,
              "original_imposed_split_sha256": SPLIT_SHA256, "side": "train",
              "public_source": PUBLIC_URL, "public_statement_sha256": STATEMENT_SHA256,
              "row_attested": False, "pool_freshly_verified_here": False,
              "binding": "FILENAME_INFERRED_NOT_POOL_ATTESTED",
              "blocker": "No independently approved index record binds row 740 to abc360_a in the pinned pool"}
    if (metadata is None) != (approved_metadata_sha256 is None):
        raise ValueError("both metadata record and independently approved digest are required")
    if metadata is not None:
        if type(metadata) is not bytes or not 0 < len(metadata) <= MAX_INDEX_BYTES:
            raise ValueError("index record must be 1..4096 bytes")
        if (type(approved_metadata_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", approved_metadata_sha256) is None):
            raise ValueError("approved index digest must be lowercase SHA-256 hex")
        digest = hashlib.sha256(metadata).hexdigest()
        if digest != approved_metadata_sha256:
            raise ValueError("index record approval hash mismatch")
        try:
            record = json.loads(metadata.decode("utf-8"), object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant)
        except UnicodeDecodeError as exc:
            raise ValueError("index record must be UTF-8 JSON") from exc
        except RecursionError as exc:
            raise ValueError("index record nesting exceeds parser limit") from exc
        required = {"schema": INDEX_SCHEMA, "task_id": TASK_ID, "row_index": ROW_INDEX,
                    "question_id": QUESTION_ID, "revision": REVISION,
                    "pool_sha256": POOL_SHA256, "split_sha256": SPLIT_SHA256,
                    "side": "train", "public_statement_sha256": STATEMENT_SHA256,
                    "source_scope": "metadata_only", "binding": "POOL_ROW_ATTESTED"}
        if not isinstance(record, dict) or set(record) != set(required) | {"custodian"}:
            raise ValueError("metadata-only index schema mismatch")
        for key, value in required.items():
            if type(record[key]) is not type(value) or record[key] != value:
                raise ValueError(f"index record mismatch: {key}")
        custodian = record["custodian"]
        if (type(custodian) is not str or not 0 < len(custodian) <= MAX_CUSTODIAN_CHARS
                or custodian != custodian.strip() or not custodian.isprintable()):
            raise ValueError("index record custodian must be 1..256 printable, trimmed characters")
        # Recompute side using original ID; do not reindex a covered subset.
        if hashlib.sha256(TASK_ID.encode()).digest()[0] % 3 == 0:
            raise ValueError("selected ID is not imposed train")
        result.update(row_attested=True, binding="TRUSTED_METADATA_POOL_ROW_ATTESTED",
                      approved_index_sha256=digest, custodian=record["custodian"], blocker=None)
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    result["identity_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result
