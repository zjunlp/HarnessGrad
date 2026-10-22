"""Execute unchanged pinned NESTFUL Win Rate functions, in a separate audit process.

No model, training or prediction generation. This is a gold-control measurement only.
Upstream function text is AST-extracted, not imported with model-specific output parsers;
the required read_file/read_json helpers are provided with their upstream semantics.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import os
import signal
import sys
from pathlib import Path


def read_file(path):
    with open(path) as handle:
        return handle.readlines()


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def official_namespace(root):
    tree = ast.parse((root / "src" / "scorer.py").read_text())
    names = {"handler", "listit", "get_basic_func_list", "calculate_ans", "calculate_win_score"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    namespace = dict(signal=signal, os=os, sys=sys, json=json, importlib=__import__("importlib"),
                     read_file=read_file, read_json=read_json)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "official_nestful_functions", "exec"), namespace)
    return namespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--indices", default=None)
    args = parser.parse_args()
    # Upstream tool modules can inspect sys.argv. Do not let audit flags become tool input.
    sys.argv[:] = ["nestful_gold_audit"]
    root = Path(args.root)
    rows = [json.loads(line) for line in (root / "data_v2/nestful_data.jsonl").open()]
    indices = list(range(len(rows))) if args.indices is None else [int(x) for x in args.indices.split(",")]
    official = official_namespace(root)
    failed, raised = [], []
    for index in indices:
        row = rows[index]
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                passed = official["calculate_win_score"](
                    row["output"], json.dumps(row["gold_answer"]), json.dumps(row["tools"]),
                    str(root / "data_v2/executable_functions"))
            if not passed:
                failed.append({"index": index, "id": row["sample_id"]})
        except Exception as exc:
            raised.append({"index": index, "id": row["sample_id"], "error": repr(exc)})
        finally:
            # Upstream leaves SIGALRM armed on successful return. Contain that side effect
            # between independent audit observations, without changing score logic.
            signal.alarm(0)
    print(json.dumps({"count": len(indices), "passed": len(indices)-len(failed)-len(raised),
                      "failed": failed, "raised": raised}, sort_keys=True))


if __name__ == "__main__":
    main()
