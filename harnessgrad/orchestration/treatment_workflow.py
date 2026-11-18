"""Compose early treatment capture and the runtime identity projection."""
from __future__ import annotations

from pathlib import Path
import shlex

from harnessgrad.orchestration import improver_readiness, treatment_identity


def runtime_projection(args, *, environment, dump_config, load_profile):
    """Project treatment inputs without including continuation or output controls."""
    operational = {'rounds', 'resume', 'resume_across_platforms', 'run_id', 'runs_root',
                   'work_root', 'show_config', 'check_only', 'prepare_only', 'check_net',
                   'treatment_manifest', 'no_summary', 'summary', 'show_summary',
                   'resume_supplement_run', 'resume_batch_supplement'}
    arguments = {key: str(value) if isinstance(value, Path) else value
                 for key, value in args._namespace_args.items() if key not in operational}
    arguments.pop('method_entrypoint', None)
    arguments['method_argv'] = shlex.split(args.method_entrypoint or '')
    profile = load_profile(args.budget_profile) if args.budget_profile else None
    return {'platform_configuration': dump_config(),
            'arguments': arguments, 'budget_profile_sha256': profile.digest if profile is not None else None,
            'method_limits': {key: environment.get(key) for key in
                              ('HG_META_ROUND_TOKEN_LIMIT', 'HG_META_RUN_TOKEN_LIMIT')}}


def read_start_metadata(args, *, read_metadata):
    """Load historical state before treating validation errors as refusals."""
    if not args.resume:
        return {}
    mode = 'unified' if args.mode in ('A', 'B') else args.mode
    run_id = args.run_id or f"{args.harness}-{args.dataset}-{args.sampling}-m{mode}"
    return read_metadata(Path(args.runs_root) / run_id)


def capture_start(args, *, environment, metadata):
    """Capture early treatment identity and enforce the existing resume binding."""
    saved = metadata.get(treatment_identity.KEY)
    declared = bool(metadata.get('treatment_manifest_declared'))
    treatment_environment = dict(environment)
    for key, value in improver_readiness.ImproverRequest(
            args.improver, args.improver_model, args.improver_effort).environment_patch().items():
        if value is None:
            treatment_environment.pop(key, None)
        else:
            treatment_environment[key] = value
    start = treatment_identity.capture(args.treatment_manifest, environment=treatment_environment)
    if args.resume:
        treatment_identity.require_same(saved, start, declared=declared, early=True)
    return start, saved, declared
