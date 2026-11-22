"""Curated HarnessGrad release baselines. Reuse is an explicit experiment choice."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

SELECTOR = "published:alfworld-loop-study"
REVISION = "dffe13f4e2eb585cb521039fe72d841da77d81e5"
SOURCE = ("https://huggingface.co/datasets/Xubqpanda/HarnessGrad/tree/" + REVISION
          + "/Experiments/ALFWorld/qwen3.5-9b_loop/h0")
CURVE_SHA256 = "74f8a9490474518b50118a18ed3acca3cb06cf4929d17460a390aa917e16bac5"
TRACES_SHA256 = "b22013f30a8af272b43983cfbb56490f069528305ee6e0d8ac6135f79ca0858e"
ASSETS = Path(__file__).resolve().parents[2] / "tools/assets"
RELEASE = ASSETS / "alfworld-loop-study-h0"


def _bytes(filename, expected):
    raw = (RELEASE / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"HarnessGrad published H0 {filename} differs from the pinned release")
    return raw


def point():
    return json.loads(_bytes("curve.jsonl", CURVE_SHA256))


def configure(args, explicit, *, scenario=False):
    """Select the release defaults before loading the normal ALFWorld adapter."""
    if args.h0_comparability != "strict" or args.h0_subset_view or args.side != "train":
        raise ValueError("the published study H0 requires an ordinary train-side run")
    for field, choice in (("dataset", "alfworld"), ("sampling", "all")):
        if (field in explicit or scenario) and getattr(args, field) != choice:
            raise ValueError(f"the published study H0 requires --{field} {choice}")
        setattr(args, field, choice)
    cohort = point()["sampling"]["task_ids"]
    csv = RELEASE / "train100.csv"
    if csv.read_text().strip().split(",") != cohort:
        raise ValueError("bundled ALFWorld cohort differs from the published measurement")
    if args.tasks and set(t.strip() for t in args.tasks.split(",")) != set(cohort):
        raise ValueError("the published study H0 covers exactly its 100 train tasks")
    args.tasks = ",".join(cohort)
    if not args.harness_path:
        if args.harness != "loop":
            raise ValueError("the published study H0 requires the measured Loop base tree")
        args.harness_path = str(ASSETS / "loop-study-0.2.0")
    return str(csv)


def traces(task_ids):
    """Restore the original single trial as both the primary and trial trace."""
    raw = _bytes("round-0.tar.gz", TRACES_SHA256)
    names = {tid + ".trial1.jsonl": tid for tid in task_ids}
    bodies = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            tid = names.get(member.name)
            if not member.isfile() or tid is None or tid in bodies:
                raise ValueError("unexpected trace in published H0")
            bodies[tid] = archive.extractfile(member).read().decode("utf-8")
    if set(bodies) != set(task_ids):
        raise ValueError("published H0 is missing a task trace")
    return bodies
