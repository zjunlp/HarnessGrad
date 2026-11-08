#!/usr/bin/env python3
"""Measure the shared H0 for a dataset once, and hand the run id to every method that follows.

**Why this is a tool and not a convention.** `driver.py --rounds N` measures round 0 as the
baseline harness's score inside every run, so a batch that just starts N runs gets N H0s. Those
are not interchangeable: the cross-method comparison is a *paired* one, and "method A beat method
B" computed against two different baselines is a statement about two different questions. The
platform has `--h0-from RUN` to share one point (and its `round0_traces`) across runs, but nothing
required anyone to use it -- measured 2026-10-19: three AppWorld study runs were launched without
it and each re-measured H0, which the operator caught. This tool is the missing step 0.

Usage:

    python3 tools/prepare_h0.py --dataset appworld --side train \
        --study-ids ids.txt --jobs 32            # measure it
    python3 tools/prepare_h0.py --dataset appworld --side train \
        --study-ids ids.txt --dry-run            # print the command, run nothing
    python3 tools/prepare_h0.py --dataset strongreject --split default \
        --study-ids train-ids.txt --jobs 1       # use the explicit research split

Output: the run id, the score, the task count, and the exact `--h0-from <id>` to pass to every
study run. It refuses (rather than measuring again) when a valid H0 for the same configuration is
already on disk: two measurements of one state are two numbers, and picking either is a choice no
record can defend.

The identity of "the same configuration" includes dataset, side, study-id set, sampling,
trials, and the effective agent model, backend and endpoint. A different model or study
set is a *different* H0, and the tool measures one instead of reusing.
An explicit split selection is part of this identity too; split files are hashed by content.
An opted-in adapter also includes its frozen scoring identity in this configuration.
Managed dataset runtimes are preflighted for the selected tasks, including in
dry-run mode. Their actual artifact identities must also match the finished
driver records before this tool publishes or reuses the H0 configuration.
The selected Harness's staged source is also part of the configuration. Use
--harness NAME or --harness-path DIR to select it. Managed Python Harnesses must
be configured first; lookup never builds, including with --dry-run. Their exact
source/artifact binding is handed to the driver and checked on reuse.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harnessgrad import scoring_identity
from harnessgrad import dataset_runtime_identity
from harnessgrad import verification_context
from eval.containers import external_runtime
from harnessgrad import candidate_runtime
from harnessgrad import h0_identity
from harnessgrad import config as platform_config

DEFAULT_RUNS = pathlib.Path("/tmp/hg-h0-runs")


def study_ids_from(path: pathlib.Path | None) -> list[str]:
    if path is None:
        return []
    if not path.is_file():
        # Refused loudly: a typo'd path must not quietly become "no study set", because then the
        # baseline would be measured on the dataset's default side and reused for a different one.
        raise SystemExit(f"no such study-ids file: {path}")
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        for token in line.replace("\t", ",").split(","):
            token = token.strip()
            if token and not token.startswith("#"):
                ids.append(token)
    if not ids:
        raise SystemExit(f"study-ids file names no task ids: {path}")
    if len(ids) != len(set(ids)):
        raise SystemExit(f"study-ids file names duplicate task ids: {path}")
    return ids


def study_ids_sha(ids) -> str | None:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest() if ids else None


def child_environment(args) -> dict[str, str]:
    """Resolve the driver's .env precedence without changing this process's environment."""
    env = dict(os.environ)
    for key, value in platform_config.read_env_file(ROOT / ".env").items():
        env.setdefault(key, value)
    if args.model is not None:
        env["HG_AGENT_MODEL"] = args.model
    if args.endpoint is not None:
        env["HG_AGENT_BASE_URL"] = args.endpoint
    return env


def harness_source(args):
    explicit = getattr(args, "harness_path", None)
    if explicit is not None:
        source = pathlib.Path(explicit).expanduser().resolve(strict=True)
    else:
        name = getattr(args, "harness", "loop")
        if not isinstance(name, str) or pathlib.Path(name).name != name or name in ("", ".", ".."):
            raise ValueError("H0 harness must be a name or an explicit --harness-path")
        source = next((base / name for base in (ROOT / "base_harness", ROOT / "tests/fixtures/base_harness")
                       if (base / name).is_dir()), None)
        if source is None:
            raise ValueError(f"H0 harness is unavailable: {name}")
    if not source.is_dir():
        raise ValueError("H0 harness source must be a directory")
    return source


def harness_context(source, *, env=None, selection=None):
    """Compute the same staged source identity the driver will actually measure.

    Only read already prepared dependencies. Initial artifact installation belongs
    to explicit configure_harness; candidate-round offline preparation is driver-owned.
    """
    from eval.ckpt.git_state import stage_workspace, commit_state
    from eval.ckpt.source_tree import copy_harness
    with tempfile.TemporaryDirectory(prefix="hg-h0-candidate-") as temporary:
        root = pathlib.Path(temporary)
        owned, staged = root / "owned", root / "workspace"
        copy_harness(source, owned, ignore=shutil.ignore_patterns(".git"))
        stage_workspace(owned, staged)
        sha = commit_state(staged, "H0 preflight source")
        # The subprocess honors the driver's effective storage settings without
        # importing caller-specific .env state into this preparation process.
        if candidate_runtime.has_python(staged):
            program = '''import json,sys
from pathlib import Path
from harnessgrad import candidate_runtime as r
from data.scaffolds import registry
request=json.loads(sys.argv[2])
with registry.provider_session():
 if request is None:
  value=r.admit(Path(sys.argv[1]))
 else:
  from harnessgrad.dataset_runtime_identity import h0_selection
  _,setups,_,envs=h0_selection(request)
  value=r.admit(Path(sys.argv[1]),envs=envs,setups=setups)
 print(json.dumps(value))
'''
            result = subprocess.run([sys.executable, "-B", "-c", program, str(staged), json.dumps(selection)],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise ValueError("H0 candidate runtime preflight failed: " + result.stderr[-3000:])
            binding = candidate_runtime.validate(json.loads(result.stdout))
        else:
            binding = None
        current = external_runtime.identities(staged, env)
        return {"harness_sha": sha, **candidate_runtime.fields(binding), **external_runtime.fields(current)}


def _verification_preflight(dataset, side, task_ids, split, sampling, environment):
    """Capture current selected scorer inputs in the same isolated loading session."""
    request = {"dataset": dataset, "side": side, "task_ids": task_ids,
               "split": split, "sampling": sampling}
    program = r"""
