#!/usr/bin/env python3
"""Render a run's curve as text. The UI comes later; this is the sanity check.

It also refuses to let you compare things that are not comparable. Two runs made
under different isolation modes, or different sandbox plans, put numbers on the same
axis that were produced under different rules -- and the whole reason this platform
records `run_meta.json` is so that a reader can tell. A table that silently mixes
them undoes that.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BAR = "▁▂▃▄▅▆▇█"


def spark(values: list[float]) -> str:
    return "".join(BAR[min(int(v * 7.999), 7)] for v in values)


def read_meta(path: Path) -> dict:
    meta = path.parent / "run_meta.json"
    if not meta.exists():
        return {}
    try:
        return json.loads(meta.read_text())
    except json.JSONDecodeError:
        return {}


def isolation_label(meta: dict) -> str:
    """How this run treated its harness, in the terms a reader needs.

    `sandbox` was added after the first runs were made, so an absent key is not an
    error -- it means the run predates the namespace and its harness could read the
    driver. Saying so is the point; a blank would read as "no difference".
    """
    box = meta.get("sandbox")
    if not box:
        return "pre-sandbox"
    if not box.get("active"):
        return "NO-SANDBOX"
    plan = box.get("plan_version")
    return f"sandboxed v{plan}" if plan else "sandboxed"


def side_label(point: dict) -> str:
    """Whether this curve is a training curve or an exam curve. INTERFACE.md §2.6.

    **`score` means two different things now and this is where that is enforced.**
    A training run's score is guaranteed to be inflated -- the method has read the traces
    of exactly the tasks it is scored on -- so putting a training curve and an exam curve
    on one axis and reading the gap as progress is the most expensive mistake this tool
    could help someone make. Same treatment as `files` vs `exec`: not comparable, so not
    plotted together, and the reason is printed rather than the points being dropped.

    A point from before the split has no `side`, and saying so is the point: an absent
    key means "this predates the question", not "the same as everything else".
    """
    side = point.get("side")
    if not side:
        return "pre-split"
    return "exam" if side == "eval" else "training"


def env_label(point: dict) -> str:
    """Where the measurement was taken (INTERFACE.md §2.5.4).

    Read from the **curve point**, not from `run_meta.json`, because that is where the
    contract puts it and because a run's environment is a property of each point: a
    dataset may declare a different image per task, and a point whose scored tasks did
    not share one carries `env.variants`.

    An absent key is not an error, exactly as with `isolation_label`: it means the point
    predates environments, and saying `pre-env` is the point. A blank would read as "the
    same as the others", which is the one thing it is not known to be.
    """
    env = point.get("env")
    if not env:
        return "pre-env"
    if env.get("variants"):
        return f"mixed({len(env['variants'])})"
    kind = env.get("kind") or "files"
    if kind == "files":
        return "files"
    digest = env.get("image_digest") or ""
    return f"exec {digest[:19]}" if digest else "exec UNPINNED"


def protocol_label(point: dict) -> str:
    protocols = point.get("evaluation_protocols") or {}
    if protocols:
        return " + ".join(sorted(set(protocols.values())))
    # Historical ChessBench points used the offline path exclusively.
    if (point.get("sampling") or {}).get("dataset") == "chessbench_puzzles":
        return "offline strict sequence; not interactive official accuracy"
    return "default protocol"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    args = ap.parse_args()

    print(f"{'run':<30} {'policy':<10} {'conditions':<34} curve (one glyph per round)")
    print("-" * 100)
    labels: dict[str, list[str]] = {}
    for r in args.runs:
        path = Path(r) / "curve.jsonl" if Path(r).is_dir() else Path(r)
        points = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        if not points:
            print(f"{path.parent.name[:29]:<30} (no curve points)", file=sys.stderr)
            continue
        scores = [p["score"] for p in points]
        policy = points[0]["sampling"]["policy"]
        # Isolation and environment in one label, so the grouping below separates both
        # by construction rather than by remembering to check the second one.
        label = (f"{isolation_label(read_meta(path))} / {env_label(points[0])}"
                 f" / {side_label(points[0])} / {protocol_label(points[0])}")
        protocols = {protocol_label(point) for point in points}
        if len(protocols) > 1 or any(len(set((point.get("evaluation_protocols") or {}).values())) > 1
                                    for point in points):
            print(f"refusing curve with mixed evaluation protocols: {path}", file=sys.stderr)
            return 1
        labels.setdefault(label, []).append(path.parent.name)
        print(f"{path.parent.name[:29]:<30} {policy:<10} {label:<34} "
              f"{spark(scores)}  {min(scores):.2f}..{max(scores):.2f}")
        print(f"{'':<30} {'':<10} {'':<34} rounds={len(points)} "
              f"tasks={len(points[0]['sampling']['task_ids'])} "
              f"trials={points[-1]['cumulative']['evaluation_trials']}")

    if len(labels) > 1:
        print()
        print("WARNING: these runs were not made under the same conditions.",
              file=sys.stderr)
        for label, names in sorted(labels.items()):
            print(f"  {label:<34} {', '.join(names)}", file=sys.stderr)
        print("  A harness that can read the platform and one that cannot are different\n"
              "  instruments; so are a directory on the host and a container from a\n"
              "  particular image digest (§2.5.4). **And a training curve is not an exam\n"
              "  curve** (§2.6): a training score is inflated by construction, because the\n"
              "  method has read the traces of the very tasks it is scored on. Comparing\n"
              "  or averaging across any of these is not valid. Interactive and offline\n"
              "  ChessBench protocols are also not comparable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
