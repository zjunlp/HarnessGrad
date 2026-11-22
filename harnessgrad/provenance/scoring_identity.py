"""Optional adapter-owned scoring profiles, frozen into measurement records.

An adapter opts in with ``<assets>/scoring_profile.py:profile()``. Looking for the
file does not import other adapters or require their data, including in prepare_h0.
Profiles contain public metric metadata and hashes, never task answers or credentials.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re

PROVIDERS = Path(__file__).resolve().parents[2] / "data/datasets"


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def create(profile):
    if (not isinstance(profile, dict) or not isinstance(profile.get("metric"), str)
            or not profile["metric"].strip()):
        raise ValueError("scoring profile requires a nonempty metric")
    payload = {"version": 1, "profile": profile}
    encoded = _canonical(payload)
    return {**json.loads(encoded), "sha256": hashlib.sha256(encoded.encode()).hexdigest()}


def validate(value):
    if (not isinstance(value, dict) or set(value) != {"version", "profile", "sha256"}
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("invalid scoring_identity envelope")
    expected = create(value["profile"])
    if value != expected:
        raise ValueError("scoring_identity digest mismatch")
    return expected


def provider_path(dataset):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", dataset):
        raise ValueError("invalid dataset name for scoring profile")
    return PROVIDERS / dataset / "scoring_profile.py"


def resolve(dataset):
    path = provider_path(dataset)
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_hg_scoring_profile_" + dataset, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = getattr(module, "profile", None)
    if not callable(provider):
        raise ValueError(f"{dataset}: scoring_profile.py requires profile()")
    return create(provider())


def fields(value):
    """No new fields at all for adapters that have not opted in."""
    return {} if value is None else {"scoring_identity": validate(value)}


def problems(reference, current):
    if reference is None and current is None:
        return []
    for label, value in (("reference", reference), ("current measurement", current)):
        if value is None:
            return [f"scoring_identity is missing from the {label}; scoring comparability is unknown"]
        try:
            validate(value)
        except (TypeError, ValueError) as exc:
            return [f"invalid scoring_identity on the {label}: {exc}"]
    if reference != current:
        return ["scoring_identity changed: scoring profile or recorded implementation differs; comparability is unknown"]
    return []


def require_same(reference, current):
    errors = problems(reference, current)
    if errors:
        raise ValueError("; ".join(errors))


def content_key(value):
    """Retain every unrecognized adapter field in the v1 content comparison."""
    if value is None:
        return None
    profile = dict(validate(value)["profile"])
    if "endpoint_sha256" in profile:
        # A route alone is not evidence about the judge that actually served it.
        judge = profile.get("judge")
        if (not isinstance(judge, dict) or not isinstance(judge.get("weights_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", judge["weights_sha256"])
                or not isinstance(judge.get("decoding"), dict) or not judge["decoding"]):
            raise ValueError("judge endpoint portability requires pinned judge weights and decoding")
        del profile["endpoint_sha256"]
    return create(profile)
