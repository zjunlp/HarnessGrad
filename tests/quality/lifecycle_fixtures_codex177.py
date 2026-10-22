"""Authored lifecycle facts, exclusively for offline unit tests."""
from copy import deepcopy

from eval.policy import diagnostic_policy as policy
from eval.admission import lifecycle_receipt as lifecycle, runner_admission


def gate_facts(binding, stage):
    observed = dict(binding=deepcopy(binding), evidence_sha256="d" * 64)
    if stage == "admit":
        resources = dict(cpu_millis=1000, memory_bytes=4096, swap_bytes=0, pids=1, timeout_ms=1000)
        limits = dict(resources=resources, network="none", filesystems={})
        observed.update(resources=resources, resource_backend="cgroup-v2",
                        timeout_backend="host-supervisor", network="none", attachments=[],
                        read_only_root=True, filesystems={})
    elif stage == "snapshot":
        limits = dict(max_bytes=1, max_inodes=1, reserve_bytes=1, reserve_inodes=1, timeout_ms=1)
        observed.update(writable_bytes=1, writable_inodes=1, free_bytes=2, free_inodes=2,
                        running=False, exit_code=0, oom_killed=False, timeout_ms=1,
                        device_id="synthetic-device-177")
    elif stage == "cleanup":
        owned = [dict(kind="workspace", id="synthetic-workspace-177")]
        limits = dict(owned=owned, timeout_ms=1)
        observed.update(objects=[dict(**owned[0], absent=True, evidence_sha256="d" * 64)],
                        namespaces={kind: dict(remaining=[], evidence_sha256="d" * 64)
                                    for kind in runner_admission.KINDS}, timeout_ms=1)
    else:
        raise AssertionError(stage)
    return dict(policy=limits, observed=observed)


def attest(binding, stage, facts=None, *, status="passed"):
    return dict(schema=lifecycle.ATTESTATION_SCHEMA, binding=deepcopy(binding), stage=stage,
                status=status, receipt_sha256="e" * 64 if status == "passed" else None,
                facts=facts if facts is not None else {},
                detail="Synthetic unit-test facts; no production observation or authorization")


def stage_evidence(binding, outcomes):
    snapshot = attest(binding, "snapshot", dict(
        completed=True, artifact_sha256="f" * 64, exit_code=0, oom_killed=False, timed_out=False,
        preflight=gate_facts(binding, "snapshot"), runtime=gate_facts(binding, "admit")))
    outcome_hash = lifecycle.digest(outcomes)
    return {
        "admit": gate_facts(binding, "admit"),
        "execute": attest(binding, "execute", dict(exit_code=0, oom_killed=False, timed_out=False)),
        "quiesce": attest(binding, "quiesce", dict(running=False, exit_code=0,
                                                  oom_killed=False, timed_out=False)),
        "score": attest(binding, "score", dict(outcomes_sha256=outcome_hash)),
        "seal": attest(binding, "seal", dict(outcomes_sha256=outcome_hash, snapshot=snapshot)),
        "cleanup": gate_facts(binding, "cleanup"),
    }


def completed_lifecycle(context, eligible, selected, outcomes, *, run_id="unit-diagnostic",
                        stage="diagnostic"):
    binding = dict(adapter_id="synthetic-adapter-177", run_id=run_id,
                   trial_id="synthetic-trial-1", manifest_sha256=context["hashes"]["manifest"])
    scope = policy.scope_digest(stage=stage, eligible_sha256=policy.eligible_digest(eligible),
                                selected=selected, context=context)
    receipt = lifecycle.start_lifecycle(binding, scope)
    for step, evidence in stage_evidence(binding, outcomes).items():
        receipt = lifecycle.transition(receipt, step, evidence)
    return receipt


def summaries(receipt):
    cleanup = receipt["events"][-1]
    snapshot = receipt["events"][-2]["evidence"]["facts"]["snapshot"]
    return [dict(status="passed", receipt_sha256=hash_value,
                 detail="Synthetic lifecycle test receipt; no production authorization")
            for hash_value in (cleanup["receipt_sha256"], lifecycle.digest(snapshot))]
