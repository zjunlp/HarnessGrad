"""upstream policy ports through the real method sandbox and platform scorer (§4)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('method,config,expected_scores,expected_calls', [
    ('opro', {'instruction_path': 'prompt.md', 'num_candidates': 2}, [0.0, 1.0, 0.0, 1.0], 2),
    ('gepa', {'instruction_path': 'prompt.md', 'reflection_minibatch_size': 1}, [0.0, 1.0, 1.0, 1.0], 1),
])
def test_method_feedback_and_final_deployment_use_platform_scores(
        tmp_path, method, config, expected_scores, expected_calls):
    launcher_dir = tmp_path / "authored-editor"
    launcher_dir.mkdir()
    launcher = launcher_dir / "launcher"
    launcher.write_text("""#!/usr/bin/env python3
import json, os, sys, tempfile
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("authored-editor 1.0.0")
    sys.exit(0)

prompt, = sys.argv[1:]
assert "role-result.json" in prompt
assert os.environ["HG_IMPROVER_MODEL"] == "authored-editor-model"
assert os.environ["HG_IMPROVER_PROVIDER"] == "offline"
counter = Path(tempfile.gettempdir()) / f"native-proposals-{os.getppid()}"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
instruction = "alpha\\n" if n == 0 else "wrong-again\\n"
result = Path("../artifacts/role-result.json")
result.write_text(json.dumps({"instruction": instruction}))
print("native-fixture-call " + json.dumps({"instruction": instruction}))
print('dsh-usage {"calls":1,"input_tokens":11,"output_tokens":7,"total_tokens":18}')
""")
    launcher.chmod(0o755)
    registry = tmp_path / "improvers.json"
    registry.write_text(json.dumps({
        "default": "authored-editor",
        "improvers": {"authored-editor": {
            "name": "authored-editor", "kind": "cli", "argv_style": "dsh",
            "launcher": str(launcher), "provider": "offline", "profile": "headless",
            "model": "authored-editor-model", "api_key_env": "FIXTURE_EDITOR_KEY",
        }},
    }))
    source = tmp_path / "source"
    source.mkdir()
    (source / "harness.json").write_text(
        json.dumps(
            {
                "name": "instruction-fixture",
                "version": "1",
                "entrypoint": "agent.py",
                "backend": "cli",
                "env_kinds": ["files"],
            }
        )
    )
    (source / "prompt.md").write_text("wrong\n")
    (source / "agent.py").write_text(
        """import argparse,json,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
work=Path(a.workdir)
answer=Path(__file__).with_name('prompt.md').read_text()
(work/'answer.txt').write_text(answer)
(work/'trace.jsonl').write_text(json.dumps({'harness_identity':{'harness':'instruction-fixture','agent_model':'mock','agent_backend':'mock'}})+'\\n'+json.dumps({'submitted':True,'answer':answer})+'\\n'+json.dumps({'usage':{'calls':0,'input_tokens':0,'output_tokens':0}})+'\\n')
"""
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HG_METHOD_", "HG_AGENT_", "HG_IMPROVER", "HARNESSGRAD_"))
    }
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        HG_AGENT_BACKEND="mock",
        HG_AGENT_MODEL="mock",
        HG_IMPROVER_CONFIG=str(registry),
        HG_REVIEWED_ENVIRONMENT_ONLY="1",
        DSH_HOME=str(tmp_path / "empty-dsh-home"),
        FIXTURE_EDITOR_KEY="fixture-local-key",
        HG_POLICY_CONFIG=json.dumps(config),
        HG_SLOT_GUARD_TIMEOUT="10",
        HG_SLOT_MIN_FREE_GB="1",
    )
    proc = subprocess.run(
        [
            sys.executable,
            "driver.py",
            "--dataset",
            "demo",
            "--sampling",
            "all",
            "--tasks",
            "t01",
            "--side",
            "train",
            "--harness",
            str(source),
            "--rounds",
            "3",
            "--trials",
            "1",
            "--jobs",
            "1",
            "--improver",
            "authored-editor",
            "--run-id",
            "native-method-fixture",
            "--runs-root",
            str(tmp_path / "runs"),
            "--work-root",
            str(tmp_path / "work"),
            "--gpu-budget",
            "1",
            "--gpu-budget-dir",
            str(tmp_path / "budget"),
            "--method-entrypoint",
            f'{sys.executable} {ROOT / "methods" / method / "run.py"}',
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    run = tmp_path / "runs/native-method-fixture"
    points = [
        json.loads(line) for line in (run / "curve.jsonl").read_text().splitlines()
    ]
    assert [p["score"] for p in points] == expected_scores, (
        proc.stdout + "\n" + proc.stderr + "\n" + json.dumps(points)
    )
    calls = []
    for path in (run / "method_logs").glob("round-*/*-receipt.log"):
        receipt = json.loads(path.read_text())
        if receipt.get("coverage") == "cli_stdout_then_stderr":
            assert receipt["complete"] and receipt["outcome"] == "success"
            transcript = path.parent / Path(receipt["full"]["path"]).name
            calls.extend(
                json.loads(line.removeprefix("native-fixture-call "))["instruction"]
                for line in transcript.read_text().splitlines() if line.startswith("native-fixture-call ")
            )
    assert sorted(calls) == ["alpha\n", "wrong-again\n"][:expected_calls]
    assert points[1]["identity"]["improver"]["name"] == "authored-editor"
    assert (
        points[-1]["identity"]["harness_sha"]
        == points[1]["identity"]["harness_sha"]
    )
    assert (run / "state_after_round3/prompt.md").read_text() == "alpha\n"
    assert points[-1]["method_reported"]["policy_port"]["selected_round"] == 1
    assert (
        points[1]["method_reported"]["policy_port"]["variant"]
        == "upstream-policy-port"
    )
    assert "fixture-local-key" not in (run / "curve.jsonl").read_text()
