"""Document the staging constraint without launching a game or a model."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.containers.sandbox import SandboxUnavailable, hidden_trees


def test_production_sandbox_refuses_a_work_root_inside_this_worktree():
    with pytest.raises(SandboxUnavailable, match="inside the platform"):
        hidden_trees(ROOT / ".cache" / "jericho-boundary" / "work", ROOT)
