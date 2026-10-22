#!/usr/bin/env python3
"""One train-task, no-model probe of the real runner, aw client and recording mount.

Run from a checkout with the three pinned AppWorld images installed:
  python tools/diagnose_appworld.py --out /absolute/fresh/output
The fake actor reads API documentation only. It never loads an oracle or calls a model.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.run.runner import run_one  # noqa: E402


ACTOR = '''import argparse, json, subprocess, sys
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument('--task', required=True); ap.add_argument('--workdir', required=True)
a = ap.parse_args(); work = Path(a.workdir)
tid = json.loads(Path(a.task).read_text())['task_id']
with (work / 'trace.jsonl').open('w', buffering=1) as trace:
    trace.write(json.dumps({'fake_actor_started': True, 'task_id': tid}) + '\\n')
    # The actor cannot inspect the recording mount or load AppWorld's task data.
    assert not Path('/recordings/appworld').exists()
    assert not Path('/appworld/data').exists()
    for args in [['init', '--task', tid],
                 ['action', '--task', tid, '--code',
                  'print(apis.api_docs.show_app_descriptions())']]:
        p = subprocess.run([sys.executable, str(work / 'aw'), *args],
                           capture_output=True, text=True, errors="replace", timeout=120)
        trace.write(json.dumps({'argv': args, 'exit_code': p.returncode,
                               'stdout': p.stdout, 'stderr': p.stderr}) + '\\n')
        if p.returncode:
            sys.exit(p.returncode)
    trace.write(json.dumps({'usage': {'calls': 0}}) + '\\n')
(work / 'answer.txt').write_text('diagnostic completed')
'''


def image_defaults() -> dict[str, str]:
    """Read image constants without importing the adapter or loading its full pool."""
    values = {}
    for node in ast.parse((ROOT / "data/datasets/appworld.py").read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                        "IMAGE", "SERVICE_IMAGE", "VERIFIER_IMAGE"}:
                    values[target.id] = ast.literal_eval(node.value.args[1])
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--task", default="07b42fd_1")
    parser.add_argument("--train-file", type=Path, default=Path(
        "/mnt/20t/xubuqiang/datasets/appworld/_src/unpacked/data/datasets/train.txt"))
    parser.add_argument("--client", type=Path, default=ROOT / "data/datasets/appworld/cli/aw")
    args = parser.parse_args()
    if args.task not in args.train_file.read_text().splitlines():
        parser.error("task must belong to the released train split")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ["HG_AGENT_BACKEND"] = "mock"
    for key in ("HG_AGENT_BASE_URL", "HG_AGENT_MODEL", "HG_AGENT_API_KEY"):
        os.environ.pop(key, None)
    images = image_defaults()
    repo = out / "fake-harness"
    repo.mkdir()
    (repo / "agent.py").write_text(ACTOR)
    (repo / "harness.json").write_text(json.dumps({
        "name": "appworld-no-model-diagnostic", "entrypoint": "agent.py",
        "version": "1", "env_kinds": ["exec"], "install": "requirements.txt"}))
    # Match loop's overlay mount plan while making zero model calls. This catches
    # launch failures hidden by a fake harness with no dependency declaration.
    shutil.copy2(ROOT / "base_harness/loop/requirements.txt", repo / "requirements.txt")
    service = {"name": "appworld", "image": images["SERVICE_IMAGE"],
               "port": 8000, "record": True,
               "health": {"argv": ["python", "-c",
                   "import urllib.request as u;"
                   "u.urlopen('http://localhost:8000/', timeout=2).read()"],
                   "timeout_s": 60}}
    result = run_one(
        repo, {"task_id": args.task, "goal": "Read API documentation only."},
        setup={"files": [{"path": "aw", "from": str(args.client.resolve())}]},
        env={"kind": "exec", "image": images["IMAGE"], "state": "mount",
             "workdir": "/app", "network": "services", "services": [service],
             "agent_timeout_s": 180},
        # No scorer is needed to prove transport and persistence.
        run_id=f"codex101-diagnostic-{os.getpid()}", run_seed="0",
        recordings_root=out / "recordings")
    (out / "result.json").write_text(json.dumps(result, indent=2))
    (out / "trace.jsonl").write_text(result.get("trace") or "")
    log = out / "recordings" / args.task / "appworld" / "experiments" / \
        "outputs/harness/tasks" / args.task / "logs/api_calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()
             if line.strip()] if log.is_file() else []
    passed = (result.get("exit_code") == 0 and log.is_file()
              and any(call.get("url") == "/api_docs/app_descriptions"
                      for call in calls))
    receipt = {"passed": passed, "task_id": args.task, "side": "train",
               "model_calls": 0, "images": images,
               "client_sha256": hashlib.sha256(args.client.read_bytes()).hexdigest(),
               "exit_code": result.get("exit_code"), "log": str(log),
               "log_exists": log.is_file(), "api_calls": len(calls)}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
