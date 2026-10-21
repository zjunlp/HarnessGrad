"""PID observations include inherited host limits, without inventing a ceiling."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from eval.containers.observed import observe_pids


@pytest.mark.parametrize("local,parent,expected", [
    ("max", "max", None), ("max", "2048", 2048),
    ("512", "2048", 512), ("2048", "512", 512),
])
def test_kernel_pid_hierarchy_preserves_effective_ceiling(tmp_path, local, parent, expected):
    root = tmp_path / "cgroup"
    group = root / "host" / "container"
    group.mkdir(parents=True)
    (group / "pids.max").write_text(local)
    (group.parent / "pids.max").write_text(parent)
    limit, evidence = observe_pids(group, root=root)
    assert limit == expected
    assert evidence == {str(group): local, str(group.parent): parent}


def test_missing_nonroot_pid_observation_does_not_mean_unlimited(tmp_path):
    group = tmp_path / "host" / "container"
    group.mkdir(parents=True)
    (group / "pids.max").write_text("max")
    with pytest.raises(FileNotFoundError):
        observe_pids(group, root=tmp_path)


@pytest.mark.parametrize("value", ["0", "-1", "unknown", "", str(2**64)])
def test_invalid_kernel_pid_value_fails_observation(tmp_path, value):
    group = tmp_path / "container"
    group.mkdir()
    (group / "pids.max").write_text(value)
    with pytest.raises(RuntimeError, match="invalid kernel pids.max"):
        observe_pids(group, root=tmp_path)


def test_pid_observation_cannot_walk_outside_cgroup_root(tmp_path):
    with pytest.raises(RuntimeError, match="outside the cgroup root"):
        observe_pids(tmp_path, root=tmp_path / "different-root")
