"""Compatibility imports must share implementation identity, state and CLI behavior."""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPAT_MODULES = {
    "eval.appworld_reporting": "eval.policy.appworld_reporting",
    "eval.artifact_admission": "eval.admission.artifact_admission",
    "eval.candidate": "eval.run.candidate",
    "eval.console": "eval.observe.console",
    "eval.container": "eval.containers.container",
    "eval.container_state": "eval.containers.container_state",
    "eval.diagnostic_policy": "eval.policy.diagnostic_policy",
    "eval.gaia_preflight": "eval.policy.gaia_preflight",
    "eval.gaia_snapshot_attestation": "eval.policy.gaia_snapshot_attestation",
    "eval.harness_runtime": "eval.containers.harness_runtime",
    "eval.integrity": "eval.run.integrity",
    "eval.lifecycle_receipt": "eval.admission.lifecycle_receipt",
    "eval.metrics": "eval.run.metrics",
    "eval.modelgate": "eval.service.modelgate",
    "eval.phases": "eval.observe.phases",
    "eval.pilot_admission": "eval.admission.pilot_admission",
    "eval.progress": "eval.observe.progress",
    "eval.runner": "eval.run.runner",
    "eval.runner_admission": "eval.admission.runner_admission",
    "eval.runtime_policy": "eval.policy.runtime_policy",
    "eval.sandbox": "eval.containers.sandbox",
    "eval.scorer_admission": "eval.admission.scorer_admission",
    "eval.scorer_backend": "eval.admission.scorer_backend",
    "eval.scorer_bundle": "eval.admission.scorer_bundle",
    "eval.separated_stdio": "eval.service.separated_stdio",
    "eval.services": "eval.service.services",
    "eval.stepwise_transport": "eval.service.stepwise_transport",
    "eval.task_contract": "eval.run.task_contract",
    "eval.trace": "eval.observe.trace",
    "eval.tracing": "eval.observe.tracing",
    "eval.unix_model_relay": "eval.service.unix_model_relay",
    "eval.verifier": "eval.verification.verifier",
}


@pytest.mark.parametrize("old,new", COMPAT_MODULES.items())
def test_legacy_import_is_the_implementation(old, new):
    legacy = importlib.import_module(old)
    implementation = importlib.import_module(new)
    assert legacy is implementation
    assert sys.modules[old] is sys.modules[new]
    # Attribute lookup through the parent package must see the same module too.
    parent, _, name = old.rpartition(".")
    assert getattr(importlib.import_module(parent), name) is implementation


def test_patch_through_legacy_path_reaches_function_globals(monkeypatch):
    legacy = importlib.import_module("eval.runner")
    implementation = importlib.import_module("eval.run.runner")
    marker = object()
    monkeypatch.setattr(legacy, "PLATFORM_ROOT", marker)
    assert implementation.run_one.__globals__["PLATFORM_ROOT"] is marker
    assert legacy._verify is implementation._verify


def test_repository_root_survives_the_move():
    from eval.run.integrity import PLATFORM_ROOT

    assert PLATFORM_ROOT == ROOT


def test_legacy_critic_recognizes_both_scorer_path_locations():
    # Inspect declarations without importing or executing the historical adapter.
    tree = ast.parse((ROOT / "legacy/adapters/harnessgrad_domain/adapter.py").read_text())
    patterns = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "critic_patterns"
                            for target in node.targets))
    for name in ("runner", "metrics", "integrity"):
        old = f"eval/{name}.py"
        new = f"eval/run/{name}.py"
        old_reasons = [reason for pattern, reason in patterns if re.search(pattern, old)]
        new_reasons = [reason for pattern, reason in patterns if re.search(pattern, new)]
        assert old_reasons and old_reasons == new_reasons


@pytest.mark.parametrize("module", [
    "gaia_preflight", "harness_runtime", "scorer_bundle", "separated_stdio",
])
def test_legacy_module_cli_delegates(module):
    old = "eval." + module
    new = COMPAT_MODULES[old]
    results = [subprocess.run(
        [sys.executable, "-m", name, "--help"], cwd=ROOT,
        capture_output=True, text=True, timeout=30,
    ) for name in (old, new)]
    assert results[0].returncode == results[1].returncode == 0
    assert results[0].stdout.replace(old, new) == results[1].stdout
    assert results[0].stderr == results[1].stderr == ""


def test_stdio_scorer_keeps_its_standalone_script_entrypoint(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "eval" / "separated_stdio.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert result.stderr == ""
