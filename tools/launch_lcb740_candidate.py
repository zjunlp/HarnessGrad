#!/usr/bin/env python3
"""One explicit /740 public smoke, never a driver or H0 launcher.

No mixed adapter import or dataset read. Tests must be a separately approved,
public-only file bound by its digest. No slicing a combined released test file.
The metadata approval digest must be supplied by trusted platform/operator custody,
not read from a candidate-controlled record. Missing attestation preserves the caveat.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.service.separated_stdio import PYTHON, namespace_command, read_regular, run_bounded, score
from eval.service.unix_model_relay import Relay
from tools.lcb740_identity import identity


def workspace_path(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("all launch artifacts must be inside the detached workspace")
    return resolved


def launch(*, agent: bytes, statement: bytes, tests: bytes, work_root: Path,
           model: str, index_record: bytes | None = None,
           approved_index_sha256: str | None = None, timeout=120, max_calls=4,
           max_tokens=512, max_total_tokens=2048, _connect=None):
    work_root = workspace_path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    if type(timeout) is not int or not 0 < timeout <= 120:
        raise ValueError("smoke timeout must be 1..120 seconds")
    if not isinstance(agent, bytes) or not 0 < len(agent) <= 1048576:
        raise ValueError("bounded agent entrypoint required")
    # Construct the receipt through admission even when called without the CLI;
    # a caller-provided receipt cannot promote an inferred row into an attested one.
    row_identity = identity(statement, index_record, approved_index_sha256)
    with tempfile.TemporaryDirectory(prefix="agent-", dir=work_root) as tmp:
        session = Path(tmp)
        actor, relay_dir = session / "actor", session / "relay"
        actor.mkdir()
        (actor / "task.md").write_bytes(statement)
        (actor / "agent.py").write_bytes(agent)
        with Relay(relay_dir, model=model, max_calls=max_calls, max_tokens=max_tokens,
                   max_total_tokens=max_total_tokens, _connect=_connect) as relay:
            argv = namespace_command(actor, [PYTHON, "-I", "-S", "agent.py"],
                                     writable=True, relay_dir=relay_dir)
            code, _, _, timed_out = run_bounded(argv, capture_dir=session, timeout=timeout,
                                               cpu=60, memory_mib=512)
        # All relay workers have quiesced and the socket is removed before scoring.
        if timed_out or code != 0:
            result = {"measured": False, "error": "agent timeout or failure"}
        else:
            try:
                solution = read_regular(actor / "solution.py")
            except (OSError, ValueError):
                result = {"measured": False, "error": "missing or unsafe solution.py"}
            else:
                result = score(solution, tests, work_root)
        result.update(task_id="livecodebench/740", question_id="abc360_a",
                      identity=row_identity, agent_sha256=hashlib.sha256(agent).hexdigest(),
                      agent_exit_code=code, agent_timed_out=timed_out, relay=relay.receipt(),
                      agent_network="none", smoke_only=True, h0_launched=False,
                      h0_admitted=False, tests_scope="separately supplied public-only")
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--public-tests", required=True)
    parser.add_argument("--public-tests-sha256", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--index-record")
    parser.add_argument("--approved-index-sha256")
    parser.add_argument("--max-calls", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--max-total-tokens", type=int, default=2048)
    args = parser.parse_args(argv)
    try:
        output = workspace_path(args.output)
        work_root = workspace_path(args.work_root)
        tests = read_regular(workspace_path(args.public_tests))
        if hashlib.sha256(tests).hexdigest() != args.public_tests_sha256:
            raise ValueError("public tests approval hash mismatch")
        statement = read_regular(ROOT / "data/datasets/livecodebench/abc360_a.public.md")
        metadata = read_regular(workspace_path(args.index_record)) if args.index_record else None
        result = launch(agent=read_regular(workspace_path(args.agent)), statement=statement,
                        tests=tests, work_root=work_root, model=args.model,
                        index_record=metadata, approved_index_sha256=args.approved_index_sha256,
                        max_calls=args.max_calls,
                        max_tokens=args.max_tokens, max_total_tokens=args.max_total_tokens)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Never follow an old actor/diagnostic symlink or accept a stale receipt.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"measured": result["measured"], "reward": result.get("reward"),
                      "row_attested": result["identity"]["row_attested"], "h0_launched": False}))
    return 0 if result["measured"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
