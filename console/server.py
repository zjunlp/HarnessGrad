#!/usr/bin/env python3
"""Console configuration, service composition and loopback HTTP startup.

Public functions retain compatibility delegates to their canonical service
owners. Request-time ports carry configured roots, authorization, settings and
process capabilities; query and explanation caches each have one app owner.

Observation reads stored records without inventing missing scores or costs.
Mutations use explicit launch, candidate, workbench, author and submission APIs.
The listener is restricted to 127.0.0.1. Existing receipt roots are selected by
HG_CONSOLE_RUNS and are never discovered, created or migrated implicitly.

Supported entrypoints: python3 console/server.py and python3 -m console.server.
Arbitrary-name importlib loading is retained for installed and test consumers.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import http.client
import importlib.util
import importlib.metadata
import json
import os
import pathlib
import subprocess
import sys
import re
import shlex
import stat
import shutil
import threading
import time
import urllib.error
import urllib.request
import ipaddress
import tempfile
import sysconfig
from email import policy as email_policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

# Keep direct-file CLI execution and package imports on the same reader source.
if __name__ == "__main__" and not __package__:
    from record_reader import (read_json, read_curve, as_int, as_num, calls_per_task,
                               model_in_loop, dir_count, split_id, pretty, pool_of, run_row)
else:
    from console.record_reader import (read_json, read_curve, as_int, as_num, calls_per_task,
                                       model_in_loop, dir_count, split_id, pretty, pool_of, run_row)

# Composition binds configured roots/authorization at request time. Query state
# has exactly one owner, inside this service, including its list cache and lock.
if __name__ == "__main__" and not __package__:
    import run_query, comparison, snapshots
    from path_policy import within
    from serialization import json_safe, human_bytes
else:
    from console import run_query, comparison, snapshots
    from console.path_policy import within
    from console.serialization import json_safe, human_bytes

_run_queries = run_query.RunQueryService()
boot_time = run_query.boot_time
kind_counts = run_query.kind_counts
method_of_run = run_query.method_of_run
_task_of = run_query._task_of
_valid_history_range = run_query._valid_history_range
HISTORY_MAX_ROUNDS = run_query.HISTORY_MAX_ROUNDS
HISTORY_MAX_ROUND_NUMBER = run_query.HISTORY_MAX_ROUND_NUMBER
_round_snapshot_path = snapshots._round_snapshot_path
COVERAGE_EXCLUDED = comparison.COVERAGE_EXCLUDED
COVERAGE_METHODS = comparison.COVERAGE_METHODS
COVERAGE_POOL_SIZES = comparison.COVERAGE_POOL_SIZES
POOLS = comparison.POOLS
_CKPT = comparison._CKPT
_EVAL_EXAM = comparison._EVAL_EXAM
_STAMP = comparison._STAMP
_STUDY = comparison._STUDY
_cell_valid = comparison._cell_valid
_coverage_cell = comparison._coverage_cell
_coverage_verdict = comparison._coverage_verdict
_measure = comparison._measure
_stamp_coverage = comparison._stamp_coverage
pick_study = comparison.pick_study
stamp_of = comparison.stamp_of
study_candidates = comparison.study_candidates

if __name__ == "__main__" and not __package__:
    import catalogue, launch
    import settings as console_settings
else:
    from console import catalogue, launch
    from console import settings as console_settings

display_base_url = console_settings.display_base_url
SETTINGS_SIDES = console_settings.SETTINGS_SIDES
SETTINGS_ALIASES = console_settings.SETTINGS_ALIASES


def _settings_store():
    return console_settings.SettingsStore(SETTINGS, LLM_CONFIG, os.environ)


def _launch_catalogue():
    return catalogue.LaunchCatalogue(adapters, base_harnesses, improvers, task_models, method_catalogue)


def _launch_paths():
    return launch.LaunchPaths(DRIVER, PLATFORM, PLATFORM_ROOT / 'methods', RUNS, STATE_ROOT,
                             LAUNCH_DIR, LAUNCH_LOG, BUDGET_DIR, EXTERNAL_STATE, ROOT.parent)


def _invocation_port():
    return launch.InvocationPort(guard_launch, launch_argv, launch_env)

if __name__ == "__main__" and not __package__:
    import candidates, candidate_trial
else:
    from console import candidates, candidate_trial

CANDIDATE_NAME = candidates.CANDIDATE_NAME
CandidateReadError = candidates.CandidateReadError
valid_workbench_candidate_name = candidates.valid_workbench_candidate_name
_candidate_record_source = candidates._candidate_record_source


def _candidate_store():
    limits = candidates.CandidateLimits(CANDIDATE_FILE_MAX_BYTES, SELECTION_MAX_BYTES,
        SELECTION_MAX_LINES, CANDIDATE_SAVE_MAX_SOURCE_BYTES, CANDIDATE_SAVE_MAX_OVERLAY_BYTES)
    return candidates.CandidateStore(CANDIDATES, BASE_HARNESS, limits=limits,
        ledger=_candidate_ledger_module, fingerprint=_candidate_tree_sha)

if __name__ == "__main__" and not __package__:
    import workbench, assistant_drivers
else:
    from console import workbench, assistant_drivers
ASSISTANT_KINDS = workbench.ASSISTANT_KINDS

if __name__ == "__main__" and not __package__:
    import explanation
else:
    from console import explanation
_explanation_cache = explanation.ExplainCache()
EXPLAIN_ENV = explanation.EXPLAIN_ENV
EXPLAIN_EVENT_TYPES = explanation.EXPLAIN_EVENT_TYPES
EXPLAIN_LAST_EVENTS = explanation.EXPLAIN_LAST_EVENTS
EXPLAIN_PATCH_BYTES = explanation.EXPLAIN_PATCH_BYTES
EXPLAIN_RANGE_SYSTEM = explanation.EXPLAIN_RANGE_SYSTEM
EXPLAIN_SYSTEM = explanation.EXPLAIN_SYSTEM
EXPLAIN_TIMEOUT_S = explanation.EXPLAIN_TIMEOUT_S
EXPLAIN_ZERO_TASKS = explanation.EXPLAIN_ZERO_TASKS
ExplainNoPatch = explanation.ExplainNoPatch
ExplainUpstream = explanation.ExplainUpstream
_clip = explanation._clip
_compact_event = explanation._compact_event
_explain_cache_key = explanation._explain_cache_key
_parse_explain_reply = explanation._parse_explain_reply
_read_jsonl_tail = explanation._read_jsonl_tail
_redact = explanation._redact
_sha = explanation._sha
explain_prompt = explanation.explain_prompt
explain_range_prompt = explanation.explain_range_prompt


def _explain_inputs():
    limits = explanation.InputLimits(EXPLAIN_PATCH_BYTES, EXPLAIN_ZERO_TASKS,
                                     EXPLAIN_LAST_EVENTS, frozenset(EXPLAIN_EVENT_TYPES))
    return explanation.ExplainInputBuilder(read_curve=read_curve, read_json=read_json,
                                           verdict=_verdict, limits=limits)


def _explain_service():
    return explanation.ExplainService(lookup=find_run, config=explain_config, chat=_chat_completion,
                                      cache_get=_explain_cache_get, cache_put=_explain_cache_put)

if __name__ == "__main__" and not __package__:
    import submission_controller
else:
    from console import submission_controller
_submission_controller = submission_controller.SubmissionController()

if __name__ == "__main__" and not __package__:
    import artifact_catalogue
    import author_bundles as authored
else:
    from console import artifact_catalogue
    from console import author_bundles as authored
MANIFEST_KEYS = artifact_catalogue.MANIFEST_KEYS
_KIND_RE = artifact_catalogue._KIND_RE
_SPLIT_RE = artifact_catalogue._SPLIT_RE
_deviation_items = artifact_catalogue._deviation_items
_size_of = artifact_catalogue._size_of


def _artifacts():
    roots = artifact_catalogue.ArtifactRoots(PLATFORM, BASE_HARNESS, BASE_HARNESSES, DATASETS_DIR)
    limits = artifact_catalogue.TextLimits(METHOD_FILE_BYTES, METHOD_FILE_LINES,
                                          PANEL_FILE_BYTES, PANEL_FILE_LINES)
    return artifact_catalogue.ArtifactCatalogue(roots, limits, skip_dirs=SKIP_DIRS)

if __name__ == "__main__" and not __package__:
    import http_routes, http_transport, submission_http
else:
    from console import http_routes, http_transport, submission_http


def _submission_endpoints():
    return submission_http.SubmissionEndpoints(
        base_environment=os.environ,
        _submission_backend=_submission_backend,
        _submission_reject=_submission_reject,
        load_settings=load_settings,
        submission_env=submission_env,
        submission_form=submission_form,
        submission_length=submission_length,
        submission_origin=submission_origin,
        submission_query=submission_query,
        submission_service=submission_service,
    )


def _evidence_export(manifest):
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    from console import evidence_service
    return evidence_service.execute(RUNS, manifest, is_export=True)


def _read_endpoints():
    return (
        (http_routes.workbench_capabilities_get,
         http_routes.WorkbenchCapabilitiesEndpoints(capabilities=workbench_capabilities)),
        (http_routes.query_collection_get,
         http_routes.QueryCollectionGetEndpoints(all_rows=all_rows, api_compare=api_compare,
                                                 api_coverage=api_coverage, api_health=api_health)),
        (http_routes.candidate_get,
         http_routes.CandidateGetEndpoints(CANDIDATE_NAME=CANDIDATE_NAME,
                                           harness_candidate_archive=harness_candidate_archive,
                                           harness_candidate_file=harness_candidate_file,
                                           harness_candidates=harness_candidates)),
        (http_routes.run_history_get,
         http_routes.RunHistoryGetEndpoints(_valid_history_range=_valid_history_range, api_run=api_run,
                                            run_compare=run_compare, run_history=run_history,
                                            run_range_diff=run_range_diff, run_verdicts=run_verdicts)),
        (http_routes.round_get,
         http_routes.RoundGetEndpoints(api_file=api_file, api_round=api_round, api_trace=api_trace,
                                       api_traces=api_traces, find_run=find_run, round_exists=round_exists)),
        (http_routes.configuration_get,
         http_routes.ConfigurationGetEndpoints(launch_form=launch_form, settings_for_ui=settings_for_ui)),
        (http_routes.artifact_get,
         http_routes.ArtifactGetEndpoints(api_adapters=api_adapters, api_base=api_base,
                                          api_base_file=api_base_file,
                                          api_base_harness_file=api_base_harness_file,
                                          api_base_harnesses=api_base_harnesses, api_method=api_method,
                                          api_method_file=api_method_file, api_methods=api_methods,
                                          api_protocol=api_protocol)),
        (http_routes.author_get,
         http_routes.AuthorGetEndpoints(author_bundles=author_bundles)),
        (http_routes.method_refusals_get, None),
    )


def _write_endpoints():
    return (
        (http_routes.evidence_post,
         http_routes.EvidencePostEndpoints(evidence_export=_evidence_export)),
        (http_routes.launch_preview_post,
         http_routes.LaunchPreviewPostEndpoints(launch_preview=launch_preview)),
        (http_routes.explain_post,
         http_routes.ExplainPostEndpoints(api_explain=api_explain, api_explain_range=api_explain_range)),
        (http_routes.candidate_post,
         http_routes.CandidatePostEndpoints(harness_candidate_from_round=harness_candidate_from_round,
                                            harness_candidate_save=harness_candidate_save,
                                            harness_candidate_trial=harness_candidate_trial)),
        (http_routes.workbench_post,
         http_routes.WorkbenchPostEndpoints(valid_workbench_candidate_name=valid_workbench_candidate_name,
                                            read_selection=read_selection, workbench_edit=workbench_edit)),
        (http_routes.author_post,
         http_routes.AuthorPostEndpoints(IMPORT_MAX_BYTES=IMPORT_MAX_BYTES, author_draft=author_draft,
                                         author_import=author_import)),
        (http_routes.settings_post,
         http_routes.SettingsPostEndpoints(SETTINGS_SIDES=SETTINGS_SIDES, load_settings=load_settings,
                                           probe_endpoint=probe_endpoint, save_settings=save_settings,
                                           settings_for_ui=settings_for_ui)),
        (http_routes.launch_start_post,
         http_routes.LaunchStartPostEndpoints(api_launch=api_launch)),
    )


if __name__ == "__main__" and not __package__:
    import health, snapshot_restore
else:
    from console import health, snapshot_restore

ROOT = pathlib.Path(__file__).resolve().parent
STATIC = ROOT / "static"
# Installed distributions keep mutable state outside the package tree. Editable
# checkouts retain their historical paths unless explicitly configured otherwise.
def _installed_distribution() -> bool:
    """Identify the distribution owning this file, including --user/--target installs."""
    try:
        for dist in importlib.metadata.distributions(path=[str(ROOT.parent)]):
            if (dist.metadata.get("Name", "").lower().replace("_", "-") == "harnessgrad"
                    and any(str(item) == "console/server.py" and
                            pathlib.Path(dist.locate_file(item)).resolve() == pathlib.Path(__file__).resolve()
                            for item in (dist.files or ()))):
                return True
    except (OSError, ValueError):
        pass
    install_dirs = (sysconfig.get_path("purelib"), sysconfig.get_path("platlib"))
    return any(ROOT.resolve().is_relative_to(pathlib.Path(p).resolve()) for p in install_dirs if p)


INSTALLED = _installed_distribution()
EXTERNAL_STATE = INSTALLED or bool(os.environ.get("HG_CONSOLE_STATE_ROOT"))
_state_base = pathlib.Path(os.environ.get("XDG_STATE_HOME") or
                           pathlib.Path.home() / ".local" / "state").expanduser()
STATE_ROOT = pathlib.Path(os.environ.get("HG_CONSOLE_STATE_ROOT") or
                          (_state_base / "harnessgrad" / "console" if INSTALLED else ROOT)).expanduser().resolve()
# The console is portable: its platform is the checkout containing this module.  Runs are
# deliberately external state; operators can bind an existing receipt tree explicitly via
# HG_CONSOLE_RUNS, while a fresh install gets a conventional per-user location (never
# created or populated implicitly).
DEFAULT_RUNS = pathlib.Path.home() / ".local" / "share" / "harnessgrad" / "runs"
RUNS = pathlib.Path(os.environ.get("HG_CONSOLE_RUNS", str(DEFAULT_RUNS)))
PLATFORM = pathlib.Path(os.environ.get("HG_CONSOLE_PLATFORM", str(ROOT.parent)))
SKIP_DIRS = {"__pycache__", ".git", ".state"}
MAX_FILE_BYTES = 400_000
# Method-source display limits; capped reads explicitly report truncation.
METHOD_FILE_BYTES = 120_000
METHOD_FILE_LINES = 2_000
# File-panel display limits are independent of record and candidate read limits.
PANEL_FILE_BYTES = 200_000
PANEL_FILE_LINES = 1_200
BASE_HARNESS = PLATFORM / "base_harness" / "loop"
BASE_HARNESSES = PLATFORM / "base_harness"
DATASETS_DIR = PLATFORM / "data" / "datasets"
LIST_TTL_S = 2.0


# Read-query composition.


def live_drivers() -> dict[str, dict]:
    return run_query.live_drivers()


def all_rows(force: bool = False) -> list[dict]:
    return _run_queries.all_rows(RUNS, live_drivers=live_drivers, force=force,
                                 ttl=LIST_TTL_S, clock=time.time)


def find_run(run_id: str) -> pathlib.Path | None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id or "") or run_id in (".", ".."):
        return None
    path = RUNS / run_id
    try:
        if path.parent.resolve() != RUNS.resolve() or not path.is_dir():
            return None
    except OSError:
        return None
    return path


# Launch configuration and compatibility delegates.
#: The launcher generates a unique run ID and appends a launch record. Shared
#: resource coordination uses the deployment's explicit slot directory/capacity.
LAUNCH_DIR = STATE_ROOT / ".launch"
LAUNCH_LOG = STATE_ROOT / ".launch-log.jsonl"
# One explicitly configured external lease directory coordinates direct launches
# and submissions. No machine-specific path is silently selected.
BUDGET_DIR = pathlib.Path(os.environ['HG_CONSOLE_BUDGET_DIR']).expanduser() if os.environ.get('HG_CONSOLE_BUDGET_DIR') else None
# Deployment-wide endpoint capacity, not per-run worker count. Invalid or absent
# configuration is rejected before launch; every sharing process must agree.
BUDGET_CAPACITY = os.environ.get('HG_CONSOLE_BUDGET_CAPACITY', '')
DRIVER = PLATFORM / "driver.py"


def budget_capacity() -> int:
    return launch.parse_capacity(BUDGET_CAPACITY)


def python_supported() -> bool:
    return launch.python_supported()


def adapters() -> list[dict]:
    return catalogue.adapters(PLATFORM)


def base_harnesses() -> list[str]:
    return catalogue.base_harnesses(PLATFORM)


def task_models() -> list[str]:
    return catalogue.task_models(PLATFORM, load_settings=load_settings)


def meta_harnesses() -> list[dict]:
    """The improver harnesses, each with the models it can drive. One choice per side."""
    return improvers()


def improvers() -> list[dict]:
    return catalogue.improvers(PLATFORM)


def procfs_readable() -> bool:
    return launch.procfs_readable()


def live_drivers_by_run() -> dict[str, dict]:
    return launch.live_drivers_by_run()


# Legacy endpoint configuration path; settings.py owns loading and redaction.
LLM_CONFIG = STATE_ROOT / ".local" / "llm.json"
# Solver, editor and assistant endpoint roles remain separate.
SETTINGS = STATE_ROOT / ".local" / "settings.json"

# Endpoint aliases and persistence rules belong to settings.py.


def load_settings() -> dict:
    return _settings_store().read()


def save_settings(payload: dict) -> dict:
    return _settings_store().write(payload, current=load_settings())


def settings_for_ui() -> dict:
    return console_settings.redacted(load_settings())


def probe_endpoint(base_url: str, api_key: str) -> dict:
    return console_settings.probe_endpoint(base_url, api_key)

def method_catalogue() -> list[dict]:
    return catalogue.method_catalogue(PLATFORM_ROOT)


def launch_form() -> dict:
    return launch.describe(_launch_catalogue(), load_settings=load_settings, pools=POOLS)


def launch_argv(form: dict, run_id: str) -> list[str]:
    return launch.build_argv(form, run_id, paths=_launch_paths(), budget_capacity=budget_capacity)


def launch_env(form: dict) -> dict:
    return launch.build_environment(form, load_settings=load_settings)


def new_run_id(form: dict) -> str:
    return launch.new_run_id(form)


def guard_launch(form: dict, *, preview_key_pending: bool = False, preview_meta_key_pending: bool = False) -> tuple[int, dict]:
    return launch.validate_launch_request(form, paths=_launch_paths(), catalogue=_launch_catalogue(),
        python_supported=python_supported, sandbox_supported=sandbox_supported,
        budget_capacity=budget_capacity, launch_env=launch_env,
        preview_key_pending=preview_key_pending, preview_meta_key_pending=preview_meta_key_pending)


def sandbox_supported():
    from harnessgrad.readiness import sandbox_check
    return sandbox_check(required=True)


def api_launch(form: dict) -> tuple[int, dict]:
    return launch.start(form, paths=_launch_paths(), catalogue=_launch_catalogue(), plan=_invocation_port(),
                        make_run_id=new_run_id, spawn=subprocess.Popen, base_environment=os.environ)


def launch_scope_preview(form: dict, effective_env: dict) -> dict:
    return launch.scope_preview(form, effective_env, catalogue=_launch_catalogue())


def launch_preview(form: dict) -> dict:
    return launch.preview(form, plan=_invocation_port(), catalogue=_launch_catalogue())


def api_health() -> dict:
    live, rows = live_drivers(), all_rows()
    _base, _model, _key, missing = explain_config()
    return health.describe(live, rows, runs=RUNS, explain_missing=missing,
                           lookup=find_run, read_json=read_json)


def api_run(run_id: str) -> dict | None:
    return _run_queries.api_run(run_id, lookup=find_run, live_drivers=live_drivers)


def api_round(run_id: str, n: int) -> dict | None:
    return _run_queries.api_round(run_id, n, lookup=find_run, skip_dirs=SKIP_DIRS)


def api_file(run_id: str, n: int, rel: str) -> dict | None:
    return _run_queries.api_file(run_id, n, rel, lookup=find_run, max_file_bytes=MAX_FILE_BYTES)


def _verdict(path: pathlib.Path, task_id: str, n: int) -> dict:
    return _run_queries._verdict(path, task_id, n)


def _trace_files(path: pathlib.Path, n: int) -> list[pathlib.Path]:
    return _run_queries._trace_files(path, n)


def round_exists(path: pathlib.Path, n: int) -> bool:
    return _run_queries.round_exists(path, n)


def api_traces(run_id: str, n: int) -> dict | None:
    return _run_queries.api_traces(run_id, n, lookup=find_run)


def api_trace(run_id: str, n: int, name: str) -> dict | None:
    return _run_queries.api_trace(run_id, n, name, lookup=find_run)


def _method_dir(name: str) -> pathlib.Path | None:
    return _artifacts()._method_dir(name)


def _method_files(d: pathlib.Path) -> list[dict]:
    return _artifacts()._method_files(d)


def _method_row(d: pathlib.Path) -> dict | None:
    return _artifacts()._method_row(d)


def api_methods() -> list[dict]:
    return _artifacts().api_methods()


def api_method(name: str) -> dict | None:
    return _artifacts().api_method(name)


def _read_text_capped(target: pathlib.Path, rel: str,
                      max_bytes: int = METHOD_FILE_BYTES,
                      max_lines: int = METHOD_FILE_LINES) -> dict | None:
    return _artifacts()._read_text_capped(target, rel, max_bytes=max_bytes, max_lines=max_lines)


def api_method_file(name: str, rel: str) -> dict | None:
    return _artifacts().api_method_file(name, rel)


def api_protocol() -> dict:
    return _artifacts().api_protocol()


def api_base() -> dict:
    return _artifacts().api_base()


def api_base_file(rel: str) -> dict | None:
    return _artifacts().api_base_file(rel)


#: `harness.json` 里跟"这是什么起点"有关的字段。其余的（install/backend 的细节）
#: 不是这一步要回答的问题，不搬上屏。


def _base_harnesses_root() -> pathlib.Path:
    return _artifacts()._base_harnesses_root()


def _base_harness_dirs() -> list[pathlib.Path]:
    return _artifacts()._base_harness_dirs()


def _safe_rel_files(root: pathlib.Path) -> list[dict]:
    return _artifacts()._safe_rel_files(root)


def api_base_harnesses() -> dict:
    return _artifacts().api_base_harnesses()


def _base_harness_dir(name: str) -> pathlib.Path | None:
    return _artifacts()._base_harness_dir(name)


def api_base_harness_file(name: str, rel: str) -> dict | None:
    return _artifacts().api_base_harness_file(name, rel)


def api_adapters() -> dict:
    return _artifacts().api_adapters()


# Comparison and coverage compatibility delegates.
def eval_index(rows: list[dict], dataset: str) -> dict:
    return comparison.eval_index(rows, dataset, runs=RUNS)


def api_compare(method: str, dataset: str = "") -> dict:
    return comparison.api_compare(method, dataset, rows=all_rows(), runs=RUNS)


def _coverage_eval_index() -> dict:
    return comparison._coverage_eval_index(runs=RUNS)


def _coverage_rounds(stamp: str, method: str) -> tuple[list[int], str | None, str]:
    return comparison._coverage_rounds(stamp, method, runs=RUNS)


def api_coverage() -> dict:
    return comparison.api_coverage(runs=RUNS)


# Explanation limits/prompts/cache mechanics belong to explanation.py.
EXPLAIN_CACHE_DIR = STATE_ROOT / ".cache" / "explain"


# Author-bundle and builder configuration.
PLATFORM_ROOT = ROOT.parent
AUTHORED_ROOT = pathlib.Path(os.environ.get("HG_CONSOLE_AUTHORED",
                                            (STATE_ROOT / "authored" if EXTERNAL_STATE else PLATFORM_ROOT / "data" / "authored")))
SCENARIO_BUILDER = pathlib.Path(os.environ.get(
    "HG_CONSOLE_SCENARIO_BUILDER", PLATFORM_ROOT / "tools" / "scenario_builder.py"))
IMPORT_MAX_BYTES = 32 * 1024 * 1024        # Authored archive request limit.


# Candidate storage never edits a measured baseline in place.
CANDIDATES = pathlib.Path(os.environ.get("HG_CONSOLE_CANDIDATES",
                                         (STATE_ROOT / "candidates" if EXTERNAL_STATE else PLATFORM_ROOT / "data" / "candidates")))
# Provenance ledger. Round recovery publishes a new candidate without replacing existing edits.
CANDIDATE_INDEX = CANDIDATES / ".sources.json"


def _candidate_tree_sha(root: pathlib.Path) -> dict[str, str]:
    return candidates._candidate_tree_sha(root)


def _candidate_ledger_module():
    return candidates.load_ledger(ROOT)


def _candidate_sources() -> dict:
    return _candidate_store().sources()


def _remember_candidate(name: str, run_id: str, round_no: int, via: str,
                        target: pathlib.Path, *, source: str | None = None,
                        from_path: pathlib.Path | None = None) -> bool:
    return _candidate_store().remember(name, run_id, round_no, via, target, source=source, from_path=from_path)


def _candidate_reusable(name: str, run_id: str, round_no: int) -> bool:
    return _candidate_store().reusable(name, run_id, round_no)


# Assistant sessions use their own settings role and explicit per-request thread choice.
ASSISTANT_THREADS = pathlib.Path(os.environ.get(
    "HG_CONSOLE_ASSISTANT_THREADS", (STATE_ROOT / ".local" / "assistant-threads.json" if EXTERNAL_STATE else PLATFORM_ROOT / ".local" / "assistant-threads.json")))


def force_new_thread() -> bool:
    """Legacy default; request session choice is now an explicit argument."""
    return False

def _threads() -> dict:
    return workbench.ThreadStore(ASSISTANT_THREADS).read()


def _remember_thread(candidate: str, thread: str) -> None:
    workbench.ThreadStore(ASSISTANT_THREADS).remember(candidate, thread)


def _assistant_argv(env: dict, thread: str | None = None) -> list[str]:
    return workbench.assistant_argv(env, thread)


def _snapshot(root: pathlib.Path) -> dict[str, str]:
    return workbench.snapshot(root)


def _resolve_assistant_registry(kind, model):
    # The canonical declaration reader performs no CLI/model probes or builds.
    from tools import improver
    return improver.declared_editor_binding(kind, model_override=model, environment=os.environ)


def _assistant_binding(kind, environment, assistant):
    return assistant_drivers.resolve(kind, environment, assistant, registry_resolver=_resolve_assistant_registry)


def workbench_capabilities():
    return assistant_drivers.capabilities(os.environ, load_settings()['assistant'], binding_resolver=_assistant_binding)


def workbench_edit(name: str, instruction: str, selection: str = "", kind: str = "codex", *, new_thread: bool = False) -> tuple[int, dict]:
    request = workbench.WorkbenchRequest(name, instruction, selection, kind, new_thread)
    service = workbench.WorkbenchService(CANDIDATES, threads=workbench.ThreadStore(ASSISTANT_THREADS),
        load_settings=load_settings, binding_resolver=_assistant_binding, run_process=subprocess.run, snapshot=_snapshot)
    return service.edit(request, environment=os.environ)


SELECTION_MAX_BYTES = 1024 * 1024
SELECTION_MAX_LINES = 10000


def _read_candidate_bytes(name: str, path: str, limit: int, *, require_manifest=False):
    return _candidate_store().read_bytes(name, path, limit, require_manifest=require_manifest)


def read_selection(name: str, path: str, first: int, last: int) -> str:
    return _candidate_store().selection(name, path, first, last)


def harness_candidate_archive(name: str) -> tuple[int, bytes | None]:
    return _candidate_store().archive(name, skip_dirs=SKIP_DIRS)


def run_verdicts(run_id: str, round_no: int) -> dict:
    return _run_queries.run_verdicts(run_id, round_no, lookup=find_run)


def run_compare(run_a: str, run_b: str, round_a: int = 0, round_b: int = 0) -> dict:
    return _run_queries.run_compare(run_a, run_b, round_a, round_b, lookup=find_run)


def run_range_diff(run_id: str, first: int, last: int) -> dict:
    return _run_queries.run_range_diff(run_id, first, last, lookup=find_run)


def run_history(run_id: str, upto: int, since: int = 1) -> dict:
    return _run_queries.run_history(run_id, upto, since, lookup=find_run)


def _snapshot_api():
    return snapshots.load(PLATFORM)


def _check_out_snapshot(snapshot_file, target) -> str | None:
    return snapshot_restore.check_out(snapshot_file, target, api=_snapshot_api())


def _round_module(name):
    return candidates.load_module(ROOT, name)


def harness_candidate_from_round(run_id: str, round_no: int,
                                 replay: bool = False) -> tuple[int, dict]:
    return candidates.materialize_round(run_id, round_no, replay, root=CANDIDATES,
        lookup=find_run, module_loader=_round_module, fingerprint=_candidate_tree_sha, console_root=ROOT)


CANDIDATE_FILE_MAX_BYTES = 2 * 1024 * 1024


def harness_candidate_file(name: str, rel: str) -> dict:
    return _candidate_store().file(name, rel)


def harness_candidates() -> dict:
    return _candidate_store().list()


CANDIDATE_SAVE_MAX_SOURCE_BYTES = 200 * 1024 * 1024
CANDIDATE_SAVE_MAX_OVERLAY_BYTES = 2 * 1024 * 1024


def harness_candidate_save(name: str, files: dict, from_base: bool = True,
                           source: str = "") -> tuple[int, dict]:
    return _candidate_store().save(name, files, from_base, source)


def harness_candidate_trial(name: str, dataset: str, run_id: str = "") -> tuple[int, dict]:
    return candidate_trial.trial(name, dataset, run_id, candidates_root=CANDIDATES,
        platform_root=PLATFORM_ROOT, environment=os.environ, run_driver=subprocess.run, parse_json=read_json_str)


def read_json_str(text: str) -> dict:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}

def author_bundles() -> dict:
    return authored.list_bundles(AUTHORED_ROOT, SCENARIO_BUILDER)


def _safe_extract(archive: pathlib.Path, target: pathlib.Path) -> None:
    return authored.safe_extract(archive, target)


def author_import(archive_path: pathlib.Path, wanted_slug: str = "") -> tuple[int, dict]:
    return authored.import_bundle(archive_path, wanted_slug, authored_root=AUTHORED_ROOT,
        max_bytes=IMPORT_MAX_BYTES, extract=_safe_extract, list_bundles=author_bundles)


def author_draft(note: str, materials: str = "") -> tuple[int, dict]:
    return authored.draft(note, materials, authored_root=AUTHORED_ROOT, builder_path=SCENARIO_BUILDER,
        platform=PLATFORM_ROOT, list_bundles=author_bundles, run_process=subprocess.run, environment=os.environ)

def explain_config() -> tuple[str, str, str, list[str]]:
    return explanation.explain_config(os.environ)


def _last_events(path: pathlib.Path, n: int) -> list[dict]:
    return _explain_inputs()._last_events(path, n)


def _zero_tasks(path: pathlib.Path, n: int, point: dict) -> list[dict]:
    return _explain_inputs()._zero_tasks(path, n, point)


def build_explain_inputs(path: pathlib.Path, run_id: str, n: int) -> dict:
    return _explain_inputs().build_explain_inputs(path, run_id, n)


def _chat_completion(base_url: str, model: str, api_key: str, system: str, user: str) -> str:
    return explanation._chat_completion(base_url, model, api_key, system, user, timeout_s=EXPLAIN_TIMEOUT_S)


def _explain_cache_path(key: str) -> pathlib.Path:
    return _explanation_cache.path(key, directory=EXPLAIN_CACHE_DIR)


def _explain_remember(key: str, payload: dict) -> None:
    _explanation_cache.remember(key, payload)


def _explain_cache_get(key: str) -> dict | None:
    return _explanation_cache.get(key, directory=EXPLAIN_CACHE_DIR)


def _explain_cache_put(key: str, payload: dict) -> None:
    _explanation_cache.put(key, payload, directory=EXPLAIN_CACHE_DIR)


#: 区间解释的系统提示。和单轮那条的区别只有一件事：单轮问"这一改动了什么"，
#: 区间问"这段时间的改动在往哪个方向走"，并允许它说"证据不足"。


def build_explain_range_inputs(path: pathlib.Path, run_id: str, first: int, last: int) -> dict:
    return _explain_inputs().build_explain_range_inputs(path, run_id, first, last)


def api_explain_range(run_id: str, first: int, last: int, force: bool = False) -> tuple[int, dict]:
    return _explain_service().range(run_id, first, last, force,
        build_explain_range_inputs=build_explain_range_inputs, explain_range_prompt=explain_range_prompt)


def api_explain(run_id: str, n: int, force: bool = False) -> tuple[int, dict]:
    return _explain_service().round(run_id, n, force, read_curve=read_curve,
        build_explain_inputs=build_explain_inputs, explain_prompt=explain_prompt)


def _submission_backend():
    """Load the adjacent service for both standalone CLI and importlib tests."""
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    from console import submissions
    return submissions


def _submission_reject(code, detail, field=None):
    _submission_backend()._reject(code, detail, field)


def submission_origin(headers, *, port: int) -> None:
    return submission_controller.origin(headers, port=port, reject=_submission_reject)


def submission_length(headers) -> int:
    return submission_controller.length(headers, reject=_submission_reject)


def submission_form(headers, body: bytes) -> tuple[bytes, dict]:
    return submission_controller.form(headers, body, reject=_submission_reject, length=submission_length)


def submission_query(query: dict, *, for_log: bool = False) -> dict:
    return submission_controller.query(query, for_log=for_log, reject=_submission_reject)


def submission_service(httpd):
    paths = submission_controller.SubmissionPaths(PLATFORM, RUNS, BUDGET_DIR)
    return _submission_controller.service(httpd, backend=_submission_backend(), environment=os.environ,
        paths=paths, budget_capacity=budget_capacity,
        liveness=lambda: (procfs_readable(), live_drivers_by_run()), env_resolver=submission_env)


def submission_env(scenario_name, *, settings=None, base_env=None):
    return submission_controller.resolve_environment(scenario_name, platform=PLATFORM,
        backend=_submission_backend(), load_settings=load_settings, environment=os.environ,
        settings=settings, base_env=base_env)


# Thin HTTP composition.
class Handler(http_transport.HttpTransport):
    def _post_framing(self, *, archive: bool = False) -> bool:
        return http_transport.post_framing(self, archive=archive, archive_limit=IMPORT_MAX_BYTES)

    def _static(self, path: str) -> None:
        return http_transport.static(self, path, root=STATIC)

    def _submission_error(self, exc) -> None:
        return submission_http.error(self, exc, ports=_submission_endpoints())

    def _submission_get(self, parts, query) -> None:
        return submission_http.get(self, parts, query, ports=_submission_endpoints())

    def _submission_post(self) -> None:
        return submission_http.post(self, ports=_submission_endpoints())

    def do_DELETE(self) -> None:
        return submission_http.delete(self, ports=_submission_endpoints())

    def do_GET(self) -> None:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip('/').split('/') if p]
        query = parse_qs(url.query)
        if not self._api_guard():
            return
        if parts[:2] == ['api', 'submissions'] or parts == ['api', 'scenarios']:
            return self._submission_get(parts, parse_qs(url.query, keep_blank_values=True))
        if not parts or parts[0] != 'api':
            return self._static(url.path)
        if parts == ['api', 'evidence', 'catalog']:
            if url.query:
                return self._error('bad-query', 400)
            if str(ROOT.parent) not in sys.path:
                sys.path.insert(0, str(ROOT.parent))
            from console import evidence_service
            status, payload = evidence_service.execute(RUNS)
            return self._send(payload, status)
        try:
            return http_routes.dispatch_get(self, parts, query, url.query, _read_endpoints())
        except (ValueError, OSError) as exc:
            return self._send({'error': str(exc)}, 500)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        route = url.path.strip('/')
        parts = [unquote(p) for p in url.path.strip('/').split('/') if p]
        if parts and parts[0] == 'api' and not self._api_guard():
            return
        if route == 'api/submissions':
            if url.query:
                self.close_connection = True
                return self._submission_error(_submission_backend().SubmissionRejected([
                    {'code': 'bad-query', 'detail': 'upload does not accept query fields'}]))
            return self._submission_post()
        if not self._post_framing(archive=(route == 'api/author/import')):
            return
        return http_routes.dispatch_post(self, route, url, _write_endpoints())


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._console_close_lock = threading.Lock()
        self._console_closed = False
        super().__init__(*args, **kwargs)

    def server_close(self):
        with self._console_close_lock:
            if self._console_closed:
                return
            self._console_closed = True
            try:
                _submission_controller.close(self)
            finally:
                super().server_close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4460)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    # ConsoleServer uses AF_INET; keep its listener on one explicit loopback
    # address, never a wildcard or an externally reachable interface.
    if args.host != '127.0.0.1':
        raise SystemExit('console requires --host 127.0.0.1')
    if not RUNS.is_dir():
        raise SystemExit(
            f"runs root not found: {RUNS} (set HG_CONSOLE_RUNS or provide an existing "
            "receipt directory; the default is not created automatically)"
        )
    httpd = ConsoleServer((args.host, args.port), Handler)
    try:
        print(f"hg-console on http://{args.host}:{args.port}  (runs={RUNS})", flush=True)
        if os.environ.get('HG_CONSOLE_SUBMISSIONS_ROOT'):
            submission_service(httpd).start()
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
