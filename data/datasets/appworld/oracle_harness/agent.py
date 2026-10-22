#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import pathlib
import urllib.request


def post(base: str, path: str, payload: dict) -> object:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read()).get("output")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task")
    parser.add_argument("--workdir")
    args = parser.parse_args()
    task = json.loads(pathlib.Path(args.task).read_text(encoding="utf-8"))
    base = os.environ["HG_SVC_APPWORLD_URL"]
    post(base, "/initialize", {"task_id": task["task_id"], "experiment_name": "harness"})
    if os.environ.get("APPWORLD_NOOP") == "1":
        post(base, "/execute", {"task_id": task["task_id"], "code": "print('noop')"})
        return 0
    reference = pathlib.Path(args.workdir, ".oracle", "reference.py").read_text(encoding="utf-8")
    post(base, "/execute", {"task_id": task["task_id"], "code": reference})
    pathlib.Path(args.workdir, "trace.jsonl").write_text(
        json.dumps({"harness_identity": {"agent_model": "oracle", "harness": "appworld-released"}}) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
