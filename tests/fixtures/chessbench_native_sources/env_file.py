"""Read the project's KEY=value configuration without executing shell code.

Keep this independent of the runtime so standalone data preparation stays stdlib-only.
"""
import os
from pathlib import Path


# Fixed opt-in for the identity-reviewed launcher. File parsers remain usable by
# legacy CLI/preparation tools; only runtime consumers opt out of file discovery.
REVIEWED_ENVIRONMENT_ONLY = "HG_REVIEWED_ENVIRONMENT_ONLY"


def reviewed_environment_only() -> bool:
    return os.environ.get(REVIEWED_ENVIRONMENT_ONLY) == "1"


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            values[key] = value.strip().strip("'\"")
    return values
