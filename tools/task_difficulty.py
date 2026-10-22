#!/usr/bin/env python3
"""How hard is each task, measured rather than asserted -- and written down per dataset.

Why this exists
---------------
The platform can already pick "the tasks the base harness finds hard"
(`--sampling below_0.5`), but that is a fact about **one measurement on one day**: H0 of the
run you are about to make. It cannot answer "is this dataset easy or hard", it cannot order a
curriculum, and it disappears when the run directory is cleaned. Training a small harness into
a large one has to go easy to hard, and that needs a per-task number that outlives any single
run.

The definition is the one Evo-Bench uses for task sensitivity, inverted:

    perf(x)       = mean score of task x over every state that measured it
    difficulty(x) = 1 - perf(x)

A **state** is one measured curve point: a run, a round, and the harness and agent model that
produced it. `perf` is therefore "how often did the things we have measured pass this task",
and a task every state passes has difficulty 0.

Three honest limits
-------------------
* **A state is not a harness.** The states available today mix agent models (a local 9B and
  `deepseek-flash`), and a task is hard *for a model* as much as for a harness. The report
  records which models contributed and `--model` narrows to one, so the number is never
  quoted without knowing what it is a number about.
* **`n_states` is small.** Most tasks on this machine have one or two states. A task with one
  state has a difficulty of exactly 0 or 1 and no evidence behind either; the report carries
  `n_states` beside every number rather than hiding the difference.
* **It is a snapshot, not a constant.** Re-running the tool after more runs land changes the
  numbers, which is the point. The file records the runs it was built from so a stale table
  can be recognised.

Where the answer goes
---------------------
`data/datasets/<dataset>_difficulty.json`, beside the dataset it describes, so a new dataset brings its
own table and the platform does not need a registry of difficulty files. Regenerate with:

    python3 tools/task_difficulty.py
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
DATA = ROOT / "data" / "datasets"

sys.path.insert(0, str(ROOT))
import data.scaffolds.registry as registry   # noqa: E402

#: Models whose measurements are **not evidence**, so they never enter a number by default.
#: `deepseek-v4-pro` was withdrawn and must not be used; `runs/strong-model-1` still carries
#: three states from it, and without this they would quietly contribute to terminal_bench's
#: difficulty. Dropped **loudly** -- the report says how many -- because a silent filter is
#: how a corrupted input becomes an invisible one.
FORBIDDEN_MODELS = ("deepseek-v4-pro",)


#: Band names, easiest first. Cut by tertile of the observed difficulties rather than by fixed
#: thresholds: a fixed 0.33/0.66 would put every task of a hard dataset in `hard` and make the
#: bands useless for ordering, which is the one thing they are for.
BANDS = ("easy", "medium", "hard")


def states_for(runs_root: Path) -> dict[str, list[dict]]:
    """Every measured state, grouped by dataset.

    A run contributes one state per curve point that carries `per_task`. Runs that predate
    `per_task`, and points with no scored tasks, contribute nothing -- they are skipped
    rather than filled with zeros, because a zero would read as "everything failed".
    """
    out: dict[str, list[dict]] = {}
    for run_dir in sorted(runs_root.iterdir()):
        meta_path, curve_path = run_dir / "run_meta.json", run_dir / "curve.jsonl"
        if not (meta_path.is_file() and curve_path.is_file()):
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        dataset = meta.get("dataset")
        if not dataset:
            continue
        for line in curve_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                point = json.loads(line)
            except json.JSONDecodeError:
                continue
            per_task = point.get("per_task") or {}
            if not per_task:
                continue
            identity = point.get("identity") or {}
            out.setdefault(dataset, []).append({
                "run_id": meta.get("run_id") or run_dir.name,
                "round": point.get("round"),
                "harness": identity.get("harness_name"),
                "harness_sha": identity.get("harness_sha"),
                "model": identity.get("agent_model"),
                "side": point.get("side"),
                "scores": {t: float(s) for t, s in per_task.items()
                           if isinstance(s, (int, float))},
            })
    return out


def analyse(states: list[dict], *, model: str | None = None) -> tuple[dict, list[dict], int]:
    """`(per-task rows, the states actually used, how many were dropped as forbidden)`."""
    forbidden = 0
    if model is None:
        keep = []
        for s in states:
            if str(s.get("model")) in FORBIDDEN_MODELS:
                forbidden += 1
            else:
                keep.append(s)
        states = keep
    used = [s for s in states if model is None or s.get("model") == model]
    per_task: dict[str, list[float]] = {}
    contributed: dict[str, set] = {}
    # Per model as well as pooled. **A pooled difficulty is mostly a statement about whichever
    # model contributed the most states**, and on this machine that is `qwen3.5-9b-local` by
    # 162 to 15 -- so the pooled number answers "how hard is this for qwen" while looking like
    # it answers "how hard is this". Keeping both lets a curriculum use the model it is
    # actually training with instead of inheriting the mix.
    by_model: dict[str, dict[str, list[float]]] = {}
    for state in used:
        key = (state["run_id"], state["round"])
        model = str(state.get("model"))
        for task, score in state["scores"].items():
            per_task.setdefault(task, []).append(score)
            contributed.setdefault(task, set()).add(key)
            by_model.setdefault(task, {}).setdefault(model, []).append(score)
    rows = []
    for task, scores in per_task.items():
        perf = sum(scores) / len(scores)
        rows.append({
            "task_id": task, "perf": perf, "difficulty": 1.0 - perf,
            "n_states": len(contributed[task]),
            "passed": sum(1 for s in scores if s >= 1.0),
            "measured": len(scores),
            "by_model": {m: {"perf": sum(v) / len(v), "difficulty": 1.0 - sum(v) / len(v),
                             "n_states": len(v)}
                         for m, v in sorted(by_model.get(task, {}).items())},
        })
    rows.sort(key=lambda r: (r["difficulty"], r["task_id"]))
    return {"rows": rows}, used, forbidden


def band_of(rows: list[dict]) -> dict[str, str]:
    """Cut the observed difficulties into tertiles.

    Ties are kept together: with the small `n_states` this machine has, most tasks sit on a
    handful of distinct values, and splitting equal difficulties across two bands would make
    the bands look like information they are not.
    """
    values = sorted({r["difficulty"] for r in rows})
    if len(values) < len(BANDS):
        # Not enough distinct levels to cut: everything gets the same band, named honestly.
        return {r["task_id"]: "ungraded" for r in rows}
    n = len(values)
    cuts = [values[min(n - 1, (i + 1) * n // len(BANDS) - 1)] for i in range(len(BANDS) - 1)]
    out = {}
    for r in rows:
        band = BANDS[-1]
        for i, cut in enumerate(cuts):
            if r["difficulty"] <= cut:
                band = BANDS[i]
                break
        out[r["task_id"]] = band
    return out


def summarise(dataset: str, rows: list[dict], used: list[dict]) -> str:
    models: dict[str, int] = {}
    for s in used:
        models[str(s.get("model"))] = models.get(str(s.get("model")), 0) + 1
    counts = {"easy": 0, "medium": 0, "hard": 0, "ungraded": 0}
    for r in rows:
        counts[r["band"]] = counts.get(r["band"], 0) + 1
    solved = sum(1 for r in rows if r["difficulty"] == 0.0)
    unsolved = sum(1 for r in rows if r["difficulty"] == 1.0)
    lines = [
        f"{dataset}: {len(rows)} task(s) from {len(used)} state(s)",
        f"  agent models that contributed: "
        + (", ".join(f"{m} x{n}" for m, n in sorted(models.items())) or "(none)"),
        f"  bands: " + "  ".join(f"{k}={v}" for k, v in counts.items() if v),
        f"  every state passed {solved} task(s); no state passed {unsolved}",
        f"  difficulty 中位 {statistics.median([r['difficulty'] for r in rows]):.3f}"
        if rows else "  (no tasks)",
    ]
    thin = sum(1 for r in rows if r["n_states"] < 2)
    if thin:
        lines.append(f"  **{thin} task(s) have a single state, so their 0/1 is the only "
                     f"evidence there is**")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", help="only this dataset; default is every dataset with states")
    ap.add_argument("--model", help="only states whose agent model is exactly this")
    ap.add_argument("--runs-root", default=str(RUNS))
    ap.add_argument("--out", default=str(DATA), help="where <dataset>_difficulty.json goes")
    ap.add_argument("--write", action="store_true", help="write the files (default: report only)")
    ap.add_argument("--include-fixtures", action="store_true",
                    help="also score the platform's own fixtures (demo, probe_set); off by "
                         "default because they are scaffolding, not datasets a reader runs")
    args = ap.parse_args(argv)

    by_dataset = states_for(Path(args.runs_root))
    # **Published datasets only, by default.** `data/scaffolds/registry.py` decides what that means and
    # the Launch form reads the same answer, so there is one list rather than two.
    published = set(registry.published())
    if args.dataset:
        wanted = [args.dataset]
    else:
        wanted = sorted(d for d in by_dataset
                        if args.include_fixtures or d in published)
    if not wanted:
        print("no run under runs/ carries a per_task score, so there is nothing to measure")
        return 1

    out_dir = Path(args.out)
    written = []
    for dataset in wanted:
        states = by_dataset.get(dataset)
        if not states:
            print(f"{dataset}: no measured state -- skipped")
            continue
        report, used, dropped = analyse(states, model=args.model)
        if dropped:
            print(f"{dataset}: {dropped} state(s) dropped -- measured with "
                  f"{', '.join(FORBIDDEN_MODELS)}, which is not evidence")
        rows = report["rows"]
        bands = band_of(rows)
        for r in rows:
            r["band"] = bands[r["task_id"]]
        print(summarise(dataset, rows, used))
        print()
        if args.write:
            payload = {
                "dataset": dataset,
                "definition": "difficulty(x) = 1 - mean over states of score(x); "
                              "a state is one measured curve point (run, round, harness, model)",
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model_filter": args.model,
                "bands": list(BANDS),
                "states": [{"run_id": s["run_id"], "round": s["round"], "harness": s["harness"],
                            "harness_sha": s["harness_sha"], "model": s["model"],
                            "side": s["side"], "tasks": len(s["scores"])} for s in used],
                "tasks": {r["task_id"]: {k: v for k, v in r.items() if k != "task_id"}
                          for r in rows},
            }
            path = out_dir / f"{dataset}_difficulty.json"
            path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
            written.append(path)
    for path in written:
        print(f"wrote {path.relative_to(ROOT)}")
    if not args.write:
        print("(report only; pass --write to produce the JSON)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
