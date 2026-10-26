#!/usr/bin/env python3
"""Which tasks actually respond to harness quality -- and a split that keeps that.

**Where this comes from, and why we did not have it.** Evo-Bench's central
methodological contribution is *harness-guided benchmark construction*: it evolves
harnesses on a separate corpus, then keeps only the tasks whose score tracks harness
quality rather than the base model's, and splits by difficulty so that validation and
evaluation follow the same distribution (Abstract, §3). Their words: *"it leverages
auxiliary-task evolution to identify tasks genuinely sensitive to framework
improvements, followed by sensitivity-aware stratified splitting to ensure robust
cross-suite generalization."*

Our own split was whatever `data/datasets/terminal_bench.py` declared -- 52 train / 37 eval, one
line of a comment and no evidence that either side can tell a better harness from a
worse one. A task that every harness solves (or none does) contributes nothing to a
comparison and quite a lot of noise, and this platform's whole claim is that its numbers
mean something. So this tool measures it, with the definition Evo-Bench used:

    Perf(x)  = mean score of task x across harnesses
    quality(h | x) = harness h's mean score over the tasks **other than x**
    Sens(x)  = Pearson correlation over harnesses of  score(x, h)  against  quality(h | x)

`Sens(x) > 0` means "a harness that is good at the rest of the set is also good at this
task", which is what a task set used to compare harnesses needs. `Sens(x) <= 0` means the
task rewards something else -- noise, or a capability the rest of the set does not measure.
`Sens(x) = undefined` means the task's scores do not vary across the harnesses we ran, so
there is nothing to correlate; that is a fact about the task *and* about the harness set,
and it is recorded rather than turned into a zero.

**Three honest limits.**
  * The harnesses are the ones we have, not 12 evolved by four frontier models. Ours are
    the base reference, its variants, and the states previous runs actually reached -- a
    narrower quality range, so `undefined` will be more common than in Evo-Bench.
  * One evaluation per (harness, task) unless `--trials` says otherwise. The correlation
    is over harnesses, not over repeats, and `--trials` multiplies the cost by that factor.
  * A task's score here is whatever the platform's checker says it is, `invalid` cells
    excepted: a cell the platform could not measure is dropped from that task's
    correlation and counted, never filled with a zero.

The measurement uses `eval.run.runner.evaluate`, with a cache shared across harnesses
for cache-eligible tasks. Declared independent trials require fresh measurements;
this tool does not resume their interrupted groups. Opted-in trial evidence is saved
under the study's run directory. Each harness gets its own recording round (its
position in --harnesses), with label and content hash in harnesses/<round>.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ckpt.git_state import commit_state, stage_workspace          # noqa: E402
from data.scaffolds import registry                                          # noqa: E402
from driver import load_env                                        # noqa: E402
from eval.observe.console import CONSOLE as console                         # noqa: E402
from eval.run.runner import evaluate                                    # noqa: E402
from eval.run import trial_evidence                                      # noqa: E402
from eval.run.repeated_trials import immutable_json                     # noqa: E402
from harnessgrad.records import _require_untampered                 # noqa: E402


# --------------------------------------------------------------- the math ---

def pearson(xs: list[float], ys: list[float], *, min_n: int = 3) -> float | None:
    """Pearson's r, or None when the question cannot be asked.

    None -- not zero -- for fewer than `min_n` pairs and for either side having no
    variance. A constant series has an undefined correlation, and returning 0.0 would put
    "this task is unrelated to harness quality" next to "we could not tell", which are the
    two readings this whole tool exists to keep apart.
    """
    if len(xs) != len(ys) or len(xs) < min_n:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sx = math.sqrt(sum(d * d for d in dx))
    sy = math.sqrt(sum(d * d for d in dy))
    if sx == 0.0 or sy == 0.0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / (sx * sy)


def analyse(scores: dict[str, dict[str, float | None]], *,
            min_harnesses: int = 3) -> list[dict]:
    """`{task_id: {harness_label: score}}` -> one record per task, sorted by task id.

    A `None` score is a cell the platform could not measure (`invalid`, a crash, a check
    that never ran). It is dropped from that task's correlation *and* from the harness's
    quality for that task, because including it as a zero would make an unmeasured cell
    look like a harness that failed.
    """
    def usable(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    out = []
    for tid in sorted(scores):
        per = scores[tid]
        others = [t for t in scores if t != tid]
        pairs: list[tuple[float, float]] = []
        for label in sorted(per):
            if not usable(per[label]):
                continue
            # Leave-one-task-out quality: how good this harness is on everything else.
            rest = [scores[t].get(label) for t in others]
            rest = [v for v in rest if usable(v)]
            if not rest:
                continue
            pairs.append((float(per[label]), sum(rest) / len(rest)))
        measured = [v for v in per.values() if usable(v)]
        sens = pearson([p[0] for p in pairs], [p[1] for p in pairs],
                       min_n=min_harnesses)
        if sens is None:
            reason = ("fewer than %d harnesses measured this task" % min_harnesses
                      if len(pairs) < min_harnesses else
                      "the task's score does not vary across the harnesses that were run")
            status = "undefined"
        else:
            status = "sensitive" if sens > 0 else "insensitive"
            reason = ""
        out.append({
            "task_id": tid,
            "perf": (sum(measured) / len(measured)) if measured else None,
            "sens": sens,
            "status": status,
            "reason": reason,
            "harnesses_measured": len(pairs),
            "cells": {label: per[label] for label in sorted(per)},
        })
    return out


def stratified_split(stats: list[dict], *, strata: int = 3, per_stratum: int = 2,
                     seed: int = 20260716) -> dict:
    """Train/eval ids that follow the same difficulty distribution.

    Only `sensitive` tasks are eligible. The eligible tasks are sorted by difficulty
    (`1 - perf`, so hardest first) and cut into `strata` contiguous groups; within each
    stratum the `per_stratum` highest-`Sens` tasks are kept; each kept task goes to train or
    eval by a hash of `(seed, task_id)` -- deterministic on purpose, because Evo-Bench's
    "randomly split" cannot be reproduced from the paper and a split nobody can recompute is
    a split nobody can check. The result is recorded per stratum, so a reader can see that
    both sides drew from every difficulty band rather than checking a summary statistic.

    **A band with two or more kept tasks feeds both sides, by construction.** The first two
    kept tasks of each band go one to each side, and only the rest are hashed. A coin flip
    would leave a band feeding a single side about a quarter of the time (for three kept
    tasks), and a band that feeds one side is exactly the failure stratification exists to
    prevent: a gain on one side and a regression on the other would be a property of the two
    task sets rather than of the harness. Evo-Bench's reason for stratifying, in their words:
    validation and evaluation must "follow the same difficulty distribution".
    """
    eligible = [s for s in stats if s["status"] == "sensitive"]
    eligible.sort(key=lambda s: (-(1.0 - (s["perf"] or 0.0)), s["task_id"]))  # hardest first
    out_strata = []
    train: list[str] = []
    eval_ids: list[str] = []
    if eligible:
        size = max(1, math.ceil(len(eligible) / strata))
        for index in range(strata):
            band = eligible[index * size:(index + 1) * size]
            if not band:
                continue
            kept = sorted(band, key=lambda s: (-(s["sens"] or 0.0), s["task_id"]))[:per_stratum]
            side = {"train": [], "eval": []}
            for position, stat in enumerate(sorted(kept, key=lambda s: s["task_id"])):
                if position < 2 and len(kept) >= 2:
                    where = "train" if position == 0 else "eval"
                else:
                    digest = hashlib.sha256(f"{seed}:{stat['task_id']}".encode()).hexdigest()
                    where = "train" if int(digest[:8], 16) % 2 == 0 else "eval"
                side[where].append(stat["task_id"])
            train += side["train"]
            eval_ids += side["eval"]
            out_strata.append({
                "band": index,
                "difficulty": [round(1.0 - (s["perf"] or 0.0), 4) for s in band],
                "kept": [s["task_id"] for s in kept],
                "train": side["train"], "eval": side["eval"],
            })
    return {"train": sorted(train), "eval": sorted(eval_ids), "strata": out_strata,
            "seed": seed, "eligible": len(eligible), "tasks_total": len(stats)}


def render_table(stats: list[dict], harnesses: list[str]) -> str:
    """The report as a terminal table, widest column last so the numbers line up."""
    lines = [f"{'task':34} {'perf':>5} {'sens':>6} {'n':>2}  status",
             "-" * 34 + " " + "-" * 5 + " " + "-" * 6 + " " + "--  " + "-" * 11]
    for stat in sorted(stats, key=lambda s: (-(s["sens"] if s["sens"] is not None else -2),)):
        sens = "n/a" if stat["sens"] is None else f"{stat['sens']:+.2f}"
        perf = "n/a" if stat["perf"] is None else f"{stat['perf']:.2f}"
        lines.append(f"{stat['task_id'][:34]:34} {perf:>5} {sens:>6} "
                     f"{stat['harnesses_measured']:>2}  {stat['status']}")
    lines.append("")
    lines.append("harnesses: " + ", ".join(harnesses))
    return "\n".join(lines)


# ----------------------------------------------------------------- the run ---

def _measure(specs: list[tuple[str, Path]], tasks: list[dict], scorable: dict,
             setups: dict, verifiers: dict, envs: dict, args, work_root: Path,
             run_dir: Path, cache: dict) -> tuple[dict[str, dict[str, float | None]], dict]:
    """Run every (harness, task) pair through the platform and collect the scores.

    Returns `(scores, invalid)`. A cell the platform could not measure is `None` in
    `scores` **and** keeps its reason in `invalid`, because "four of six tasks came back
    invalid" is the finding of a study like this one, and dropping the receipts is how the
    first version of this tool reported "every task undefined" without saying why.
    """
    scores: dict[str, dict[str, float | None]] = {t["task_id"]: {} for t in tasks}
    invalid: dict[str, dict] = {}
    archive = any(trial_evidence.enabled(verifiers.get(t['task_id'])) for t in tasks)
    for index, (label, path) in enumerate(specs):
        work = work_root / f"hs-{label}"
        stage_workspace(path, work)
        sha = commit_state(work, f"sensitivity: {label}")
        if archive:
            # A single private archive root covers every harness. Distinct rounds
            # also separate identical harness contents evaluated under two labels.
            immutable_json(run_dir / 'harnesses' / f'{index}.json', {
                'label': label, 'harness_sha': sha, 'round': index, 'run_id': run_dir.name,
            }, reuse=False)
        console.note(f"measuring {label} ({sha[:10]}) on {len(tasks)} task(s)")
        t0 = time.time()
        res = evaluate(work, tasks, scorable, cache, harness_sha=sha, sandbox=args.sandbox,
                       setups=setups, verifiers=verifiers, envs=envs,
                       run_id=run_dir.name, run_seed=args.seed,
                       recordings_root=run_dir / "recordings", round_no=index if archive else 0,
                       trials=args.trials, jobs=args.jobs,
                       **({'progress_dir': run_dir} if archive else {}))
        _require_untampered(res)
        for tid, value in (res.get("per_task") or {}).items():
            scores.setdefault(tid, {})[label] = float(value)
        for tid, why in (res.get("invalid") or {}).items():
            scores.setdefault(tid, {})[label] = None
            invalid[f"{label}/{tid}"] = why
        if res.get("invalid"):
            # Named, per harness, because 4-of-6 invalid is not a footnote -- it is the
            # reason the correlation below is undefined, and the reader needs it there.
            console.note(f"  {label}: INVALID cells — " + "; ".join(
                f"{tid}: {str(why.get('detail'))[:90]}"
                for tid, why in sorted((res.get("invalid") or {}).items())[:3]),
                level="warn")
        console.note(f"  {label}: {len(res.get('per_task') or {})} measured, "
                     f"{len(res.get('invalid') or {})} invalid, "
                     f"{time.time() - t0:.0f}s")
        if args.cache:
            # After every harness, not at the end: this is a paid measurement and an
            # interrupted run must not lose the cells it already bought. (Measured on the
            # first run of this tool: the cache was written in a `finally`, so a kill at
            # harness five would have thrown away five harnesses of evaluations.)
            Path(args.cache).write_text(json.dumps(cache, indent=1))
    return scores, invalid


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harnesses", required=True,
                    help="comma-separated label=path pairs, e.g. "
                         "base=base_harness/loop,flash=runs/flash-three-2/state_after_round1")
    ap.add_argument("--dataset", default="terminal_bench")
    ap.add_argument("--tasks", default=None,
                    help="comma-separated subset; default is every task of the dataset")
    ap.add_argument("--trials", type=int, default=1,
                    help="evaluations per (harness, task) pair; multiplies the cost")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--seed", default="sensitivity")
    ap.add_argument("--out", default=None, help="where the JSON report goes")
    ap.add_argument("--cache", default=None,
                    help="a JSON cache shared across harnesses; reuses cache-eligible task measurements")
    ap.add_argument("--strata", type=int, default=3)
    ap.add_argument("--per-stratum", type=int, default=2)
    ap.add_argument("--split-seed", type=int, default=20260716)
    ap.add_argument("--no-sandbox", dest="sandbox", action="store_false")
    ap.set_defaults(sandbox=True)
    args = ap.parse_args(argv)

    # **The same `.env` a run gets.** Measured, and it cost a whole measurement: without
    # this, `HG_EGRESS_PROXY` never reached the check phase, 4-5 of 6 Terminal-Bench tasks
    # came back `invalid` (their checks install their own runner over the network), and the
    # first version of this tool dropped those receipts on the floor and reported "every
    # task undefined". Real environment variables still win (`load_env` uses setdefault), so
    # an explicit `HG_AGENT_MODEL=... python3 tools/task_sensitivity.py` overrides the file.
    load_env(ROOT / ".env")

    specs: list[tuple[str, Path]] = []
    for item in args.harnesses.split(","):
        label, _, path = item.partition("=")
        if not path:
            print(f"--harnesses entry {item!r} is not label=path", file=sys.stderr)
            return 2
        if not (Path(path) / "harness.json").is_file():
            print(f"{path} has no harness.json", file=sys.stderr)
            return 2
        specs.append((label.strip(), Path(path)))

    tasks_all, scorable, split = registry.load_split(args.dataset)
    _, setups, verifiers = registry.load_tasks(args.dataset)
    envs = registry.load_envs(args.dataset)
    wanted = None
    if args.tasks:
        wanted = [t.strip() for t in args.tasks.split(",") if t.strip()]
        unknown = sorted(set(wanted) - {t["task_id"] for t in tasks_all})
        if unknown:
            print(f"--tasks names {unknown}, which {args.dataset} does not define",
                  file=sys.stderr)
            return 2
    tasks = [t for t in tasks_all if wanted is None or t["task_id"] in set(wanted)]
    if split:
        # Only tasks the dataset actually assigns to a side: a task with no declared side
        # cannot be part of a train/eval split, and silently inventing one would make the
        # report's own split disagree with the dataset it came from.
        declared = set(split["train"]) | set(split["eval"])
        tasks = [t for t in tasks if t["task_id"] in declared]
    if len(tasks) < 3:
        # Below three tasks the leave-one-out quality is computed from one or two numbers
        # and the correlation is undefined for every task. Refused rather than run.
        print(f"refusing: {len(tasks)} task(s) is too few for a leave-one-out "
              f"correlation; pass at least 3 with --tasks", file=sys.stderr)
        return 2

    runs_root = ROOT / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=f"sensitivity-{int(time.time())}-", dir=runs_root))
    work_root = Path(os.environ.get("HARNESSGRAD_WORK_ROOT", ROOT.parent / "harnessgrad_work"))
    work_root = work_root / "sensitivity"
    cache: dict = {}
    if args.cache and Path(args.cache).is_file():
        try:
            cache = json.loads(Path(args.cache).read_text())
        except json.JSONDecodeError:
            print(f"warning: {args.cache} is not readable JSON; starting with an empty "
                  f"cache", file=sys.stderr)

    scores, invalid = _measure(specs, tasks, scorable, setups, verifiers, envs, args,
                               work_root, run_dir, cache)

    stats = analyse(scores)
    # What each harness scored overall, so a reader can see the quality range the
    # correlation was computed over -- a flat set of harnesses makes every task undefined,
    # and that should be visible as "these six harnesses all scored 0" rather than as an
    # absence of sensitive tasks.
    harness_rows = []
    for label, path in specs:
        values = [scores.get(t["task_id"], {}).get(label) for t in tasks]
        values = [v for v in values if isinstance(v, (int, float))]
        harness_rows.append({
            "label": label, "path": str(path), "tasks_measured": len(values),
            "perf": round(sum(values) / len(values), 4) if values else None,
        })
    result = {
        "kind": "task-sensitivity-v1",
        "dataset": args.dataset,
        "trials": args.trials,
        "jobs": args.jobs,
        "seed": args.seed,
        "harnesses": harness_rows,
        "tasks": stats,
        # Every cell the platform could not measure, with its stage and wording. A study
        # whose cells are 80% invalid has that as its result; the record has to say so.
        "invalid_cells": invalid,
        "summary": {
            "sensitive": [s["task_id"] for s in stats if s["status"] == "sensitive"],
            "insensitive": [s["task_id"] for s in stats if s["status"] == "insensitive"],
            "undefined": [s["task_id"] for s in stats if s["status"] == "undefined"],
        },
        "split": stratified_split(stats, strata=args.strata, per_stratum=args.per_stratum,
                                  seed=args.split_seed),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out = Path(args.out) if args.out else run_dir / "sensitivity.json"
    out.write_text(json.dumps(result, indent=1))
    print(render_table(stats, [label for label, _ in specs]))
    if invalid:
        by_stage: dict[str, int] = {}
        for why in invalid.values():
            stage = str(why.get("stage") or "?")
            by_stage[stage] = by_stage.get(stage, 0) + 1
        print("\ninvalid cells: " + ", ".join(f"{n} at stage {k!r}"
                                              for k, n in sorted(by_stage.items())))
        for key, why in list(sorted(invalid.items()))[:3]:
            print(f"  {key}: {str(why.get('detail'))[:140]}")
    s = result["summary"]
    print(f"\nsensitive {len(s['sensitive'])} · insensitive {len(s['insensitive'])} · "
          f"undefined {len(s['undefined'])}")
    print(f"split: train {len(result['split']['train'])} · eval "
          f"{len(result['split']['eval'])} (from {result['split']['eligible']} eligible)")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
