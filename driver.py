#!/usr/bin/env python3
"""The sampling loop. Framework-owned (INTERFACE.md §2).

    H0 evaluate -> curve point 0 -> trainer edits -> commit -> evaluate -> ...
                                                              -> curve point t

Two execution modes, because real methods come in two shapes (trainers/base.py):

    A  framework-driven   the platform owns the round loop and calls improve()
    B  method-driven      the method runs its own loop and returns a Trajectory

Both write the same `curve.jsonl`, so curves from either mode land on one axis.

The Trainer never sees this file. Everything it is allowed to know is staged
into <workspace>/_harnessgrad/ before each call.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import data.scaffolds.registry as datasets
from scenarios import load as load_scenario
from tools.improver import forbidden_reason
from tools.improver import load_config as load_improvers
from tools.improver import resolve as resolve_improver
from tools.validate_harness import check as check_harness, source_identity, filesystem_identity
# Imported, never copied: the page a finished run leaves behind and the one
# `python3 tools/run_summary.py runs/<id>` writes must be the *same* page, including its
# rule that an unknown is a named absence and never a fabricated zero.
import tools.run_summary as run_summary
from eval.ckpt.git_state import (STATE_DIRNAME, commit_state, content_sha, manifest,
                                stage_workspace)
from eval.ckpt.source_tree import copy_harness, copy_rejected_harness, has_python_runtime
from eval.observe.console import CONSOLE as console
from eval.run.metrics import select_task_set
from eval.run import metrics
from eval.policy import diagnostic_policy
from eval.policy.unobserved_diagnostic import Authorization
from eval.run.integrity import PLATFORM_ROOT, workspace_is_outside_platform
from eval.run.runner import evaluate
import eval.containers.container as container
import eval.service.modelgate as modelgate
import eval.containers.sandbox as sandbox
import eval.service.services as services_mod
import tools.web_gateway as web_gateway_mod


# ------------------------------------------------------------------- layers ---
#
# The implementation lives in `harnessgrad/`, layered so the dependencies point one way
# (`harnessgrad/__init__.py` draws the picture). This file is the entrypoint: argparse, the
# wiring, and the order in which the platform refuses things -- the name that INTERFACE.md,
# `tools/serve_ui.py` and every recorded run's argv use, so it stays where it is.
#
# Only what this file itself wires up is imported. Everything else is reached where it
# lives (`from harnessgrad.loops.mode_a import ...`), so the layer a change belongs to is
# visible in the import line rather than flattened through the entrypoint.
from harnessgrad import FRAMEWORK_VERSION
from harnessgrad import config as platform_config
from harnessgrad import h0_identity
from harnessgrad.entrypoints.experiment_config import (
    config_help as _config_help, expand_experiment_argv as _expand_experiment_argv,
)
from harnessgrad.entrypoints.run_parser import parse_run_args
from harnessgrad.orchestration.selected_dataset import (
    SelectionOptions, SelectedRunRefused, build_selected_run_plan,
    check_scenario_runtime as _check_scenario_runtime,
)
from harnessgrad import run_namespace
from harnessgrad import readiness
from harnessgrad import slots
from harnessgrad import scoring_identity
from harnessgrad import dataset_runtime_identity
from harnessgrad import verification_context
from eval.containers import external_runtime
from harnessgrad import candidate_runtime
from harnessgrad.orchestration import resume as resume_planning
from harnessgrad.orchestration import h0 as h0_planning
from harnessgrad.orchestration import boundary_readiness, improver_readiness
from harnessgrad.orchestration import scenario_config, run_configuration
from harnessgrad.orchestration import run_metadata
from harnessgrad.orchestration import run_completion
from harnessgrad.orchestration.run_completion import SummaryWriteNotices as _SummaryWriteNotices
from harnessgrad.orchestration.resume import ResumeRefused, ResumeNothingToDo
from harnessgrad.orchestration.historical_evidence import (
    base_from_point as _base_from_point, read_trace_bodies as _read_trace_bodies,
    require_verification_context as _require_verification_context,
)
from harnessgrad.orchestration.resume import tree_present as _tree_present
from harnessgrad.orchestration import source_lease as source_leases
from harnessgrad.orchestration.source_lease import (
    SourceLease, SourceRequest, SourceRefused, select_source,
    external_source_snapshot as _external_source_snapshot,
    strip_reviewed_bytecode as _strip_reviewed_bytecode,
)
from harnessgrad.orchestration.candidate_stage import (
    CandidateStageRefused, OwnedSource, PreparationOptions, PreparationJournal, WorkspaceOps,
    stage_owned_source, prepare_committed_candidate, bind_external_candidate,
    managed_python_intent as _managed_python_intent,
    refuse_managed_source as _refuse_managed_source,
)
from harnessgrad.budget_integration import (
    BudgetBinding,
    BudgetConfigurationError,
    bind_budget,
)
from harnessgrad.environments import (
    _check_env_kinds, _env_record, _narrow_to_run, _report_harness_failed,
    _report_invalid, _required_env_kinds, _resolve_envs, _warn_missing_overlay)
from harnessgrad.identity import _identity
from harnessgrad.loops.eval_side import (_eval_from_record, _eval_resume_seed, _eval_source,
                                        _progress_results, _run_eval_side)
from harnessgrad.loops.mode_a import _run_mode_a, _snapshot_round
from harnessgrad.methods import PlatformTampered, _check_method_entrypoint
from harnessgrad.records import (
    _cost_of, _curve_point, _require_untampered, _save_round_state, _save_traces,
    _save_verdicts, _scored, _write_curve, _write_run_meta, test_rate_fields)


# --------------------------------------------------- improver capability ---

# Keep legacy callback entrypoints for callers that patch these probes.
def _cli_provider(improver: dict) -> dict:
    return improver_readiness.cli_provider(improver)


def _cli_provider_base_url(improver: dict) -> str:
    return _cli_provider(improver).get("base_url", "")


def _probe_tool_calls(base_url: str, model: str) -> tuple[bool | None, str]:
    return improver_readiness.probe_tool_calls(base_url, model, provider=_cli_provider)


# --------------------------------------------------------- model config ---

def load_env(path: Path) -> dict:
    """Load `.env` into the environment the harness will run in.

    The harness reads its model configuration from the environment, so somebody
    has to put it there. Doing it in the driver rather than in the harness keeps
    the harness free of a dotenv dependency and means a method that edits the
    harness cannot accidentally change how the model is reached.

    Real environment variables win: an explicit `HG_AGENT_MODEL=... ./driver.py`
    must not be silently overridden by a file on disk.
    """
    from harnessgrad.env_file import install_env_file
    return install_env_file(path)


def _platform_content_sha() -> str:
    """One digest for the whole platform tree (see `eval/run/integrity.py`).

    Written into every run's `run_meta` so a later `--resume` can refuse to continue a
    curve across a platform change: two code versions in one curve is the confound this
    platform exists to remove, and nothing on disk said which version a run used.
    """
    from eval.run.integrity import content_sha
    return content_sha(PLATFORM_ROOT)


def _git_sha() -> str | None:
    """`HEAD` of the platform checkout, or None when it is not a repository."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(PLATFORM_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def _resume_request(args):
    return resume_planning.ResumeRequest(args.rounds, resume_planning.ResumeIdentities(
        getattr(args, "scoring_identity", None), getattr(args, "dataset_runtime_identity", None),
        getattr(args, "verification_context", None)), getattr(args, "resume_across_platforms", None),
        getattr(args, "_resume_initial_source", None), getattr(args, "_resume_candidate_source", None))


