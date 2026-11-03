"""Local fixtures for upstream-policy ports. Never imports external benchmark code."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "methods"))
import _siblings

runtime = _siblings.load("policy_ports/runtime")
P = runtime.P


def fixture(tmp_path, points=None):
    base = tmp_path / "harness"
    base.mkdir(parents=True)
    (base / "harness.json").write_text(
        json.dumps(
            {
                "name": "port-fixture",
                "version": "1",
                "entrypoint": "agent.py",
                "backend": "cli",
                "env_kinds": ["files"],
            }
        )
    )
    (base / "agent.py").write_text('print("fixture")\n')
    (base / "prompt.md").write_text("Baseline instruction\n")
    (base / "skills").mkdir()
    (base / "skills/main.md").write_text("Existing reusable guidance\n")
    hg = base / "_harnessgrad"
    (hg / "history").mkdir(parents=True)
    (hg / "tasks").mkdir()
    (hg / "states").mkdir()
    points = points or [
        {
            "round": 0,
            "score_kind": "training",
            "score": 0.5,
            "per_task": {"t1": 1.0, "t2": 0.0},
            "identity": {"harness_sha": "root"},
        }
    ]
    for row in points:
        (hg / "history" / f'round-{row["round"]}.json').write_text(json.dumps(row))
    (hg / "round.json").write_text(json.dumps(points[-1]))
    for t, v in points[-1].get("per_task", {}).items():
        (hg / "tasks" / f"{t}.json").write_text(
            json.dumps(
                {"task_id": t, "score": v, "verdict": {"detail": "public feedback"}}
            )
        )
    for point in points[:-1]:
        shutil.copytree(
            base,
            hg / "states" / f'round-{point["round"]}',
            ignore=shutil.ignore_patterns("_harnessgrad", "__pycache__"),
        )
    workspace = tmp_path / "method-work"
    workspace.mkdir()
    req = {
        "platform_api_version": "0.1.0",
        "base_harness": str(base),
        "workspace": str(workspace),
        "trajectory_out": str(tmp_path / "trajectory.json"),
        "round_index": 1,
        "epochs_remaining": 4,
        "epochs_total": 4,
        "epochs_measured": 0,
        "task_ids": ["t1", "t2"],
    }
    return base, req


def capture(monkeypatch):
    captured = {}

    def report(req, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(runtime.editor, "report_many", report)

    def fail(req, **kwargs):
        captured["failure"] = str(kwargs["exc"])
        return 0

    monkeypatch.setattr(runtime.editor, "fail", fail)
    return captured


def member(index, score, scores=None, info=None):
    return P.Member(index, score, scores or {"t": score}, f"/tree/{index}", info or {})
