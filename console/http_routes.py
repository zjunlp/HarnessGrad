"""Named HTTP endpoint adapters, split by application capability.

Only parsing/status/wire-shape decisions live here. Services are supplied as
explicit endpoint capabilities, never imported from server or a world context.
"""
from __future__ import annotations
import pathlib
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs

UNHANDLED = object()

@dataclass(frozen=True)
class QueryCollectionGetEndpoints:
    all_rows: Callable
    api_compare: Callable
    api_coverage: Callable
    api_health: Callable


def query_collection_get(self, parts, query, raw_query, ports):
    if parts[1:] == ["health"]:
        return self._send(ports.api_health())

    if parts[1:] == ["runs"]:
        kind = (query.get("kind") or [""])[0]
        if kind not in ("", "study", "eval", "unknown"):
            return self._error("kind must be study, eval, unknown or empty", 400)
        rows = ports.all_rows()
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return self._send({"rows": rows, "count": len(rows)})

    if parts[1:] == ["compare"]:
        method = (query.get("method") or [""])[0].strip()
        dataset = (query.get("dataset") or [""])[0].strip()
        if method and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", method):
            return self._error("method must match [A-Za-z0-9_.-]{1,64}", 400)
        if dataset and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", dataset):
            return self._error("dataset must match [A-Za-z0-9_.-]{1,64}", 400)
        return self._send(ports.api_compare(method, dataset))

    if parts[1:] == ["coverage"]:
        return self._send(ports.api_coverage())

    return UNHANDLED


@dataclass(frozen=True)
class CandidateGetEndpoints:
    CANDIDATE_NAME: object
    harness_candidate_archive: Callable
    harness_candidate_file: Callable
    harness_candidates: Callable


def candidate_get(self, parts, query, raw_query, ports):
    if parts[1:] == ["harness", "candidates"]:
        return self._send(ports.harness_candidates())

    if parts[1:] == ["harness", "archive"]:
        code, blob = ports.harness_candidate_archive((query.get("candidate") or [""])[0])
        if blob is None:
            message = {404: "candidate not found", 400: "invalid or unsafe candidate archive (links, special files, or unsafe paths are not allowed)", 413: "candidate archive exceeds entry, expanded, or compressed size limits"}.get(code, "candidate archive unavailable")
            return self._error(message, code)
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{query["candidate"][0]}.tar.gz"')
        self.end_headers()
        return self.wfile.write(blob)

    if parts[1:] == ["harness", "file"]:
        name = (query.get("candidate") or [""])[0]
        rel = (query.get("path") or [""])[0]
        if not ports.CANDIDATE_NAME.fullmatch(name) or not rel:
            return self._error("candidate and path are required", 400)
        return self._send(ports.harness_candidate_file(name, rel))

    return UNHANDLED


@dataclass(frozen=True)
class RunHistoryGetEndpoints:
    _valid_history_range: Callable
    api_run: Callable
    run_compare: Callable
    run_history: Callable
    run_range_diff: Callable
    run_verdicts: Callable


