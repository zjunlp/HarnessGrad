"""Pure projections for prepared/planned run records, not record persistence.

Inputs are record values already observed by the composition root. This module
never reads configuration/environment/files, samples budgets or installs state.
The canonical records writer retains its None/origin/merge semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from harnessgrad.config.areas import RunConfig
from harnessgrad import candidate_runtime, dataset_runtime_identity, scoring_identity, verification_context
from eval.policy import diagnostic_policy


@dataclass(frozen=True)
class IdentityRecords:
    scoring: dict | None
    dataset: dict | None
    verification: dict | None
    harness: dict | None


@dataclass(frozen=True)
class DiagnosticRecord:
    eligible_ids: Sequence[str]
    selected_ids: Sequence[str]
    evidence: dict


@dataclass(frozen=True)
class SplitRecord:
    split: dict | None
    source: str | None
    sha: str | None
    protocol: dict | None


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace: str
    work_root: str
    platform_root: str
    sandbox: dict


@dataclass(frozen=True)
class ProvenanceRecord:
    harness_source: dict | None
    scenario: dict | None
    evaluated_from: dict | None
    platform_content_sha: str
    oracle_source: str | None
    oracle_coverage: object


@dataclass(frozen=True)
class AttributionRecord:
    agent_model: str | None
    agent_backend: str
    framework_version: str
    improver: dict | None
    improver_tool_calls: bool | None
    improver_tool_calls_detail: str


def identity_fields(identities: IdentityRecords) -> dict:
    return {**scoring_identity.fields(identities.scoring),
            **dataset_runtime_identity.fields(identities.dataset),
            **verification_context.fields(identities.verification),
            **candidate_runtime.fields(identities.harness)}


def diagnostic_fields(record: DiagnosticRecord | None):
    if record is None:
        return None
    return {"receipt_schema": diagnostic_policy.SCHEMA,
            "eligible_tasks": len(set(record.eligible_ids)),
            "eligible_task_ids_sha256": diagnostic_policy.eligible_digest(record.eligible_ids),
            "selected_tasks": len(record.selected_ids), "selected_task_ids": list(record.selected_ids),
            "expected_trials": len(record.selected_ids), "trials": 1, "jobs": 1,
            "evidence_sha256": diagnostic_policy.receipt_digest(record.evidence),
            "context": record.evidence["context"]}


def split_fields(record: SplitRecord) -> dict:
    return {"split": record.split, "split_source": record.source,
            "split_sha": record.sha, "split_protocol": record.protocol}


def prepared_run_fields(run: RunConfig, config_record: dict, framework_version: str,
                         split: SplitRecord | None) -> dict:
    return {"run_id": run.run_id, "harness": run.harness, "dataset": run.dataset, "side": run.side,
            "sampling_policy": run.sampling, "rounds": run.rounds, "trials": run.trials, "jobs": run.jobs,
            "sandbox": {"requested": bool(run.sandbox), "active": False},
            "runs_root": run.runs_root, "work_root": run.work_root,
            "method_entrypoint": run.method_entrypoint, "config": config_record,
            "framework_version": framework_version, "prepared_by": "--prepare-only", "launched": False,
            **(split_fields(split) if split is not None else {})}


def planned_run_fields(run: RunConfig, *, identities: dict, diagnostic, enforcement,
                       workspace: WorkspaceRecord, config_record: dict, budget_record: dict | None,
                       env_record: dict, split: SplitRecord, provenance: ProvenanceRecord,
                       attribution: AttributionRecord, web_record: dict) -> dict:
    return {"run_id": run.run_id, "mode": run.mode, "harness": run.harness,
            "harness_source": provenance.harness_source, "scenario": provenance.scenario, **identities,
            "dataset": run.dataset, "sampling_policy": run.sampling,
            "enforcement": enforcement, "diagnostic_policy": diagnostic,
            "workspace": workspace.workspace, "work_root": workspace.work_root,
            "platform_root": workspace.platform_root, "sandbox": workspace.sandbox,
            "config": config_record, "budget": budget_record, "env": env_record,
            "split": split.split, "split_declared": split.split is not None,
            "split_source": split.source, "split_protocol": split.protocol, "split_sha": split.sha,
            "evaluated_from": provenance.evaluated_from, "platform_content_sha": provenance.platform_content_sha,
            "oracle_source": provenance.oracle_source, "oracle_coverage": provenance.oracle_coverage, **web_record,
            "agent_model": attribution.agent_model or None, "agent_backend": attribution.agent_backend,
            "framework_version": attribution.framework_version, "method_entrypoint": run.method_entrypoint,
            "improver": attribution.improver or None, "improver_tool_calls": attribution.improver_tool_calls,
            "improver_tool_calls_detail": attribution.improver_tool_calls_detail[:300] or None,
            "trials": run.trials, "jobs": run.jobs}