def _resume_train_state(args, run_dir: Path, split, work: Path, *, defer=False):
    lease = resume_planning.prepare_resume_workspace_lease(_resume_request(args), run_dir, split, work,
        ops=WorkspaceOps(stage_workspace, commit_state),
        platform=resume_planning.PlatformEvidence(_platform_content_sha, _git_sha), reporter=console)
    args.current_harness_runtime_identity = lease.current_binding
    if defer:
        lease.state["_publication"] = lease
    else:
        try:
            lease.publish()
        except BaseException:
            lease.close()
            raise
    return lease.state


def _restore_train_workspace(args, run_dir, existing, last, view, work):
    request = resume_planning.ResumeRequest(0, resume_planning.ResumeIdentities(None, None, None),
        initial_source=getattr(args, "_resume_initial_source", None))
    state, binding = resume_planning.restore_train_workspace(request, run_dir, existing, last, view, work,
        WorkspaceOps(stage_workspace, commit_state))
    args.current_harness_runtime_identity = binding
    return state


def _publish_resume(result):
    result.pop("_publication").publish()


def _record_platform_seam(run_dir, before, after, reason, platform_before):
    return resume_planning.record_platform_seam(run_dir, before, after, reason, platform_before,
        platform=resume_planning.PlatformEvidence(_platform_content_sha, _git_sha), reporter=console)


def describe_model() -> dict:
    """What the platform will record as `agent_model`, and whether it is real.

    Reported at the top of a run because the alternative is discovering three
    rounds later that every score came from the mock stand-in.
    """
    return run_configuration.describe_agent(platform_config.current().agent)


# The curve table is rendered by `eval/observe/console.py`, next to the header and the
# round lines, so that the human text and `events.jsonl` cannot drift apart. It
# used to live here and carried a `sel` column that read `?` on every row of
# every run this platform has produced, because mode A has no selection step to
# report. A column that is constant noise teaches the reader to skip the table,
# which is the opposite of what a table is for.


# ----------------------------------------------------------------- main ---

_SCENARIO_ARG_DEFAULTS = {'dataset': 'demo', 'split': None, 'side': 'train',
                          'pool': None, 'sampling': 'diagnostic', 'rounds': None,
                          'trials': 1, 'jobs': 1}


def _apply_scenario(args: argparse.Namespace, spec: dict, explicit: set[str]) -> None:
    """Apply reviewed values to a complete parser-shaped namespace.

    All ScenarioOptions fields must be present, including parser-defaulted fields.
    Partial private namespaces are outside this adapter's input contract.
    """
    current = scenario_config.ScenarioOptions(args.dataset, args.split, args.side,
        args.sampling, args.rounds, args.trials, args.pool, args.jobs)
    for field, value in scenario_config.scenario_option_updates(
            current, spec, explicit, platform_root=PLATFORM_ROOT, select_sampling=select_task_set):
        setattr(args, field, value)


def _scenario_pool_env(dataset: str, pool: str | None) -> dict[str, str]:
    return scenario_config.pool_environment(dataset, pool)


def _check_scenario_dataset(spec: dict) -> None:
    return scenario_config.check_scenario_dataset(spec, platform_root=PLATFORM_ROOT,
                                                  datasets=datasets)


def _check_scenario_resume(args, receipt: dict | None) -> None:
    if not args.resume:
        return
    return scenario_config.check_scenario_resume(args.resume, args.run_id, args.runs_root, receipt)


def _harness_roots() -> list[Path]:
    return source_leases.harness_roots(Path(__file__).parent)


def _harness_by_name(name: str) -> Path | None:
    return source_leases.harness_by_name(name, _harness_roots())


def _readiness(args) -> int:
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
    load_env(Path(__file__).parent / ".env")
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
            harness_dir = _eval_source(args, Path(args.runs_root))[0]
        except SystemExit as exc:
            # Kept for the `harness` row: "no run X under Y" is the fix the user needs, and
            # collapsing it into a generic "no harness" would hide exactly the missing id.
            harness_problem = str(exc)
            harness_dir = None
    if harness_dir is None and args.side != "eval":
        harness_dir = _harness_by_name(args.harness)
    args.harness_dir = str(harness_dir) if harness_dir is not None else None
    args.harness_problem = harness_problem

    # A pool is an adapter *input* selector (`HG_APPWORLD_POOL`), not a driver concept: a
    # real run sets it before the adapter is imported. The probe has to do the same or it
    # reports "not provisioned" for data that is right there -- but a bad pool is the
    # run's refusal to make, so it is reported and not re-implemented here.
    if getattr(args, "pool", None) is not None:
        try:
            pool_env = _scenario_pool_env(args.dataset, args.pool)
            for key, value in pool_env.items():
                if os.environ.get(key) and os.environ[key] != value:
                    raise ValueError(f"pool conflicts with existing {key}")
                os.environ[key] = value
        except (OSError, ValueError) as exc:
            print(f"warning: the dataset probe cannot apply --pool {args.pool!r}: {exc}",
                  file=sys.stderr)

    if args.check_only:
        checks = readiness.plan_checks(args)
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
        prepared_config, platform_config.dump(run=prepared_config), FRAMEWORK_VERSION, prepared_split)
    _write_run_meta(run_dir, fresh=True, **prepared_fields)
    run_namespace.finish_preparation(run_dir, args._namespace_config)
    print(run_dir)
    return 0


def _summary_options(args):
    return run_completion.SummaryOptions(getattr(args, "no_summary", False), getattr(args, "summary_dir", None),
        getattr(args, "check_only", False), getattr(args, "prepare_only", False))


def _write_end_summary(args, run_dir: Path, curve_path: Path) -> Path | None:
    return run_completion.write_end_summary(_summary_options(args), run_dir, curve_path)


def _main(attempt: dict) -> int:
    with ExitStack() as sources:
        return _main_with_sources(attempt, sources)


