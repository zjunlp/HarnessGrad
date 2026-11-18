"""Independent provenance envelopes for data declarations and implementation roles.

These values are evidence of a declaration or of captured implementation bytes,
not proof that a dataset artifact was downloaded or used in a measurement.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[a-z][a-z0-9_.-]*\Z")
_KINDS = {"data_source", "loader", "scorer"}
PROVIDERS = Path(__file__).resolve().parents[2] / "data/datasets"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _envelope(kind, details):
    payload = {"version": 1, "kind": kind, "details": details}
    return {**json.loads(_canonical(payload)), "sha256": hashlib.sha256(_canonical(payload)).hexdigest()}


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value


def data_source(dataset, *, origin, revision, declared_artifact_sha256=None):
    """Identify an upstream declaration; the digest is not a verification result."""
    if not isinstance(dataset, str) or not _NAME.fullmatch(dataset):
        raise ValueError("invalid dataset name")
    _text(origin, "data source origin")
    _text(revision, "data source revision")
    if declared_artifact_sha256 is not None and (not isinstance(declared_artifact_sha256, str)
            or not _DIGEST.fullmatch(declared_artifact_sha256)):
        raise ValueError("invalid declared artifact digest")
    return _envelope("data_source", {"dataset": dataset, "origin": origin, "revision": revision,
                                     "declared_artifact_sha256": declared_artifact_sha256})


def implementation(kind, components, *, protocol=None):
    """Identify named implementation roles by bytes, independently of file paths."""
    if not isinstance(kind, str) or kind not in {"loader", "scorer"}:
        raise ValueError("invalid implementation kind")
    if not isinstance(components, dict) or not components:
        raise ValueError("implementation components must be nonempty")
    if any(not isinstance(name, str) or not _NAME.fullmatch(name)
           or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
           for name, digest in components.items()):
        raise ValueError("invalid implementation component")
    if protocol is not None:
        _text(protocol, "implementation protocol")
    return _envelope(kind, {"components": dict(sorted(components.items())), "protocol": protocol})


def capture_implementation(kind, root, components, *, protocol=None):
    """Hash explicitly named source files; names are stable across file moves.

    Paths are confined to ``root``. Capture is not an attestation that these files
    were the ones executed, and callers must bind it to their execution contract.
    """
    root = Path(root).resolve(strict=True)
    if not isinstance(components, dict) or not components:
        raise ValueError("implementation components must be nonempty")
    hashes = {}
    for name, raw in components.items():
        if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
            raise ValueError("invalid implementation path")
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or str(path) != raw:
            raise ValueError("invalid implementation path")
        resolved = (root / raw).resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("implementation path escapes root or is not a file")
        with resolved.open("rb") as stream:
            first = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            last = os.fstat(stream.fileno())
        current = resolved.stat()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if stamp(first) != stamp(last) or stamp(last) != stamp(current):
            raise ValueError("implementation changed during capture")
        hashes[name] = digest.hexdigest()
    return implementation(kind, hashes, protocol=protocol)


def validate(value, *, kind=None):
    if (not isinstance(value, dict) or set(value) != {"version", "kind", "details", "sha256"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["kind"], str) or value["kind"] not in _KINDS
            or (kind is not None and value["kind"] != kind)
            or not isinstance(value["details"], dict)):
        raise ValueError("invalid measurement component identity")
    details = value["details"]
    if value["kind"] == "data_source":
        if set(details) != {"dataset", "origin", "revision", "declared_artifact_sha256"}:
            raise ValueError("invalid data source declaration")
        expected = data_source(details["dataset"], origin=details["origin"],
                               revision=details["revision"],
                               declared_artifact_sha256=details["declared_artifact_sha256"])
    else:
        if set(details) != {"components", "protocol"}:
            raise ValueError("invalid implementation declaration")
        expected = implementation(value["kind"], details["components"], protocol=details["protocol"])
    if value != expected:
        raise ValueError("measurement component identity digest mismatch")
    return expected


def problems(reference, current, *, kind):
    """Report missing, invalid or changed evidence without inferring equality."""
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("invalid measurement component kind")
    for label, value in (("reference", reference), ("current", current)):
        if value is None:
            return [f"{kind} identity missing from {label}; comparability is unknown"]
        try:
            validate(value, kind=kind)
        except (TypeError, ValueError):
            return [f"invalid {kind} identity on {label}"]
    return [] if reference == current else [f"{kind} identity changed"]


def validate_bundle(value, *, dataset=None):
    """Validate all three separately named identities for a selected dataset."""
    if not isinstance(value, dict) or set(value) != _KINDS:
        raise ValueError("measurement profile requires data_source, loader and scorer")
    result = {kind: validate(value[kind], kind=kind) for kind in sorted(_KINDS)}
    if dataset is not None and result["data_source"]["details"]["dataset"] != dataset:
        raise ValueError("measurement profile dataset mismatch")
    return result


def fields(value):
    return {} if value is None else {"measurement_components": validate_bundle(value)}


def bundle_problems(reference, current):
    if reference is None and current is None:
        return []
    for label, value in (("reference", reference), ("current", current)):
        if value is None:
            return [f"measurement_components missing from {label}; comparability is unknown"]
        try:
            validate_bundle(value)
        except (TypeError, ValueError) as exc:
            return [f"invalid measurement_components on {label}: {exc}"]
    return [problem for kind in sorted(_KINDS)
            for problem in problems(reference[kind], current[kind], kind=kind)]


def require_same(reference, current):
    errors = bundle_problems(reference, current)
    if errors:
        raise ValueError("; ".join(errors))


def require_scoring_alignment(scoring, components):
    """An opted-in profile must bind scoring and component captures to one scorer."""
    if components is None:
        return
    from harnessgrad.provenance import scoring_identity
    components = validate_bundle(components)
    scoring = scoring_identity.validate(scoring)
    if scoring['profile'].get('scorer_sha256') != components['scorer']['sha256']:
        raise ValueError('scoring identity does not match scorer component')


def resolve(dataset):
    """Load an optional adapter-owned declaration without importing its dataset."""
    if not isinstance(dataset, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", dataset):
        raise ValueError("invalid dataset name for measurement profile")
    path = PROVIDERS / dataset / "measurement_profile.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_hg_measurement_profile_" + dataset, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, "profile", None)
    if not callable(factory):
        raise ValueError(f"{dataset}: measurement_profile.py requires profile()")
    return validate_bundle(factory(), dataset=dataset)
