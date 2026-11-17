"""Read-only CLI for declared method metadata, independent of any workflow."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from harnessgrad import PLATFORM_ROOT
from harnessgrad.core.methods import describe_method, discover_methods


def _row(method):
    return {
        "name": method.name,
        "entrypoint": str(method.entrypoint) if method.entrypoint else None,
        "metadata": method.metadata,
        "error": method.error,
    }


def main(argv: list[str] | None = None, *, methods_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harnessgrad method", description=(
        "Inspect declared method metadata. Listing is not a readiness check."))
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list method declarations")
    listing.add_argument("--json", action="store_true", help="print complete metadata as JSON")
    detail = sub.add_parser("show", help="show one method declaration")
    detail.add_argument("name")
    root = Path(methods_root) if methods_root is not None else PLATFORM_ROOT / "methods"
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            rows = [_row(method) for method in discover_methods(root)]
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    metadata = row["metadata"] or {}
                    kind = metadata.get("kind") or "unknown"
                    state = "metadata error" if row["error"] else ("no entrypoint" if not row["entrypoint"] else "declared")
                    print(f"{row['name']}\t{kind}\t{state}")
                print("Declarations only; availability and workflow compatibility are not checked.", file=sys.stderr)
            return 0
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.name):
            raise ValueError("method name must contain only letters, digits, underscores or hyphens")
        method = describe_method(root / args.name)
        print(json.dumps(_row(method), ensure_ascii=False, indent=2))
        return 1 if method.error else 0
    except ValueError as exc:
        print(f"harnessgrad method: {exc}", file=sys.stderr)
        return 1
