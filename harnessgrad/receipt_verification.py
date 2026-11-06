"""Bounded offline reads of run receipts, without loading tasks or execution code.

Hashes identify observed bytes, not authenticity. Findings use fixed codes;
exception text can contain private task material or credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from harnessgrad import evidence
from harnessgrad.store.schema import check_point, check_run_meta

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def safe_id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()


def parse_json(data):
    """Reject duplicate keys; allow historical NaN confidence bounds on input.

    Numeric claims are checked separately. NaN is never an exported number.
    """
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate_json_key")
            obj[key] = value
        return obj
    return json.loads(data, object_pairs_hook=unique)


def _directory(path, *, parent=None):
    fd = os.dup(parent) if parent is not None else os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    parts = Path(path).parts if parent is not None else Path(os.path.abspath(path)).parts[1:]
    try:
        for part in parts:
            if part in (".", ".."):
                raise ValueError("unsafe_directory")
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


class ReceiptLimitExceeded(Exception):
    """A configured aggregate receipt or inventory limit was exceeded."""


def _read_file(directory, name, limit, budget=None):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("unsafe_receipt")
        if before.st_size > limit:
            if budget is not None:
                raise ReceiptLimitExceeded()
            raise ValueError("receipt_too_large")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(min(limit, budget[0]) + 1 if budget is not None else limit + 1)
        if budget is not None:
            budget[0] -= len(data)
            if budget[0] < 0:
                raise ReceiptLimitExceeded()
        after = os.fstat(fd)
        if len(data) > limit:
            if budget is not None:
                raise ReceiptLimitExceeded()
            raise ValueError("receipt_too_large")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("receipt_changed")
        return data
    finally:
        os.close(fd)


def measurement_problems(point):
    """Check structure without re-running a scorer; preserve partial coverage."""
    try:
        problems, _ = check_point(point)
        if problems:
            return ["invalid_point_schema"]
        if type(point.get("round")) is not int or point["round"] < 0:
            return ["invalid_round"]
        if not evidence.finite_number(point.get("score")):
            return ["nonfinite_score"]
        tasks = point.get("per_task")
        if (not isinstance(tasks, dict) or not tasks
                or type(point.get("n_scored")) is not int or point["n_scored"] != len(tasks)
                or any(not evidence.finite_number(v) for v in tasks.values())):
            return ["invalid_task_scores"]
        if point.get("startup_failure") or point.get("tampered") or point.get("unmeasured"):
            return ["unusable_measurement"]
        for key in ("identity", "sampling", "env", "cost"):
            if not isinstance(point.get(key), dict):
                return ["invalid_point_schema"]
        if not isinstance(point['identity'].get('harness_sha'), str) or not point['identity']['harness_sha']:
            return ['invalid_harness_identity']
    except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
        return ["invalid_point_schema"]
    return []


class ReceiptStore:
    """Read-only snapshot of immediate entries under an explicit run root.

    Unsafe/unreadable entries are inventoried, never followed or silently dropped.
    Recheck detects changes before export, but is not a live database transaction.
    """
    def __init__(self, root, max_bytes=DEFAULT_MAX_BYTES, *, max_runs=None, max_total_bytes=None):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise evidence.EvidenceError("invalid_receipt_limit")
        if max_runs is not None and (type(max_runs) is not int or max_runs <= 0):
            raise evidence.EvidenceError("invalid_run_limit")
        if max_total_bytes is not None and (type(max_total_bytes) is not int or max_total_bytes <= 0):
            raise evidence.EvidenceError("invalid_total_receipt_limit")
        self.limit = max_bytes
        self.max_runs = max_runs
        self.budget = [max_total_bytes] if max_total_bytes is not None else None
        self.fd = _directory(root)
        self.files = {}
        self.runs = {}
        try:
            self.names = self._names()
        except BaseException:
            self.close()
            raise

    def _names(self):
        names = []
        with os.scandir(self.fd) as entries:
            count = 0
            for entry in entries:
                count += 1
                if self.max_runs is not None and count > self.max_runs:
                    raise ReceiptLimitExceeded()
                if not entry.is_file(follow_symlinks=False):
                    names.append(entry.name)
                    if self.max_runs is not None and len(names) > self.max_runs:
                        raise ReceiptLimitExceeded()
        return sorted(names)

    def close(self):
        os.close(self.fd)

    def read(self, run_id, relative):
        if not safe_id(run_id):
            raise ValueError("unsafe_run_id")
        parts = Path(relative).parts
        if not parts or any(not safe_id(x) for x in parts):
            raise ValueError("unsafe_receipt_path")
        try:
            fd = _directory(Path(run_id).joinpath(*parts[:-1]), parent=self.fd)
        except FileNotFoundError:
            self.files[(run_id, relative)] = None
            return None
        try:
            try:
                data = _read_file(fd, parts[-1], self.limit, self.budget)
            except FileNotFoundError:
                data = None
        finally:
            os.close(fd)
        self.files[(run_id, relative)] = None if data is None else digest(data)
        return data

    def load_run(self, run_id):
        if run_id in self.runs:
            return self.runs[run_id]
        run = {"run_id": run_id, "meta": None, "points": {}, "problems": [],
               "missing": [], "hashes": {}}
        self.runs[run_id] = run
        for filename in ("run_meta.json", "curve.jsonl"):
            try:
                data = self.read(run_id, filename)
                if data is None:
                    run["missing"].append(filename)
                    continue
                run["hashes"][filename] = digest(data)
                if filename == "run_meta.json":
                    meta = parse_json(data)
                    if not isinstance(meta, dict) or meta.get("run_id") != run_id:
                        raise ValueError("metadata_identity")
                    run["meta"] = meta
                else:
                    points = [parse_json(line) for line in data.splitlines() if line.strip()]
                    for point in points:
                        if not isinstance(point, dict) or point.get("run_id") != run_id:
                            raise ValueError("point_identity")
                        number = point.get("round")
                        if type(number) is not int or number < 0 or number in run["points"]:
                            raise ValueError("duplicate_or_invalid_round")
                        run["points"][number] = point
            except (OSError, ValueError, TypeError, RecursionError):
                run["problems"].append("unreadable_or_invalid_" + filename)
        return run

    def measured(self, run):
        if run["problems"] or not run["meta"] or "curve.jsonl" in run["missing"]:
            return []
        problems, _ = check_run_meta(run["meta"])
        if any("platform" not in p and "improver" not in p for p in problems):
            return []
        if any(not isinstance(run['meta'].get(key), str) for key in
               ('run_id', 'agent_model', 'agent_backend', 'harness', 'dataset', 'workspace')):
            return []
        return [r for r, point in sorted(run["points"].items()) if not measurement_problems(point)]

    def recheck(self):
        if self._names() != self.names:
            raise evidence.EvidenceError("run_inventory_changed")
        observed = dict(self.files)
        for (run_id, name), expected in observed.items():
            data = self.read(run_id, name)
            if (None if data is None else digest(data)) != expected:
                raise evidence.EvidenceError("receipt_changed_during_export")


def claim_states(manifest):
    """Never guess a baseline round or choose a best score."""
    yield "submission", manifest.get("submission") or {}
    yield "selection.reported", (manifest.get("selection") or {}).get("reported") or {}
    for i, request in enumerate(manifest.get("requests") or []):
        if request.get("result"):
            yield f"requests[{i}].result", request["result"]
        if request.get("vs_baseline"):
            base = request["vs_baseline"]
            yield f"requests[{i}].vs_baseline", {"run_id": base.get("baseline_run"),
                                                 "round": base.get("baseline_round")}


def verify_in_store(manifest, store):
    findings = []
    attempts = {a["run_id"]: a for a in manifest["attempts"]}
    for location, claim in claim_states(manifest):
        run_id, number = claim.get("run_id"), claim.get("round")
        code = None
        if not safe_id(run_id):
            code = "unsafe_run_id"
        elif type(number) is not int or number < 0:
            code = "missing_or_invalid_round"
        elif number not in attempts.get(run_id, {}).get("rounds", []):
            code = "round_not_in_ledger"
        else:
            run = store.load_run(run_id)
            point = run["points"].get(number)
            if number not in store.measured(run):
                code = "missing_or_invalid_measurement"
            elif "metric" in claim or "value" in claim:
                if claim.get("metric") != "score" or not evidence.finite_number(claim.get("value")):
                    code = "unsupported_or_invalid_metric"
                elif claim["value"] != point["score"]:
                    code = "value_mismatch"
        if code:
            findings.append({"code": code, "location": location})
    return findings


def verify_receipts(manifest, run_root):
    """Check score claims against source receipts, returning no private contents."""
    try:
        if evidence.check_manifest(manifest)[0]:
            return {"valid": False, "findings": [{"code": "invalid_manifest"}], "hashes": {}}
        store = ReceiptStore(run_root)
        try:
            findings = verify_in_store(manifest, store)
            hashes = {name: run["hashes"] for name, run in store.runs.items()}
            store.recheck()
            return {"valid": not findings, "findings": findings, "hashes": hashes}
        finally:
            store.close()
    except (OSError, ValueError, TypeError, RecursionError):
        return {"valid": False, "findings": [{"code": "unreadable_or_changing_receipts"}], "hashes": {}}