def run_history_get(self, parts, query, raw_query, ports):
    if len(parts) == 4 and parts[1] == "runs" and parts[3] == "verdicts":
        raw = (query.get("round") or ["0"])[0]
        if not re.fullmatch(r"\d{1,9}", raw):
            return self._error("round must be a non-negative integer", 400)
        return self._send(ports.run_verdicts(parts[2], int(raw)))

    if len(parts) == 4 and parts[1] == "runs" and parts[3] == "compare":
        other = (query.get("with") or [""])[0]
        if not other:
            return self._error("with parameter is required", 400)
        try:
            ra = int((query.get("round") or ["0"])[0])
            rb = int((query.get("round_b") or ["0"])[0])
        except ValueError:
            return self._error("round must be integers", 400)
        return self._send(ports.run_compare(parts[2], other, ra, rb))

    if len(parts) == 4 and parts[1] == "runs" and parts[3] == "range-diff":
        first = (query.get("from") or [""])[0]
        last = (query.get("to") or [""])[0]
        if not (re.fullmatch(r"\d{1,9}", first) and re.fullmatch(r"\d{1,9}", last)):
            return self._error("from/to must be non-negative integers", 400)
        return self._send(ports.run_range_diff(parts[2], int(first), int(last)))

    if len(parts) == 4 and parts[1] == "runs" and parts[3] == "history":
        history_query = parse_qs(raw_query, keep_blank_values=True)
        to = (history_query.get("to") or ["0"])[0]
        since = (history_query.get("from") or ["1"])[0]
        if not (re.fullmatch(r"[0-9]{1,9}", to) and re.fullmatch(r"[0-9]{1,9}", since)):
            return self._error("from/to must be non-negative integers of at most 9 digits", 400)
        if not ports._valid_history_range(int(since), int(to)):
            return self._error("from/to must be ordered and span at most 1000 rounds", 400)
        return self._send(ports.run_history(parts[2], int(to), int(since)))

    if len(parts) == 3 and parts[1] == "runs":
        data = ports.api_run(parts[2])
        return self._send(data) if data else self._error("run not found", 404)

    return UNHANDLED


@dataclass(frozen=True)
class RoundGetEndpoints:
    api_file: Callable
    api_round: Callable
    api_trace: Callable
    api_traces: Callable
    find_run: Callable
    round_exists: Callable


def round_get(self, parts, query, raw_query, ports):
    if len(parts) == 5 and parts[1] == "runs" and parts[3] == "rounds":
        if not re.fullmatch(r"\d{1,9}", parts[4]):
            return self._error("round must be a non-negative integer", 400)
        run_path = ports.find_run(parts[2])
        if run_path and not ports.round_exists(run_path, int(parts[4])):
            return self._error(f"round {parts[4]} is not in this run", 400)
        data = ports.api_round(parts[2], int(parts[4]))
        return self._send(data) if data else self._error("run not found", 404)

    if (len(parts) == 6 and parts[1] == "runs" and parts[3] == "rounds"
            and parts[5] == "file"):
        if not re.fullmatch(r"\d{1,9}", parts[4]):
            return self._error("round must be a non-negative integer", 400)
        rel = (query.get("path") or [""])[0]
        if not rel:
            return self._error("path query parameter is required", 400)
        data = ports.api_file(parts[2], int(parts[4]), rel)
        return self._send(data) if data else self._error("file not found", 404)

    if len(parts) == 6 and parts[1] == "runs" and parts[3] == "rounds" and parts[5] == "traces":
        if not re.fullmatch(r"\d{1,9}", parts[4]):
            return self._error("round must be a non-negative integer", 400)
        run_path = ports.find_run(parts[2])
        if run_path and not ports.round_exists(run_path, int(parts[4])):
            return self._error(f"round {parts[4]} is not in this run", 400)
        data = ports.api_traces(parts[2], int(parts[4]))
        return self._send(data) if data else self._send({"error": "not found"}, 404)

    if len(parts) == 6 and parts[1] == "runs" and parts[3] == "rounds" and parts[5] == "trace":
        if not re.fullmatch(r"\d{1,9}", parts[4]):
            return self._error("round must be a non-negative integer", 400)
        name = (query.get("name") or [""])[0]
        if not name:
            return self._error("name query parameter is required", 400)
        data = ports.api_trace(parts[2], int(parts[4]), name)
        return self._send(data) if data else self._send({"error": "not found"}, 404)

    return UNHANDLED


@dataclass(frozen=True)
class ConfigurationGetEndpoints:
    launch_form: Callable
    settings_for_ui: Callable


def configuration_get(self, parts, query, raw_query, ports):
    if parts[1:] == ["launch", "form"]:
        return self._send(ports.launch_form())

    if parts[1:] == ["settings"]:
        return self._send(ports.settings_for_ui())

    return UNHANDLED


@dataclass(frozen=True)
class ArtifactGetEndpoints:
    api_adapters: Callable
    api_base: Callable
    api_base_file: Callable
    api_base_harness_file: Callable
    api_base_harnesses: Callable
    api_method: Callable
    api_method_file: Callable
    api_methods: Callable
    api_protocol: Callable