import json, sys
from contextlib import redirect_stdout
from data.scaffolds import registry
from harnessgrad.dataset_runtime_identity import h0_selection
from harnessgrad.verification_context import capture
request = json.loads(sys.argv[1])
with redirect_stdout(sys.stderr), registry.provider_session():
    ids, setups, verifiers, envs = h0_selection(request)
    from driver import _resolve_envs
    envs, error = _resolve_envs(envs)
    if error:
        raise ValueError('H0 environment identity unavailable')
    tasks, scorable, _ = registry.load_split(request['dataset'])
    selected = [task for task in tasks if task['task_id'] in set(ids)]
    result = capture(selected, scorable, setups, verifiers, envs)
print(json.dumps(result))
"""
    result = subprocess.run([sys.executable, '-B', '-c', program, json.dumps(request)],
                            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError('H0 verification context preflight failed')
    return verification_context.validate(json.loads(result.stdout))


def configuration(args, *, study_ids: list[str] | None = None, env=None) -> dict:
    """What makes two H0 measurements the same measurement."""
    ids = study_ids_from(args.study_ids) if study_ids is None else study_ids
    environment = dict(child_environment(args) if env is None else env)
    agent = platform_config.load(environment).agent
    if args.study_ids is not None:
        for key in ("HG_APPWORLD_STUDY_IDS", "HG_ALFWORLD_STUDY_IDS"):
            environment[key] = str(args.study_ids.resolve())
    split = getattr(args, "split", "declared")
    split_fields = {}
    if split in ("default", "official"):
        split_fields = {"split": split}
    elif split != "declared":
        path = pathlib.Path(split)
        if not path.is_file():
            raise SystemExit(f"no such split file: {path}")
        split_fields = {"split": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    source = harness_source(args)
    managed = candidate_runtime.has_python(source)
    image_contract = managed and 'exec' in candidate_runtime.python_runtime.descriptor(source)
    dataset_identity = dataset_runtime_identity.resolve_h0(
        args.dataset, side=args.side, task_ids=ids, split=split,
        sampling=getattr(args, "sampling", "all"), env=environment,
        **({"managed_harness": True} if managed else {}),
        **({'managed_exec': True} if image_contract else {}))
    verifier_context = _verification_preflight(args.dataset, args.side, ids, split,
                                               getattr(args, "sampling", "all"), environment)
    context = harness_context(source, env=environment,
        **({'selection': {'dataset': args.dataset, 'side': args.side, 'task_ids': ids,
                          'split': split, 'sampling': getattr(args, 'sampling', 'all')}}
           if image_contract else {}))
    return {
        "dataset": args.dataset,
        "side": args.side,
        "n_study_ids": len(ids),
        # The *file contents*, not the path: two files with the same ids are the same study set.
        "study_ids_sha": study_ids_sha(ids),
        "agent_model": agent.model,
        "agent_backend": agent.backend,
        "endpoint": agent.base_url,
        "trials": args.trials,
        "sampling": getattr(args, "sampling", "all"),
        **split_fields,
        **scoring_identity.fields(scoring_identity.resolve(args.dataset)),
        **dataset_runtime_identity.fields(dataset_identity),
        **verification_context.fields(verifier_context),
        **context,
    }


def points(path: pathlib.Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def find_existing(config: dict, runs_root: pathlib.Path, *, source=None, env=None) -> tuple[pathlib.Path, dict] | None:
    """A valid H0 already recorded for this configuration, or None."""
    dataset_runtime_identity.admit(config.get("dataset_runtime_identity"))
    if source is not None:
        try:
            external_runtime.admit_reuse(source, config, env=env)
        except external_runtime.RuntimeUnavailable:
            return None
    for run in sorted(runs_root.glob("h0-" + config["dataset"] + "-*")):
        h0 = run / "h0.json"
        meta = run / "run_meta.json"
        if not (h0.is_file() and meta.is_file()):
            continue
        metadata = json.loads(meta.read_text(encoding="utf-8"))
        recorded = metadata.get("h0_configuration")
        # Old configurations can still prove their source through the actual H0
        # payload and curve. Do not manufacture a second baseline for a new key.
        optional = {'harness_sha', 'harness_runtime'}
        missing = optional - set(recorded) if isinstance(recorded, dict) else set()
        compatible = recorded == {key: value for key, value in config.items() if key not in missing}
        if not compatible:
            continue
        payload = json.loads(h0.read_text(encoding="utf-8"))
        identity = config.get("scoring_identity")
        if identity is not None or payload.get("scoring_identity") is not None:
            curve = points(run / "curve.jsonl")
            if (scoring_identity.problems(payload.get("scoring_identity"), identity)
                    or scoring_identity.problems(metadata.get("scoring_identity"), identity)
                    or not curve or scoring_identity.problems(curve[0].get("scoring_identity"), identity)):
                continue
        base = payload.get("base") or {}
        per_task = base.get("per_task") or {}
        invalid = base.get("invalid") or {}
        if not per_task or invalid:
            continue
        if config.get("study_ids_sha") is not None and study_ids_sha(per_task) != config["study_ids_sha"]:
            continue
        try:
            summary = verify(run, None, config.get("n_study_ids", 0),
                             scoring=identity, configuration=config, source=source, env=env, reusable=True)
        except SystemExit:
            continue
        return run, {"score": summary["score"], "n_tasks": summary["n_tasks"]}
    return None


def verify(run: pathlib.Path, args, expected_ids: int, *, scoring=None, study_ids=None,
           configuration=None, runtime=None, source=None, env=None, reusable=False) -> dict:
    """Refuse to call something an H0 unless it is a complete, valid measurement."""
    h0 = run / "h0.json"
    if not h0.is_file():
        raise SystemExit(f"{run.name}: no h0.json; the run did not finish its baseline measurement")
    payload = json.loads(h0.read_text(encoding="utf-8"))
    base = payload.get("base") or {}
    per_task = base.get("per_task") or {}
    invalid = base.get("invalid") or {}
    problems = []
    if configuration is not None:
        runtime = configuration.get("dataset_runtime_identity")
    meta_path = run / "run_meta.json"
    metadata = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    curve = points(run / "curve.jsonl")
    recorded_runtimes = [payload.get("dataset_runtime_identity"), base.get("dataset_runtime_identity"),
                         metadata.get("dataset_runtime_identity"),
                         curve[0].get("dataset_runtime_identity") if curve else None]
    if isinstance(metadata.get("h0_configuration"), dict):
        recorded_runtimes.append(metadata["h0_configuration"].get("dataset_runtime_identity"))
    for recorded in recorded_runtimes:
        problems.extend(dataset_runtime_identity.problems(recorded, runtime))
    expected_context = (configuration or {}).get("verification_context")
    context_copies = [payload.get("verification_context"), base.get("verification_context"),
                      metadata.get("verification_context"), curve[0].get("verification_context") if curve else None]
    if isinstance(metadata.get("h0_configuration"), dict):
        context_copies.append(metadata["h0_configuration"].get("verification_context"))
    for recorded in context_copies:
        if recorded is None or expected_context is None:
            problems.append("verification context missing")
        else:
            try:
                ids = list(per_task)
                problems.extend(verification_context.problems(
                    verification_context.project(recorded, ids), verification_context.project(expected_context, ids)))
            except (KeyError, TypeError, ValueError):
                problems.append("verification context task set mismatch")
    try:
        if source is not None:
            external_runtime.admit_reuse(source, base, env=env, reusable=reusable)
            if curve:
                external_runtime.admit_reuse(source, curve[0], env=env, reusable=reusable)
        else:
            expected_external = {}
            for entry in external_runtime.records(configuration or {}):
                expected_external.update({key: item for key, item in entry.items() if key in external_runtime.KINDS})
            external_runtime.require_records(expected_external, base, reusable=reusable)
            if curve:
                external_runtime.require_records(expected_external, curve[0], reusable=reusable)
                if reusable:
                    external_runtime.admit_checkpoint(run, curve[0], env=env)
    except (OSError, TypeError, ValueError) as exc:
        problems.append(str(exc))
    expected_actor = (configuration or {}).get("harness_runtime_identity")
    actor_copies = [payload.get("harness_runtime_identity"), base.get("harness_runtime_identity"),
                    metadata.get("harness_runtime_identity"),
                    curve[0].get("harness_runtime_identity") if curve else None]
    if isinstance(metadata.get("h0_configuration"), dict):
        actor_copies.append(metadata["h0_configuration"].get("harness_runtime_identity"))
    for recorded in actor_copies:
        problems.extend(candidate_runtime.problems(recorded, expected_actor))
    if expected_actor is not None and curve:
        try:
            candidate_runtime.require_same(candidate_runtime.checkpoint_binding(run, curve[0]), expected_actor)
        except (OSError, TypeError, ValueError) as exc:
            problems.append(str(exc))
    expected_source = (configuration or {}).get("harness_sha")
    if expected_source is not None:
        if payload.get("h0_sha") != expected_source or not curve or (curve[0].get("identity") or {}).get("harness_sha") != expected_source:
            problems.append("H0 baseline harness source differs from the requested staged source")
    try:
        dataset_runtime_identity.admit(runtime)
    except (OSError, TypeError, ValueError) as exc:
        problems.append(str(exc))
    if configuration is not None:
        meta_path = run / "run_meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        curve = points(run / "curve.jsonl")
        point = curve[0] if curve else {}
        problems.extend(h0_identity.agent_problems(
            run, point, model=configuration.get("agent_model"),
            backend=configuration.get("agent_backend"), endpoint=configuration.get("endpoint")))
        sampling = configuration.get("sampling")
        for source, recorded in (
                ("curve", (point.get("sampling") or {}).get("policy")),
                ("run metadata", meta.get("sampling_policy")),
                ("run configuration", ((meta.get("config") or {}).get("run") or {}).get("sampling"))):
            if sampling is None or recorded != sampling:
                problems.append(f"{source} sampling differs from requested configuration or is missing")
    if scoring is not None or payload.get("scoring_identity") is not None:
        problems.extend(scoring_identity.problems(payload.get("scoring_identity"), scoring))
        meta = json.loads((run / "run_meta.json").read_text())
        problems.extend(scoring_identity.problems(meta.get("scoring_identity"), scoring))
        curve = points(run / "curve.jsonl")
        problems.extend(scoring_identity.problems(curve[0].get("scoring_identity") if curve else None, scoring))
    if not per_task:
        problems.append("no per-task verdicts")
    if expected_ids and len(per_task) != expected_ids:
        problems.append(f"{len(per_task)} tasks measured, the study set has {expected_ids}")
    if study_ids is not None and set(per_task) != set(study_ids):
        problems.append("measured task ids differ from the study set "
                        f"({len(set(study_ids) - set(per_task))} missing, "
                        f"{len(set(per_task) - set(study_ids))} unexpected)")
    if invalid:
        problems.append(f"{len(invalid)} task(s) invalid -- an H0 with holes is not a baseline")
    if base.get("harness_failed"):
        problems.append(f"{base.get('harness_failed')} harness failure(s)")
    if not payload.get("h0_sha"):
        problems.append("no h0_sha recorded")
    if problems:
        raise SystemExit(f"{run.name}: refusing to publish this as H0: " + "; ".join(problems))
    # evaluate() retains per-task results; older/raw payloads need not carry a mean.
    score = base.get("score")
    if score is None:
        score = sum(per_task.values()) / len(per_task)
    return {"score": score, "n_tasks": len(per_task),
            "h0_sha": payload.get("h0_sha")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", required=True)
    selection = ap.add_mutually_exclusive_group()
    selection.add_argument("--harness", default="loop", help="base Harness name (default: loop)")
    selection.add_argument("--harness-path", type=pathlib.Path, help="owned candidate source directory")
    ap.add_argument("--side", choices=("train", "eval"), default="train")
    ap.add_argument("--split", default="declared",
                    help="driver split: declared (default), default, official, or a JSON file")
    ap.add_argument("--study-ids", type=pathlib.Path, default=None,
                    help="file of task ids that defines the study set (optional)")
    ap.add_argument("--jobs", type=int, default=32)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--sampling", default="all")
    ap.add_argument("--model", default=None, help="agent model (default: the platform's)")
    ap.add_argument("--endpoint", default=None, help="agent endpoint (default: the platform's)")
    ap.add_argument("--runs-root", type=pathlib.Path, default=DEFAULT_RUNS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    args.runs_root = args.runs_root.resolve()
    if args.harness_path is not None:
        args.harness_path = args.harness_path.expanduser().resolve()

    ids = study_ids_from(args.study_ids)
    if args.split not in ("declared", "default", "official"):
        args.split = str(pathlib.Path(args.split).resolve())
    if scoring_identity.provider_path(args.dataset).is_file():
        # Scoring providers read the judge's effective process environment.
        # Preserve driver precedence while keeping preparation independent of driver imports.
        for key, value in platform_config.read_env_file(ROOT / ".env").items():
            os.environ.setdefault(key, value)
        platform_config.reset()
    env = child_environment(args)
    config = configuration(args, study_ids=ids, env=env)
    args.runs_root.mkdir(parents=True, exist_ok=True)
    existing = find_existing(config, args.runs_root, source=harness_source(args), env=env)
    if existing is not None:
        run, summary = existing
        print(f"reusing {run.name}: score {summary['score']:.4f} on {summary['n_tasks']} tasks "
              f"(same dataset, study set, model and endpoint)")
        print(f'\n  --runs-root "{args.runs_root}" --h0-from {run.name}\n')
        return 0

    stamp = __import__("time").strftime("%Y%m%dT%H%M%SZ", __import__("time").gmtime())
    run_id = f"h0-{args.dataset}-{stamp}"
    selector = (["--harness-path", str(args.harness_path)] if args.harness_path is not None
                else ["--harness", args.harness])
    command = [sys.executable, str(ROOT / "driver.py"), *selector,
               "--dataset", args.dataset, "--split", args.split,
               "--side", args.side, "--sampling", args.sampling,
               "--trials", str(args.trials), "--jobs", str(args.jobs),
               "--rounds", "0", "--improver", "none",
               "--run-id", run_id, "--runs-root", str(args.runs_root),
               "--work-root", f"/tmp/hg-h0-work/{run_id}"]
    if args.study_ids:
        # Generic selection happens before setup and H0 evaluation in the driver.
        # Keep the legacy knobs too: those adapters use them to construct their pool.
        command.extend(["--tasks", ",".join(ids)])
        env["HG_APPWORLD_STUDY_IDS"] = str(args.study_ids.resolve())
        env["HG_ALFWORLD_STUDY_IDS"] = str(args.study_ids.resolve())
    print(" ".join(command))
    if args.dry_run:
        return 0
    log = args.runs_root / f"{run_id}.log"
    with tempfile.TemporaryDirectory(prefix="hg-h0-binding-") as temporary:
        binding = config.get("harness_runtime_identity")
        actual_command = list(command)
        if binding is not None:
            path = pathlib.Path(temporary) / "binding.json"
            path.write_text(json.dumps(binding))
            path.chmod(0o600)
            actual_command += ["--harness-runtime-binding", str(path)]
        with log.open("w", encoding="utf-8") as handle:
            code = subprocess.run(actual_command, stdout=handle, stderr=subprocess.STDOUT,
                                  env=env, cwd=str(ROOT)).returncode
    run = args.runs_root / run_id
    if code != 0:
        raise SystemExit(f"the H0 run failed (rc={code}); see {log}")
    summary = verify(run, args, config["n_study_ids"], scoring=config.get("scoring_identity"),
                     study_ids=ids if args.study_ids else None, configuration=config, source=harness_source(args), env=env)
    # Stamp what the baseline *is* on the run's own meta, so the next invocation can recognise it
    # without re-deriving the configuration from paths.
    meta_path = run / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    meta["h0_configuration"] = config
    meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nH0 measured: {run_id}")
    print(f"  score {summary['score']:.4f} on {summary['n_tasks']} tasks, h0_sha {summary['h0_sha'][:12]}")
    print(f'\n  --runs-root "{args.runs_root}" --h0-from {run_id}\n')
    print("pass that to every study run for this dataset; without it each run measures its own H0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
