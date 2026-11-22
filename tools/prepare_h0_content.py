"""Audit or package a published H0 using separately reviewed measurement evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from harnessgrad.provenance import h0_content
from harnessgrad.provenance.runtime_content import digest

REVIEW_SCHEMA = "harnessgrad.h0-publication-evidence.v1"


def _published_point(directory):
    with (directory / "curve.jsonl").open(encoding="utf-8") as stream:
        return h0_content.point_json(json.loads(next((line for line in stream if line.strip()), "")))


def package(directory, point, review_path, review_pin):
    raw = review_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != review_pin:
        raise ValueError("reviewed evidence differs from the trusted SHA-256 pin")
    review = json.loads(raw)
    fields = {"schema", "run_id", "content", "agent", "deployment", "source", "point_sha256", "traces"}
    if not isinstance(review, dict) or set(review) != fields or review["schema"] != REVIEW_SCHEMA:
        raise ValueError("invalid reviewed publication evidence")
    if digest(point) != review["point_sha256"]:
        raise ValueError("published point differs from the reviewed measurement")
    envelope = {
        "schema": h0_content.SCHEMA, "run_id": review["run_id"], "content": review["content"],
        "agent": review["agent"], "deployment": review["deployment"],
        "measurement_evidence": {"origin": "published-measurement", "source": review["source"],
                                 "point": point, "traces": review["traces"]},
    }
    h0_content.admit(envelope, review["content"], review["agent"], review["deployment"], directory)
    return h0_content.publish(directory, envelope)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="staged H0 publication directory")
    group = parser.add_argument_group("packaging (omitting both options audits the available evidence)")
    group.add_argument("--reviewed-evidence", type=Path)
    group.add_argument("--reviewed-evidence-sha256")
    args = parser.parse_args(argv)
    if bool(args.reviewed_evidence) != bool(args.reviewed_evidence_sha256):
        parser.error("provide both reviewed evidence and its trusted SHA-256 pin")
    try:
        directory = args.directory.resolve(strict=True)
        if args.reviewed_evidence:
            point = _published_point(directory)
            print("H0 content attestation SHA-256:",
                  package(directory, point, args.reviewed_evidence, args.reviewed_evidence_sha256))
            return 0
        from eval.run.metrics import mean
        point = _published_point(directory)
        identity = point.get("identity") or {}
        scores = point.get("per_task") or {}
        print(json.dumps({
            "run_id": point.get("run_id"), "base_tree": identity.get("harness_sha"),
            "score": point.get("score"), "recorded_task_scores": len(scores),
            "recomputed_score": mean(list(scores.values()), dataset=(point.get("sampling") or {}).get("dataset")),
            "point_sha256": digest(point),
            "legacy_identity_fields_present": [key for key in (
                "verification_context", "measurement_components", "dataset_runtime_identity",
                "scoring_identity", "harness_runtime_identity") if key in point],
            "portable_attestation_present": (directory / h0_content.FILENAME).is_file(),
        }, indent=2))
        if not (directory / h0_content.FILENAME).is_file():
            raise ValueError("missing original content attestation. Obtain reviewed measurement-time "
                             "weights, decoding, task/verifier and runtime identities or measure a fresh H0")
        return 0
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"H0 publication: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
