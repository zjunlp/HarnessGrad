"""Service setup failures must release resources already acquired by the platform."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eval.service import services


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_environment_failure_releases_the_task_network(monkeypatch, failure_type):
    acquired = []
    released = []

    def docker(*args, **kwargs):
        if args[:2] == ("network", "create"):
            acquired.append(args[3])
        elif args[:2] == ("network", "rm"):
            released.append(args[2])
        else:
            pytest.fail(f"unexpected Docker operation: {args}")
        return subprocess.CompletedProcess(args, 0, "", "")

    def unavailable_config():
        raise failure_type("configuration unavailable")

    monkeypatch.setattr(services.container_mod, "docker", docker)
    monkeypatch.setattr(services, "current_config", unavailable_config)
    service = {"name": "orders", "image": "sha256:" + "a" * 64,
               "needs_model": True}

    with pytest.raises(failure_type, match="configuration unavailable"):
        services.start([service], recordings_root=Path("unused-recordings"),
                       run_id="env-failure", task_id="s01", run_seed="seed")

    assert acquired == ["hg-env-failure-s01"]
    assert released == acquired
