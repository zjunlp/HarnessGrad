"""Native verifier keeps OS startup prerequisites without inheriting model keys."""
import os
import subprocess
import sys

from eval.verification.verifier import _verifier_env


def test_native_verifier_starts_python_without_model_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("HG_AGENT_API_KEY", "synthetic-agent-key")
    monkeypatch.setenv("HG_METHOD_API_KEY", "synthetic-method-key")
    env = _verifier_env(tmp_path)
    assert "HG_AGENT_API_KEY" not in env
    assert "HG_METHOD_API_KEY" not in env
    if os.name == "nt":
        assert env.get("SystemRoot") == os.environ["SystemRoot"]
    result = subprocess.run([sys.executable, "-c", "print('ready')"],
                            env=env, cwd=tmp_path, capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ready"
