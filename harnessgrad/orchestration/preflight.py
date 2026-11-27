"""Run preflight checks and explicit zero-execution preparation."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import sys
from typing import Callable

import data.scaffolds.registry as datasets
from harnessgrad import config as platform_config
from harnessgrad.execution import run_namespace
from harnessgrad.orchestration import readiness, run_metadata


@dataclass(frozen=True)
class ReadinessPorts:
    """Driver-owned callbacks and constants required by the preflight workflow."""
    platform_root: Path
    framework_version: str
    load_env: Callable
    eval_source: Callable
    harness_by_name: Callable
    scenario_pool_env: Callable
    write_run_meta: Callable


def _action_policy_check(args):
    from eval.service.action_policy import bind_service
    from harnessgrad.datasets.runtime_identity import h0_selection

    problem = getattr(args, '_action_policy_problem', None)
    if problem is not None:
        return readiness.Check('action-policy', True, False, problem)
    try:
        ids, setups, _, envs = h0_selection({
            'dataset': args.dataset, 'side': args.side, 'split': args.split,
            'task_ids': list(dict.fromkeys(tid.strip() for tid in (args.tasks or '').split(',') if tid.strip())),
            'sampling': args.sampling,
        })
        bind_service(args._action_policy_service, ids, setups, envs, sandbox=args.sandbox)
    except (OSError, ImportError, ValueError, RuntimeError) as exc:
        return readiness.Check('action-policy', True, False, str(exc))
    return readiness.Check('action-policy', True, True,
                           'local service and selected task controls are compatible (no inference requested)')


def run_readiness(args, ports: ReadinessPorts) -> int:
    """`--check-only` / `--prepare-only`: the door, without walking through it.

    **Why these exist.** Every prerequisite a run has -- a dataset whose bytes are absent,
    a `bwrap` that cannot build a namespace, a harness manifest naming an entrypoint that
    is not on disk -- used to be discovered *inside* the run, after round 0 had already
    spent model calls. A new user's first question is "will this run?", and the only
    answer the platform had was to spend the budget. These two flags answer it from the
    same parsed argv, before the staged-policy gate and before a harness is resolved,
    because `--check-only` has to work on a machine where the rest of the run cannot --
    that is the whole point of asking.

    `--check-only` creates nothing: it prints the table and returns 0 (every required
    check ok) or 2. `--prepare-only` creates the two artifacts of a run that cost
    nothing -- the run directory and its redacted `run_meta.json` -- and launches
    nothing. Given together, the checks gate the preparation.
    """
    # `.env` is folded in first, exactly as `--show-config` and a real run do: the
    # endpoint probe and the prepared `config` block must describe the deployment this
    # machine would actually use, not the bare process environment.
    ports.load_env(ports.platform_root / ".env")
    args._namespace_config = run_namespace.fingerprint(
        args._namespace_args, platform_config.dump(platform_config.load()))
    staged = args.sampling in ("diagnostic", "pilot_10pct")
    if getattr(args, "rounds", None) is None:
        args.rounds = 0 if staged else 3

    # **Which harness the check inspects.** An explicit `--harness-path` wins; an eval run
    # measures a state out of its `--from-run` record; otherwise it is the named directory
    # the driver would stage. Resolved here rather than inside `readiness` so the table
    # describes the harness a real run would stage, and a missing one is a row rather than
    # the driver refusing before the table is printed (`_eval_source` raises SystemExit
    # for a `--from-run` that is not there).
    harness_dir: Path | None = Path(args.harness_path) if args.harness_path else None
    harness_problem = ""
    if harness_dir is None and args.side == "eval" and args.from_run:
        try:
            harness_dir = ports.eval_source(args, Path(args.runs_root))[0]
        except SystemExit as exc:
            # Kept for the `harness` row: "no run X under Y" is the fix the user needs, and
            # collapsing it into a generic "no harness" would hide exactly the missing id.
            harness_problem = str(exc)
            harness_dir = None
    if harness_dir is None and args.side != "eval":
        harness_dir = ports.harness_by_name(args.harness)
    args.harness_dir = str(harness_dir) if harness_dir is not None else None
    args.harness_problem = harness_problem

    # A pool is an adapter *input* selector (`HG_APPWORLD_POOL`), not a driver concept: a
    # real run sets it before the adapter is imported. The probe has to do the same or it
    # reports "not provisioned" for data that is right there -- but a bad pool is the
    # run's refusal to make, so it is reported and not re-implemented here.
    if getattr(args, "pool", None) is not None:
        try:
            pool_env = ports.scenario_pool_env(args.dataset, args.pool)
            for key, value in pool_env.items():
                if os.environ.get(key) and os.environ[key] != value:
                    raise ValueError(f"pool conflicts with existing {key}")
                os.environ[key] = value
        except (OSError, ValueError) as exc:
            print(f"warning: the dataset probe cannot apply --pool {args.pool!r}: {exc}",
                  file=sys.stderr)

    policy_check = _action_policy_check(args) if getattr(args, 'action_policy_socket', None) else None
    if args.check_only:
        checks = readiness.plan_checks(args)
        if policy_check is not None:
            checks.append(policy_check)
        print(readiness.render(checks))
        failed = [check.name for check in checks if check.required and not check.ok]
        if failed:
            print("refusing to run: required checks failed: " + ", ".join(failed),
                  file=sys.stderr)
            return 2
        print("ready: every required check passed")
        warnings = [check.name for check in checks if not check.required and not check.ok]
        if warnings:
            # A failed optional check is not a refusal, but it is not silence either: the
            # disk row and the opt-in endpoint probe are exactly the warnings a reader
            # wants beside the word "ready".
            print("warning: optional check(s) failed: " + ", ".join(warnings))
        if not args.prepare_only:
            return 0
    if policy_check is not None and not policy_check.ok:
        print(f'refusing --prepare-only: action policy: {policy_check.detail}', file=sys.stderr)
        return 2

    # The run directory `--prepare-only` creates is the one a real run would use, from the
    # same argv -- including the resolved mode and the staged-run id suffix -- so the
    # prepared record is a promise about *that* invocation, not about a generic one.
    mode = "unified" if args.mode in ("A", "B") else args.mode
    run_id = args.run_id or (
        f"{args.harness}-{args.dataset}-{args.sampling}-m{mode}"
        + (f"-{secrets.token_hex(8)}" if staged else ""))
    run_dir = Path(args.runs_root) / run_id
    prepared_split = None
    if args.split not in (None, 'declared'):
        from data.scaffolds.custom_split import resolve as resolve_split
        try:
            tasks, _, declared = datasets.load_split(args.dataset)
            division, source, protocol = resolve_split(args.dataset, tasks, declared, args.split, side=args.side)
            prepared_split = run_metadata.SplitRecord(division, source, datasets.split_sha(division), protocol)
        except (OSError, ValueError, ImportError) as exc:
            print(f'refusing --prepare-only: split: {exc}', file=sys.stderr)
            return 2
    try:
        run_dir = run_namespace.reserve(args, run_id, prepare=True)
    except run_namespace.NamespaceRefused as exc:
        print(f"refusing --prepare-only: {exc}", file=sys.stderr)
        return 2
    # `platform_config.dump()` is the same redaction a real run's first write uses: every
    # secret field is `{"present": bool}` and never a value, because `run_meta.json` is
    # written to disk and read by people.
    prepared_config = platform_config.run_config(
        args, run_id, seed=os.environ.get("HARNESSGRAD_SEED"), mode=mode)
    prepared_fields = run_metadata.prepared_run_fields(
        prepared_config, platform_config.dump(run=prepared_config), ports.framework_version, prepared_split)
    if policy_check is not None:
        prepared_fields['action_policy'] = args._action_policy_service[0]['identity']
    ports.write_run_meta(run_dir, fresh=True, **prepared_fields)
    run_namespace.finish_preparation(run_dir, args._namespace_config)
    print(run_dir)
    return 0
