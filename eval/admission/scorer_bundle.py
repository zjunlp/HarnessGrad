"""Task-independent, content-addressed declarations for offline scorer bundles.

Only declared local bytes are checked. No dependency discovery, installation,
network request, subprocess, image inspection or scorer invocation occurs here.
An external trusted caller owns the manifest pin and the platform-owned bundle
tree. The receipt is an integrity observation, never an H0 admission or proof of
official provenance, runtime isolation, ABI completeness or network enforcement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from eval.admission.artifact_admission import (
    _digest, _load_sealed_object, load_contract, validate_scorer_spec, verify_scorer_files,
)
from eval.admission.pilot_admission import AdmissionError

SCHEMA = "harnessgrad.scorer-bundle.v1"
ISOLATION_SCHEMA = "harnessgrad.scorer-bundle.v2"


def _validate(manifest: dict) -> None:
    versioned = isinstance(manifest, dict) and manifest.get("schema") == ISOLATION_SCHEMA
    fields = {"schema", "source_sha256", "runtime_image_id", "network", "scorer"}
    if versioned:
        fields.add("isolation_class")
    if (not isinstance(manifest, dict)
            or set(manifest) != fields
            or manifest["schema"] not in (SCHEMA, ISOLATION_SCHEMA)
            or not _digest(manifest["source_sha256"])
            or not isinstance(manifest["runtime_image_id"], str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", manifest["runtime_image_id"])
            or (versioned and (not isinstance(manifest["isolation_class"], str)
                or manifest["isolation_class"] not in ("pristine-offline", "snapshot-service")))
            or not isinstance(manifest["network"], str) or not manifest["network"]
            or ((not versioned or manifest["isolation_class"] == "pristine-offline")
                and manifest["network"] != "none")):
        raise AdmissionError("invalid offline scorer bundle identity")
    validate_scorer_spec(manifest["scorer"])


def seal_bundle(manifest: dict) -> tuple[bytes, str]:
    """Serialize an explicit declaration deterministically; do not discover files.

    The returned pin must be stored in a trusted location apart from actor state.
    Equivalent JSON key order produces the same identity. File and argv list order
    remains significant. This function does not certify the declaration's origin.
    """
    _validate(manifest)
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("ascii")
    return raw, hashlib.sha256(raw).hexdigest()


def load_bundle(raw: bytes, expected_sha256: str) -> dict:
    """Authenticate exact manifest bytes against a separately supplied SHA-256."""
    manifest = _load_sealed_object(raw, expected_sha256, label="scorer bundle manifest")
    _validate(manifest)
    return manifest


def verify_bundle(root: Path, raw: bytes, expected_sha256: str) -> dict:
    """Verify the exact local closure, independent of a task/export/quota policy."""
    manifest = load_bundle(raw, expected_sha256)
    counts = verify_scorer_files(root, manifest["scorer"])
    return dict(schema="harnessgrad.scorer-bundle-receipt.v1",
                bundle_sha256=expected_sha256, source_sha256=manifest["source_sha256"],
                runtime_image_id=manifest["runtime_image_id"],
                declared_network=manifest["network"], **counts,
                **({"isolation_class": manifest["isolation_class"]} if "isolation_class" in manifest else {}))


def require_contract_bundle(contract_raw: bytes, contract_sha256: str,
                            bundle_raw: bytes, bundle_sha256: str) -> dict:
    """Bind a legacy per-task artifact contract to an independently pinned bundle.

    Only source, runtime and scorer declarations are compared. The task's export
    and quota policies remain separately pinned by the artifact contract. Callers
    must still verify local bytes, projection receipts and real runtime observations.
    """
    contract = load_contract(contract_raw, contract_sha256)
    bundle = load_bundle(bundle_raw, bundle_sha256)
    if bundle.get("isolation_class") not in (None, "pristine-offline"):
        raise AdmissionError("artifact-only runtime requires a pristine-offline bundle")
    if any(contract[key] != bundle[key] for key in
           ("source_sha256", "runtime_image_id", "scorer")):
        raise AdmissionError("artifact contract does not match pinned scorer bundle")
    return contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate declared local scorer bytes offline")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="manifest pin from trusted caller storage")
    args = parser.parse_args(argv)
    try:
        receipt = verify_bundle(args.root, args.manifest.read_bytes(), args.sha256)
    except (AdmissionError, OSError):
        # No traceback or manifest/scorer contents are printed on failed admission.
        print(json.dumps({"valid": False, "scope": "local_declared_closure_integrity"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
