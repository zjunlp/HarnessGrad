"""The τ² v0.1.0 runtime pieces, pinned at the source level.

These run on the platform's Python 3.12 and never import `tau2` (which needs 3.13 and the isolated
runtime), so they can sit in the normal gate. What they pin is what a later edit could quietly
break: the operation set the actor can reach, the two task identities the relay keeps apart, the
rule that decides which rows declare a judge, and the release identity the metric carries.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "data" / "datasets" / "tau2_bench"
sys.path.insert(0, str(ROOT))

from data.datasets import tau2_bench as dataset  # noqa: E402


def source(relative: str) -> str:
    return (ASSETS / relative).read_text(encoding="utf-8")


def test_the_relay_exposes_exactly_the_four_half_duplex_operations():
    """`show` is a file read in the CLI; the socket carries the conversation only.

    There is no batch operation: v0.1.0's step budget is one assistant action per turn, so
    accepting several tool calls in one request would let an actor spend fewer steps than the
    release counts -- a different benchmark with a larger budget.
    """
    text = source("relay.py")
    for operation in ("'start'", "'say'", "'call'", "'status'"):
        assert operation in text, operation
    assert "'calls'" not in text
    assert "unsupported_operation_or_fields" in text
    cli = source("cli/tau2.py")
    assert "choices=['show', 'start', 'say', 'call', 'status']" in cli


def test_the_relay_keeps_the_two_task_identities_apart():
    """The platform id the caller selected and the released id the official row carries.

    v0.1.0's ids are per-domain strings ("3"), so binding `payload['task_id']` to the selected id
    would compare two different values -- and `case_task_id` is what makes the difference visible.
    """
    relay = source("relay.py")
    assert "context['task_id']" in relay and "relay task binding mismatch" in relay
    assert "'case_task_id': self.config['task_id']" in relay
    assert "'revision': self.config['release']" in relay
    check = source("verify/check.py")
    assert "payload.get('case_task_id') != args.task_id" in check
    assert "config = {'task_id': payload['task_id']" in check


def test_the_verifier_scores_the_selected_task_and_installs_the_judge_channel():
    check = source("verify/check.py")
    assert "from scoring import score" in check
    assert "install('judge', call)" in check
    assert "METRIC = 'tau2.v0.1.0.ALL.binary'" in check
    assert check.count("tau2_code/verify/check.py") == 0  # the adapter names it, not the check


def test_the_metric_carries_the_release_and_so_does_the_verifier():
    assert dataset.METRIC == "tau2.v0.1.0.ALL.binary"
    check = source("verify/check.py")
    assert f"METRIC = '{dataset.METRIC}'" in check
    assert "tau2.v0.1.0.ALL.binary" in source("../tau2_bench.py")


def test_no_v1_0_1_only_name_survives_the_port():
    """The two releases differ in exactly these names; a leftover is an import-time `AttributeError`
    inside the actor's episode, which reads as a harness failure rather than an adapter defect.

    Comments may name them -- explaining an absence is the point of a comment -- so this reads the
    parsed module, not the text.
    """
    banned = {"Tau3Runtime", "tau3_transport", "tau3_bundle", "get_environment_manual_policy",
              "banking_knowledge", "validate_message_history", "CommunicationMode"}
    found = []
    for relative in ("relay.py", "lib/runtime.py", "lib/model.py", "lib/scoring.py",
                     "lib/bootstrap.py", "verify/check.py", "cli/tau2.py", "control.py"):
        tree = ast.parse(source(relative))
        for node in ast.walk(tree):
            name = node.attr if isinstance(node, ast.Attribute) else (
                node.id if isinstance(node, ast.Name) else None)
            if name in banned:
                found.append(f"{relative}:{name}")
    assert found == [], found


def test_the_python_pin_is_the_interpreter_the_release_declares():
    """v0.1.0's `pyproject.toml` says `requires-python = ">=3.13"`; τ³ runs on 3.12."""
    assert "sys.version_info[:2] != (3, 13)" in source("lib/bootstrap.py")
    adapter = (ROOT / "data/datasets/tau2_bench.py").read_text(encoding="utf-8")
    assert 'if info["version"] != [3, 13]:' in adapter
