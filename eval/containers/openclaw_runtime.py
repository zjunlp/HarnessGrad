"""Trusted admission of prepared OpenClaw installations, before task/cache dispatch."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path

from harnessgrad.paths import main_checkout


ROOT = Path(__file__).resolve().parents[2]
FIELDS = ("schema", "version", "source_identity", "node", "node_version", "entrypoint",
          "package_root", "argv", "runtime_paths", "binding_identities", "files",
          "packages", "package_lookups", "symlinks")


class RuntimeUnavailable(RuntimeError):
    """An OpenClaw installation cannot be admitted for measurement."""


def identity(repo: Path, env: dict | None = None) -> dict | None:
    """Validate with platform-owned code, never import candidate Python on the host."""
    try:
        repo = Path(repo).resolve()
        manifest_path = repo / "harness.json"
        if not manifest_path.is_file():
            return None
        if not manifest_path.resolve(strict=True).is_relative_to(repo):
            raise ValueError("OpenClaw manifest must belong to the Harness")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("Harness manifest must be an object")
        if "_openclaw_runtime" not in manifest:
            return None
        metadata = repo / "runtime-identity.json"
        if not metadata.resolve(strict=True).is_relative_to(repo):
            raise ValueError("OpenClaw source identity must belong to the Harness")
        path = ROOT / "base_harness/openclaw/openclaw_runtime.py"
        spec = importlib.util.spec_from_file_location("_platform_openclaw_runtime", path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        record = helper.resolve(repo, dict(os.environ) if env is None else env,
                                forbidden=(ROOT, main_checkout(ROOT)))
        # This record pins selected files and metadata, not every dependency byte.
        # Preserve execution compatibility while always taking a fresh measurement.
        payload = json.dumps({key: record[key] for key in FIELDS}, sort_keys=True, separators=(",", ":"))
        return {"kind": "openclaw", "cacheable": False, "version": record["version"],
                "node_version": record["node_version"],
                "installation_sha256": hashlib.sha256(payload.encode()).hexdigest()}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise RuntimeUnavailable(f"OpenClaw runtime admission failed: {exc}") from exc
