"""Static import graph and reviewed layer boundaries; see docs/architecture.md.

Run with pytest, or directly with Python (stdlib only). ``--graph`` prints the
current module -> imported modules graph without importing production code.
"""
from __future__ import annotations

import ast
import json
import sys
import tokenize
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("data", "eval", "harnessgrad", "tools", "console")

# Small extracted units have explicit dependencies, including stdlib capabilities.
# Keep these separate from permissions: extraction must not grant a reverse edge.
EXTRACTED_UNIT_IMPORTS = {
    # Byte commitments and the built-in release reader are leaf modules.
    "harnessgrad/provenance/runtime_content.py": {"__future__", "hashlib", "json", "pathlib", "re"},
    "harnessgrad/provenance/published_h0.py": {"__future__", "hashlib", "io", "json", "pathlib", "tarfile"},
    "harnessgrad/improvement/contracts.py": {"__future__", "dataclasses", "difflib", "typing"},
    "harnessgrad/improvement/model.py": {"__future__", "json", "typing", "harnessgrad.improvement.contracts", "methods.endpoint_client"},
    "harnessgrad/personal/profile.py": {"__future__", "dataclasses", "math", "os", "pathlib", "typing", "harnessgrad.config", "harnessgrad.personal.contracts", "methods.endpoint_client"},
    "harnessgrad/personal/model.py": {"__future__", "contextlib", "os", "threading", "time", "uuid", "fcntl", "msvcrt", "harnessgrad.personal.contracts", "harnessgrad.personal.profile", "harnessgrad.improvement.model", "methods.endpoint_client"},
    "harnessgrad/personal/model_store.py": {"__future__", "uuid", "harnessgrad.personal.contracts", "harnessgrad.personal.profile"},
    "harnessgrad/improvement/stages.py": {"__future__", "dataclasses", "json", "typing", "harnessgrad.improvement.contracts", "methods.policy_ports.ace_proposal", "methods.policy_ports.skillopt_fast", "methods.policy_ports.skillopt_patch", "methods.policy_ports.evo_harness_proposal", "methods.policy_ports.gepa_proposal", "methods.policy_ports.vendor.ace"},
    "harnessgrad/orchestration/treatment_workflow.py": {"__future__", "pathlib", "shlex", "harnessgrad.orchestration.improver_readiness", "harnessgrad.orchestration.treatment_identity"},
    "harnessgrad/orchestration/execution_policy.py": {"__future__", "json", "pathlib", "sys", "eval.policy.diagnostic_policy"},
    "harnessgrad/orchestration/preflight.py": {"__future__", "dataclasses", "os", "pathlib", "secrets", "sys", "typing", "data.scaffolds.registry", "data.scaffolds.custom_split", "harnessgrad.config", "harnessgrad.execution.run_namespace", "harnessgrad.orchestration.readiness", "harnessgrad.orchestration.run_metadata"},
    "harnessgrad/customization/patch.py": {"__future__", "contextlib", "os", "pathlib", "re", "typing", "harnessgrad.improvement.contracts", "harnessgrad.customization.review"},
    "harnessgrad/customization/review.py": {"__future__", "dataclasses", "hashlib", "json", "re", "typing", "harnessgrad.customization.inputs", "harnessgrad.improvement.contracts", "harnessgrad.core.harness"},
    "harnessgrad/customization/inputs.py": {"__future__", "contextlib", "json", "os", "pathlib", "stat", "typing", "harnessgrad.improvement.contracts"},
    "harnessgrad/customization/model.py": {"__future__", "harnessgrad.improvement.model"},
    "harnessgrad/entrypoints/customization.py": {"harnessgrad.core.harness", "__future__", "argparse", "dataclasses", "json", "os", "pathlib", "sys", "harnessgrad.customization.model", "harnessgrad.customization.inputs", "harnessgrad.improvement.contracts", "harnessgrad.customization.proposals", "harnessgrad.customization.review", "harnessgrad.customization.patch"},
    "harnessgrad/execution/python_runtime/python_artifacts.py": {"__future__", "contextlib", "fcntl", "hashlib", "json", "os", "pathlib", "shutil", "stat", "tempfile", "time"},
    "harnessgrad/execution/python_runtime/python_harness_runtime.py": {"__future__", "hashlib", "json", "math", "os", "pathlib", "platform", "re", "shutil", "signal", "stat", "subprocess", "sys", "tempfile", "time", "eval.ckpt.source_tree", "harnessgrad.paths", "harnessgrad.execution.bubblewrap", "harnessgrad.execution.python_runtime.python_artifacts"},
    "harnessgrad/execution/python_runtime/python_exec_runtime.py": {"__future__", "contextlib", "hashlib", "json", "os", "pathlib", "re", "shutil", "stat", "subprocess", "tempfile", "time", "eval.containers.container", "harnessgrad.execution.python_runtime.python_harness_runtime", "harnessgrad.execution.python_runtime.python_artifacts"},
    "harnessgrad/execution/budget_dispatch.py": {"__future__", "collections.abc", "contextlib", "copy", "dataclasses", "errno", "fcntl", "hashlib", "inspect", "json", "math", "os", "pathlib", "secrets", "tempfile", "threading", "time", "typing"},
    "harnessgrad/execution/budget_integration.py": {"__future__", "collections.abc", "dataclasses", "json", "os", "pathlib", "typing", "harnessgrad.execution.budget_dispatch"},
    "harnessgrad/datasets/provider.py": {"__future__", "copy", "json", "math", "os", "pathlib", "subprocess", "tempfile", "data.scaffolds.registry", "eval.containers.process", "harnessgrad.datasets.runtime", "harnessgrad.env_file", "harnessgrad.paths"},
    "harnessgrad/datasets/runtime_identity.py": {"__future__", "contextlib", "hashlib", "json", "pathlib", "re", "subprocess", "sys", "harnessgrad.datasets.runtime", "harnessgrad.datasets.task_python", "harnessgrad.provenance.native_judge_identity", "harnessgrad.execution.candidate_runtime", "data.scaffolds.registry", "data.scaffolds.custom_split", "eval.run.metrics", "harnessgrad.provenance.runtime_content"},
    "harnessgrad/datasets/runtime.py": {"__future__", "contextlib", "contextvars", "copy", "hashlib", "json", "os", "pathlib", "platform", "re", "shutil", "signal", "stat", "subprocess", "sys", "tempfile", "harnessgrad.paths", "harnessgrad.execution.python_runtime.python_artifacts"},
    "harnessgrad/execution/candidate_runtime.py": {"__future__", "contextlib", "json", "os", "pathlib", "tempfile", "harnessgrad.core.snapshot", "eval.ckpt.source_tree", "harnessgrad.execution.python_runtime.python_harness_runtime", "harnessgrad.execution.python_runtime.python_exec_runtime", "harnessgrad.provenance.runtime_content", "harnessgrad.execution.python_runtime.python_artifacts"},
    "harnessgrad/improvers/resolver.py": {"__future__", "argparse", "hashlib", "json", "os", "re", "shutil", "subprocess", "sys", "pathlib", "tempfile", "harnessgrad.config", "eval.containers.sandbox"},
    "harnessgrad/execution/harness_validation.py": {"__future__", "argparse", "contextlib", "hashlib", "json", "shutil", "stat", "subprocess", "sys", "tempfile", "pathlib", "os", "eval.ckpt.source_tree", "eval.containers.sandbox", "harnessgrad.execution.candidate_runtime", "harnessgrad.execution.python_runtime.python_harness_runtime", "harnessgrad.execution.python_runtime.python_exec_runtime", "harnessgrad.execution.python_runtime"},
    "harnessgrad/execution/tool_call_probe.py": {"__future__", "json", "urllib.error", "urllib.request"},
    "harnessgrad/execution/bubblewrap.py": {"__future__", "os", "pathlib", "shutil"},
    "harnessgrad/execution/slots.py": {"__future__", "contextlib", "errno", "fcntl", "os", "pathlib", "random", "re", "sys", "time"},
    "harnessgrad/execution/run_namespace.py": {"contextlib", "fcntl", "hashlib", "json", "os", "pathlib", "re", "stat"},
    "harnessgrad/evidence_reports/run_summary.py": {"__future__", "argparse", "hashlib", "json", "sys", "pathlib"},
    "harnessgrad/evidence_reports/evidence.py": {"__future__", "json", "math", "pathlib", "typing"},
    "harnessgrad/evidence_reports/receipt_verification.py": {"__future__", "hashlib", "json", "os", "pathlib", "re", "stat", "harnessgrad.evidence_reports.evidence", "harnessgrad.store.schema"},
    "harnessgrad/evidence_reports/evidence_stats.py": {"__future__", "hashlib", "json", "math", "statistics", "harnessgrad.evidence_reports.evidence", "harnessgrad.evidence_reports.receipt_verification", "harnessgrad.datasets.runtime_identity", "harnessgrad.provenance.verification_context", "harnessgrad.provenance.measurement_components", "harnessgrad.execution.candidate_runtime"},
    "harnessgrad/evidence_reports/evidence_export.py": {"__future__", "copy", "datetime", "json", "os", "pathlib", "re", "statistics", "tempfile", "harnessgrad.evidence_reports.evidence", "harnessgrad.evidence_reports.evidence_stats", "harnessgrad.evidence_reports.receipt_verification"},
    "harnessgrad/customization/proposals.py": {"__future__", "dataclasses", "typing", "harnessgrad.improvement.contracts", "harnessgrad.improvement.stages"},
    "harnessgrad/experiment/policies.py": {"__future__", "math", "harnessgrad.provenance.scoring_identity", "harnessgrad.provenance.measurement_components", "harnessgrad.datasets.runtime_identity"},
    "harnessgrad/experiment/config.py": {"__future__", "copy", "json", "os", "pathlib", "re", "shlex", "sys", "typing", "yaml"},
    "harnessgrad/experiment/completion.py": {"__future__", "harnessgrad.provenance.h0_identity"},
    "harnessgrad/experiment/splits.py": {"__future__", "argparse", "json", "pathlib", "sys", "data.scaffolds.registry", "data.scaffolds.custom_split"},
    "harnessgrad/core/snapshot.py": {"__future__", "base64", "hashlib", "json", "os", "tempfile", "pathlib"},
    "harnessgrad/core/readiness.py": {"__future__", "dataclasses"},
    "harnessgrad/datasets/task_python.py": {"__future__", "os", "pathlib", "harnessgrad.datasets.runtime"},
    "harnessgrad/datasets/provisioning.py": {"__future__", "harnessgrad.core.readiness"},
    "harnessgrad/core/code_metrics.py": {"__future__", "ast", "json", "pathlib"},
    "harnessgrad/core/components.py": {"__future__", "json", "pathlib"},
    "harnessgrad/core/harness.py": {"__future__", "dataclasses", "json", "pathlib", "harnessgrad.core.components"},
    "harnessgrad/core/methods.py": {"__future__", "dataclasses", "json", "pathlib", "typing"},
    "harnessgrad/core/method_transport.py": {"__future__", "json", "typing"},
    "harnessgrad/entrypoints/method_catalogue.py": {"__future__", "argparse", "json", "pathlib", "re", "sys", "harnessgrad", "harnessgrad.core.methods"},
    "eval/service/web_gateway.py": {"__future__", "argparse", "asyncio", "crawl4ai", "http.client", "http.server", "ipaddress", "json", "os", "socket", "ssl", "sys", "threading", "time", "urllib.error", "urllib.parse", "urllib.request", "pathlib"},
    "eval/run/outcomes.py": {"__future__", "eval.observe.tracing"},
    "console/record_reader.py": {"__future__", "json", "math", "pathlib", "re", "time"},
    "harnessgrad/execution/method_runner.py": {
        "__future__", "pathlib", "json", "os", "shutil", "subprocess", "sys",
        "eval.run.integrity", "harnessgrad", "harnessgrad.core.method_transport",
        "eval.containers.sandbox", "eval.containers.bun_runtime", "harnessgrad.training_window", "methods.stop_status"},
    "harnessgrad/experiment/channel.py": {
        "__future__", "pathlib", "hashlib", "json", "os", "re", "shutil", "subprocess",
        "eval.ckpt.git_state", "eval.observe.tracing", "harnessgrad", "harnessgrad.config",
        "harnessgrad.core.snapshot",
        "harnessgrad.datasets.runtime", "harnessgrad.datasets.runtime_identity",
        "harnessgrad.provenance.native_judge_identity"},
    "harnessgrad/experiment/method_attempts.py": {
        "contextlib", "fcntl", "json", "os", "pathlib", "tempfile", "uuid", "methods.stop_status"},
    "harnessgrad/experiment/records.py": {
        "__future__", "json", "os", "pathlib", "shutil", "stat", "subprocess", "time",
        "eval.ckpt.source_tree", "eval.observe.console", "eval.observe.tracing",
        "eval.run.metrics", "eval.verification.isolation", "harnessgrad.provenance.scoring_identity",
        "harnessgrad.provenance.measurement_components", "harnessgrad.provenance.verification_context",
        "harnessgrad.datasets.runtime_identity", "harnessgrad.execution.candidate_runtime",
        "harnessgrad.orchestration.environments", "harnessgrad.execution.method_runner", "eval.run.integrity"},
    "harnessgrad/entrypoints/experiment_config.py": {
        "__future__", "pathlib", "harnessgrad.config", "harnessgrad.experiment.config"},
    "harnessgrad/entrypoints/run_parser.py": {
        "__future__", "argparse", "collections.abc", "pathlib",
        "harnessgrad.entrypoints.experiment_config"},
    "harnessgrad/orchestration/environments.py": {
        "__future__", "pathlib", "os", "eval.observe.console", "data.scaffolds.registry",
        "eval.containers.container", "eval.containers.harness_runtime", "eval.containers.sandbox",
        "harnessgrad.config", "eval.service.web_gateway"},
    "harnessgrad/orchestration/readiness.py": {
        "__future__", "json", "importlib.util", "os", "re", "shlex", "shutil", "subprocess",
        "sys", "time", "urllib.request", "dataclasses", "pathlib", "data.scaffolds.registry",
        "harnessgrad", "harnessgrad.config", "harnessgrad.core.readiness", "harnessgrad.execution.method_runner",
        "data.scaffolds.custom_split", "eval.run.metrics", "eval.service.judge", "harnessgrad.improvers.resolver",
        "types", "harnessgrad.execution.python_runtime", "harnessgrad.execution.python_runtime.python_harness_runtime",
        "harnessgrad.execution.python_runtime.python_exec_runtime",
        "eval.containers.sandbox"},
    "harnessgrad/orchestration/selected_dataset.py": {
        "__future__", "dataclasses", "pathlib", "sys", "typing",
        "data.scaffolds.custom_split", "eval.ckpt.git_state", "eval.policy.diagnostic_policy",
        "eval.run.metrics", "harnessgrad.config", "harnessgrad.orchestration.environments",
        "harnessgrad.orchestration.readiness", "harnessgrad.provenance.scoring_identity",
        "harnessgrad.datasets.runtime_identity", "harnessgrad.provenance.verification_context",
        "harnessgrad.provenance.measurement_components"},
    "harnessgrad/orchestration/source_lease.py": {
        "__future__", "collections.abc", "contextlib", "dataclasses", "os", "pathlib",
        "secrets", "shutil", "sys", "tempfile", "eval.ckpt.git_state",
        "eval.ckpt.source_tree", "harnessgrad.loops.eval_side", "harnessgrad.execution.harness_validation"},
    "harnessgrad/orchestration/candidate_stage.py": {
        "__future__", "collections.abc", "dataclasses", "json", "pathlib", "shutil",
        "sys", "tempfile", "time", "eval.ckpt.git_state", "eval.ckpt.source_tree",
        "eval.containers.external_runtime", "eval.observe.console", "harnessgrad.execution.candidate_runtime",
        "harnessgrad.loops.mode_a", "harnessgrad.orchestration.source_lease",
        "harnessgrad.experiment.records", "harnessgrad.execution.harness_validation"},
    "harnessgrad/orchestration/historical_evidence.py": {
        "__future__", "json", "pathlib", "harnessgrad.execution.candidate_runtime",
        "harnessgrad.datasets.runtime_identity", "harnessgrad.provenance.verification_context",
        "harnessgrad.provenance.measurement_components"},
    "harnessgrad/orchestration/resume.py": {
        "__future__", "collections.abc", "dataclasses", "datetime", "json", "pathlib",
        "shutil", "subprocess", "tempfile", "data.scaffolds.registry", "eval.ckpt.git_state",
        "eval.containers.external_runtime", "eval.observe.console", "harnessgrad.execution.candidate_runtime",
        "harnessgrad.datasets.runtime_identity", "harnessgrad.orchestration.candidate_stage",
        "harnessgrad.orchestration.historical_evidence", "harnessgrad.provenance.scoring_identity",
        "harnessgrad.provenance.measurement_components"},
    "harnessgrad/orchestration/h0.py": {
        # stat preserves donor modes while granting owner-write on private copies.
        "__future__", "collections.abc", "dataclasses", "json", "pathlib", "shutil", "stat", "sys",
        "eval.run.metrics", "eval.containers.external_runtime", "harnessgrad.config",
        "harnessgrad.execution.candidate_runtime", "harnessgrad.datasets.runtime_identity",
        "harnessgrad.provenance.scoring_identity", "harnessgrad.provenance.verification_context", "harnessgrad.provenance.h0_identity",
        "harnessgrad.provenance.measurement_components",
        "harnessgrad.orchestration.environments", "harnessgrad.provenance.identity", "harnessgrad.experiment.records",
        "harnessgrad.orchestration.historical_evidence", "harnessgrad.provenance.h0_content", "harnessgrad.provenance.published_h0"},
    "harnessgrad/orchestration/h0_subset.py": {
        "__future__", "copy", "hashlib", "json", "eval.run.aggregation",
        "harnessgrad.datasets.runtime_identity", "harnessgrad.provenance.scoring_identity",
        "harnessgrad.provenance.verification_context", "harnessgrad.provenance.measurement_components", "harnessgrad.experiment.records",
        "harnessgrad.orchestration.historical_evidence", "harnessgrad.orchestration.h0"},
    "harnessgrad/orchestration/treatment_identity.py": {
        "__future__", "hashlib", "json", "os", "pathlib", "re", "stat"},
    "harnessgrad/orchestration/run_metadata.py": {
        "__future__", "collections.abc", "dataclasses", "eval.policy.diagnostic_policy",
        "harnessgrad.execution.candidate_runtime", "harnessgrad.config.areas", "harnessgrad.datasets.runtime_identity",
        "harnessgrad.provenance.scoring_identity", "harnessgrad.provenance.verification_context",
        "harnessgrad.provenance.measurement_components"},
    "harnessgrad/orchestration/run_completion.py": {
        "__future__", "dataclasses", "json", "pathlib", "sys", "typing", "harnessgrad.evidence_reports.run_summary",
        "harnessgrad.experiment.completion", "harnessgrad.experiment.records"},
    "harnessgrad/provenance/source_declarations.py": {"__future__", "ast"},
    "harnessgrad/provenance/native_judge_identity.py": {
        "__future__", "hashlib", "ipaddress", "os", "urllib.parse"},
    "harnessgrad/orchestration/boundary_readiness.py": {
        "__future__", "dataclasses", "pathlib", "eval.containers.container",
        "eval.containers.sandbox", "eval.run.integrity", "harnessgrad.orchestration.environments"},
    "harnessgrad/orchestration/improver_readiness.py": {
        "__future__", "dataclasses", "os", "pathlib", "sys", "eval.run.integrity",
        "harnessgrad.config", "harnessgrad.execution.tool_call_probe"},
    "harnessgrad/orchestration/run_configuration.py": {"__future__", "harnessgrad.config"},
    "harnessgrad/orchestration/scenario_config.py": {
        "__future__", "dataclasses", "json", "pathlib", "eval.run.metrics"},
    "eval/run/evaluation_plan.py": {
        "__future__", "dataclasses", "eval.containers.external_runtime",
        "eval.run.chess_stepwise", "eval.run.outcomes", "harnessgrad.execution.candidate_runtime",
        "harnessgrad.datasets.runtime_identity", "harnessgrad.datasets.task_python",
        "harnessgrad.provenance.verification_context"},
    "eval/run/measurement_cache.py": {
        "__future__", "dataclasses", "hashlib", "json", "eval.containers.external_runtime",
        "eval.run.chess_stepwise", "eval.verification.isolation", "eval.verification.pristine",
        "harnessgrad.execution.candidate_runtime", "harnessgrad.datasets.runtime_identity",
        "harnessgrad.provenance.verification_context", "harnessgrad.provenance.measurement_components"},
    "eval/run/trial_worker.py": {
        "__future__", "dataclasses", "os", "pathlib", "sys", "typing",
        "eval.containers.external_runtime", "eval.observe.progress",
        "eval.observe.task_verdicts", "eval.observe.tracing", "eval.run.chess_stepwise",
        "eval.run.outcomes", "eval.run.repeated_trials", "eval.run.trial_evidence",
        "eval.service.modelgate", "eval.verification.isolation", "eval.verification.pristine",
        "harnessgrad.execution.candidate_runtime", "harnessgrad.datasets.runtime_identity",
        "harnessgrad.datasets.task_python", "harnessgrad.provenance.verification_context"},
    "eval/run/scheduling.py": {
        "__future__", "concurrent.futures", "eval.run.outcomes", "harnessgrad.execution.slots"},
    "eval/run/aggregation.py": {
        "__future__", "dataclasses", "eval.run.outcomes", "eval.run.repeated_trials",
        "eval.run.trial_evidence", "eval.verification.isolation", "eval.verification.verifier"},
    "eval/run/evaluation_usage.py": {"__future__", "dataclasses", "eval.observe.tracing"},
    "eval/run/actor_environment.py": {
        "__future__", "os", "pathlib", "shutil", "sys",
        "harnessgrad.execution.python_runtime.python_exec_runtime", "harnessgrad.datasets.task_python"},
    "eval/run/actor_spec.py": {"__future__", "dataclasses"},
    "eval/run/actor_execution.py": {"__future__", "eval.run.actor_spec"},
    "eval/run/task_workspace.py": {"__future__", "json", "pathlib", "shutil", "tempfile"},
    "eval/run/task_session.py": {
        "__future__", "sys", "eval.containers.responses_overlay", "eval.run.tool_relay",
        "eval.service.modelgate", "eval.service.services"},
    "eval/run/task_output.py": {"__future__", "dataclasses", "eval.run.trial_evidence"},
    "eval/run/task_verification.py": {"__future__", "dataclasses", "eval.containers.immutable_runtime"},
    "console/run_query.py": {
        "__future__", "json", "os", "pathlib", "re", "threading", "time", "subprocess",
        "console.record_reader", "console.path_policy", "console.serialization",
        "record_reader", "path_policy", "serialization"},
    "console/comparison.py": {
        "__future__", "pathlib", "re", "console.record_reader", "console.serialization",
        "record_reader", "serialization"},
    "console/path_policy.py": {"__future__", "pathlib"},
    "console/serialization.py": {"__future__", "math"},
    "console/snapshots.py": {"importlib.util", "pathlib"},
    "console/settings.py": {
        "__future__", "json", "urllib.parse", "urllib.request",
        "console.record_reader", "record_reader"},
    "console/catalogue.py": {
        "__future__", "dataclasses", "re", "sys", "typing", "harnessgrad.config",
        "data.scaffolds.registry", "console.record_reader", "record_reader"},
    "console/launch.py": {
        "__future__", "dataclasses", "json", "math", "os", "pathlib", "platform", "re", "shlex", "subprocess",
        "sys", "time", "typing", "urllib.parse", "uuid", "harnessgrad.config",
        "harnessgrad.execution.slots", "console.record_reader", "console.settings",
        "console.custom_methods", "custom_methods", "harnessgrad.execution.harness_validation", "record_reader", "settings"},
    "console/candidates.py": {
        "__future__", "dataclasses", "hashlib", "importlib.util", "io", "json", "os",
        "pathlib", "re", "secrets", "shlex", "shutil", "stat", "sys", "tarfile", "time",
        "console.record_reader", "console.submissions", "record_reader"},
    "console/candidate_trial.py": {
        "__future__", "json", "os", "pathlib", "subprocess", "sys",
        "console.candidates", "candidates"},
    "console/workbench.py": {
        "__future__", "contextlib", "dataclasses", "difflib", "fcntl", "json", "os", "pathlib",
        "shlex", "shutil", "subprocess", "tempfile", "threading", "time", "console.assistant_drivers",
        "console.candidates", "console.record_reader", "assistant_drivers", "candidates", "record_reader"},
    "console/assistant_drivers.py": {
        "__future__", "contextlib", "dataclasses", "hashlib", "importlib.util", "json", "os",
        "pathlib", "re", "shlex", "shutil", "tempfile", "urllib.parse"},
    "console/explanation.py": {
        "__future__", "dataclasses", "hashlib", "http.client", "json", "os", "pathlib",
        "threading", "time", "urllib.error", "urllib.request", "console.record_reader",
        "console.run_query", "record_reader", "run_query"},
    "console/submission_controller.py": {
        "__future__", "dataclasses", "email", "email.parser", "ipaddress", "os", "pathlib",
        "re", "sys", "threading", "urllib.parse"},
    "console/artifact_catalogue.py": {
        "__future__", "ast", "dataclasses", "hashlib", "json", "pathlib", "re",
        "console.path_policy", "console.record_reader", "console.serialization",
        "path_policy", "record_reader", "serialization"},
    "console/author_bundles.py": {
        "__future__", "importlib.util", "json", "os", "pathlib", "re", "shutil",
        "subprocess", "sys", "tarfile", "tempfile", "time", "zipfile"},
    "console/http_transport.py": {
        "__future__", "http.server", "ipaddress", "json", "pathlib", "re", "urllib.parse",
        "console.path_policy", "console.serialization", "path_policy", "serialization"},
    "console/http_routes.py": {
        "__future__", "dataclasses", "pathlib", "re", "tempfile", "typing", "urllib.parse"},
    "console/submission_http.py": {
        "__future__", "dataclasses", "os", "typing", "urllib.parse"},
    "console/health.py": {"console.run_query", "run_query", "shutil"},
    "console/snapshot_restore.py": {"__future__", "pathlib", "shutil"},
}