def _main_with_sources(attempt: dict, sources: ExitStack) -> int:
    sources.enter_context(datasets.provider_session())
    ap, args, explicit = parse_run_args(sys.argv[1:], _SCENARIO_ARG_DEFAULTS)
    scenario_spec = scenario_receipt = None

    if (args.run_id or "").startswith("task25-") and not args.budget_profile:
        print("refusing approved Task25 run without --budget-profile", file=sys.stderr)
        return 2

    if args.show_config:
        # The effective configuration, not the declared one: `.env` is folded in first,
        # because that is what a run would actually read. Nothing else runs -- no
        # dataset is loaded, no image is resolved, no improver is probed.
        load_env(Path(__file__).parent / ".env")
        print(json.dumps(platform_config.dump(), indent=2))
        return 0

    try:
        if args.scenario is not None:
            scenario_spec, scenario_receipt = load_scenario(args.scenario)
            _apply_scenario(args, scenario_spec, explicit)
        _check_scenario_resume(args, scenario_receipt)
    except (OSError, ValueError) as exc:
        print(f'refusing --scenario: {exc}', file=sys.stderr)
        return 2

    if args.expected_harness_sha256 is not None:
        value = args.expected_harness_sha256
        if args.harness_path is None or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            print('--expected-harness-sha256 requires --harness-path and a lowercase SHA256', file=sys.stderr)
            return 2

    if args.expected_harness_filesystem_sha256 is not None:
        value = args.expected_harness_filesystem_sha256
        if (args.expected_harness_sha256 is None or len(value) != 64
                or any(c not in '0123456789abcdef' for c in value)):
            print('--expected-harness-filesystem-sha256 requires expected source SHA256 and a lowercase SHA256', file=sys.stderr)
            return 2

    # Preserve parsed arguments before readiness and runtime add derived fields.
    namespace_args = dict(vars(args))
    namespace_staged = args.sampling in ("diagnostic", "pilot_10pct")
    namespace_mode = "unified" if args.mode in ("A", "B") else args.mode
    if not args.resume and (args.run_id or not namespace_staged):
        namespace_id = args.run_id or f"{args.harness}-{args.dataset}-{args.sampling}-m{namespace_mode}"
        namespace_config = None
        if (Path(args.runs_root) / namespace_id / 'prepared-reservation.json').is_file():
            load_env(Path(__file__).parent / ".env")
            namespace_config = run_namespace.fingerprint(
                namespace_args, platform_config.dump(platform_config.load()))
        try:
            run_namespace.available(args, namespace_id, namespace_config,
                                    allow_prepared=not args.prepare_only)
        except run_namespace.NamespaceRefused as exc:
            print(f"refusing run: {exc}", file=sys.stderr)
            return 2
    args._namespace_args = namespace_args

    # **Readiness is answered from the arguments a run would use, and answered first.**
    # Here rather than after the harness/dataset resolution above because `--check-only`
    # must still print its table when one of those would refuse: the table is how the user
    # learns *which* prerequisite is missing. A normal run never enters this branch.
    if args.check_only or args.prepare_only:
        return _readiness(args)

    if args.harness_path is not None:
        if args.from_run is not None or args.from_round is not None:
            print('cannot combine --harness-path with --from-run or --from-round', file=sys.stderr)
            return 2
        if args.side == 'eval':
            if args.resume:
                print('direct --harness-path eval does not support --resume; use a fresh run ID', file=sys.stderr)
                return 2
            if args.rounds not in (None, 0):
                print('direct --harness-path eval requires --rounds 0', file=sys.stderr)
                return 2
            if args.sampling != 'all':
                print('direct --harness-path eval requires --sampling all', file=sys.stderr)
                return 2
            args.rounds = 0

    staged = args.sampling in ("diagnostic", "pilot_10pct")
    if args.rounds is None:
        args.rounds = 0 if staged else 3
    if args.authorize_unobserved_diagnostic is not None and (
            args.sampling != "diagnostic" or args.side != "train"
            or not args.authorize_unobserved_diagnostic.strip()
            or args.method_entrypoint):
        print("refusing unobserved diagnostic: explicit operator reason, diagnostic sampling, "
              "train side and no method entrypoint are required", file=sys.stderr)
        return 2
    policy_evidence = None
    if staged:
        try:
            diagnostic_policy.staged_task_set([], args.sampling, trials=args.trials,
                                               jobs=args.jobs, rounds=args.rounds)
            if not args.policy_evidence:
                raise ValueError("staged runs require --policy-evidence with passing dataset gates")
            policy_evidence = json.loads(Path(args.policy_evidence).read_text())
            if not isinstance(policy_evidence, dict):
                raise ValueError("policy evidence must be an object")
            context = policy_evidence.get("context")
            diagnostic_policy.validate_context(context)
            if (context["dataset"] != args.dataset or context["side"] != args.side
                    or (context["gates"]["status"] != "passed" and not (
                        args.authorize_unobserved_diagnostic
                        and context["gates"]["status"] == "unknown"))):
                raise ValueError("passing dataset gates must bind this dataset and side")
        except (OSError, ValueError) as exc:
            print(f"refusing staged policy: {exc}", file=sys.stderr)
            return 2
        # A diagnostic/pilot measures one state; it cannot invoke an improver.
        args.improver = "none"

    runs_root = Path(args.runs_root)
    source_lease = sources.enter_context(SourceLease())
    try:
        source = select_source(SourceRequest(
            args.harness, args.harness_path, args.side, args.run_id, args.runs_root, args.work_root,
            args.from_run, args.from_round, args.expected_harness_sha256,
            args.expected_harness_filesystem_sha256), source_lease,
            roots=_harness_roots, named_source=_harness_by_name)
    except SourceRefused as exc:
        exc.report()
        return 2
    harness_src, eval_from = source.path, source.eval_from
    harness_source, source_receipt = source.provenance, source.receipt
    reviewed_original = source.reviewed_original
    args.harness, args.run_id = source.harness, source.run_id

    env_file = Path(__file__).parent / ".env"
    load_env(env_file)
    args._namespace_config = run_namespace.fingerprint(
        args._namespace_args, platform_config.dump(platform_config.load()))

    # Isolation before configuration, and both before a dataset is loaded. Where a run writes
    # is a safety property; whether the method can start is a configuration one. Reversed, a
    # run with its workspace inside the platform reports the method problem and hides the
    # violation (`test_isolation`); delayed past the load, a missing method is only noticed
    # after the tasks were read (`test_missing_method_stops_before_dataset_loading`).
    early_work = (Path(args.work_root) / (args.run_id or "<run-id>") / "workspace").resolve()
    if not workspace_is_outside_platform(early_work, PLATFORM_ROOT):
        print(f"refusing to run: the workspace {early_work} is inside the platform "
              f"({PLATFORM_ROOT}). A harness there can read the driver and .env "
              f"and write anywhere in the platform. Pass --work-root outside it.",
              file=sys.stderr)
        return 2
    for check in readiness.method_checks(args):
        if check.required and not check.ok:
            print(f"refusing {check.name}: {check.detail}", file=sys.stderr)
            return 2
    try:
        pool_env = _scenario_pool_env(args.dataset, args.pool)
        if args.pool is not None and not pool_env and args.pool != args.side:
            raise ValueError('pool does not match the selected side')
        for key, value in pool_env.items():
            if os.environ.get(key) and os.environ[key] != value:
                raise ValueError(f'pool conflicts with existing {key}')
            if datasets.module_name(args.dataset) in sys.modules:
                raise ValueError('pool adapter was already imported; use a fresh driver process')
            os.environ[key] = value
        if scenario_spec is not None:
            _check_scenario_dataset(scenario_spec)
    except (OSError, ValueError) as exc:
        print(f'refusing scenario/pool: {exc}', file=sys.stderr)
        return 2
    model = describe_model()
    if staged and (not model["live"] or policy_evidence["context"]["endpoint"]
                   != platform_config.current().agent.base_url):
        print("refusing staged policy: bind evidence to the live local agent endpoint",
              file=sys.stderr)
        return 2
    # The project's model rule, enforced at the door next to the credential check: the
    # harness's model is a run-level choice and the same rule covers it. `improvers.json`
    # holds the list and the reason (`tools/improver.py:forbidden_reason`).
    model_problem = run_configuration.agent_refusal(
        model, load_improvers=load_improvers, forbidden_reason=forbidden_reason)
    if model_problem:
        print(model_problem, file=sys.stderr)
        return 2

    options = SelectionOptions(args.dataset, args.split, args.side, args.tasks, args.sampling,
                               args.run_id, args.from_run, args.sandbox, args.check_net)
    try:
        selected = build_selected_run_plan(
            options, datasets, runs_root=runs_root, scenario=scenario_spec, model=model,
            harness_source=harness_src, policy_evidence=policy_evidence, reporter=console)
    except SelectedRunRefused as exc:
        exc.report(console)
        return 2
    tasks_all, side_ids = selected.selection.tasks, selected.selection.ids
    eligible_ids, scorable = selected.selection.eligible_ids, selected.partition.scorable
    split, split_source = selected.partition.split, selected.partition.source
    split_protocol = selected.partition.protocol
    oracle_source, oracle_coverage = selected.partition.oracle_source, selected.partition.oracle_coverage
    setups, verifiers, envs = selected.setups, selected.verifiers, selected.envs
    args.scoring_identity = selected.scoring_identity
    args.dataset_runtime_identity = selected.dataset_runtime_identity
    args.verification_context = selected.verification_context
    args.harness_runtime_identity = None
    args.current_harness_runtime_identity = None
    args.harness_preparation = None
    verifier_kinds = sorted({v.get("kind", "answer") for v in verifiers.values()})
    # The record says **unified**, not the flag. `--mode` is accepted so existing scripts
    # keep working, but it selects nothing any more, and a point that said `run_mode: B`
    # while the unified loop ran A-shaped steps would be the same class of lie as
    # `agent_model: mock`. Old records keep their own `A`/`B`; these say what is true now.
    mode = "unified" if args.mode in ("A", "B") else args.mode

    run_id = args.run_id or (
        f"{args.harness}-{args.dataset}-{args.sampling}-m{mode}"
        + (f"-{secrets.token_hex(8)}" if staged else ""))
    run_dir = Path(args.runs_root) / run_id
    if not args.resume:
        try:
            run_dir = run_namespace.claim(args, run_id, args._namespace_config)
        except run_namespace.NamespaceRefused as exc:
            print(f"refusing run: {exc}", file=sys.stderr)
            return 2
    else:
        run_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the saved seed before any metadata write or workspace restaging.
    # A rejected explicit override must not rewrite the interrupted run's config.
    resumed_seed = None
    if args.resume:
        saved_meta = resume_planning.read_metadata(run_dir)
        if resume_planning.frozen_split_changed(saved_meta, split_protocol):
            console.note('refusing --resume: frozen split manifest changed; use the original manifest', level='error')
            return 2
        try:
            admission = resume_planning.admit_resume_bindings(resume_planning.AdmissionRequest(
                args.side, side_ids, resume_planning.ResumeIdentities(
                    args.scoring_identity, args.dataset_runtime_identity, args.verification_context),
                setups, envs, args.sandbox), run_dir, harness_src, metadata=saved_meta, hold=sources.enter_context)
            saved_meta = admission.metadata
            if admission.restored is not None:
                args.harness_runtime_identity = admission.restored["initial"]
                args.current_harness_runtime_identity = admission.restored["current"]
            if admission.initial_source is not None:
                harness_src = admission.initial_source
                args._resume_initial_source = admission.initial_source
                args._resume_candidate_source = admission.candidate_source
                harness_source = saved_meta.get("harness_source")
            args.harness_preparation = saved_meta.get("harness_preparation")
        except (OSError, TypeError, ValueError) as exc:
            console.note(f"refusing --resume: {exc}", level="error")
            return 2
    if args.side == 'eval' and args.resume:
        try:
            resumed_seed = _eval_resume_seed(run_dir, tasks_all, verifiers, args.trials, args.seed)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            console.note(f'refusing ordinary evidence resume: {exc}', level='error')
            return 2

    diagnostic_authorization = (Authorization(run_id, side_ids[0],
                                args.authorize_unobserved_diagnostic)
                                if args.authorize_unobserved_diagnostic else None)
    enforcement = diagnostic_authorization.record() if diagnostic_authorization else None

    def save_policy_receipt(res: dict | None = None) -> bool:
        if not staged:
            return True
        unknown = {"status": "unknown", "receipt_sha256": None,
                   "detail": "driver has no verified lifecycle receipt; trusted orchestration must attest"}
        receipt = diagnostic_policy.build_receipt(
            run_id=run_id, stage=args.sampling, eligible=eligible_ids, selected=side_ids,
            context=policy_evidence["context"],
            outcomes=(diagnostic_policy.evaluation_outcomes(res, side_ids) if res is not None else []),
            cleanup=unknown, snapshot=unknown, enforcement=enforcement)
        # Keep the pre-execution plan even if evaluation is interrupted or refused.
        diagnostic_policy.write_receipt(
            run_dir / ("diagnostic_receipt.json" if res is not None else "staged_plan.json"), receipt)
        return (enforcement is None and receipt["lifecycle"] is not None
                and receipt["lifecycle"]["publication_ready"]
                and receipt["coverage"]["measured_tasks"] == len(side_ids))

    save_policy_receipt()
    if diagnostic_authorization is not None:
        # Persist intent before any runtime preflight; interruption/refusal remains
        # evidence of an incomplete diagnostic, never an inferred successful run.
        _write_run_meta(run_dir, run_id=run_id, dataset=args.dataset,
                        sampling_policy="diagnostic", enforcement=enforcement,
                        **scoring_identity.fields(args.scoring_identity),
                        **dataset_runtime_identity.fields(args.dataset_runtime_identity))
        def finalize_incomplete(detail):
            if not (run_dir / "diagnostic_receipt.json").exists():
                save_policy_receipt({"trials": 1, "invalid": {
                    side_ids[0]: {"stage": "preflight_or_execution", "detail": detail}}})
        attempt["finalize"] = finalize_incomplete

    # The trusted dispatcher is opt-in for ordinary runs.  An approved Task25
    # run is the one deliberate exception: its profile is part of the approval
    # contract and the run must refuse to start without a configured ledger.
    budget_profile_source = args.budget_profile
    budget_arm = args.budget_arm or "agent"
    budget_ledger = args.budget_ledger or str(run_dir / "budget_ledger.json")
    budget_binding: BudgetBinding | None = None
    if budget_profile_source:
        try:
            budget_binding = bind_budget(
                budget_profile_source, budget_ledger, run_id=run_id, arm_id=budget_arm)
        except BudgetConfigurationError as exc:
            print(f"refusing budget profile run: {exc}", file=sys.stderr)
            return 2
        # These counters describe orchestration units.  Provider calls and token
        # usage are populated only by the trusted gateway itself.
        budget_binding.record("rounds", args.rounds)
        budget_binding.record("trials", args.trials)
        budget_binding.record("jobs", args.jobs)
        args.budget_profile = str(budget_profile_source)
        args.budget_ledger = str(budget_binding.ledger_path)
        args.budget_arm = budget_binding.arm_id
        args.budget_profile_hash = budget_binding.profile_hash

    # **The machine-wide measurement budget, armed here and not only for budget-profile
    # runs.** It sat inside the `if budget_profile_source:` block above for a while, so a run
    # without `--budget-profile` -- which is every run we launch -- silently measured with no
    # ceiling at all: `--gpu-budget 40` was accepted, recorded on the point, and enforced
    # nowhere. Measured 2026-10-17: the slot directory was never created, so a nine-method
    # batch at `--jobs 24` each would have offered the endpoint 216 concurrent tasks instead
    # of 40.
    slots.configure(args.gpu_budget, args.gpu_budget_dir)

    resume_state = resume_lease = None
    if args.resume and args.side == 'train':
        work = (Path(args.work_root) / run_id / "workspace").resolve()
        if not workspace_is_outside_platform(work, PLATFORM_ROOT):
            print(f"refusing to run: workspace {work} is inside the platform", file=sys.stderr)
            return 2
        try:
            resumed_seed = resume_planning.admit_train_options(resume_planning.TrainOptions(
                args.harness, args.dataset, args.sampling, args.trials, args.seed), saved_meta)
            resume_lease = resume_planning.prepare_resume_workspace_lease(_resume_request(args), run_dir, split, work,
                ops=WorkspaceOps(stage_workspace, commit_state),
                platform=resume_planning.PlatformEvidence(_platform_content_sha, _git_sha), reporter=console)
            resume_state = resume_lease.state
            args.current_harness_runtime_identity = resume_lease.current_binding
            sources.callback(resume_lease.close)
        except ResumeNothingToDo as exc:
            print(f"--resume: {exc}")
            return 0
        except (ResumeRefused, OSError, ValueError) as exc:
            print(f"refusing --resume: {exc}", file=sys.stderr)
            return 2

    # From here on the run narrates itself through the console, which writes the
    # human text and `events.jsonl` together.
    if not args.resume and args.side != 'eval':
        console.attach(run_dir / "events.jsonl")

    # **Which improver this run's edits came from, resolved before anything is paid for.**
    #
    # A method is a decision rule *and* an improver, and the platform used to record only
    # the rule's model. Resolved here rather than at the first method call so that a
    # machine with no usable improver refuses at the door -- the same reason images are
    # pinned above: a run that discovers its tooling is missing three rounds in has
    # already spent the rounds.
    #
    # A refusal is only for a run that *needs* an improver and cannot find one. The
    # reference methods carry their own (`methods/editor.py` calls `HG_METHOD_*` directly),
    # so an unresolvable improver is a warning plus an empty identity field, not a stop --
    # and `improver` stays empty so no curve point can claim an improver it did not use.
    #
    # Held in a local and handed to the loops that write points. It used to be a module
    # global here that `_identity` read from the other side of the call stack: a curve
    # point's improver field then depended on a mutable global, and "what does this point
    # claim" could not be answered without running the program that produced it.
    improver_request = improver_readiness.ImproverRequest(
        args.improver, args.improver_model, args.improver_effort)
    # Outputs inherited by method subprocesses intentionally persist after this run.
    for key, value in improver_request.environment_patch().items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    improver_ready = improver_readiness.resolve_readiness(
        improver_request, reporter=console, load_config=load_improvers,
        resolve_config=resolve_improver, provider_url=_cli_provider_base_url,
        probe=_probe_tool_calls)
    if improver_ready.refused:
        return 2
    improver = improver_ready.declaration
    improver_tool_calls = improver_ready.tool_calls
    improver_tool_calls_detail = improver_ready.detail

    # **The capability check goes first, and the order is the whole point.**
    #
    # Both of these refuse before anything is paid for, so either order is "correct" in
    # the sense that nothing runs. They are not equivalent to a reader: a harness that
    # declares it cannot operate on this environment is **unfixable by importing
    # anything**, while a missing image is fixed by one command. Reported the other way
    # round -- which is what happened, because the image resolution was moved up here
    # for `run_meta.json`'s sake and the gate was left behind -- a user whose harness
    # could never work was told to import an image, imported it, came back, and was told
    # to import the next one. Measured: two UI runs, two different missing images, and
    # the real reason (the harness declares `files` only) never appeared.
    #
    # Read from `harness_src` rather than the staged `work`, because staging happens
    # later and nothing has copied anything yet. The two are the same bytes at this
    # point: `stage_workspace` is a plain copy.
    # A harness that declares dependencies, running where none were provided.
    #
    # A warning rather than a refusal, because it is not always a problem: a `files`
    # dataset runs the harness on the platform's interpreter, which may already have
    # them. What it must not be is silent -- measured, `loop` on Terminal-Bench died on
    # its first model call and the run reported nothing but `score 0.000`, which reads
    # exactly like a method that decided not to edit.
    try:
        managed_source = has_python_runtime(harness_src)
        if not managed_source:
            _warn_missing_overlay(harness_src, envs, args.dataset)
    except (OSError, TypeError, ValueError) as exc:
        if not _managed_python_intent(harness_src):
            raise
        return _refuse_managed_source(run_dir, harness_src, exc)

    man_at_door = manifest(harness_src)
    problem = _check_env_kinds(man_at_door, _required_env_kinds(envs))
    if problem:
        console.note(f"refusing to run: {problem}", level="error")
        return 2

    # Pin the images here, before anything describes the run. The first version of this
    # resolved them further down, next to the `env_kinds` gate -- and the header and
    # `run_meta.json` were both written before it, so they reported `image_digest: null`
    # and named the image's *tag* while every curve point carried the digest. Two records
    # of one run disagreeing about what it ran in is the exact failure §2.5.4 exists to
    # prevent.
    # The selected image identities were resolved before verification context capture.

    # The container boundary is built once, at the door, for the same reason the bwrap
    # one is: a boundary that cannot be built must stop the run before it is paid for,
    # and must never degrade into running on the host.
    if not boundary_readiness.check_container_boundaries(envs, reporter=console):
        return 2

    if split is None:
        console.note(
            f"dataset {args.dataset!r} declares no train/eval split: every task is "
            f"scored and the method is shown traces for all of them. The run is "
            f"still measured, but a score from it cannot support a claim about "
            f"generalisation -- the method studied its own exam. Pass `--split default` "
            f"for the platform's documented division, `--split official` to score the "
            f"benchmark's own evaluation set, or `--split <file.json>` to supply one.",
            level="warn")

    # The harness's workspace is deliberately NOT under the platform's own tree.
    # It used to be `runs/<id>/workspace`, which put it two `..` from the driver,
    # the scorer and `.env` -- measured: a harness could read all three and write
    # anywhere in the platform directory. A subprocess cannot walk out of a tree it
    # was never placed inside, so this is the half of isolation that actually holds
    # without a container. The run record stays under `runs/`; only the working
    # tree moves.
    #
    # Checked before anything is created, so a refusal leaves no partial state
    # inside the platform for the next run to trip over.
    work = (Path(args.work_root) / run_id / "workspace").resolve()
    if not workspace_is_outside_platform(work, PLATFORM_ROOT):
        print(f"refusing to run: the workspace {work} is inside the platform "
              f"({PLATFORM_ROOT}). A harness there can read the driver and .env "
              f"and write anywhere in the platform. Pass --work-root outside it.",
              file=sys.stderr)
        return 2

    # A known model/method misconfiguration is refused here too -- before H0 can spend a
    # single generation -- but *after* the isolation check above, because where the run writes
    # is a safety property and this is a configuration one. Ordered the other way round, a run
    # with its workspace inside the platform reported the missing method endpoint and hid the
    # violation (`test_isolation` caught that when the preflight was first merged).
    args.harness_dir = str(harness_src)
    local_checks = readiness.configuration_checks(args)
    if args.check_net:
        local_checks.extend(readiness.endpoint_checks(args))
    failed_configuration = [check for check in local_checks if check.required and not check.ok]
    if failed_configuration:
        print(readiness.render(local_checks))
        print('refusing to run: model/method configuration failed before H0', file=sys.stderr)
        return 2

    if args.resume and resume_state is None:
        work.parent.mkdir(parents=True, exist_ok=True)
    curve_path = run_dir / "curve.jsonl"

    # The sandbox is verified before the first paid call, not once per task. A
    # namespace that cannot be built stops the run at the door; the alternative is
    # a run that quietly measured something other than what its metadata claims.
    boundary = boundary_readiness.check_sandbox_boundary(
        args.sandbox, work.parent.parent, envs, reporter=console)
    if boundary is None:
        return 2
    sandbox_state, sandbox_line = boundary.state, boundary.line

    # `run_one` needs the work root to know which tree to leave visible. Exported
    # rather than passed through every layer: it is a property of this run, and the
    # runner is the only reader. Kept as an environment *write*: the value is produced
    # by this run (below), not read as configuration, and it must reach subprocesses.
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work.parent.parent)
    # The environment's seed is a run-level fact, and it has to exist before any
    # environment is described. `secrets` rather than `random`: this seeds an
    # environment a method may be trying to fit to, and a predictable seed is a
    # predictable environment. A write for the same reason as the work root above.
    os.environ["HARNESSGRAD_SEED"] = resumed_seed or args.seed or secrets.token_hex(8)
    if any(services_mod.requires_model(spec.get("services") or [])
           for spec in envs.values()):
        # An environment-side model is a third budget. Refused when a service needs one
        # and none is configured, rather than passed as an empty string: a simulator
        # that silently has no model produces a task that fails for a reason nothing
        # records, and §2.5.7 requires `env.model` to be non-null exactly when a
        # service declared it needs one.
        if not platform_config.current().environment.model:
            console.note(
                "refusing to run: a service declares needs_model but HG_ENV_MODEL is "
                "unset. The environment's model is its own budget -- configure it in "
                ".env (HG_ENV_MODEL, HG_ENV_API_KEY) rather than reusing the agent's.",
                level="error")
            return 2

    # A containerised harness whose model is not on this host needs the internet to
    # reach it, and a task that did not ask for the internet must not silently get it --
    # that would change what the task *is* (§2.5.4). Refused rather than attempted: the
    # harness would have no model, so every score would be a zero that means nothing,
    # and it would look like a weak harness.
    if "exec" in _required_env_kinds(envs) \
            and platform_config.current().agent.backend != "mock":
        base_url = platform_config.current().agent.base_url or ""
        if base_url and not modelgate.is_host_local(base_url):
            offline = sorted(tid for tid, spec in envs.items()
                             if spec.get("kind") == "exec"
                             and spec.get("network") != "bridge")
            if offline:
                console.note(
                    f"refusing to run: the agent's model is at {base_url!r}, which is "
                    f"not on this host, so a containerised harness needs the internet "
                    f"to reach it. Task(s) {offline} are 'exec' with network != "
                    f"'bridge', so the harness would have no model at all. Either set "
                    f"network='bridge' and accept that the task is a networked task, or "
                    f"point HG_AGENT_BASE_URL at a model on this host.", level="error")
                return 2

    # Recheck the captured scorer inputs immediately before any resume
    # publication, event attachment or metadata write, even without progress.
    if args.resume or args.side == 'eval':
        try:
            current = verification_context.capture(tasks_all, scorable, setups, verifiers, envs, sandbox=args.sandbox)
            _require_verification_context(args.verification_context, current)
        except (OSError, TypeError, ValueError) as exc:
            print(f"refusing run: verification context changed: {exc}", file=sys.stderr)
            return 2
    if resume_state is not None:
        resume_lease.publish()
    if args.resume or args.side == 'eval':
        console.attach(run_dir / "events.jsonl", append=bool(args.resume))


    # A run ID alone cannot establish ownership of backend objects across roots.
    # Leave pre-existing objects untouched; backend creation will report collisions.

    web_declarations = [spec.get("web") for spec in envs.values() if spec.get("web")]
    web_record = {}
    if web_declarations:
        web_search, web_fetch, web_endpoint = web_gateway_mod.selected_providers()
        web_record = {"web_search_provider": web_search,
                      "web_fetch_provider": web_fetch,
                      "web_searxng_url": web_endpoint}

    # Preserve the original keyword observation order before the pure projection.
    metadata_identities = run_metadata.identity_fields(run_metadata.IdentityRecords(
        args.scoring_identity, args.dataset_runtime_identity, args.verification_context, args.harness_runtime_identity))
    metadata_diagnostic = run_metadata.diagnostic_fields(
        run_metadata.DiagnosticRecord(eligible_ids, side_ids, policy_evidence) if staged else None)
    metadata_workspace = run_metadata.WorkspaceRecord(
        str(work), str(work.parent.parent), str(PLATFORM_ROOT), sandbox_state)
    metadata_run = platform_config.run_config(args, run_id, seed=os.environ.get("HARNESSGRAD_SEED"), mode=mode)
    metadata_config = platform_config.dump(run=metadata_run)
    metadata_budget = budget_binding.metadata() if budget_binding is not None else None
    metadata_env = _env_record(envs, [])
    metadata_split = run_metadata.SplitRecord(split, split_source, datasets.split_sha(split) if split else None, split_protocol)
    metadata_origin = ({"run_id": args.from_run, "round": args.from_round} if getattr(args, "from_run", None) else None)
    metadata_platform = _platform_content_sha()
    metadata_fields = run_metadata.planned_run_fields(metadata_run, identities=metadata_identities,
        diagnostic=metadata_diagnostic, enforcement=enforcement, workspace=metadata_workspace,
        config_record=metadata_config, budget_record=metadata_budget, env_record=metadata_env, split=metadata_split,
        provenance=run_metadata.ProvenanceRecord(harness_source, scenario_receipt, metadata_origin,
            metadata_platform, oracle_source, oracle_coverage),
        attribution=run_metadata.AttributionRecord(model["model"], model["backend"], FRAMEWORK_VERSION,
            improver, improver_tool_calls, improver_tool_calls_detail), web_record=web_record)
    _write_run_meta(run_dir, fresh=not getattr(args, "resume", False), **metadata_fields)

    workspace_ops = WorkspaceOps(stage=stage_workspace, commit=commit_state)
    preparation_journal = PreparationJournal(run_dir, run_id, console)
    try:
        man = stage_owned_source(OwnedSource(
            harness_src, harness_source, source_receipt, reviewed_original,
            args.expected_harness_sha256, args.expected_harness_filesystem_sha256,
            getattr(args, '_resume_initial_source', None) is not None), work,
            restored=resume_state is not None, ops=workspace_ops, journal=preparation_journal)
        if not args.resume:
            prepared = prepare_committed_candidate(work, PreparationOptions(
                args.side, args.from_run, args.h0_from, Path(args.runs_root),
                args.harness_runtime_binding, args.sandbox), envs=envs, setups=setups,
                eval_from=eval_from, ops=workspace_ops, journal=preparation_journal)
            if prepared.preparation is not None:
                args.harness_preparation = prepared.preparation
            args.harness_runtime_identity = prepared.binding
            args.current_harness_runtime_identity = prepared.binding
            preparation_journal.publish(prepared)
        bind_external_candidate(work, preparation_journal)
    except CandidateStageRefused as exc:
        exc.report()
        return 2
    # `_check_env_kinds` already ran, against this same manifest read from the source --
    # see the note beside `envs, env_problem = _resolve_envs(envs)` for why it has to be
    # the first refusal rather than this one.

    console.header(
        run_id=run_id, harness=man["name"], version=man["version"], mode=mode,
        dataset=args.dataset, n_tasks=len(tasks_all),
        model=model["model"], backend=model["backend"], live=model["live"],
        sandbox=sandbox_line, split=split, argv=sys.argv, side=args.side)

    if args.side == "eval":
        result = _run_eval_side(
            work=work, man=man, run_id=run_id, run_dir=run_dir, tasks=tasks_all,
            scorable=scorable, setups=setups, verifiers=verifiers, envs=envs,
            side_ids=side_ids, split=split, args=args, eval_from=eval_from,
            improver=improver, after_evaluation=save_policy_receipt)
        if budget_binding is not None:
            _write_run_meta(run_dir, budget=budget_binding.metadata())
        return run_completion.finish_eval(run_completion.EvalFinishOptions(
            args.fail_on_incomplete, _summary_options(args)), run_dir, result, console)

    cache: dict = {}
    # `env_generation_tokens` is its own axis rather than part of `generation_tokens`:
    # the x-axis is compute, and an environment that spends on every task adds a
    # constant per round. Adding it to the *method's* number would move the axis by an
    # amount the method did not spend (INTERFACE.md §2.5.7).
    cumulative = {"evaluation_trials": 0, "generation_tokens": 0,
                  "env_generation_tokens": 0, "wall_clock_s": 0.0}
    preparation_seconds = float((args.harness_preparation or {}).get("seconds") or 0.0)
    if args.harness_preparation is not None and not args.resume:
        cumulative["harness_preparation_wall_clock_s"] = preparation_seconds
        cumulative["wall_clock_s"] += preparation_seconds

    # Staged runs already fixed their task set; score-based policies select after H0.
    # Committed **before** the measurement, so the measurement can be cached under the
    # content it actually measured.
    #
    # The cache key used to be the literal string `"H0"`, while every later round keyed on
    # the content sha -- so a candidate that was byte-identical to H0 (a control method,
    # or a method whose candidates were all rejected) missed the cache and paid for a
    # second full measurement of the same tree. Measured on `B2-noop` (2026-10-04): round
    # 1 re-ran all five tasks and scored 0.467 against round 0's 0.267, on the same
    # `harness_sha`, and the second number was read as the control "moving". Which of
    # those two numbers a run reports must not depend on whether a cache key happened to
    # match: content-addressed, an unchanged tree has one measurement, and the spread of
    # a measurement is what `--trials` is for.
    # **A resumed run rebuilds round 0 from the record instead of measuring it.**
    # `--h0-from` shares another run's first score; `--resume` continues *this* run,
    # so the same rebuild applies to its own curve, and no state is measured twice.
    if resume_state is not None:
        baseline_state = run_dir / "state_after_round0"
        if baseline_state.is_dir():
            man = manifest(baseline_state)
    h0_options = h0_planning.H0Options(args.dataset, args.sampling, args.side, args.trials,
                                       args.jobs, args.gpu_budget, args.sandbox)
    h0_identities = h0_planning.H0Identities(args.scoring_identity, args.dataset_runtime_identity,
        args.verification_context, args.harness_runtime_identity, args.current_harness_runtime_identity)
    h0_attribution = h0_planning.RunAttribution(run_id, mode, improver)
    if resume_state is None:
        h0_sha = h0_planning.commit_h0_source(work, commit_state)
        t0 = time.time()
        shared_point = None
        try:
            if args.h0_from:
                donor = h0_planning.load_h0_donor(args.h0_from, Path(args.runs_root), h0_sha)
                h0_planning.admit_h0_donor_records(donor, work, h0_identities, h0_options, setups=setups, envs=envs)
                reused = h0_planning.materialize_shared_h0(
                    donor, run_dir, h0_sha, man, h0_attribution, h0_options, console)
                base, shared_point = reused.base, reused.point
            else:
                base = h0_planning.measure_h0(evaluate, work, tasks_all, scorable, cache, h0_sha,
                    h0_options, h0_identities, run_dir=run_dir, attribution=h0_attribution,
                    run_seed=os.environ["HARNESSGRAD_SEED"], setups=setups, verifiers=verifiers, envs=envs,
                    diagnostic_authorization=diagnostic_authorization)
                h0_planning.write_raw_h0(run_dir, run_id, h0_sha, base, h0_identities, console)
        except h0_planning.H0Refused as exc:
            exc.report(console)
            return 2
        if enforcement is not None:
            # Preserve raw execution/scorer facts with their comparison prohibition.
            # No aggregate score is published and the curve path below cannot run.
            base["enforcement"] = enforcement
            with (run_dir / "diagnostic_execution.json").open("x") as stream:
                json.dump(base, stream, indent=2)
        complete_measurement = save_policy_receipt(base)
        _require_untampered(base)
        _report_invalid(base, args.dataset)
        _report_harness_failed(base, args.dataset)
        if not complete_measurement:
            console.note("refusing staged curve: lifecycle publication gate or measurement incomplete; "
                         "invalid/unmeasured outcomes are preserved in the receipt",
                         level="error")
            return 2
        if not base["per_task"]:
            # Nothing was measurable, so there is no curve to draw. Refused rather than
            # recorded as a point: `mean([])` is 0.0, and a zero here would say "the
            # harness scored nothing" about a run in which the harness never ran
            # (INTERFACE.md §2.5.7).
            console.note(
                f"refusing to run: none of the {len(tasks_all)} tasks in "
                f"{args.dataset!r} produced a scoreable result. The curve would have to "
                f"report a score for a task set that was never measured.", level="error")
            return 2
        # Donor cost belongs to the referenced measurement, not this invocation.
        # No H0 evaluation means known zero new model/environment spend, even
        # when the donor's historical usage is unknown.
        if shared_point is None:
            cumulative["evaluation_trials"] += len(tasks_all)
            environment_tokens = _cost_of(base, len(tasks_all), 0)["env_generation_tokens"]
            previous_environment_tokens = cumulative.get("env_generation_tokens")
            cumulative["env_generation_tokens"] = (
                previous_environment_tokens + environment_tokens
                if previous_environment_tokens is not None and environment_tokens is not None else None)
        cumulative["wall_clock_s"] += time.time() - t0

        try:
            h0_selection = h0_planning.select_after_h0(base, tasks_all, side_ids, split, h0_options, console)
        except h0_planning.H0Refused as exc:
            exc.report(console)
            return 2
        task_ids, train_ids = h0_selection.task_ids, h0_selection.train_ids
        tasks, sampling = h0_selection.tasks, h0_selection.sampling
    else:
        base = resume_state["base"]
        h0_sha = resume_state["h0_sha"]
        shared_point = None
        # **The task set is the one this run already used.** Re-selecting would let a resume
        # quietly measure a different set of tasks under the same curve -- and the original
        # selection is on the record (`sampling.task_ids` of round 0), so there is nothing
        # to re-derive.
        task_ids = list((resume_state["existing"][0].get("sampling") or {}).get("task_ids") or [])
        if not task_ids:
            task_ids = select_task_set(base["per_task"], args.sampling,
                                       within=(side_ids or None))
        if not task_ids:
            console.note("refusing --resume: the run's round 0 recorded no task set",
                         level="error")
            return 2
        train_ids = list(task_ids) if args.side == "train" else []
        tasks = [t for t in tasks_all if t["task_id"] in set(task_ids + train_ids)]
        sampling = {"dataset": args.dataset, "policy": args.sampling, "task_ids": task_ids,
                    "selection_basis_round": 0,
                    "selected_within": args.side if split else "all"}
    # Round 0's state too: "measure H0 on the exam" is the baseline every later eval is
    # read against, and it needs a state to measure exactly like any other round.
    # (`h0_sha` was committed before the measurement, above.)
    if resume_state is None and not (run_dir / "state_after_round0").exists():
        _save_round_state(run_dir, 0, work)
    if resume_state is not None:
        # The points already on disk are the record; a resumed run appends to them.
        curve = list(resume_state["existing"])
    elif shared_point is not None:
        # The donor's point verbatim except for the id: its `identity` describes *how that
        # number was produced* (jobs, budget, model), which is the honest record, and
        # `shared_from` says which run produced it.
        shared_point["current_run_cost"] = {
            "version": 1, "source": "shared_h0_no_execution", "evaluation_trials": 0,
            "harness_tokens": 0, "harness_model_calls": 0, "env_generation_tokens": 0,
            "wall_clock_s": cumulative["wall_clock_s"]}
        curve = [shared_point]
    else:
        curve = [h0_planning.build_h0_point(base, h0_sha, man, h0_attribution, h0_options,
            h0_identities, h0_selection, split, envs, cumulative, len(tasks_all))]
    if resume_state is None and args.harness_preparation is not None:
        curve[0]["harness_preparation"] = args.harness_preparation
        current_cost = curve[0]["current_run_cost"] if shared_point is not None else curve[0]["cost"]
        current_cost["harness_preparation_wall_clock_s"] = preparation_seconds
    # The base harness is always evaluated by the platform, so its point is a
    # platform measurement like any other. Leaving the field unset made the
    # exported record claim the score was the method's -- the opposite of true.
    curve[0]["measured_by_platform"] = True
    # The verifier's *kind*, never its spec. This point is written into the method's
    # channel, so an argv or an expected answer here would hand a method the grade it
    # is being measured against -- the same leak the eval traces are withheld for.
    curve[0]["verifier_kinds"] = verifier_kinds
    console.round_line(0, "H0 (base harness)", curve[0])
    # Written now, not only at the end. A run that is interrupted used to leave the
    # *previous* run's curve.jsonl on disk, so the console showed a curve belonging
    # to a different method than the one in run_meta -- read as "I picked RRSI and
    # it ran DGM". The record has to be durable at the moment it is produced.
    _write_curve(curve_path, curve)
    _save_verdicts(run_dir, 0, base, [t["task_id"] for t in tasks])
    _save_traces(run_dir, 0, base.get("traces") or {}, train_ids, split,
                 trial_traces=base.get("trial_traces"))

    # 在分派之前挡住相对的入口路径。放在 main 里,因为 `_run_mode_a/b` 返回的是
    # 曲线,不是退出码 —— 第一次把检查放进 mode 函数时,`return 2` 让 main 拿一个
    # 整数当曲线去迭代,报的是 "TypeError: 'int' object is not iterable" 而不是
    # 那句本该出现的拒绝理由。
    if args.method_entrypoint:
        problem = _check_method_entrypoint(shlex.split(args.method_entrypoint))
        if problem:
            console.note(f"refusing to run:\n  {problem}", level="error")
            return 2

    # **One engine.** `--mode A` and `--mode B` selected two loops, and the difference
    # they encoded was never a property of the *method*: it was (a) which tree the method
    # may build on -- the incumbent or the pristine base -- and (b) whether it sees a score
    # between the states it submits. Both are decisions a method makes, and both are now on
    # the record: `steps_in_submission > 1` means it saw no score between them, and the
    # request carries the pristine tree as well as the incumbent. So the flag is read as
    # *nothing*: a B-flavoured method is simply one that submits its whole sequence in one
    # call, which the loop already allows.
    curve = _run_mode_a(args, work, man, run_id, mode, curve, h0_sha,
                        base, tasks, task_ids, scorable, sampling, cache,
                        cumulative, train_ids, split, setups, verifiers,
                        verifier_kinds, envs, improver=improver,
                        start_steps=(resume_state["last"] if resume_state else 0),
                        resumed_from=(resume_state["last"] if resume_state else None))

    # **A resumed curve says how it was stitched.** `from_round` and the two content
    # addresses are the whole claim: the state re-staged from the record reproduced the
    # address the earlier points already named, and the platform content matched, so no
    # second platform version entered this curve.
    if resume_state is not None:
        _write_run_meta(run_dir, resumed=[{
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "from_round": resume_state["last"],
            "recorded_sha": resume_state["recorded_sha"],
            "staged_sha": resume_state["staged_sha"],
            "platform_content_sha": _platform_content_sha(),
        }])

    if budget_binding is not None:
        _write_run_meta(run_dir, budget=budget_binding.metadata())

    _write_curve(curve_path, curve)
    # **The record is final here: the last `_write_curve` has landed and nothing below
    # changes it.** The page is derived from what is on disk, so it is written now, before
    # the console prints the table it describes. This is the only call site -- a refusal,
    # an exception or a run that measured no curve never reaches it -- and a failure inside
    # it is one line on stderr that never reaches the exit code (`_write_end_summary`).
    return run_completion.finish_train(run_completion.TrainFinishOptions(
        args.rounds, args.fail_on_incomplete, _summary_options(args)), run_dir, curve_path, curve, split, work, console)


def main() -> int:
    attempt = {}
    detail = "driver interrupted before evaluation completed; see command output"
    try:
        result = _main(attempt)
        detail = f"driver exited {result} before evaluation completed; see command output"
        return result
    except BaseException as exc:
        detail = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if "finalize" in attempt:
            attempt["finalize"](detail)


if __name__ == "__main__":
    # A tampered run has no partial value to salvage: the curve it printed would
    # be the attacker's self-assessment. Exit non-zero and say so.
    try:
        sys.exit(main())
    except PlatformTampered:
        sys.exit(3)