def artifact_get(self, parts, query, raw_query, ports):
    if parts[1:] == ["methods"]:
        return self._send({"rows": ports.api_methods()})

    if len(parts) == 3 and parts[1] == "methods":
        data = ports.api_method(parts[2])
        return self._send(data) if data else self._error("method not found", 404)

    if len(parts) == 4 and parts[1] == "methods" and parts[3] == "file":
        rel = (query.get("path") or [""])[0]
        if not rel:
            return self._error("path query parameter is required", 400)
        data = ports.api_method_file(parts[2], rel)
        return self._send(data) if data else self._error("file not found", 404)

    if parts[1:] == ["protocol"]:
        return self._send(ports.api_protocol())

    if parts[1:] == ["base"]:
        return self._send(ports.api_base())

    if parts[1:] == ["base", "file"]:
        rel = (query.get("path") or [""])[0]
        if not rel:
            return self._error("path query parameter is required", 400)
        data = ports.api_base_file(rel)
        return self._send(data) if data else self._error("file not found", 404)

    if parts[1:] == ["base-harnesses"]:
        return self._send(ports.api_base_harnesses())

    if parts[1:] == ["base-harness", "file"]:
        name = (query.get("name") or [""])[0].strip()
        rel = (query.get("path") or [""])[0]
        if not name:
            return self._error("name query parameter is required", 400)
        if not rel:
            return self._error("path query parameter is required", 400)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            return self._error("name must match [A-Za-z0-9_.-]{1,64}", 400)
        data = ports.api_base_harness_file(name, rel)
        return self._send(data) if data else self._error("file not found", 404)

    if parts[1:] == ["adapters"]:
        return self._send(ports.api_adapters())

    return UNHANDLED


@dataclass(frozen=True)
class AuthorGetEndpoints:
    author_bundles: Callable


def author_get(self, parts, query, raw_query, ports):
    if parts[1:] == ["author", "bundles"]:
        return self._send(ports.author_bundles())

    return UNHANDLED


def method_refusals_get(self, parts, query, raw_query, ports):
    if parts[1:] == ["settings", "test"]:
        return self._error("method-not-allowed", 405)

    if parts[1:] == ["launch", "preview"]:
        return self._error("method-not-allowed", 405)

    if parts[1:] == ["explain"]:
        return self._error("method-not-allowed", 405)

    return UNHANDLED


@dataclass(frozen=True)
class EvidencePostEndpoints:
    evidence_export: Callable


def evidence_post(self, route, url, ports):
    if route == 'api/evidence/export':
        if url.query:
            self.close_connection = True
            return self._error('bad-query', 400)
        body = self._json_body(strict=True)
        if body is None or set(body) != {'manifest'} or not isinstance(body['manifest'], dict):
            return self._error('invalid-manifest', 400)
        status, payload = ports.evidence_export(body['manifest'])
        return self._send(payload, status, disposition='attachment; filename="evidence-export.json"' if status == 200 else None)

    return UNHANDLED


@dataclass(frozen=True)
class LaunchPreviewPostEndpoints:
    launch_preview: Callable


def launch_preview_post(self, route, url, ports):
    if route == 'api/launch/preview':
        if url.query:
            self.close_connection = True
            return self._error('bad-query', 400)
        body = self._json_body()
        if not isinstance(body, dict):
            return self._error('bad-json', 400)
        fields = ('dataset', 'harness', 'rounds', 'jobs', 'trials', 'sampling',
                  'improver', 'improver_model', 'method', 'task_backend', 'task_model',
                  'task_base_url', 'meta_base_url', 'task_key_present', 'meta_key_present')
        form = {key: body[key] for key in fields if key in body}
        for key, value in form.items():
            if key in ('task_key_present', 'meta_key_present'):
                valid = isinstance(value, bool) or (isinstance(value, str) and value in ('true', 'false', '1', '0', ''))
            elif key in ('rounds', 'jobs', 'trials'):
                valid = isinstance(value, str) or type(value) is int
            else:
                valid = isinstance(value, str)
            if not valid:
                return self._error('bad-preview-fields', 400)
        return self._send(ports.launch_preview(form))

    return UNHANDLED


