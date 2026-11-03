#!/usr/bin/env python3
"""Contract-only submission service for submit.html UAT.

Standard library only. It accepts any archive bytes, but uses the uploaded filename to
exercise the frozen rejection vocabulary: name a file `reject-unsafe-path.tar.gz`, for
example. The service is deliberately deterministic and never executes the archive.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from email import policy
from email.parser import BytesParser
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import parse_qs, unquote, urlparse

STEP_SECONDS = float(os.environ.get("HG_MOCK_SUBMISSION_STEP_SECONDS", "1"))
MAX_UPLOAD = 20 * 1024 * 1024 + 64 * 1024

SCENARIOS = [
    {"name": "demo-valid", "dataset": "demo", "split": "declared", "side": "train", "pool": "train", "sampling": "all", "rounds": 0, "trials": 1, "tasks": 4, "available": True, "reason": "ok", "expected_minutes": 1, "note": "四道本地 mock 任务，完整演示 queued → running → scored。"},
    {"name": "demo-invalid", "dataset": "demo", "split": "declared", "side": "eval", "pool": "dev", "sampling": "all", "rounds": 0, "trials": 1, "tasks": 3, "available": True, "reason": "ok", "expected_minutes": 1, "note": "出分结果包含 invalid，用来检查分母和警告展示。"},
    {"name": "demo-unavailable", "dataset": "terminal_bench", "split": "declared", "side": "eval", "pool": "exam", "sampling": "all", "rounds": 0, "trials": 1, "tasks": None, "available": False, "reason": "dataset-not-provisioned", "expected_minutes": None, "note": "演示不可用场景；服务没有准备该数据集。"},
]
SCENARIO_MAP = {item["name"]: item for item in SCENARIOS}
# Every frozen §6 code that can be represented by an uploaded archive is available
# through a filename such as `reject-unsafe-path.tar.gz`.  The remaining service
# and query codes are exercised by their ordinary mock routes or `/mock/cases`.
REJECTION_CODES = {
    "bad-archive", "payload-too-large", "too-many-entries", "archive-too-large",
    "unsafe-path", "unsafe-entry", "ambiguous-root", "manifest-missing",
    "manifest-invalid", "entrypoint-missing", "backend-unsupported", "install-missing",
    "env-kind-unsupported", "smoke-failed", "bad-multipart", "unknown-scenario",
    "scenario-not-available", "duplicate-submission", "queue-full", "running",
    "not-started", "service-not-ready",
}


def now_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts or time.time()))


def error(code: str, detail: str, status: int = 400, **extra):
    body = {"error": {"code": code, "detail": detail, **extra}, "api_version": "1"}
    return status, body


def checked_query(raw: str, allowed: set[str]) -> dict[str, str]:
    """Validate the v1 query boundary: no unknown or repeated names."""
    try:
        parsed = parse_qs(raw, keep_blank_values=True, strict_parsing=bool(raw))
    except ValueError as exc:
        raise ValueError("query is not valid key=value data") from exc
    unknown = set(parsed) - allowed
    if unknown:
        raise ValueError(f"unknown query field: {sorted(unknown)[0]}")
    repeated = [key for key, values in parsed.items() if len(values) != 1]
    if repeated:
        raise ValueError(f"query field repeated: {repeated[0]}")
    return {key: values[0] for key, values in parsed.items()}


def query_error(detail: str, field: str = ""):
    status, body = error("bad-query", detail, 400, **({"field": field} if field else {}))
    return status, body


def parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict[str, str], tuple[str, bytes] | None]:
    content_type = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_UPLOAD + 65536 or not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("request must be multipart/form-data with one archive")
    raw = handler.rfile.read(length)
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
    msg = BytesParser(policy=policy.default).parsebytes(header)
    fields: dict[str, str] = {}
    archive = None
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename is not None and name == "archive":
            archive = (filename, data)
        elif name:
            fields[name] = data.decode("utf-8", "replace")
    return fields, archive


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}
        self.by_key: dict[tuple[str, str], str] = {}

    def create(self, fields: dict[str, str], archive: tuple[str, bytes]):
        filename, data = archive
        scenario = fields.get("scenario", "")
        if scenario not in SCENARIO_MAP:
            return error("unknown-scenario", f"unknown scenario {scenario!r}", 400)
        if not data:
            return error("bad-multipart", "archive is empty", 400, field="archive")
        stem = filename.lower()
        named_rejection = next((code for code in REJECTION_CODES if f"reject-{code}" in stem), None)
        if named_rejection in ("payload-too-large", "archive-too-large"):
            return error(named_rejection, f"mock rejection for {named_rejection}", 413, field="archive")
        if len(data) > 20 * 1024 * 1024:
            return error("payload-too-large", "compressed archive exceeds 20 MiB", 413, field="archive")
        if not filename.lower().endswith((".tar.gz", ".tgz", ".tar", ".zip")):
            return error("bad-archive", "expected tar.gz, tar or zip", 400, field="archive")
        label = fields.get("label", "")
        contact = fields.get("contact", "")
        if len(label) > 64 or (label and not __import__("re").fullmatch(r"[A-Za-z0-9._-]+", label)):
            return error("bad-multipart", "label must be <=64 characters [A-Za-z0-9._-]", 400, field="label")
        if len(contact) > 200:
            return error("bad-multipart", "contact must be <=200 characters", 400, field="contact")
        archive_sha = hashlib.sha256(data).hexdigest()
        key = (archive_sha, scenario)
        with self.lock:
            if key in self.by_key:
                old = self.by_key[key]
                return error("duplicate-submission", "the same archive and scenario already exist", 409, existing_submission_id=old)
            if not SCENARIO_MAP[scenario]["available"]:
                return error("scenario-not-available", SCENARIO_MAP[scenario]["note"], 409)
            rejection = named_rejection
            if rejection:
                detail = {"unsafe-path": "entry ../agent.py escapes the archive root", "manifest-invalid": "harness.json field version is missing", "smoke-failed": "smoke did not write answer.txt"}.get(rejection, f"mock rejection for {rejection}")
                code_status = 413 if rejection in ("payload-too-large", "archive-too-large") else 400
                return error(rejection, detail, code_status, field="archive")
            ident = f"sub-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
            item = {"submission_id": ident, "run_id": ident, "scenario": scenario, "filename": filename, "archive_sha256": archive_sha, "tree_sha256": "mock:" + hashlib.sha256(data + b"\0mock-tree").hexdigest(), "created_at": now_iso(), "created_ts": time.time(), "status": "queued", "label": fields.get("label") or None, "contact": fields.get("contact") or None, "failed": scenario == "demo-failed" or "failed" in stem, "log": "mock driver started\n", "result": None}
        self.items[ident] = item; self.by_key[key] = ident
        return 202, {"api_version": "1", "submission_id": ident, "run_id": ident, "status": "queued", "scenario": scenario, "archive_sha256": archive_sha, "tree_sha256": item["tree_sha256"], "created_at": item["created_at"], "queue_position": 1}

    def view(self, ident: str):
        with self.lock:
            item = self.items.get(ident)
            if not item:
                return None
            elapsed = max(0, time.time() - item["created_ts"])
            steps = int(elapsed // max(STEP_SECONDS, 0.05))
            # POST returns queued. A later GET never regresses to validating:
            # queued -> running -> scored/failed is monotonic. `validating` is
            # documented only as a pre-acceptance fixture, not a live submission.
            if item["failed"] and steps >= 2:
                item["status"] = "failed"
            elif steps >= 2:
                item["status"] = "scored"
            elif steps >= 1:
                item["status"] = "running"
            else:
                item["status"] = "queued"
            scenario = SCENARIO_MAP[item["scenario"]]
            detail = {"api_version": "1", "submission_id": ident, "status": item["status"], "scenario": scenario, "harness": {"name": "mock-harness", "version": "0.1.0", "entrypoint": "agent.py", "declared_env_kinds": ["files"], "tree_sha256": item["tree_sha256"]}, "archive_sha256": item["archive_sha256"], "created_at": item["created_at"], "queued_at": item["created_at"], "started_at": now_iso(item["created_ts"] + STEP_SECONDS) if steps >= 1 else None, "scored_at": now_iso() if item["status"] == "scored" else None, "queue_position": 1 if item["status"] == "queued" else None, "run_id": ident, "result": None, "evidence": {"run_dir": f"runs/{ident}/", "curve": f"runs/{ident}/curve.jsonl", "per_task": f"runs/{ident}/verdicts/", "log": f"/api/submissions/{ident}/log", "receipt": f"submissions/{ident}/receipt.json"}, "error": None}
            if item["status"] == "scored": detail["result"] = result_for(item["scenario"])
            if item["status"] == "failed": detail["error"] = {"code": "sandbox-unavailable", "detail": "mock sandbox unavailable; no score was produced"}
            return detail


def result_for(scenario: str):
    invalid = scenario == "demo-invalid"
    pools = {"train": {"n": 4, "measured": 4, "invalid": 0, "harness_failed": 0, "score": 0.5}, "dev": {"n": 3, "measured": 0 if invalid else 3, "invalid": 3 if invalid else 0, "harness_failed": 0, "score": None if invalid else 0.667}, "exam": {"n": 2, "measured": 2, "invalid": 0, "harness_failed": 0, "score": 0.5}}
    warnings = ["dev: 3 task invalid，未计入 score 分母。"] if invalid else []
    return {"pools": pools, "warnings": warnings, "identity": {"platform_content_sha": "mock-platform", "agent_model": "mock-model", "agent_backend": "mock", "harness_sha": "mock-tree", "framework_version": "0.1.0"}}


def tasks_for(scenario: str):
    result = result_for(scenario)
    tasks = []
    for pool, summary in result["pools"].items():
        for index in range(summary["n"]):
            invalid = index >= summary["measured"]
            score = None if invalid else (1.0 if index % 2 == 0 else 0.0)
            tasks.append({"task_id": f"mock/{pool}/{index}", "pool": pool, "trial": 1,
                          "name": f"mock-{pool}-{index}.jsonl", "score": score,
                          "solved": score == 1.0 if score is not None else False,
                          "detail": "invalid: excluded from score denominator" if invalid else "mock measured task"})
    return tasks


def run_detail(ident: str, item: dict):
    result = result_for(item["scenario"])
    return {"row": {"id": ident, "dataset": item["scenario"], "pool": "mock", "status": item["status"], "rounds": 1,
                     "last": None, "invalid": sum(v["invalid"] for v in result["pools"].values())},
            "rounds": [{"round": 0, "tokens": None, "calls": None, "wall_s": None,
                        "n_scored": sum(v["measured"] for v in result["pools"].values()),
                        "n_invalid": sum(v["invalid"] for v in result["pools"].values()),
                        "n_harness_failed": 0}],
            "meta": {"dataset": item["scenario"], "harness": "mock-harness", "model": "mock-model"}}


def trace_detail(ident: str, item: dict):
    task_rows = tasks_for(item["scenario"])
    return {"run": ident, "round": 0, "count": len(task_rows), "tasks": task_rows}


STORE = Store()


class Handler(BaseHTTPRequestHandler):
    server_version = "HarnessGradMock/1"
    def log_message(self, fmt, *args):
        return
    def send_json(self, status: int, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self._cors(); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def send_text(self, status: int, body: str):
        raw = body.encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "text/plain; charset=utf-8"); self._cors(); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _cors(self):
        # Mock-only CORS for the documented console:4470 -> mock:4471 setup.
        # The actual service stays same-origin and applies its stricter frozen Origin check.
        origin = self.headers.get("Origin", "")
        try:
            parsed = urlparse(origin)
            address = ipaddress.ip_address(parsed.hostname or "")
            if parsed.scheme == "http" and address.is_loopback and parsed.port:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        except ValueError:
            pass
    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        if path == "/mock/cases":
            if url.query:
                return self.send_json(*query_error("mock/cases does not accept query fields", next(iter(parse_qs(url.query, keep_blank_values=True)), "")))
            return self.send_json(200, {"api_version": "1", "note": "Filename fixtures only; no upload is executed.", "rejection_filenames": sorted(f"reject-{code}.tar.gz" for code in REJECTION_CODES), "failed_filename": "demo-failed.tar.gz", "unavailable_scenario": "demo-unavailable"})
        if path == "/api/scenarios":
            if url.query:
                return self.send_json(*query_error("scenarios does not accept query fields", next(iter(parse_qs(url.query, keep_blank_values=True)), "")))
            return self.send_json(200, {"api_version": "1", "items": SCENARIOS})
        if path == "/api/submissions":
            try:
                params = checked_query(url.query, {"limit", "cursor"})
                limit_raw = params.get("limit", "50")
                if not re.fullmatch(r"[0-9]+", limit_raw):
                    return self.send_json(*query_error("limit must be an integer in 1..200", "limit"))
                limit = int(limit_raw)
                if not 1 <= limit <= 200:
                    return self.send_json(*query_error("limit must be an integer in 1..200", "limit"))
            except ValueError as exc:
                detail = str(exc)
                field = detail.partition(":")[0].replace("unknown query field", "").replace("query field repeated", "").strip()
                return self.send_json(*query_error(detail, field))
            items = []
            ordered = sorted(STORE.items.values(), key=lambda row: (row["created_ts"], row["submission_id"]), reverse=True)
            if "cursor" in params:
                cursor_ids = [row["submission_id"] for row in ordered]
                if params["cursor"] not in cursor_ids:
                    return self.send_json(*query_error("cursor does not identify a visible submission", "cursor"))
                ordered = ordered[cursor_ids.index(params["cursor"]) + 1:]
            for item in ordered[:limit]:
                # Advance the deterministic mock state before exposing a list row.
                STORE.view(item["submission_id"])
                row = {k: item[k] for k in ("submission_id", "scenario", "label", "status", "created_at")}
                row.update(score=None, pools={})
                if item["status"] == "scored":
                    result = result_for(item["scenario"])
                    row["pools"] = {name: pool["score"] for name, pool in result["pools"].items() if pool.get("score") is not None}
                items.append(row)
            more = len(ordered) > limit
            return self.send_json(200, {"api_version": "1", "items": items, "next_cursor": items[-1]["submission_id"] if more and items else None})
        prefix = "/api/submissions/"
        if path.startswith(prefix):
            tail = path[len(prefix):]
            if tail.endswith("/log"):
                try:
                    params = checked_query(url.query, {"tail"})
                    raw_tail = params.get("tail", "20000")
                    if not re.fullmatch(r"[0-9]+", raw_tail) or not 1 <= int(raw_tail) <= 200000:
                        return self.send_json(*query_error("tail must be an integer in 1..200000", "tail"))
                    tail_bytes = int(raw_tail)
                except ValueError as exc:
                    detail = str(exc)
                    field = detail.partition(":")[0].replace("unknown query field", "").replace("query field repeated", "").strip()
                    return self.send_json(*query_error(detail, field))
                ident = tail[:-4].rstrip("/"); item = STORE.items.get(ident)
                if not item: return self.send_json(*error("submission-not-found", "unknown submission", 404))
                status = STORE.view(ident)["status"]
                if status == "validating": return self.send_json(*error("not-started", "driver log is readable only after queueing", 409))
                body = item["log"] + f"status={status}\n"
                return self.send_text(200, body.encode("utf-8")[-tail_bytes:].decode("utf-8", "replace"))
            if url.query:
                try:
                    params = checked_query(url.query, set())
                except ValueError as exc:
                    return self.send_json(*query_error(str(exc)))
            ident = tail.rstrip("/"); detail = STORE.view(ident)
            if not detail: return self.send_json(*error("submission-not-found", "unknown submission", 404))
            return self.send_json(200, detail)
        if path.startswith("/api/runs/"):
            parts = path.strip("/").split("/")
            ident = parts[2] if len(parts) > 2 else ""
            item = STORE.items.get(ident)
            if not item or item["status"] != "scored":
                return self.send_json(404, {"api_version": "1", "error": {"code": "submission-not-found", "detail": "mock run is not scored"}})
            if len(parts) == 3:
                return self.send_json(200, run_detail(ident, item))
            if len(parts) >= 5 and parts[3] == "rounds" and parts[4] == "0":
                if len(parts) == 6 and parts[5] == "traces":
                    return self.send_json(200, trace_detail(ident, item))
                if len(parts) == 6 and parts[5] == "trace":
                    query = parse_qs(urlparse(self.path).query)
                    name = (query.get("name") or [""])[0]
                    task = next((row for row in tasks_for(item["scenario"]) if row["name"] == name), None)
                    if not task:
                        return self.send_json(404, {"api_version": "1", "error": {"code": "submission-not-found", "detail": "unknown mock trace"}})
                    return self.send_json(200, {"run": ident, "round": 0, "name": name, "events": [{"reply": "mock task turn"}, {"command": "printf mock", "output": task["detail"]}]})
            return self.send_json(404, {"api_version": "1", "error": {"code": "submission-not-found", "detail": "unknown mock run route"}})
        return self.send_json(404, {"api_version": "1", "error": {"code": "submission-not-found", "detail": "unknown endpoint"}})
    def do_POST(self):
        if urlparse(self.path).path != "/api/submissions": return self.send_json(404, {"api_version": "1", "error": {"code": "submission-not-found", "detail": "unknown endpoint"}})
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
            return self.send_json(*error("bad-host", "mock accepts only its loopback host", 400))
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urlparse(origin); address = ipaddress.ip_address(parsed.hostname or "")
                if parsed.scheme != "http" or not address.is_loopback or not parsed.port:
                    return self.send_json(*error("origin-forbidden", "mock accepts loopback browser origins only", 403))
            except ValueError:
                return self.send_json(*error("origin-forbidden", "mock accepts loopback browser origins only", 403))
        try: fields, archive = parse_multipart(self)
        except Exception as exc: return self.send_json(*error("bad-multipart", str(exc), 400))
        if not fields.get("scenario") or archive is None: return self.send_json(*error("bad-multipart", "archive and scenario are required", 400))
        return self.send_json(*STORE.create(fields, archive))


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--port", type=int, default=4471); parser.add_argument("--host", default="127.0.0.1"); args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"mock submissions on http://{args.host}:{args.port} (step={STEP_SECONDS}s)", flush=True)
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally: httpd.server_close()


if __name__ == "__main__": main()
