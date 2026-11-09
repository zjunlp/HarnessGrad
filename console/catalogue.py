"""Read-only platform catalogues consumed by the launch form and validation."""
from __future__ import annotations
import re
import sys
from dataclasses import dataclass
from typing import Callable
if __package__:
    from .record_reader import read_json
else:
    from record_reader import read_json


@dataclass(frozen=True)
class LaunchCatalogue:
    adapters: Callable
    base_harnesses: Callable
    improvers: Callable
    task_models: Callable
    method_catalogue: Callable

def adapters(platform) -> list[dict]:
    """List datasets from the platform registry; scan declarations only if it cannot load.

    The fallback reads complete source text without importing dataset adapters.
    """
    try:
        sys.path.insert(0, str(platform))
        from data.scaffolds.registry import published  # noqa: PLC0415
        rows = [{"name": name, "path": f"data/datasets/{name}.py"} for name in published()]
        if (platform / 'data' / 'demo.py').is_file():
            rows.insert(0, {'name': 'demo', 'path': 'data/demo.py', 'kind': 'fixture'})
        return rows
    except Exception:  # noqa: BLE001 - 平台不可导入时仍然给一个能用的列表
        out = []
        for f in sorted((platform / "data" / "datasets").glob("*.py")):
            if f.name.startswith("_"):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r'^KIND\s*=\s*"dataset"', text, re.M):
                out.append({"name": f.stem, "path": f"data/datasets/{f.name}"})
        return out


def base_harnesses(platform) -> list[str]:
    """Read the declared launchable task harnesses.

    Older checkouts without launchable.json fall back to directories containing a
    manifest. Listing does not establish runtime readiness.
    """
    root = platform / "base_harness"
    if not root.is_dir():
        return []
    declared = read_json(root / "launchable.json").get("harnesses")
    if isinstance(declared, list) and declared:
        return [str(name) for name in declared
                if (root / str(name) / "harness.json").is_file()]
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / "harness.json").is_file())


def task_models(platform, *, load_settings) -> list[str]:
    """Offer mock plus saved/configured solver models without probing an endpoint."""
    names = ["mock"]
    settings = load_settings()
    if settings["task"].get("model"):
        names.append(settings["task"]["model"])
    try:
        sys.path.insert(0, str(platform))
        from harnessgrad.config import current
        configured = current().agent.model
        if configured:
            names.append(str(configured))
    except Exception:  # noqa: BLE001 - a console without the package still lists `mock`
        pass
    return list(dict.fromkeys(names))


def improvers(platform) -> list[dict]:
    """Project the launchable editor harnesses from the full improver registry.

    Keep registry IDs for driver/record identity and separate display labels. Older
    checkouts without a launchable declaration retain the registry fallback.
    """
    data = read_json(platform / "harnessgrad" / "improvers" / "improvers.json")
    wanted = data.get("improvers") if isinstance(data.get("improvers"), dict) else {}
    declared = read_json(platform / "harnessgrad" / "improvers" / "launchable.json").get("improvers")
    out = []
    if isinstance(declared, list) and declared:
        for row in declared:
            name = str(row.get("name") or "") if isinstance(row, dict) else str(row or "")
            spec = wanted.get(name) if isinstance(wanted.get(name), dict) else {}
            if not name or not spec:
                continue
            out.append({"name": name,
                        "label": str((row or {}).get("label") or name) if isinstance(row, dict) else name,
                        "model": spec.get("model") or "", "kind": spec.get("kind") or ""})
        return out
    for name, spec in sorted(wanted.items()):
        if not isinstance(spec, dict):
            continue
        out.append({"name": name, "label": name, "model": spec.get("model") or "",
                    "kind": spec.get("kind") or ""})
    return out


def method_catalogue(platform_root) -> list[dict]:
    """`methods/*/method.json` 的清单。没有 method.json 的目录不算一个可选方法。"""
    rows = []
    for path in sorted((platform_root / "methods").glob("*/method.json")):
        entry = read_json(path)
        if not isinstance(entry, dict):
            continue
        name = path.parent.name
        if name.startswith(("_", ".")):
            continue
        rows.append({"name": name, "title": str(entry.get("title") or name),
                     "kind": str(entry.get("kind") or "method")})
    return rows
