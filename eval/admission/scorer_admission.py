"""Stable offline facade for sealed scorer bundles and artifact runtime checks.

Bundle pins, task contract pins, platform projections and trusted observations are
separate inputs. Passed checks establish only their stated scopes. No execution,
runtime provisioning, official provenance or production authorization occurs here.
Legacy modules and receipt schemas remain available for existing callers.
"""
from __future__ import annotations

from functools import wraps
from pathlib import Path

from eval.admission import artifact_admission as _artifacts
from eval.admission import scorer_bundle as _bundles
from eval.admission.artifact_admission import (
    EvidenceUnavailable, export_state, load_contract, runtime_tmpfs, verify_state_export,
)
from eval.admission.pilot_admission import AdmissionError
from eval.admission.scorer_bundle import load_bundle, seal_bundle

__all__ = (
    "AdmissionError", "EvidenceUnavailable", "ScorerAdmissionError", "seal_bundle", "load_bundle",
    "load_contract", "export_state", "verify_state_export", "runtime_tmpfs",
    "validate_bundle", "admit_runtime",
)


class ScorerAdmissionError(AdmissionError):
    """A refused offline check with a fixed code and JSON-compatible receipt."""

    def __init__(self, code: str, receipt: dict):
        super().__init__(code)
        self.code = code
        self.receipt = receipt


def _gate(stage: str):
    def decorate(check):
        @wraps(check)
        def wrapped(*args, **kwargs):
            receipt = dict(schema="harnessgrad.scorer-admission.v1", stage=stage,
                           status="unknown", measured=False, production_authorized=False)
            try:
                evidence = check(*args, **kwargs)
            except EvidenceUnavailable as exc:
                code = f"{stage}_evidence_unavailable"
                receipt["reason"] = code
                raise ScorerAdmissionError(code, receipt) from exc
            except AdmissionError as exc:
                code = f"{stage}_contract_refused"
                receipt.update(status="blocked", reason=code)
                raise ScorerAdmissionError(code, receipt) from exc
            receipt.update(status="passed", evidence=evidence)
            return receipt
        return wrapped
    return decorate


@_gate("bundle")
def validate_bundle(*, closure_root: Path, bundle_raw: bytes, bundle_sha256: str) -> dict:
    """Verify caller-pinned manifest bytes and the exact platform-owned local tree.

    A passed receipt is local declared closure integrity only. Unknown means bytes
    were unavailable; blocked means the seal, declaration or observed bytes failed.
    """
    return _bundles.verify_bundle(closure_root, bundle_raw, bundle_sha256)


@_gate("runtime")
def admit_runtime(*, bundle_raw: bytes, bundle_sha256: str,
                  contract_raw: bytes, contract_sha256: str,
                  closure_root: Path, artifact_root: Path,
                  export_receipt: dict | None, info: dict | None, run_id: str) -> dict:
    """Bind independent pins, recheck both projections and inspect supplied facts.

    The task contract retains export/quota policy; the bundle retains its reusable
    scorer identity. Neither supplies runtime observations or export evidence.
    Pass None for missing evidence; uncertainty stays unmeasured and unauthorized.
    All local bytes are rechecked per call; no cached receipt can bypass a check.
    Ownership, CPU/memory/PID limits, freshness, quiescence, cleanup and official
    scorer execution remain independent caller obligations.
    """
    contract = _bundles.require_contract_bundle(contract_raw, contract_sha256,
                                               bundle_raw, bundle_sha256)
    if export_receipt is None or info is None:
        raise EvidenceUnavailable("artifact runtime evidence missing")
    bundle = _bundles.verify_bundle(closure_root, bundle_raw, bundle_sha256)
    _artifacts.verify_state_export(artifact_root, export_receipt, contract_raw,
                                  contract_sha256, run_id=run_id)
    runtime = _artifacts._verify_artifact_runtime(info, contract, contract_sha256,
                                                 artifact_root=artifact_root,
                                                 closure_root=closure_root)
    return dict(bundle=bundle, runtime=runtime, run_id=run_id)
