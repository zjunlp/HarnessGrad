"""Run argument declarations and syntax validation, without execution orchestration."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

from .experiment_config import config_help, expand_experiment_argv


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "Ordinary runs have no universal filesystem-quota or backend-observer\n"
            "prerequisite. Existing declared-policy refusals still apply.\n"
            "Ordinary local development runs as your user. Native/files demos and --help\n"
            "require no sudo, dedicated quota volume, root Docker access, or strict runtime mode.\n"
            "Sandboxed runs still require working bubblewrap/user namespaces; container-backed\n"
            "tasks and image/overlay builds have separate Docker prerequisites.\n"
            "Local/demo success grants no production/H0 authorization. Declared runtime policies\n"
            "still fail closed without trusted backend observations: missing evidence remains\n"
            "blocked and invalid/unmeasured, never an ordinary zero score.\n"
            "Synthetic checks do not authorize production diagnostics or H0."),
        epilog=config_help())
    source_args = ap.add_mutually_exclusive_group()
    source_args.add_argument("--harness", default="loop")
    source_args.add_argument("--harness-path", metavar="DIR",
                             help="external harness directory (absolute path)")
    ap.add_argument("--expected-harness-sha256", metavar="SHA256",
                    help="require reviewed source bytes with --harness-path before any model/task")
    ap.add_argument("--expected-harness-filesystem-sha256", metavar="SHA256",
                    help="opt-in normalized owned-source filesystem identity; requires expected source SHA256")
    ap.add_argument("--scenario", metavar="NAME", help="reviewed JSON scenario from scenarios/")
    ap.add_argument("--dataset", default=argparse.SUPPRESS)
    ap.add_argument("--sampling", default=argparse.SUPPRESS,
                    help="diagnostic (one task, default) | pilot_10pct (temporary rerun: "
                         "ceil(10%%), capped at 30; reviewed diagnostic required) | "
                         "all | below_X | above_X | random_N. "
                         "For the local demo, explicitly select all; this grants no "
                         "production/H0 authorization")
    ap.add_argument("--policy-evidence", default=None,
                    help="required for diagnostic/pilot_10pct sampling: staged policy "
                         "JSON with context and passing dataset gates; "
                         "pilot_10pct also requires receipt and separate hash-bound review")
    ap.add_argument("--authorize-unobserved-diagnostic", metavar="OPERATOR_REASON",
                    help="explicitly authorize one train-side diagnostic attempt with policy "
                         "enforcement NOT observed; no measured score, curve or production authorization")
    ap.add_argument("--rounds", type=int, default=argparse.SUPPRESS,
                    help="improvement rounds (default 0 for staged runs, 3 otherwise)")
    #: How many times each (harness, task) pair is measured. One is one sample, and one
    #: sample cannot tell an edit's effect from the noise: measured here, the same
    #: unmodified harness scored 0.333 in one run and 0.000 in the next because the agent
    #: model intermittently emits a tool call the harness cannot parse. Costs `trials`x
    #: the wall clock -- 88% of a run is the harness loop -- so a first look uses 1 and a
    #: claim about an edit uses 3.
    ap.add_argument("--trials", type=int, default=argparse.SUPPRESS,
                    help="measure each task N times; the point carries n_trials and "
                         "score_std. Default 1 (one sample, no spread recorded)")
    #: How many tasks are measured **at the same time**. The other axis from `--trials`,
    #: and they multiply: trials buy a spread, jobs buy wall clock. Measured here, 87.8%
    #: of a run's wall clock is the harness's own agent loop (2578 s of 2936 s on a
    #: three-task round) and the platform does nothing while it waits, so a task set run
    #: four at a time is close to four times shorter. The tasks are independent by
    #: construction -- own container, own network, own gateway, own workspace -- but they
    #: share the model endpoint and this host's CPU, which is why the default stays 1: a
    #: number produced under contention is not comparable with one produced without it,
    #: and the run records the value it used.
    ap.add_argument("--gpu-budget", type=int, default=None, metavar="N",
                    help="machine-wide ceiling on concurrently *measured* tasks across every "
                         "run sharing this host, enforced with file locks "
                         "(harnessgrad/slots.py). `--jobs` stays this run's own ceiling; the "
                         "budget is what keeps N runs from each measuring `--jobs` at once. "
                         "Unset keeps the old behaviour: `--jobs` alone")
    ap.add_argument("--gpu-budget-dir", default=None, metavar="DIR",
                    help="where the budget's slot locks live (default: $TMPDIR/hg-gpu-slots). "
                         "Two batches with different capacities must not share a directory")
    ap.add_argument("--jobs", type=int, default=argparse.SUPPRESS,
                    help="measure up to N tasks concurrently (default 1). The tasks share "
                         "one model endpoint and one host, so raise it deliberately")
    ap.add_argument("--mode", default="A", choices=["A", "B"],
                    help="DEPRECATED and ignored: there is one engine. A method that "
                         "submits its whole sequence in one call behaves as mode B did; "
                         "one that submits a step per call behaves as mode A did, and "
                         "`steps_in_submission` on each point says which happened.")
    ap.add_argument("--method-timeout", type=int, default=86400,
                    help="mode B: seconds before the method's own loop is killed")
    # **哪个改进器。** 方法自带改进器时这三个开关是无害的;用一种"把方法拆成规则+
    # 改进器"的方法时,它们决定谁来做那一步 —— 而解析出来的
    # 版本和哈希会进每个曲线点(INTERFACE.md §4.49)。放在命令行而不是只读配置:
    # 一次实验"用哪个改进器"正是最该被显式记录、最不该靠环境变量碰运气的东西。
    ap.add_argument("--improver", default=None,
                    help="harnessgrad/improvers/improvers.json 里的名字,或一个可执行文件的路径")
    ap.add_argument("--improver-model", default=None, help="覆盖改进器自己的模型")
    ap.add_argument("--improver-effort", default=None,
                    help="codex 系:low|medium|high|xhigh")
    ap.add_argument("--method-entrypoint", default=None,
                    help="mode B only: argv for the method's own runner, e.g. "
                         "'python /path/to/rrsi_wrap.py'. Defaults to "
                         "harness.json's improve_entrypoint.")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--budget-profile", default=None,
                    help="JSON trusted-dispatch profile; required for approved Task25 runs")
    ap.add_argument("--budget-ledger", default=None,
                    help="path to the process-shared trusted-dispatch ledger")
    ap.add_argument("--budget-arm", default="agent",
                    help="identity of this run arm in the trusted-dispatch ledger")
    ap.add_argument("--tasks", default=None,
                    help="a comma-separated subset of task ids to run, instead of the "
                         "whole side. Refused if any id is not in the dataset: silently "
                         "dropping one would produce a curve whose task count does not "
                         "match what was asked for, which is the kind of difference "
                         "nobody notices until they compare two runs.")
    ap.add_argument("--side", choices=("train", "eval"), default=argparse.SUPPRESS,
                    help="which side of the dataset's split this run is about. A run "
                         "evaluates ONE side: a train run evolves the harness and shows "
                         "the method the traces of the tasks it is scored on, and an "
                         "eval run measures one state of a train run on the exam. They "
                         "are separate runs so that the exam's traces are not in the "
                         "train run at all -- an absence, not a withheld field.")
    ap.add_argument("--pool", default=argparse.SUPPRESS,
                    help="existing adapter pool selector (e.g. appworld dev); no inferred pools")
    ap.add_argument("--split", default=argparse.SUPPRESS,
                    help="which division of the dataset this run uses, when the caller "
                         "does not want the one the dataset declares. `declared` (the "
                         "default) uses the adapter's own SPLIT; `default` uses the "
                         "platform's documented rule (a deterministic third, by hash of "
                         "the task id or the adapter's declared group); `official` uses "
                         "the benchmark's own evaluation set "
                         "as the adapter declares it, which is the comparable run -- the "
                         "whole official set, with no study material in this pool. Anything "
                         "else is a custom split manifest from `harnessgrad split create`, or a legacy JSON file {\"train\": [...], \"eval\": [...]}. "
                         "The division is a property of the experiment protocol, not of the "
                         "benchmark, so the run may supply it; whichever is used is recorded "
                         "on the run as `split_source` and `split_sha`, and two points are "
                         "comparable only when those agree.")
    ap.add_argument("--from-run", default=None,
                    help="--side eval: the train run whose harness state to measure")
    ap.add_argument("--reuse-identical", action="store_true",
                    help="a candidate whose content address equals an already-measured "
                         "state still gets a curve point, but that point reuses the earlier "
                         "measurement instead of spending another task-set run. Off by "
                         "default: it changes what a re-measured state means, and the "
                         "isolation tests rely on the measurement actually happening")
    ap.add_argument("--resume", action="store_true",
                    help="continue an interrupted run instead of starting clean: the train "
                         "side continues from its last measured round, the eval side scores "
                         "only the tasks whose progress file is missing")
    ap.add_argument("--resume-across-platforms", default=None, metavar="REASON",
                    help="--resume: continue a run whose `platform_content_sha` is not this "
                         "tree's, after a human reviewed what changed. Refused by default; the "
                         "REASON is recorded with both hashes and both git shas under the run's "
                         "`platform-seams/`, so the curve says which code measured which rounds "
                         "instead of hiding it")
    ap.add_argument("--from-round", type=int, default=None,
                    help="--side eval: which round of that run (default: its last)")
    ap.add_argument("--h0-from", default=None, metavar="RUN_ID",
                    help="reuse another run's round-0 measurement instead of measuring one. "
                         "Same base tree, same study tasks, so every method must be offered "
                         "the *same* first score: re-measuring it per method lets batching "
                         "non-determinism hand one method a different number and a different "
                         "trace set, which is the one thing a comparison cannot absorb. The "
                         "reused point records `shared_from` naming the run it came from. "
                         "Refused unless both runs share the base harness sha")
    ap.add_argument("--baseline", default=None,
                    help="a frozen reference point to measure this run against, as a JSON "
                         "file holding one curve point (the object a `curve.jsonl` line "
                         "carries). Declared rather than inferred: without it the baseline "
                         "is round 0 of this same run, which is the control for one method "
                         "and a different reference for every other one. A frozen reference "
                         "puts every method on one axis, which is what cross-method "
                         "comparison needs -- and because nothing re-measures it, this run "
                         "checks and records whether it still describes the run in front of "
                         "it (agent model, backend, harness family, framework version and "
                         "the task set). Measure the frozen reference well: it is amortized "
                         "over every method that uses it, so its own sampling noise is "
                         "baked into all of them.")
    ap.add_argument("--seed", default=None,
                    help="the environment's seed (INTERFACE.md §2.5.7). A per-task "
                         "seed is derived from it and given to services that declare "
                         "`needs_seed`; recorded on every curve point. Drawn at random "
                         "when omitted, and re-supplyable so a recorded run can be "
                         "reproduced.")
    ap.set_defaults(sandbox=True)
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--no-sandbox", dest="sandbox", action="store_false",
                    help="run the harness WITHOUT a mount namespace. A sandboxed "
                         "harness cannot see the platform at all; without one it "
                         "can read the driver and .env, and only the hash check in "
                         "eval/integrity.py stands between it and the scorer. "
                         "Explicit debugging opt-out, never an automatic fallback. "
                         "Exec tasks retain their container boundary and declared-policy "
                         "refusals still apply. Numbers are not comparable across the two modes.")
    ap.add_argument("--work-root", default="../harnessgrad_work",
                    help="where harness working trees live; must be OUTSIDE the "
                         "platform tree, or a harness can reach the platform")
    ap.add_argument("--show-config", action="store_true",
                    help="print the effective configuration (the declared `HG_*` "
                         "environment variables, after `.env` is folded in) as JSON "
                         "and exit 0 without running anything")
    # The page a finished run leaves behind. Documented here, beside `--show-config`,
    # because both answer a question a reader has *after* the numbers exist: what did
    # this run configure, and what does its record actually say. Neither can change a
    # score -- the summary is derived from curve.jsonl and its failure never reaches the
    # exit code -- so the help says what they do, not what they might invalidate.
    ap.add_argument("--no-summary", action="store_true",
                    help="do not write end-summary.md / end-summary.json when the run "
                         "finishes. A finished run writes that one page beside its curve "
                         "by default (tools/run_summary.py); this switches it off. The "
                         "page is derived from curve.jsonl, so it can always be "
                         "regenerated, and a failure to write it never changes the exit "
                         "code")
    ap.add_argument("--summary-dir", default=None, metavar="DIR",
                    help="write end-summary.md / end-summary.json into DIR instead of the "
                         "run directory. The run directory stays the default so the page "
                         "is found beside the curve it describes")
    # Two flags a new user needs before their first run. Everything else in this parser
    # describes the run; these ask whether the run can happen at all, which used to cost
    # a round of model calls to find out (`harnessgrad/readiness.py`).
    ap.add_argument("--check-only", action="store_true",
                    help="print one row per prerequisite of the run these arguments "
                         "describe, and exit 0 if every required one is ok else 2. "
                         "Creates nothing, stages nothing, calls no model. Pass the "
                         "flags the real run will use: they are what is checked")
    ap.add_argument("--prepare-only", action="store_true",
                    help="create the run directory and its redacted run_meta.json, print "
                         "the directory and exit 0, launching nothing. Combined with "
                         "--check-only, the checks gate the preparation")
    ap.add_argument("--check-net", action="store_true",
                    help="--check-only: also GET /models for each necessary agent/method endpoint with a 3 s "
                         "timeout. Opt-in because it is the one check that leaves the "
                         "machine")
    ap.add_argument('--fail-on-incomplete', action='store_true',
                    help='exit 2 when the requested experiment ended with method/measurement failures; '
                         'a valid method-requested early stop remains successful')
    # `--config` / `--set` are expanded into the flags they name before the parser sees
    # them (`expand_experiment_argv`); nothing else about the parse changes.
    ap.add_argument("--harness-runtime-binding", type=Path, help=argparse.SUPPRESS)
    return ap


def parse_run_args(
    argv: list[str], scenario_defaults: Mapping[str, object],
) -> tuple[argparse.ArgumentParser, argparse.Namespace, set[str]]:
    """Parse expanded flags and preserve which scenario fields the caller supplied."""
    ap = build_parser()
    expanded = expand_experiment_argv(argv)
    expanded = argv if expanded is None else expanded
    reviewed_flags = ('--expected-harness-sha256', '--expected-harness-filesystem-sha256')
    strict_reviewed = any(value.startswith('--') and any(
        option.startswith(value.split('=', 1)[0]) for option in reviewed_flags) for value in expanded)
    if strict_reviewed:
        # Legacy CLI retains argparse abbreviations/last-value behavior. A reviewed
        # invocation cannot have hidden abbreviated or duplicate effective options.
        ap.allow_abbrev = False
        seen = set()
        for value in expanded:
            option = value.split('=', 1)[0]
            action = ap._option_string_actions.get(option)
            if action is not None:
                if action.dest in seen:
                    ap.error('reviewed source forbids duplicate argument: ' + option)
                seen.add(action.dest)
        if '--no-sandbox' in expanded:
            ap.error('reviewed source requires the sandbox request; --no-sandbox is unsupported')
    args = ap.parse_args(expanded)
    if args.harness_runtime_binding is not None and (args.resume or args.h0_from):
        ap.error("--harness-runtime-binding cannot be combined with --resume or --h0-from")
    explicit = set(vars(args)) & scenario_defaults.keys()
    for field, default in scenario_defaults.items():
        if not hasattr(args, field):
            setattr(args, field, default)
    return ap, args, explicit
