"""Keep pytest workspaces outside the platform, just like harness workspaces."""
from pathlib import Path

import pytest


def pytest_configure(config):
    basetemp = config.getoption("basetemp")
    if basetemp is None:
        return  # Preserve pytest's normal temporary-directory selection.
    platform = Path(__file__).resolve().parent
    workspace = Path(basetemp).resolve()
    if workspace == platform or platform in workspace.parents:
        raise pytest.UsageError(
            f"--basetemp must be OUTSIDE the platform tree ({platform}); "
            f"got {workspace}. Driver-based tests use tmp_path as a harness "
            "work root, which the runner correctly refuses inside the platform. "
            "Use --basetemp=/tmp/harnessgrad-pytest-quality or omit --basetemp."
        )