@dataclass(frozen=True)
class ExplainPostEndpoints:
    api_explain: Callable
    api_explain_range: Callable


def explain_post(self, route, url, ports):
    if route == 'api/explain':
        body = self._json_body()
        if body is None:
            return self._error('bad-json', 400)
        run_id = str(body.get('run') or '').strip()
        raw_round = str(body.get('round', '')).strip()
        raw_from = str(body.get('from', '')).strip()
        raw_to = str(body.get('to', '')).strip()
        if not run_id:
            return self._error('run is required', 400)
        force = str(body.get('force', '0')).strip().lower() not in ('', '0', 'false', 'no')
        try:
            if raw_from or raw_to:
                if not (re.fullmatch(r'\d{1,9}', raw_from) and re.fullmatch(r'\d{1,9}', raw_to)):
                    return self._error('from and to must be non-negative integers', 400)
                status, payload = ports.api_explain_range(run_id, int(raw_from), int(raw_to), force=force)
            else:
                if not re.fullmatch(r'\d{1,9}', raw_round):
                    return self._error('round must be a non-negative integer', 400)
                status, payload = ports.api_explain(run_id, int(raw_round), force)
            return self._send(payload, status)
        except (ValueError, OSError) as exc:
            return self._send({'error': str(exc)}, 500)

    return UNHANDLED


@dataclass(frozen=True)
class CandidatePostEndpoints:
    harness_candidate_from_round: Callable
    harness_candidate_save: Callable
    harness_candidate_trial: Callable


def candidate_post(self, route, url, ports):
    if route == "api/harness/save":
        body = self._json_body()
        if body is None:
            return self._send({"error": "bad-json"}, 400)
        code, payload = ports.harness_candidate_save(str(body.get("name") or ""),
                                               body.get("files") or {},
                                               source=str(body.get("from") or ""))
        return self._send(payload, code)

    if route == "api/harness/from-round":
        body = self._json_body()
        if body is None:
            return self._send({"error": "bad-json"}, 400)
        try:
            round_no = int(body.get("round"))
        except (TypeError, ValueError):
            return self._send({"error": "bad-round"}, 400)
        code, payload = ports.harness_candidate_from_round(
            str(body.get("run") or ""), round_no, replay=bool(body.get("replay")))
        return self._send(payload, code)

    if route == "api/harness/trial":
        body = self._json_body()
        if body is None:
            return self._send({"error": "bad-json"}, 400)
        code, payload = ports.harness_candidate_trial(str(body.get("name") or ""),
                                                str(body.get("dataset") or
                                                    "authored_example_fix_bug"))
        return self._send(payload, code)

    return UNHANDLED


@dataclass(frozen=True)
class WorkbenchPostEndpoints:
    valid_workbench_candidate_name: Callable
    read_selection: Callable
    workbench_edit: Callable


def workbench_post(self, route, url, ports):
    if route == "api/workbench/edit":
        body = self._json_body()
        if body is None:
            return self._send({"error": "bad-json"}, 400)
        name = str(body.get("name") or "")
        if not ports.valid_workbench_candidate_name(name):
            return self._send({"error": "candidate-not-found"}, 404)
        selection = ""
        sel = body.get("selection") or {}
        if sel:
            if not isinstance(sel, dict):
                return self._send({"error": "bad-selection", "detail": "invalid selection"}, 400)
            try:
                selection = ports.read_selection(name, str(sel.get("path") or ""),
                                           int(sel.get("from") or 1),
                                           int(sel.get("to") or 1))
            except (TypeError, ValueError, OSError) as exc:
                return self._send({"error": "bad-selection", "detail": str(exc)}, 400)
        code, payload = ports.workbench_edit(str(body.get("name") or ""),
                                       str(body.get("instruction") or ""), selection,
                                       str(body.get("kind") or "codex"),
                                       new_thread=bool(body.get("new_thread")))
        return self._send(payload, code)

    return UNHANDLED


