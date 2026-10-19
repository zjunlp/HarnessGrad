"""The eval side: measure the state a train run left behind, and evolve nothing.

A run covers exactly one side (§2.6). This schedule takes a harness state out of a finished
train run's record, measures it on the tasks that side holds, and writes one point. There is
no method here and no rounds: an exam that can be retaken is not an exam. The join back to
the train point (`evaluated_from.harness_sha`) is what makes the train/eval pair
reconstructible from two records rather than assumed from two runs started close together.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import time

from ckpt.git_state import commit_state
from eval.observe.console import CONSOLE as console
from eval.run.runner import evaluate
from harnessgrad import policies
from harnessgrad.environments import _env_record
from harnessgrad.environments import _report_harness_failed
from harnessgrad.environments import _report_invalid
from harnessgrad.identity import _identity
from harnessgrad.records import _cost_of
from harnessgrad.records import _curve_point
from harnessgrad.records import level_fields as _level_fields
from harnessgrad.records import test_rate_fields as _test_rate_fields
from harnessgrad.records import _require_untampered
from harnessgrad.records import _save_traces
from harnessgrad.records import _save_verdicts
from harnessgrad.records import _scored
from harnessgrad.records import _write_curve

def _baseline_block(path: str, point: dict) -> dict:
    """What the frozen reference is, and why it may not describe this point.

    A missing or malformed file is recorded as a problem rather than raised: the measurement
    already happened and is valid, and it is the *comparison* that is unavailable. Raising here
    would throw away a real score because a side file moved.
    """
    block: dict = {"kind": "frozen", "path": path}
    try:
        reference = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        block["problems"] = [f"the frozen reference could not be read: {exc}"]
        return block
    if not isinstance(reference, dict):
        block["problems"] = ["the frozen reference is not one curve-point object"]
        return block
    block["run_id"] = reference.get("run_id")
    block["round"] = reference.get("round")
    block["problems"] = policies.reference_problems(
        reference, point,
        reference_tasks=reference.get("per_task") or {},
        current_tasks=point.get("per_task") or {})
    return block


def _eval_source(args, runs_root: Path) -> tuple[Path, int]:
    """The harness state an `--side eval` run measures, and which round it was.

    An eval run has no evolution in it: it takes a state a **train** run produced and
    puts a number on it. The state is `round<N>_base/` inside that run's record, which
    is a complete harness tree -- measured: 16 files including `harness.json` -- so the
    rest of the run (staging, the manifest, the header, the sandbox) works on it with no
    special case at all.

    Refuses with the rounds that *are* available, because "round 3 of a run that stopped
    at 1" is a typo a reader can fix in one second and a silent fallback would hide.
    """
    if not args.from_run:
        raise SystemExit(
            "--side eval measures a harness state, so it needs --from-run <train run id>. "
            "There is nothing to evaluate otherwise: an eval run does not evolve anything.")
    src = Path(runs_root) / args.from_run
    if not src.is_dir():
        raise SystemExit(f"no run {args.from_run!r} under {runs_root}")
    # `state_after_round<N>` is the current name and `round<N>_base` is the older one:
    # records made before states were kept in the run directory used that, and an eval
    # run should be able to point at them too. Both are complete harness trees.
    def _rounds(pattern: str) -> dict[int, Path]:
        out: dict[int, Path] = {}
        for p in src.glob(pattern):
            if not p.is_dir():
                continue
            digits = "".join(c for c in p.name if c.isdigit())
            if digits:
                out[int(digits)] = p
        return out

    found = _rounds("state_after_round*") or _rounds("round*_base")
    if not found:
        raise SystemExit(
            f"run {args.from_run!r} keeps no harness state to evaluate. Every round "
            f"should leave one as `state_after_round<N>/`; a record without one predates "
            f"that, and there is nothing to measure.")
    want = args.from_round if args.from_round is not None else max(found)
    if want not in found:
        raise SystemExit(
            f"run {args.from_run!r} has no round {want}; it has {sorted(found)}")
    return found[want], want

def _eval_from_record(runs_root: Path, run_id: str, round_no: int) -> dict:
    """What the train run's own record says about the state being measured.

    Read rather than recomputed: the link has to survive on the record, and a reader of
    the eval run should be able to follow it to the train run's point without running
    anything. `harness_sha` is the join key and it is on both points already.
    """
    curve = Path(runs_root) / run_id / "curve.jsonl"
    if not curve.exists():
        return {"run_id": run_id, "round": round_no}
    for line in reversed(curve.read_text().splitlines()):
        if not line.strip():
            continue
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            continue
        if p.get("round") == round_no:
            return {"run_id": run_id, "round": round_no,
                    "harness_sha": (p.get("identity") or {}).get("harness_sha"),
                    "train_score": p.get("score") if p.get("side") != "eval" else None}
    return {"run_id": run_id, "round": round_no}

def _progress_results(run_dir: Path) -> dict[str, dict]:
    """Per-task results an interrupted eval run already wrote.

    `evaluate()` records one JSON per finished task under `progress/round-0/`. Those files
    are the only evidence that a task was measured, so a resume reads them rather than
    trusting a receipt count.
    """
    out: dict[str, dict] = {}
    for path in sorted((run_dir / "progress").glob("round-0/*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        task_id = record.get("task_id")
        if task_id and record.get("complete"):
            out[task_id] = record
    return out


def _resume_mismatch(run_dir: Path, args, pool: str, sha: str) -> str | None:
    """Why this run cannot be resumed, or None when it can.

    A resumed point must describe **one** measurement: same pool, same state, same
    task set. Anything else would silently concatenate two different measurements into a
    single number.
    """
    meta_path = run_dir / "run_meta.json"
    if not meta_path.is_file():
        return f"{run_dir.name} has progress files but no run_meta.json; cannot verify it"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return f"{run_dir.name}/run_meta.json is unreadable: {exc}"
    recorded_from = meta.get("from_run") or meta.get("evaluated_from") or {}
    if isinstance(recorded_from, dict):
        was_run, was_round = recorded_from.get("run_id"), recorded_from.get("round")
    else:
        was_run, was_round = recorded_from, None
    # Runs created before `evaluated_from` was written record nothing to verify against.
    # Treating that absence as "no mismatch" would resume a state nobody can name.
    if not was_run:
        return (f"{run_dir.name}/run_meta.json records no evaluated_from; "
                f"which state this run was measuring cannot be verified")
    if was_run != args.from_run:
        return f"the interrupted run measured {was_run!r}, this invocation --from-run {args.from_run!r}"
    if was_round is not None and args.from_round is not None and was_round != args.from_round:
        return (f"the interrupted run measured round {was_round}, this invocation "
                f"--from-round {args.from_round}")
    was_pool = (meta.get("env") or {}).get("pool") if isinstance(meta.get("env"), dict) else None
    if was_pool and was_pool != pool:
        return f"the interrupted run measured pool {was_pool!r}, this invocation {pool!r}"
    return None


def _merge_prior(res: dict, prior: dict[str, dict]) -> dict:
    """Fold the already-scored tasks back into a fresh `evaluate()` result.

    Counts of invalid and harness-failed tasks come from the same records, so the merged
    point's `n_invalid`/`n_harness_failed` describe the whole pool rather than the half that
    this process re-measured.
    """
    merged = dict(res)
    per_task = dict(merged.get("per_task") or {})
    invalid = list(merged.get("invalid") or [])
    failed = list(merged.get("harness_failed") or [])
    known = {str(x.get("task_id")) for x in invalid if isinstance(x, dict)}
    known_failed = {str(x.get("task_id")) for x in failed if isinstance(x, dict)}
    for task_id, record in prior.items():
        if task_id in per_task:
            continue
        outcome = record.get("process_outcome")
        if outcome == "completed" and record.get("score") is not None:
            per_task[task_id] = float(record["score"])
        elif outcome in ("invalid", "unmeasured"):
            if task_id not in known:
                invalid.append({"task_id": task_id, "why": record.get("detail") or outcome})
                known.add(task_id)
        elif task_id not in known_failed:
            failed.append({"task_id": task_id, "why": record.get("detail") or str(outcome)})
            known_failed.add(task_id)
    merged["per_task"] = per_task
    merged["invalid"] = invalid
    merged["harness_failed"] = failed
    return merged


def _run_eval_side(*, work: Path, man: dict, run_id: str, run_dir: Path, tasks: list[dict],
                   scorable: dict, setups, verifiers, envs, side_ids, split, args,
                   eval_from: dict | None, improver: dict | None = None,
                   after_evaluation=None) -> int:
    """Measure one harness state on the exam side. INTERFACE.md §2.6.

    No method, no rounds, one point: an eval run does not evolve anything, so there is
    nothing for a method to do and nothing to iterate. It exists so that a train run can
    stop carrying the exam at all -- the eval traces are then not a field this platform
    withholds from the method channel, they are a side that is not in the train run.

    The state is staged and committed like any harness, so the point carries the
    `harness_sha` of what was measured and a reader can join it to the train run's point
    for the same round. That join is the train-vs-eval pair, reconstructed from two
    records instead of living on one point.
    """
    cache: dict = {}
    cumulative = {"evaluation_trials": 0, "generation_tokens": 0,
                  "env_generation_tokens": 0, "wall_clock_s": 0.0}
    sha = commit_state(work, f"eval of {args.from_run} round "
                             f"{(eval_from or {}).get('round')}")
    t0 = time.time()
    # **--resume: finish what was interrupted, do not re-measure what was not.** The
    # per-task progress files are the evidence of which tasks are done; a task that was
    # scored once must not be scored again, because two measurements of one task are two
    # numbers and picking either is a choice the record cannot defend.
    prior: dict[str, dict] = {}
    if getattr(args, "resume", False):
        prior = _progress_results(run_dir)
        if prior:
            why = _resume_mismatch(run_dir, args,
                                   os.environ.get("HG_ALFWORLD_POOL", ""), sha)
            if why:
                console.note(f"refusing --resume: {why}", level="error")
                return 2
            before = len(tasks)
            tasks = [t for t in tasks if str(t.get("task_id")) not in prior]
            console.note(f"--resume: {before - len(tasks)} of {before} tasks already carry "
                         f"a result; scoring the remaining {len(tasks)}", level="info")
    if prior and not tasks:
        console.note("--resume: every task in this pool already has a result; writing the "
                     "point from the progress files alone", level="info")
        res = {"per_task": {}, "trials": args.trials, "identity_declared": None}
    else:
        res = evaluate(work, tasks, scorable, cache, harness_sha=sha, sandbox=args.sandbox,
                       progress_dir=run_dir,
                       trials=args.trials, jobs=args.jobs,
                       setups=setups, verifiers=verifiers, envs=envs, run_id=run_id,
                       run_seed=os.environ["HARNESSGRAD_SEED"],
                       recordings_root=run_dir / "recordings", round_no=0)
    if prior:
        res = _merge_prior(res, prior)
    complete_measurement = after_evaluation(res) if after_evaluation is not None else True
    _require_untampered(res)
    _report_invalid(res, args.dataset)
    _report_harness_failed(res, args.dataset)
    if not complete_measurement:
        console.note("refusing staged curve: lifecycle publication gate or measurement incomplete; "
                     "invalid/unmeasured outcomes are preserved in the receipt",
                     level="error")
        return 2

    scored = _scored(res, side_ids)
    if not scored:
        console.note(
            f"refusing to write a point: none of the {len(tasks)} eval tasks produced a "
            f"scoreable result, so there is no exam score to record.", level="error")
        return 2

    cumulative["evaluation_trials"] = len(tasks)
    cumulative["wall_clock_s"] = time.time() - t0
    cumulative["env_generation_tokens"] = sum(
        (res.get("env_usage") or {}).get(k, 0) for k in ("input", "output"))

    point = _curve_point(
        run_id=run_id, round_index=0,
        identity=_identity(man, sha, args.mode, improver=improver,
                           declared=res.get("identity_declared"),
                           jobs=args.jobs, trials=args.trials,
                           gpu_budget=getattr(args, "gpu_budget", None)),
        sampling={"dataset": args.dataset, "policy": "all", "task_ids": list(scored),
                  "selection_basis_round": 0, "selected_within": "eval"},
        scores=[res["per_task"][t] for t in scored], task_ids=scored,
        train_scores=None, train_ids=None, split=split,
        env_record=_env_record(envs, scored),
        invalid=res.get("invalid"),
        harness_failed=res.get("harness_failed"),
        model_gateway=res.get("model_gateway"),
        harness_runtime=res.get("harness_runtime"),
        evaluation_protocols=res.get("evaluation_protocols"),
        cost=_cost_of(res, len(tasks), cumulative["wall_clock_s"]),
        cumulative=cumulative, label="exam", side="eval",
        evaluated_from=eval_from, n_trials=res.get("trials"),
        score_std=res.get("score_std"), per_task_std=res.get("per_task_std"),
        **_test_rate_fields(res),
        **_level_fields(res, scored))
    point["measured_by_platform"] = True
    point["verifier_kinds"] = sorted({(verifiers.get(t) or {}).get("kind", "answer")
                                      for t in scored})
    # **The frozen reference's comparability belongs on the exam point too.** `mode_a` has
    # written `baseline.problems` since the frozen-reference protocol existed; the eval side
    # -- whose whole purpose is to be compared against that reference -- wrote nothing.
    # Measured 2026-10-18: all eleven published exam points carry no `baseline` block, so the
    # one fact a reader could not see is that every frozen reference was measured at
    # `jobs=13` while these exams ran at `jobs=12`-`40`, and `jobs` is precisely the axis
    # `policies.REFERENCE_MEASUREMENT` exists to keep visible: contention does not show up in
    # a score.
    if getattr(args, "baseline", None):
        point["baseline"] = _baseline_block(args.baseline, point)
    _save_verdicts(run_dir, 0, res, [t["task_id"] for t in tasks])
    _save_traces(run_dir, 0, res.get("traces") or {}, None, split,
                 trial_traces=res.get("trial_traces"))
    _write_curve(run_dir / "curve.jsonl", [point])
    console.results([point], split=split)
    return 0
