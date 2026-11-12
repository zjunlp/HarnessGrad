"""H0 donor admission, one-shot measurement and public point assembly.

Cache/cumulative/budget ownership, publication gates, resume dispatch and record
persistence stay in the driver. Historical decoding has one canonical owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import json
from pathlib import Path
import shutil
import stat
import sys

from eval.run.metrics import select_task_set
from harnessgrad import config as platform_config
from harnessgrad import candidate_runtime, dataset_runtime_identity, scoring_identity, verification_context, h0_identity
from eval.containers import external_runtime
from harnessgrad.environments import _env_record
from harnessgrad.identity import _identity
from harnessgrad.records import _curve_point, _cost_of, _scored, test_rate_fields
from .historical_evidence import base_from_point, read_trace_bodies, require_verification_context


@dataclass(frozen=True)
class H0Options:
    dataset: str
    sampling: str
    side: str
    trials: int
    jobs: int
    gpu_budget: int | None
    sandbox: bool


@dataclass(frozen=True)
class H0Identities:
    scoring: dict | None
    dataset: dict | None
    verification: dict | None
    initial_actor: dict | None
    current_actor: dict | None


@dataclass(frozen=True)
class RunAttribution:
    run_id: str
    mode: str
    improver: dict


@dataclass(frozen=True)
class H0Donor:
    run_id: str
    directory: Path
    payload: dict | None
    point: dict


@dataclass(frozen=True)
class SharedH0:
    base: dict
    point: dict


@dataclass(frozen=True)
class H0Selection:
    task_ids: list[str]
    train_ids: list[str]
    tasks: list[dict]
    sampling: dict


class H0Refused(Exception):
    def __init__(self, message, *, channel="console"):
        super().__init__(message)
        self.channel = channel

    def report(self, reporter):
        if self.channel == "stderr":
            print(str(self), file=sys.stderr)
        else:
            reporter.note(str(self), level="error")


def commit_h0_source(work: Path, commit: Callable) -> str:
    return commit(work, "H0")


def load_h0_donor(run_id: str, runs_root: Path, source_sha: str) -> H0Donor:
    directory = runs_root / run_id
    path = directory / "h0.json"
    payload = None
    if path.is_file():
        payload = json.loads(path.read_text())
        if payload.get("h0_sha") != source_sha:
            raise H0Refused(f"refusing --h0-from {run_id!r}: it measured base "
                            f"{payload.get('h0_sha')!r}, this run's base is {source_sha!r}", channel="stderr")
    point = None
    curve = directory / "curve.jsonl"
    if curve.is_file():
        for line in curve.read_text().splitlines():
            if line.strip():
                point = json.loads(line)
                break
    if point is None:
        raise H0Refused(f"refusing --h0-from {run_id!r}: it has no curve point", channel="stderr")
    return H0Donor(run_id, directory, payload, point)


def admit_h0_donor_records(donor: H0Donor, work: Path, identities: H0Identities,
                            options: H0Options, *, setups, envs):
    agent = platform_config.current().agent
    problems = h0_identity.agent_problems(donor.directory, donor.point, model=agent.model,
                                         backend=agent.backend, endpoint=agent.base_url)
    if problems:
        raise H0Refused(f"refusing --h0-from {donor.run_id!r}: " + "; ".join(problems))
    try:
        dataset_runtime_identity.admit(identities.dataset)
        metadata = json.loads((donor.directory / "run_meta.json").read_text())
        require_verification_context(metadata.get("verification_context"), identities.verification)
        require_verification_context(donor.point.get("verification_context"), identities.verification,
            list((donor.point.get("sampling") or {}).get("task_ids") or (donor.point.get("per_task") or {})))
        dataset_runtime_identity.require_same(metadata.get("dataset_runtime_identity"), identities.dataset)
        dataset_runtime_identity.require_same(donor.point.get("dataset_runtime_identity"), identities.dataset)
        candidate_runtime.admit(work, identities.initial_actor, envs=envs, setups=setups, sandbox=options.sandbox)
        external_runtime.admit_reuse(work, donor.point)
        if donor.payload is not None:
            external_runtime.admit_reuse(work, donor.payload.get('base') or {})
        candidate_runtime.require_same(metadata.get("harness_runtime_identity"), identities.initial_actor)
        candidate_runtime.require_same(donor.point.get("harness_runtime_identity"), identities.initial_actor)
        scoring_identity.require_same(donor.point.get("scoring_identity"), identities.scoring)
        if donor.payload is not None:
            require_verification_context(donor.payload.get("verification_context"), identities.verification)
            require_verification_context((donor.payload.get("base") or {}).get("verification_context"), identities.verification)
            dataset_runtime_identity.require_same(donor.payload.get("dataset_runtime_identity"), identities.dataset)
            dataset_runtime_identity.require_same((donor.payload.get("base") or {}).get("dataset_runtime_identity"), identities.dataset)
            candidate_runtime.require_same(donor.payload.get("harness_runtime_identity"), identities.initial_actor)
            candidate_runtime.require_same((donor.payload.get("base") or {}).get("harness_runtime_identity"), identities.initial_actor)
            scoring_identity.require_same(donor.payload.get("scoring_identity"), identities.scoring)
    except (OSError, TypeError, ValueError) as exc:
        raise H0Refused(f"refusing --h0-from {donor.run_id!r}: {exc}") from exc


def reconstruct_donor_base(donor: H0Donor, source_sha: str, reporter) -> dict:
    if donor.payload is not None:
        return donor.payload["base"]
    if (donor.point.get("identity") or {}).get("harness_sha") != source_sha:
        raise H0Refused(f"refusing --h0-from {donor.run_id!r}: its point measured base "
            f"{(donor.point.get('identity') or {}).get('harness_sha')!r}, this "
            f"run's base is {source_sha!r}", channel="stderr")
    base = base_from_point(donor.point)
    reporter.note(f"{donor.run_id} has no h0.json; rebuilt the measurement from its "
                  f"curve point (same base tree, same number)", level="info")
    return base


def materialize_donor_traces(donor: H0Donor, recipient: Path, base: dict):
    traces = donor.directory / "round0_traces"
    if traces.is_dir() and not (recipient / "round0_traces").exists():
        shutil.copytree(traces, recipient / "round0_traces")
        # A fixed donor may be read-only. Its private recipient copy is written
        # again by normal round persistence; leave the donor itself untouched.
        copied = recipient / "round0_traces"
        for path in [copied, *copied.rglob("*")]:
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)
    base["traces"], base["trial_traces"] = read_trace_bodies(traces)


def build_shared_point(donor, source_sha, manifest, attribution, options):
    current = _identity(manifest, source_sha, attribution.mode, improver=attribution.improver,
                        jobs=options.jobs, trials=options.trials, gpu_budget=options.gpu_budget)
    identity = dict(donor.point.get("identity") or {})
    for key in ("improver", "skill_sha", "method_model"):
        if key in current:
            identity[key] = current[key]
        else:
            identity.pop(key, None)
    return {**donor.point, "run_id": attribution.run_id, "identity": identity,
            "shared_from": {"run_id": donor.run_id, "path": str(donor.directory), "h0_sha": source_sha,
                "measured_with": {"jobs": (donor.point.get("identity") or {}).get("jobs"),
                                  "gpu_budget": (donor.point.get("identity") or {}).get("gpu_budget")}}}


def materialize_shared_h0(donor, recipient, source_sha, manifest, attribution, options, reporter) -> SharedH0:
    base = reconstruct_donor_base(donor, source_sha, reporter)
    materialize_donor_traces(donor, recipient, base)
    point = build_shared_point(donor, source_sha, manifest, attribution, options)
    reporter.note(f"round 0 reused from {donor.run_id} — one first score for every method", level="info")
    return SharedH0(base, point)


def measure_h0(measurement: Callable, work, tasks, scorable, cache, source_sha, options,
               identities, *, run_dir, attribution, run_seed, setups, verifiers, envs,
               diagnostic_authorization=None):
    return measurement(work, tasks, scorable, cache, harness_sha=source_sha,
        trials=options.trials, jobs=options.jobs, progress_dir=run_dir, sandbox=options.sandbox,
        setups=setups, verifiers=verifiers, **dataset_runtime_identity.fields(identities.dataset),
        **candidate_runtime.fields(identities.current_actor), verification_context=identities.verification,
        envs=envs, run_id=attribution.run_id, run_seed=run_seed, recordings_root=run_dir / "recordings",
        **({"diagnostic_authorization": diagnostic_authorization} if diagnostic_authorization is not None else {}))


def write_raw_h0(run_dir, run_id, source_sha, base, identities, reporter):
    try:
        (run_dir / "h0.json").write_text(json.dumps({"base": base, "h0_sha": source_sha, "run_id": run_id,
            **scoring_identity.fields(identities.scoring), **dataset_runtime_identity.fields(identities.dataset),
            **verification_context.fields(identities.verification),
            **candidate_runtime.fields(identities.initial_actor)}, default=str))
    except (TypeError, OSError) as exc:
        reporter.note(f"could not write h0.json ({exc}); this run cannot be a --h0-from donor", level="warn")


def select_after_h0(base, tasks, side_ids, split, options, reporter) -> H0Selection:
    ids = (list(side_ids) if options.sampling in ("diagnostic", "pilot_10pct")
           else select_task_set(base["per_task"], options.sampling, within=(side_ids or None)))
    if not ids:
        raise H0Refused(f"sampling policy {options.sampling!r} selected no tasks on H0")
    train_ids = list(ids) if options.side == "train" else []
    chosen = [task for task in tasks if task["task_id"] in set(ids + train_ids)]
    sampling = {"dataset": options.dataset, "policy": options.sampling, "task_ids": ids,
                "selection_basis_round": 0, "selected_within": options.side if split else "all"}
    if split and len(ids) < 8:
        reporter.note(f"this run scores {len(ids)} tasks — the score moves in steps of "
                      f"{1.0 / len(ids):.3f}", level="warn")
    return H0Selection(ids, train_ids, chosen, sampling)


def build_h0_point(base, source_sha, manifest, attribution, options, identities, selection,
                   split, envs, cumulative, evaluated_tasks):
    return _curve_point(run_id=attribution.run_id, round_index=0,
        identity=_identity(manifest, source_sha, attribution.mode, improver=attribution.improver,
            declared=base.get("identity_declared"), jobs=options.jobs, trials=options.trials, gpu_budget=options.gpu_budget),
        sampling=selection.sampling, scoring_identity=identities.scoring,
        dataset_runtime_identity=identities.dataset, harness_runtime_identity=identities.initial_actor,
        verification_context=verification_context.project(base.get("verification_context"), selection.task_ids),
        scores=[base["per_task"][task] for task in _scored(base, selection.task_ids)], task_ids=_scored(base, selection.task_ids),
        train_scores=[base["per_task"][task] for task in _scored(base, selection.train_ids)], train_ids=_scored(base, selection.train_ids),
        split=split, invalid=base.get("invalid"), harness_failed=base.get("harness_failed"),
        model_gateway=base.get("model_gateway"), harness_runtime=base.get("harness_runtime"),
        evaluation_protocols=base.get("evaluation_protocols"), env_record=_env_record(envs, selection.task_ids),
        cost=_cost_of(base, evaluated_tasks, cumulative["wall_clock_s"]), cumulative=cumulative, label="H0 (base harness)",
        n_trials=base.get("trials"), score_std=base.get("score_std"), per_task_std=base.get("per_task_std"),
        per_task_trials=base.get("per_task_trials"), cached_tasks=base.get("cached_tasks"), **test_rate_fields(base))