@dataclass(frozen=True)
class AuthorPostEndpoints:
    IMPORT_MAX_BYTES: object
    author_draft: Callable
    author_import: Callable


def author_post(self, route, url, ports):
    if route == "api/author/draft":
        body = self._json_body()
        if body is None:
            return self._send({"error": "bad-json"}, 400)
        code, payload = ports.author_draft(str(body.get("note") or ""),
                                     str(body.get("materials") or ""))
        return self._send(payload, code)

    if route == "api/author/import":
        slug = (parse_qs(url.query).get("slug") or [""])[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send({"error": "bad-length"}, 400)
        if length <= 0 or length > ports.IMPORT_MAX_BYTES:
            return self._send({"error": "archive-too-large", "limit": ports.IMPORT_MAX_BYTES}, 413)
        import tempfile
        raw = self.rfile.read(length)
        if len(raw) != length:
            return self._error('bad-length', 400)
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as fh:
            fh.write(raw)
            archive = pathlib.Path(fh.name)
        try:
            code, payload = ports.author_import(archive, slug)
        finally:
            archive.unlink(missing_ok=True)
        return self._send(payload, code)

    return UNHANDLED


@dataclass(frozen=True)
class SettingsPostEndpoints:
    SETTINGS_SIDES: object
    load_settings: Callable
    probe_endpoint: Callable
    save_settings: Callable
    settings_for_ui: Callable


def settings_post(self, route, url, ports):
    if route == "api/settings":
        body = self._json_body()
        if body is None:
            return self._send({"error": "bad-json"}, 400)
        try:
            ports.save_settings(body)
        except ValueError as exc:
            return self._send({'error': 'settings-key-required', 'detail': str(exc)}, 400)
        return self._send(ports.settings_for_ui())

    if route == "api/settings/test":
        body = self._json_body()
        if body is None:
            return self._error('bad-json', 400)
        side = str(body.get("side") or "task")
        if side not in ports.SETTINGS_SIDES:
            return self._send({"error": "unknown-side"}, 400)
        current = ports.load_settings().get(side, {})
        base_url = str(body.get("base_url") or current.get("base_url") or "")
        api_key = str(body.get("api_key") or "")
        if not api_key:
            if base_url != str(current.get("base_url") or ""):
                return self._error("api-key-required-for-different-base-url", 400)
            api_key = str(current.get("api_key") or "")
        return self._send(ports.probe_endpoint(base_url, api_key))

    return UNHANDLED


@dataclass(frozen=True)
class LaunchStartPostEndpoints:
    api_launch: Callable


def launch_start_post(self, route, url, ports):
    if route != "api/runs":
        return self._send({"error": "unknown endpoint"}, 404)

    form = self._json_body()

    if form is None:
        return self._send({"error": "bad-json"}, 400)

    try:
        code, payload = ports.api_launch(form)
    except OSError as exc:
        return self._send({"error": "launch-failed", "detail": str(exc)}, 500)

    return self._send(payload, code)



@dataclass(frozen=True)
class WorkbenchCapabilitiesEndpoints:
    capabilities: Callable


def workbench_capabilities_get(self, parts, query, raw_query, ports):
    if parts[1:] == ['workbench', 'capabilities']:
        if raw_query:
            return self._error('bad-query', 400)
        return self._send(ports.capabilities())
    return UNHANDLED


def dispatch_get(handler, parts, query, raw_query, endpoints):
    for operation, ports in endpoints:
        result = operation(handler, parts, query, raw_query, ports)
        if result is not UNHANDLED:
            return result
    return handler._error('unknown endpoint', 404)


def dispatch_post(handler, route, url, endpoints):
    for operation, ports in endpoints:
        result = operation(handler, route, url, ports)
        if result is not UNHANDLED:
            return result
    return handler._send({'error': 'unknown endpoint'}, 404)
