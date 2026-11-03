"""upstream policy ports through the real method sandbox and platform scorer (§4)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_opro_batch_feedback_and_final_deployment_use_platform_scores(tmp_path):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request)
            # Human-authored demo fixture outcomes, not a benchmark model call.
            instruction = "alpha\n" if len(calls) == 1 else "wrong-again\n"
            payload = {
                "id": "scripted",
                "object": "chat.completion",
                "created": 0,
                "model": "method-fixture",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"instruction": instruction}),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
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
        if not k.startswith(("HG_METHOD_", "HG_AGENT_", "HG_IMPROVER_", "HARNESSGRAD_"))
    }
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        HG_AGENT_BACKEND="mock",
        HG_AGENT_MODEL="mock",
        HG_METHOD_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
        HG_METHOD_API_KEY="fixture-local-key",
        HG_METHOD_MODEL="method-fixture",
        HG_POLICY_CONFIG=json.dumps(
            {"instruction_path": "prompt.md", "num_candidates": 2}
        ),
        HG_SLOT_GUARD_TIMEOUT="10",
        HG_SLOT_MIN_FREE_GB="1",
    )
    try:
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
                "none",
                "--run-id",
                "native-opro-fixture",
                "--runs-root",
                str(tmp_path / "runs"),
                "--work-root",
                str(tmp_path / "work"),
                "--gpu-budget",
                "1",
                "--gpu-budget-dir",
                str(tmp_path / "budget"),
                "--method-entrypoint",
                f'{sys.executable} {ROOT / "methods/opro/run.py"}',
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        run = tmp_path / "runs/native-opro-fixture"
        points = [
            json.loads(line) for line in (run / "curve.jsonl").read_text().splitlines()
        ]
        assert [p["score"] for p in points] == [0.0, 1.0, 0.0, 1.0], (
            proc.stdout + "\n" + proc.stderr + "\n" + json.dumps(points)
        )
        assert len(calls) == 2
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
