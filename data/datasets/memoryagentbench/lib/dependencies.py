"""Offline identities for the distinct preparation and serving dependency closures.

Package metadata identifies an environment; it is not evidence that the frozen
corpus was prepared or that a provider/model supports a judge profile.
"""
import importlib.metadata
import platform
import sys

from .inputs import read_spec

PREPARE_READER_PINS = {"pyarrow": "23.0.1"}
OPTIONAL_DIFFERENTIAL_PINS = {"rouge-score": "0.1.2", "editdistance": "0.8.1", "numpy": "2.5.4"}


def identity(role):
    """Report the actual process, never infer runtime identity from a prep venv."""
    if role not in {"prepare", "runtime"}:
        raise ValueError("identity role must be prepare or runtime")
    pins = {**read_spec("chunk-policy.json")["dependencies"], **PREPARE_READER_PINS} if role == "prepare" else {}
    installed = {}
    for package in pins:
        try:
            installed[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed[package] = None
    return {"schema": "hg-mab-environment-v1", "role": role,
            "executable": sys.executable, "python_version": platform.python_version(),
            "required_distributions": pins, "installed_distributions": installed,
            "dependencies_match": all(installed[name] == version for name, version in pins.items()),
            "scope": "frozen input generation" if role == "prepare" else "relay and primary scorer; excludes optional ROUGE diagnostics",
            "validation": "environment identity only; no corpus, resource, service or model readiness asserted"}


def validate_preparation():
    result = identity("prepare")
    if not result["dependencies_match"]:
        mismatches = [f"{name}=={version} (installed {result['installed_distributions'][name]})"
                      for name, version in result["required_distributions"].items()
                      if result["installed_distributions"][name] != version]
        raise ValueError("preparation_dependency_mismatch: " + ", ".join(mismatches))
    return result
