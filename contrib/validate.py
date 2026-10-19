#!/usr/bin/env python3
"""Validate a submission file against `contrib/submission.schema.json`.

A schema nobody runs is a suggestion. This is the command a contributor runs before opening a
pull request, and the command a reviewer runs on the file in it:

    python3 contrib/validate.py contrib/example-submission.json
    python3 contrib/validate.py contrib/results/alfworld/your-method.json

It checks schema shape only. Whether the *numbers* are real is a separate gate -- the task set must
match the frozen reference, `n_invalid` must be zero, the score must recompute from `per_task`, and
the harness sha must resolve; that gate is described in `website/src/content/docs/contributing.md`
and run by a maintainer, not by this script.
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SCHEMA = HERE / "submission.schema.json"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(f"usage: {pathlib.Path(sys.argv[0]).name} <submission.json>", file=sys.stderr)
        return 2
    target = pathlib.Path(argv[0])
    if not target.is_file():
        print(f"no such file: {target}", file=sys.stderr)
        return 2
    try:
        import jsonschema
    except ImportError:
        print("jsonschema is not installed; `pip install jsonschema`", file=sys.stderr)
        return 2
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"{target}: not JSON: {exc}", file=sys.stderr)
        return 1
    try:
        jsonschema.validate(document, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        print(f"{target}: INVALID at {where}: {exc.message}", file=sys.stderr)
        return 1
    run = document["runs"][0]
    print(f"{target}: valid · {document['dataset']} · {document['method']['name']} · "
          f"score {run['score']:.4f} on {run['n_tasks']} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