# Exact file/module edges, not permission for all of eval to depend on config.
ALLOWED_EVAL_CONFIG_EDGES = {
    ("eval/service/judge.py", "harnessgrad.config"):
        "Reads only the explicitly configured verifier judge identity and credential.",
    ("eval/containers/harness_runtime.py", "harnessgrad.config"):
        "Reads the resolved runtime overlay configuration.",
    ("eval/containers/sandbox.py", "harnessgrad.config"):
        "Reads the resolved sandbox configuration.",
    ("eval/service/services.py", "harnessgrad.config"):
        "Reads the resolved service configuration.",
}

# Reviewed operator/transport bridges. Keys are (rule, file, module), so
# neither a whole control directory nor other imports from these files are exempt.
# Remove an entry when its dependency is moved; stale entries fail the guard.
EXCEPTIONS = {
    ("data-eval", "data/datasets/alfworld/control.py", "eval.run.tool_relay"):
        "The operator control starts the admitted private engine service rather than importing it on the platform.",
    ("data-eval", "data/datasets/alfworld/control.py", "eval.verification.verifier"):
        "Legacy operator controls use the same sealed native verifier as the normal runner.",
    ("data-eval", "data/datasets/scienceworld/control.py", "eval.containers.python_service"):
        "Only the explicit oracle control launches the managed private reference worker.",
    ("data-eval", "data/datasets/scienceworld/control.py", "eval.run.tool_relay"):
        "The operator control owns the fixed-task replay service lifecycle.",
    ("data-eval", "data/datasets/scienceworld/control.py", "eval.containers.sandbox"):
        "The operator control invokes the same isolated stdlib client as the actor.",
    ("data-eval", "data/datasets/scienceworld/control.py", "eval.verification.verifier"):
        "Legacy operator controls keep the independently pinned native grading route.",
    ("data-eval", "data/datasets/scienceworld/relay.py", "eval.service.tool_transport"):
        "The fixed-task private engine service uses the shared transport, with no oracle operation.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.run.tool_relay"):
        "The operator control owns the same selected-game relay lifecycle as the runner.",
    ("data-eval", "data/datasets/jericho/relay.py", "eval.service.tool_transport"):
        "The trusted single-game server reuses the shared transport rather than another wire protocol.",
    ("data-eval", "data/scaffolds/command_control.py", "eval.verification.verifier"):
        "The operator-only control validates private scorer receipts against the selected declaration.",
    ("data-eval", "data/scaffolds/command_control.py", "eval.verification.result_contract"):
        "The operator-only control interprets measured and unmeasured results through the shared contract.",
    ("data-eval", "data/scaffolds/native_research/swe_relay.py", "eval.run.native_supervisor"):
        "Fixed native relay cleanup is supervised independently of blocked startup, calls and patch sealing.",
    ("data-eval", "data/scaffolds/native_harbor_runtime/relay.py", "eval.run.native_supervisor"):
        "Harbor worlds use the same bounded parent cleanup and exact owned-container inventory as SWE.",
    ("data-eval", "data/scaffolds/native_harbor_runtime/session.py", "eval.run.native_supervisor"):
        "A trusted runtime publishes exact owned Docker names before any create/start operation.",
    ("data-eval", "data/scaffolds/native_research/swe_native.py", "eval.containers.observed"):
        "Fixed native SWE execution reuses kernel-observed offline containers and private patch export.",
    ("data-eval", "data/scaffolds/native_research/swe_relay.py", "eval.service.tool_transport"):
        "Selected native repository is exposed only through the existing private UNIX capability.",
    ("data-eval", "data/scaffolds/native_harbor_runtime/relay.py", "eval.service.tool_transport"):
        "Fixed native Harbor worlds use the existing private UNIX capability and sealed records.",
    ("data-eval", "data/scaffolds/native_harbor_runtime/session.py", "eval.containers.observed"):
        "Native actor worlds reuse the admitted container executor rather than opening a Docker socket to the actor.",
    ("data-eval", "data/scaffolds/native_harbor_runtime/public_runtime.py", "eval.containers.observed"):
        "Explicit native public-network profile reuses bounded Docker transport; it does not claim observed-offline admission.",
    ("data-eval", "data/scaffolds/native_harbor_runtime/judge_gateway.py", "eval.service.modelgate"):
        "The bound native judge gateway publishes only the prepared loopback endpoint through the canonical owned forwarder.",
    ("data-eval", "data/scaffolds/native_research/workbench_relay.py", "eval.service.tool_transport"):
        "Selected native workplace state uses the existing private relay; no actor scoring operation.",
    ("data-eval", "data/scaffolds/native_research/web_relay.py", "eval.service.web_gateway"):
        "Trusted native-research web relay uses the canonical host-side gateway.",
    ("data-eval", "data/scaffolds/native_research/web_relay.py", "eval.service.tool_transport"):
        "Files/network=none actors access selected host web providers over the existing UNIX capability.",
    ("data-eval", "data/datasets/costbench_dynamic/control.py", "eval.run.runner"):
        "Dynamic protocol control validates real sandbox execution and sealed scoring.",
    ("data-eval", "data/datasets/costbench_dynamic/control.py", "eval.run.tool_relay"):
        "Dynamic control owns the selected-task relay and trusted event record lifecycle.",
    ("data-eval", "data/datasets/costbench_dynamic/control.py", "eval.verification.verifier"):
        "Direct dynamic controls reuse bound-runtime admission and the read-only verifier sandbox.",
    ("data-eval", "data/datasets/costbench_dynamic/control.py", "eval.service.tool_transport"):
        "Dynamic control uses the actor transport for costed, event-triggering actions.",
    ("data-eval", "data/datasets/costbench/control.py", "eval.run.runner"):
        "Dataset control validates the real sandbox and sealed official trajectory scorer.",
    ("data-eval", "data/datasets/costbench/control.py", "eval.run.tool_relay"):
        "Manual control owns the same selected-task relay and trusted record lifecycle.",
    ("data-eval", "data/datasets/costbench/control.py", "eval.verification.verifier"):
        "Direct static controls reuse bound-runtime admission and the read-only verifier sandbox.",
    ("data-eval", "data/datasets/costbench/control.py", "eval.service.tool_transport"):
        "Control executes costed tool actions through the actor's actual transport.",
    ("data-eval", "data/datasets/costbench/relay.py", "eval.service.tool_transport"):
        "Private CostBench environment server reuses the shared bounded transport.",
    ("data-eval", "data/datasets/personamem_v2/control.py", "eval.run.runner"):
        "Dataset control exercises the real actor and verifier namespaces.",
    ("data-eval", "data/datasets/personamem_v2/control.py", "eval.run.tool_relay"):
        "Local control owns the selected relay lifecycle and sealed episode.",
    ("data-eval", "data/datasets/personamem_v2/control.py", "eval.verification.verifier"):
        "Local control scores the sealed record in the isolated verifier.",
    ("data-eval", "data/datasets/personamem_v2/control.py", "eval.service.tool_transport"):
        "Local control traverses the same ordered wire protocol as the actor.",
    ("data-eval", "data/datasets/personamem_v2/control.py", "eval.policy.runtime_policy"):
        "Dataset control applies the existing platform verification timeout.",
    ("data-eval", "data/datasets/personamem_v2/relay.py", "eval.service.tool_transport"):
        "Private episode server reuses the platform transport.",
    ("data-eval", "data/datasets/memoryagentbench/control.py", "eval.run.runner"):
        "Dataset control exercises the real actor and verifier namespaces.",
    ("data-eval", "data/datasets/memoryagentbench/control.py", "eval.run.tool_relay"):
        "Local control owns the selected relay lifecycle and sealed episode.",
    ("data-eval", "data/datasets/memoryagentbench/control.py", "eval.service.tool_transport"):
        "Local control traverses the same ordered wire protocol as the actor.",
    ("data-eval", "data/datasets/memoryagentbench/relay.py", "eval.service.tool_transport"):
        "Private episode server reuses the platform's bounded transport.",
    ("data-eval", "data/datasets/memoryagentbench/control.py", "eval.verification.verifier"):
        "Local control invokes the trusted verifier in its actual sandbox.",
    ("data-eval", "data/datasets/memoryagentbench/control.py", "eval.policy.runtime_policy"):
        "Control uses the shared timeout precedence when invoking verification.",
    ("data-eval", "data/datasets/strongreject/control.py", "eval.run.runner"):
        "Explicit dataset control uses the platform execution path, never the actor API.",
    ("data-eval", "data/datasets/strongreject/control.py", "eval.verification.verifier"):
        "Explicit dataset control exercises the trusted judge wrapper in its real sandbox.",
    ("data-eval", "data/datasets/strongreject/control.py", "eval.policy.runtime_policy"):
        "Dataset control uses the platform's verification timeout.",
    ("data-eval", "data/datasets/cooperbench/control.py", "eval.run.runner"):
        "The dataset control executes the candidate in both observed workers.",
    ("data-eval", "data/datasets/cooperbench/control.py", "eval.verification.verifier"):
        "The direct control invokes the same observed container scorer as evaluation.",
    ("data-eval", "data/datasets/tau3_bench/control.py", "eval.run.runner"):
        "Dataset control validates real sandbox execution against the official scorer.",
    ("data-eval", "data/datasets/tau3_bench/control.py", "eval.run.tool_relay"):
        "Manual control uses the same private relay and record sealing as the runner.",
    ("data-eval", "data/datasets/tau3_bench/control.py", "eval.service.tool_transport"):
        "Control sends business actions over the shared transport.",
    ("data-eval", "data/datasets/tau3_bench/relay.py", "eval.service.tool_transport"):
        "Private environment server reuses the platform transport and model capability.",
    ("data-eval", "data/datasets/tau2_bench/control.py", "eval.run.runner"):
        "Dataset control validates real sandbox execution against the official scorer.",
    ("data-eval", "data/datasets/tau2_bench/control.py", "eval.run.tool_relay"):
        "Manual control uses the same private relay and record sealing as the runner.",
    ("data-eval", "data/datasets/tau2_bench/control.py", "eval.service.tool_transport"):
        "Control sends business actions over the shared transport.",
    ("data-eval", "data/datasets/tau2_bench/control.py", "eval.service.judge"):
        "Control starts the same verifier-only judge channel for the 58 tasks whose ALL evaluation calls one.",
    ("data-eval", "data/datasets/tau2_bench/relay.py", "eval.service.tool_transport"):
        "Private τ² environment server reuses the platform's bounded transport, as τ³'s does.",
    ("data-eval", "data/datasets/kumo/control.py", "eval.run.runner"):
        "The dataset-local control validates SETUP -> oracle harness -> VERIFY, as ALFWorld does.",
    ("data-eval", "data/datasets/kumo/control.py", "eval.run.tool_relay"):
        "The local control starts and cleans its selected private relay without changing the runner.",
    ("data-eval", "data/datasets/kumo/control.py", "eval.service.tool_transport"):
        "The local control performs an actual query through the existing shared transport.",
    ("data-eval", "data/datasets/kumo/relay.py", "eval.service.tool_transport"):
        "The private KUMO server program reuses the platform transport, as ALFWorld does.",
    ("data-eval", "data/datasets/agentdojo/relay.py", "eval.service.tool_transport"):
        "The dataset's host-side environment server needs the case and the dataset runtime, so it"
        " lives dataset-side; it serves through the platform's one transport instead of writing a"
        " second implementation of the wire format.",
    ("data-eval", "data/datasets/alfworld/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one to validate adapter wiring.",
    ("data-eval", "data/datasets/alfworld/relay.py", "eval.service.tool_transport"):
        "The host-side ALFWorld environment server is a program, not a library: it serves "
        "the platform's one tool transport, which lives in eval/service.",
    ("data-eval", "data/datasets/appworld/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one with the AppWorld verifier.",
    ("data-eval", "data/datasets/appworld.py", "eval.policy.appworld_reporting"):
        "The adapter shares make_manifest with reporting; moving it needs a separate refactor.",
    ("data-eval", "data/scaffolds/command_control.py", "eval.run.runner"):
        "The shared command control invokes run_one for platform verification.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.policy.runtime_policy"):
        "The local control stages the declared runtime and applies the platform timeout.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.containers.sandbox"):
        "The local control executes the game CLI in the platform sandbox.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.verification.verifier"):
        "The local control uses the platform verifier to interpret measured evidence.",
    ("data-eval", "data/datasets/jericho/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one with the Jericho verifier.",
    ("data-eval", "data/scaffolds/qa/control.py", "eval.run.runner"):
        "The shared QA control invokes run_one to validate adapter wiring.",
    ("data-eval", "data/datasets/scienceworld/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one with the ScienceWorld verifier.",
    ("data-eval", "data/datasets/spreadsheetbench/control.py", "eval.run.runner"):
        "The existing platform control invokes run_one with the spreadsheet verifier.",
}


@dataclass(frozen=True)
class ImportEdge:
    file: str
    source: str
    target: str
    line: int
    statement: str


def _module_name(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _within(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _imports(file: str, text: str, modules: set[str]) -> list[ImportEdge]:
    """Resolve absolute/relative imports, including function and conditional ones.

    For ``from package import child``, a known child module is an edge to that
    child; an imported attribute is an edge to the containing module. Namespace
    packages and __init__.py re-exports use the same module index.
    """
    path = Path(file)
    source = _module_name(path)
    package = source if path.name == "__init__.py" else source.rpartition(".")[0]
    edges = []
    for node in ast.walk(ast.parse(text, filename=file)):
        if isinstance(node, ast.Import):
            targets = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                if node.level > len(parts):
                    raise ValueError(f"{file}:{node.lineno}: relative import beyond package")
                base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
            targets = set()
            for alias in node.names:
                child = base + "." + alias.name
                targets.add(child if child in modules else base)
        else:
            continue
        statement = " ".join(ast.get_source_segment(text, node).split())
        edges.extend(ImportEdge(file, source, target, node.lineno, statement)
                     for target in sorted(targets))
    return edges


def _source_paths(root: Path) -> list[Path]:
    return sorted(path for directory in SOURCE_DIRS
                  for path in (root / directory).rglob("*.py")
                  if "__pycache__" not in path.parts) + [root / "driver.py"]


def _source_imports(root: Path) -> list[ImportEdge]:
    paths = _source_paths(root)
    modules = {_module_name(path.relative_to(root)) for path in paths}
    # Include namespace packages, even when they do not have __init__.py.
    for name in list(modules):
        parts = name.split(".")
        modules.update(".".join(parts[:i]) for i in range(1, len(parts)))
    edges = []
    for path in paths:
        with tokenize.open(path) as stream:
            edges.extend(_imports(path.relative_to(root).as_posix(), stream.read(), modules))
    return edges


def _adapter_namespace(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 3 and parts[:2] == ["data", "datasets"]:
        return ".".join(parts[:3])
    return None


def _rule(edge: ImportEdge) -> str | None:
    if (edge.file in EXTRACTED_UNIT_IMPORTS
            and edge.target not in EXTRACTED_UNIT_IMPORTS[edge.file]):
        return "extracted-unit-dependency"
    adapter = _adapter_namespace(edge.source)
    if (adapter and _within(edge.target, "data")
            and not any(_within(edge.target, shared)
                        for shared in ("data.scaffolds", "data.runtimes"))
            and not _within(edge.target, adapter)):
        return "adapter-data"
    if (any(_within(edge.source, name) for name in
            ("harnessgrad.config", "harnessgrad.runtime"))
            and edge.target.split(".")[0] in {"eval", "data", "tools", "driver"}):
        return "foundation-direction"
    if (edge.source.split(".")[0] in {"data", "eval", "harnessgrad"}
            and _within(edge.target, "legacy")):
        return "production-legacy"
    if _within(edge.source, "data") and _within(edge.target, "eval"):
        return "data-eval"
    if _within(edge.source, "eval") and _within(edge.target, "harnessgrad.config"):
        return "eval-config"
    return None


def _policy_errors(edges: list[ImportEdge], exceptions=EXCEPTIONS,
                   allowed_config_edges=ALLOWED_EVAL_CONFIG_EDGES) -> list[str]:
    permitted = {("eval-config", file, target): reason
                 for (file, target), reason in allowed_config_edges.items()}
    permitted.update(exceptions)
    observed = set()
    errors = []
    for edge in edges:
        rule = _rule(edge)
        if rule is None:
            continue
        key = (rule, edge.file, edge.target)
        observed.add(key)
        if key not in permitted:
            errors.append(f"{edge.file}:{edge.line}: {edge.statement} "
                          f"[{rule}: {edge.source} -> {edge.target}]")
    for key, reason in sorted(permitted.items()):
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{key}: permission must have a review reason")
        if key not in observed:
            errors.append(f"{key}: stale permission; remove it with the import")
    return errors


class ImportDirectionTests(unittest.TestCase):
    def test_production_imports_use_canonical_eval_paths(self):
        compatibility_modules = {
            "eval." + path.stem for path in (ROOT / "eval").glob("*.py")
        }
        stale = [edge for edge in _source_imports(ROOT)
                 if edge.target in compatibility_modules
                 and edge.source not in compatibility_modules]
        self.assertFalse(stale, "Import the canonical eval implementation:\n" + "\n".join(
            f"{edge.file}:{edge.line}: {edge.statement}" for edge in stale))

    def test_production_imports_use_canonical_shared_paths(self):
        aliases = {
            'harnessgrad.components', 'harnessgrad.task_python', 'harnessgrad.provisioning', 'harnessgrad.snapshot', 'harnessgrad.code_metrics', 'harnessgrad.dataset_provider', 'harnessgrad.dataset_runtime', 'harnessgrad.dataset_runtime_identity', 'harnessgrad.identity',
            'harnessgrad.h0_identity', 'harnessgrad.scoring_identity',
            'harnessgrad.verification_context', 'harnessgrad.native_judge_identity',
            'harnessgrad.budget_dispatch', 'harnessgrad.budget_integration',
            'harnessgrad.bubblewrap', 'harnessgrad.slots', 'harnessgrad.run_namespace',
            'harnessgrad.candidate_runtime', 'harnessgrad.python_artifacts',
            'harnessgrad.python_harness_runtime', 'harnessgrad.python_exec_runtime',
            'harnessgrad.customization.contracts',
            'harnessgrad.evidence', 'harnessgrad.evidence_stats',
            'harnessgrad.evidence_export', 'harnessgrad.receipt_verification',
            'tools.datasets', 'tools.prepare_dataset', 'tools.dataset_classes', 'tools.dataset_lint',
            'tools.datasets_doc', 'tools.new_dataset', 'tools.split_inventory',
            'tools.run_summary', 'tools.results_matrix', 'tools.plot_curve', 'tools.evidence_export',
            'tools.component_attribution', 'tools.report_appworld', 'tools.report_livemathbench', 'tools.tau3_report',
            'tools.configure_harness', 'tools.configure_codex_harness', 'tools.configure_dsh_harness',
            'tools.configure_opencode_harness', 'tools.configure_openclaw_harness',
            'tools.configure_bun_harness', 'tools.configure_responses_overlay',
            'tools.run_tests', 'tools.run_selected', 'tools.ci_select', 'tools.quality_sweep',
            'harnessgrad.policies', 'harnessgrad.experiments', 'harnessgrad.completion', 'harnessgrad.splits',
            'harnessgrad.records', 'harnessgrad.method_attempts', 'harnessgrad.channel', 'harnessgrad.methods',
            'harnessgrad.readiness', 'harnessgrad.environments',
        }
        stale = [edge for edge in _source_imports(ROOT)
                 if edge.target in aliases and edge.source not in aliases]
        self.assertFalse(stale, 'Import the canonical shared implementation:\n' + '\n'.join(
            f'{edge.file}:{edge.line}: {edge.statement}' for edge in stale))

    def test_selected_platform_modules_use_canonical_dataset_runtime(self):
        owners = {'eval/verification/public_native_runtime.py', 'eval/verification/verifier.py',
                  'eval/run/tool_relay.py', 'data/scaffolds/registry.py',
                  'data/scaffolds/provider_worker.py', 'data/scaffolds/native_qa/__init__.py',
                  'data/scaffolds/native_qa/prepare.py', 'data/scaffolds/runtime_extensions.py',
                  'data/scaffolds/spreadsheet_runtime/actor.py',
                  'data/scaffolds/native_harbor_runtime/python_runtime.py',
                  'data/scaffolds/native_harbor_runtime/prepare_python.py',
                  'data/scaffolds/native_research/adapter.py',
                  'data/scaffolds/native_research/component_runtime.py',
                  'data/scaffolds/native_research/special_runtime.py',
                  'data/scaffolds/command_control.py', 'data/datasets/gaia/prepare.py',
                  'data/datasets/gaia/prepare_worker.py', 'data/datasets/reasoning_gym/prepare.py',
                  'data/datasets/costbench/execution.py',
                  'data/datasets/costbench/prepare_controller.py', 'data/datasets/costbench/report.py',
                  'data/datasets/jobbench/prepare.py', 'data/datasets/jobbench/render_worker.py',
                  'eval/run/chess_stepwise.py', 'data/datasets/tau2_bench/prepare.py',
                  'data/datasets/tau3_bench/prepare.py', 'data/datasets/scienceworld/control.py',
                  'data/datasets/reasoning_gym.py', 'data/datasets/agentdojo.py',
                  'data/datasets/alfworld.py', 'data/datasets/appworld.py',
                  'data/datasets/jericho.py', 'data/datasets/scienceworld.py',
                  'data/datasets/ds1000.py', 'data/datasets/livecodebench.py',
                  'data/datasets/tau2_bench.py', 'data/datasets/tau3_bench.py'}
        stale = [edge for edge in _source_imports(ROOT)
                 if edge.file in owners and edge.target == 'harnessgrad.dataset_runtime']
        self.assertFalse(stale, 'Use harnessgrad.datasets.runtime in migrated platform modules')
        # The scanner must also recognize an old function-local import.
        probe = _imports('data/scaffolds/registry.py',
                         'def load():\n    from harnessgrad.dataset_runtime import descriptor',
                         {'harnessgrad.dataset_runtime'})
        self.assertEqual([edge.target for edge in probe], ['harnessgrad.dataset_runtime'])

    def test_platform_imports_use_canonical_provenance_paths(self):
        aliases = {'harnessgrad.' + name for name in (
            'identity', 'h0_identity', 'scoring_identity', 'verification_context',
            'native_judge_identity')}
        # Dataset scaffolds can be mounted independently into runtime environments.
        stale = [edge for edge in _source_imports(ROOT)
                 if edge.target in aliases and edge.source not in aliases
                 and not edge.source.startswith(('data.', 'tools.'))]
        self.assertFalse(stale, 'Import canonical provenance:\n' + '\n'.join(
            f'{edge.file}:{edge.line}: {edge.statement}' for edge in stale))

    def test_platform_imports_use_canonical_execution_paths(self):
        aliases = {'harnessgrad.' + name for name in (
            'bubblewrap', 'slots', 'run_namespace', 'budget_dispatch', 'budget_integration', 'candidate_runtime')}
        stale = [edge for edge in _source_imports(ROOT)
                 if edge.target in aliases and edge.source not in aliases
                 and not edge.source.startswith(('data.', 'tools.'))]
        self.assertFalse(stale, 'Import canonical execution utilities:\n' + '\n'.join(
            f'{edge.file}:{edge.line}: {edge.statement}' for edge in stale))

    def test_platform_imports_use_canonical_python_runtime_paths(self):
        aliases = {'harnessgrad.' + name for name in (
            'python_artifacts', 'python_exec_runtime', 'python_harness_runtime')}
        # Data, tools and checkpoint helpers can have independent runtime mounts.
        stale = [edge for edge in _source_imports(ROOT)
                 if edge.target in aliases and edge.source not in aliases
                 and not edge.source.startswith(('data.', 'tools.', 'eval.ckpt.'))]
        self.assertFalse(stale, 'Import canonical Python runtime:\n' + '\n'.join(
            f'{edge.file}:{edge.line}: {edge.statement}' for edge in stale))

    def test_scaffolds_and_adapter_asset_directories_are_distinct(self):
        # KIND is the publication declaration, so a scaffold must never declare it.
        for path in (ROOT / "data/scaffolds").rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and t.id == "KIND" for t in targets):
                        self.assertNotEqual(ast.literal_eval(node.value), "dataset", str(path))
        # unused-inventory is unclassified inventory, deliberately not adapter assets.
        # `data.runtimes` holds the platform's own sealed-runtime closure pins: pure data, no
        # imports, shared by adapters exactly like `data.scaffolds`. It was added by the sealed
        # native-runtime work and never classified here, which made every adapter that imports
        # `data.runtimes.NATIVE_PINS` an unclassified data directory / a forbidden edge.
        # `authored` 是控制台「起草」的产物根：每个 bundle 一个目录（bundle.json + 材料 +
        # oracle），由 `tools/scenario_builder.py` 读写，和 `runtimes` 一样是纯数据、没有 import。
        # `candidates` 是控制台"编辑这一版"复制出来的候选 harness 根（每个目录一个
        # harness.json + 代码），也是纯数据：它不参与 import，平台只复制和读取它。
        infrastructure = {"scaffolds", "datasets", "__pycache__", "runtimes", "authored",
                          "candidates"}
        for directory in (ROOT / "data").iterdir():
            if directory.is_dir():
                self.assertIn(directory.name, infrastructure, f"unclassified data directory: {directory}")
        adapters = ROOT / "data/datasets"
        preparation_profiles = {"native_qa", "jobbench_renderer", "native_harbor"}
        for directory in adapters.iterdir():
            if not directory.is_dir() or directory.name == "__pycache__":
                continue
            if directory.name in preparation_profiles:
                # This is a dependency recipe, not a published adapter. Keep its
                # explicit classification and ensure it cannot add a 57th dataset.
                self.assertFalse(directory.with_suffix(".py").exists())
                profile = json.loads((directory / "execution-runtime.json").read_text())
                self.assertEqual(profile["schema"], "harnessgrad.dataset-execution-runtime.v1")
                self.assertEqual(profile["dataset"], directory.name)
                self.assertTrue((directory / profile["requirements"]).is_file())
                self.assertTrue((directory / "README.md").is_file())
                continue
            self.assertTrue(directory.with_suffix(".py").is_file(),
                            f"assets lack their own adapter: {directory}")
            for path in directory.rglob("*.py"):
                for node in ast.parse(path.read_text(), filename=str(path)).body:
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        if any(isinstance(t, ast.Name) and t.id == "KIND" for t in targets):
                            self.assertNotEqual(ast.literal_eval(node.value), "dataset", str(path))
        # A top-level adapter (including a compatibility shim) defeats the split.
        fixtures = {"__init__", "demo", "probe_set", "broken_service",
                    "verify_demo", "verify_exec", "verify_service"}
        self.assertEqual({p.stem for p in (ROOT / "data").glob("*.py")}, fixtures)
        for old in ("qa", "registry.py", "command_control.py", "command_oracle_harness"):
            self.assertFalse((ROOT / "data" / old).exists(), f"old scaffold shim: {old}")

    def test_publication_preserves_the_reviewed_adapter_inventory(self):
        from data.scaffolds import registry
        # Reviewed upstream source/alias inventory; runtime readiness is separate.
        expected = (
            'agentdojo',
            'aime',
            'alfworld',
            'appworld',
            'authored_example_fix_bug',
            'bbeh',
            'bfcl_v3',
            'bird_minidev',
            'browsecomp',
            'chessbench_puzzles',
            'cooperbench',
            'costbench',
            'costbench_dynamic',
            'deepsearchqa',
            'deepswe',
            'ds1000',
            'fever',
            'gaia',
            'gsm8k',
            'harvey_lab',
            'hle',
            'hotpotqa',
            'jericho',
            'jobbench',
            'kumo',
            'livecodebench',
            'livemathbench',
            'locomo',
            'math',
            'math500',
            'memoryagentbench',
            'musique',
            'nq',
            'officeqa_pro',
            'personamem_v2',
            'physicsquestions',
            'ptc_bench',
            'reasoning_gym',
            'scienceworld',
            'searchqa',
            'sportsunderstanding',
            'spreadsheetbench',
            'spreadsheetbench2',
            'strategyqa',
            'strongreject',
            'supergpqa',
            'swe_bench',
            'swe_bench_pro',
            'tau2_bench',
            'tau3_bench',
            'tau_bench',
            'terminal_bench',
            'travelplanner',
            'triviaqa',
            'twowikimultihopqa',
            'widesearch',
            'workbench',
        )
        # The reviewed inventory and catalogue are properties of source declarations;
        # neither requires dataset payloads or optional adapter dependencies.
        declared = tuple(sorted(path.stem for path in (ROOT / "data/datasets").glob("*.py")
                                if path.stem != "__init__"))
        self.assertEqual(declared, expected)
        published = registry.published()
        self.assertLessEqual(set(published), set(expected))
        self.assertEqual(set(expected) - set(published), set(registry.broken_adapters()),
                         "a declared adapter disappeared from the catalogue without a reason")
        for name in published:
            self.assertEqual(registry.module_name(name), f"data.datasets.{name}")
            self.assertTrue((ROOT / "data/datasets" / f"{name}.py").is_file())
            self.assertEqual(registry.kind_of(name), registry.KIND_DATASET)
            self.assertFalse((ROOT / "data" / f"{name}.py").exists())
        self.assertEqual(registry.module_name("demo"), "data.demo")

    def test_extracted_units_reject_reverse_imports_and_execution_capabilities(self):
        for file in EXTRACTED_UNIT_IMPORTS:
            for statement in ("import driver", "from console import server",
                              "import eval.run.runner",
                              "def nested():\n    import tools.improver"):
                with self.subTest(file=file, statement=statement):
                    edges = _imports(file, statement, {"console.server"})
                    errors = _policy_errors(edges, {}, {})
                    self.assertTrue(any("extracted-unit-dependency" in e for e in errors))
            # Query uses read-only diff; process owners use constants/exceptions
            # for their explicit invocation ports.
            # Other extracted units cannot acquire a subprocess dependency.
            edges = _imports(file, "import subprocess", set())
            errors = _policy_errors(edges, {}, {})
            if file in {"console/run_query.py", "console/launch.py",
                        "console/candidate_trial.py", "console/workbench.py",
                        "console/author_bundles.py",
                        "harnessgrad/orchestration/resume.py",
                        "harnessgrad/orchestration/readiness.py",  # version and sandbox readiness checks
                        "harnessgrad/experiment/records.py",  # Git metadata and round diffs
                        "harnessgrad/experiment/channel.py",  # catches materialization failures
                        "harnessgrad/execution/method_runner.py",  # owns method subprocess execution
                        "harnessgrad/datasets/provider.py",
                        "harnessgrad/datasets/runtime_identity.py",  # selected H0 preflight subprocess
                        "harnessgrad/datasets/runtime.py",  # owns isolated runtime preparation
                        "harnessgrad/improvers/resolver.py",  # owns CLI version and optional smoke subprocesses
                        "harnessgrad/execution/harness_validation.py",  # owns explicit smoke subprocess
                        "harnessgrad/execution/python_runtime/python_harness_runtime.py",
                        "harnessgrad/execution/python_runtime/python_exec_runtime.py"}:
                self.assertEqual(errors, [])
            else:
                self.assertTrue(any("extracted-unit-dependency" in e for e in errors))
        edges = _imports("harnessgrad/entrypoints/run_parser.py",
                         "from .experiment_config import config_help",
                         {"harnessgrad.entrypoints.experiment_config"})
        self.assertEqual(_policy_errors(edges, {}, {}), [])

    def test_current_source_layering(self):
        for directory in SOURCE_DIRS:
            self.assertTrue((ROOT / directory).is_dir(), f"missing source directory: {directory}")
        errors = _policy_errors(_source_imports(ROOT))
        self.assertFalse(errors, "Import direction violations:\n" + "\n".join(errors))

    def test_forbidden_import_forms(self):
        cases = [
            ("harnessgrad/config/areas.py", "import eval.run.runner as runner"),
            ("harnessgrad/config/__init__.py", "from data.scaffolds import registry"),
            ("harnessgrad/config/loader.py", "from tools.validate_run import main"),
            ("harnessgrad/config/loader.py", "from driver import main"),
            ("harnessgrad/runtime/events.py", "import eval"),
            ("harnessgrad/runtime/events.py", "import data.scaffolds.registry"),
            ("harnessgrad/runtime/events.py", "import tools.validate_run"),
            ("harnessgrad/runtime/events.py", "import driver as cli"),
            ("data/new_adapter.py", "def load():\n    from eval import runner"),
            ("data/new_adapter.py", "if False:\n    import eval.run.runner"),
            ("data/new_adapter.py", "from legacy import adapter"),
            ("eval/new_runner.py", "from legacy.compat import run"),
            ("harnessgrad/new_runtime.py", "import legacy.compat as compat"),
            ("eval/new_runner.py", "from harnessgrad import config"),
            # An approved file cannot add a dependency on a config submodule.
            ("eval/service/services.py", "from harnessgrad.config import loader"),
            # A directory/file exception cannot admit a different eval module.
            ("data/scaffolds/qa/control.py", "from eval.service.services import start"),
        ]
        modules = {"eval.run.runner", "data.scaffolds.registry", "harnessgrad.config",
                   "harnessgrad.config.loader"}
        for file, text in cases:
            with self.subTest(file=file, text=text):
                errors = _policy_errors(_imports(file, text, modules), {}, {})
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(file + ":", errors[0])
                self.assertIn(" ".join(text.splitlines()[-1].split()), errors[0])

    def test_adapter_data_dependencies_stay_private_or_use_scaffolds(self):
        modules = {"data.datasets.alpha", "data.datasets.beta", "data.demo",
                   "data.scaffolds.registry", "data.datasets.alpha.runtime"}
        for statement in ("import data.datasets.beta", "from data.datasets import beta",
                          "from .. import beta", "import data.demo",
                          "import data.registry", "import data.qa.base"):
            with self.subTest(statement=statement):
                edges = _imports("data/datasets/alpha/control.py", statement, modules)
                self.assertTrue(_policy_errors(edges, {}, {}))
        for statement in ("from data.scaffolds import registry",
                          "from data.scaffolds.qa import base",
                          "from . import runtime", "import json"):
            with self.subTest(statement=statement):
                edges = _imports("data/datasets/alpha/__init__.py", statement, modules)
                self.assertEqual(_policy_errors(edges, {}, {}), [])

    def test_relative_imports_and_module_attributes(self):
        cases = [
            ("harnessgrad/runtime/__init__.py", "from .events import Stage", "harnessgrad.runtime.events"),
            ("eval/run/runner.py", "from ..containers import sandbox as sb", "eval.containers.sandbox"),
            ("harnessgrad/runtime/compat.py", "from ..config import current", "harnessgrad.config"),
            ("eval/sub/runner.py", "from ..service import services", "eval.service.services"),
            ("eval/run/runner.py", "from harnessgrad import config as cfg", "harnessgrad.config"),
            ("eval/run/runner.py", "from harnessgrad.config import *", "harnessgrad.config"),
        ]
        modules = {"eval.containers.sandbox", "eval.service.services", "harnessgrad.config"}
        for file, text, target in cases:
            with self.subTest(file=file, text=text):
                edges = _imports(file, text, modules)
                self.assertEqual([edge.target for edge in edges], [target])

    def test_permissions_are_exact_and_require_reasons(self):
        edges = _imports("data/example.py", "from eval.run.runner import run_one", set())
        key = ("data-eval", "data/example.py", "eval.run.runner")
        self.assertEqual(_policy_errors(edges, {key: "Existing control dependency."}, {}), [])
        self.assertIn("review reason", "\n".join(_policy_errors(edges, {key: " "}, {})))
        self.assertIn("stale permission", "\n".join(_policy_errors([], {key: "Old edge."}, {})))
        allowed = {("eval/service/services.py", "harnessgrad.config"): "Reads service configuration."}
        edges = _imports("eval/service/services.py", "from harnessgrad import config", {"harnessgrad.config"})
        self.assertEqual(_policy_errors(edges, {}, allowed), [])
        self.assertIn("stale permission", "\n".join(_policy_errors([], {}, allowed)))

    def test_permissions_do_not_admit_neighboring_files_or_modules(self):
        cases = [
            ("data/example.py", "eval.run.runner", "eval.service.services", "data/other.py", "data-eval"),
            ("eval/service/services.py", "harnessgrad.config", "harnessgrad.config.loader",
             "eval/other.py", "eval-config"),
        ]
        for file, target, other_target, other_file, rule in cases:
            with self.subTest(rule=rule):
                permitted = {(rule, file, target): "Reviewed existing edge."}
                edges = _imports(file, f"import {target}\nimport {other_target}", set())
                edges += _imports(other_file, f"import {target}", set())
                errors = _policy_errors(edges, permitted, {})
                self.assertEqual(len(errors), 2, errors)
                self.assertTrue(any(f"{file}:2: import {other_target}" in error for error in errors))
                self.assertTrue(any(f"{other_file}:1: import {target}" in error for error in errors))

    def test_unrelated_layers_and_import_text_are_not_banned(self):
        cases = [
            ("harnessgrad/trusted_runner.py", "from eval.run.runner import run_one"),
            ("data/example.py", "import evaluation\nimport legacy_helpers"),
            ("harnessgrad/runtime/events.py", "from ..config import current"),
            ("data/example.py", '# import eval.run.runner\ntext = "import legacy"'),
        ]
        for file, text in cases:
            with self.subTest(file=file, text=text):
                self.assertEqual(_policy_errors(_imports(file, text, set()), {}, {}), [])


if __name__ == "__main__":
    if sys.argv[1:] == ["--graph"]:
        graph = {_module_name(path.relative_to(ROOT)): set() for path in _source_paths(ROOT)}
        for edge in _source_imports(ROOT):
            graph[edge.source].add(edge.target)
        print(json.dumps({name: sorted(targets) for name, targets in sorted(graph.items())}, indent=2))
    else:
        unittest.main()
