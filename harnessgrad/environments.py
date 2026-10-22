"""What a task needs in order to run, and what the platform could not measure.

`data/` declares an environment kind, `eval/containers/container.py` pins an image; this is the
platform's half in between: narrowing a dataset to the tasks a run actually scores,
resolving image digests before anything is paid for, checking that a harness can operate on
the environment at all, and saying out loud which tasks the platform refused to score and
why.

A refusal is a record too. `_report_invalid` and `_report_harness_failed` are here because
"the platform could not measure this" and "the harness failed this" are different claims
and must not both arrive as a zero.
"""

from __future__ import annotations

from pathlib import Path
import os

from eval.observe.console import CONSOLE as console
import data.scaffolds.registry as datasets
import eval.containers.container as container
import eval.containers.harness_runtime as harness_runtime
from harnessgrad.config import current as current_config

def _required_env_kinds(envs: dict) -> set[str]:
    """What a dataset needs from the environment it runs in.

    Read from the task environments rather than inferred from whether SETUP or VERIFY
    happen to be present. An environment is *declared* (INTERFACE.md §2.5); a platform
    that guessed the kind from the shape of the other fields would refuse the wrong
    runs and accept the wrong ones, and would do it silently in both directions.
    """
    kinds = {str((spec or {}).get("kind", "files")) for spec in (envs or {}).values()}
    return kinds or {"files"}

def _narrow_to_run(setups: dict, verifiers: dict, envs: dict,
                   task_ids) -> tuple[dict, dict, dict]:
    """Restrict the three per-task side tables to the tasks this run will touch.

    `load_envs` and `load_tasks` read the *whole* dataset, so each of these carries an
    entry for every task, including the other side of the split. Everything downstream
    -- the capability gate, the image preflight, the environment block on each curve
    point -- has to see this run's tasks and nothing else. Otherwise a task the run
    will never execute can still refuse it, which is what happened: a one-task train
    run was refused over an *eval* task's missing image, and the read of that refusal
    is "it ignored my --tasks".

    Narrowing here is also what makes the capability gate about *this run* rather than
    about the dataset: a `files`-only harness can legitimately run a files-only slice
    of a benchmark whose other tasks need containers.
    """
    keep = set(task_ids)
    return ({t: s for t, s in setups.items() if t in keep},
            {t: v for t, v in verifiers.items() if t in keep},
            {t: e for t, e in envs.items() if t in keep})

def _resolve_envs(envs: dict) -> tuple[dict, str | None]:
    """Pin every dataset image to a digest, once, before the run starts.

    §2.5.4: *"A run that cannot resolve a digest refuses to start."* The alternative --
    recording the tag and calling the run reproducible -- is the same failure this
    platform already shipped once for `agent_model`, where a curve point said `mock`
    while a live model answered every task. A tag moves; a digest does not.

    Resolution happens here rather than per task so that a 400-task benchmark forks the
    docker client a handful of times per *run* rather than once per task, and so that a
    bad image costs nothing instead of costing a whole round.
    """
    resolved: dict[str, dict] = {}
    for tid, spec in (envs or {}).items():
        spec = spec or {"kind": "files"}
        if spec.get("kind") != "exec":
            resolved[tid] = dict(spec)
            continue
        try:
            pinned = container.resolve_digest(spec["image"])
        except container.ContainerUnavailable as exc:
            return envs, f"task {tid!r}: {exc}"
        # Service images are pinned by the same rule and in the same place. A service
        # is where a task's environment is most likely to change under it -- a mock API
        # image retagged is a different task -- so "no digest, no start" has to reach
        # here too, and doing it once at startup keeps it off the per-task path where
        # it would cost a `docker inspect` for every task.
        services = []
        for svc in spec.get("services") or []:
            try:
                svc_pinned = container.resolve_digest(svc["image"])
            except container.ContainerUnavailable as exc:
                return envs, f"task {tid!r}, service {svc['name']!r}: {exc}"
            services.append({**svc, "image": svc_pinned,
                             "image_digest": container.digest_of(svc_pinned)})
        resolved[tid] = {**spec, "image": pinned,
                         "image_digest": container.digest_of(pinned),
                         "services": services}
    return resolved, None

def _env_record(envs: dict, task_ids) -> dict:
    """The environment block on a curve point (INTERFACE.md §2.5.4).

    All six fields the contract names, because a reader comparing two points has to be
    able to tell `files` from `exec`, and one image from another, without reading
    anything else. `model` and `seed` are the interactive-environment fields (§2.5.7)
    and are `null` until those exist -- recorded rather than omitted, so that "not
    applicable yet" is distinguishable from "this platform forgot".

    `variants` appears only when the scored tasks do not share one environment. The
    point is still a single number, but a reader must not take the first environment as
    the whole story.
    """
    specs: list[dict] = []
    for tid in task_ids:
        spec = (envs or {}).get(tid) or {"kind": "files"}
        if spec not in specs:
            specs.append(spec)
    if not specs:
        # No scored tasks: a point whose evaluation did not happen (a method round
        # that produced no candidate) or a caller that passed no ids. Fall back to the
        # dataset's environments rather than asserting `files`, which would be a
        # positive claim about a measurement that was not taken.
        for spec in (envs or {}).values():
            if spec not in specs:
                specs.append(spec)
    if not specs:
        specs = [{"kind": "files"}]
    first = specs[0]
    # Which services ran, and at which digests. A task with a mock API is a different
    # task from one without, and two runs with different service images are not two
    # measurements of one thing -- the same argument as `image_digest`, applied one
    # level down. `[]` rather than an absent key, so "no services" is a fact.
    services = sorted(
        ({"name": svc["name"], "image_digest": svc.get("image_digest"),
          **({"command": list(svc["command"])} if "command" in svc else {}),
          **({"entrypoint": svc["entrypoint"]} if "entrypoint" in svc else {}),
          **({"limits": container.limits_from(svc)}
             if "cpus" in svc or "memory_mb" in svc else {})}
         for svc in (first.get("services") or [])),
        key=lambda s: s["name"])
    # Read from the platform's own environment, the same way `HARNESSGRAD_WORK_ROOT`
    # is and for the same reason: these are facts about *this run* rather than about
    # any one task, and threading them through four call sites would make them
    # parameters of functions that have no other use for them. `env.model` and
    # `env.seed` are recorded only when a service actually declares it needs one --
    # otherwise `null`, which is a fact about the run rather than a missing field.
    env_seed = os.environ.get("HARNESSGRAD_SEED") or None
    env_model = current_config().environment.model
    record = {
        "kind": first.get("kind", "files"),
        "image_digest": first.get("image_digest"),
        "network": first.get("network"),
        "placement": first.get("placement"),
        "services": services,
        # Filled per point from the runner's result; the key always exists so that
        # "no gateway was needed" and "the platform forgot" are different records.
        "model_gateway": None,
        "model": env_model if services_needing_model(specs) else None,
        "seed": env_seed if services_needing_seed(specs) else None,
    }
    web_specs = [spec.get("web") for spec in specs if spec.get("web")]
    if web_specs:
        from tools.web_gateway import selected_providers
        search, fetch, endpoint = selected_providers()
        record["web"] = {"search": bool(web_specs[0].get("search")),
                          "fetch": bool(web_specs[0].get("fetch")),
                          "search_provider": search, "fetch_provider": fetch}
        if endpoint:
            record["web"]["searxng_url"] = endpoint
    if len(specs) > 1:
        record["variants"] = specs
    return record

def services_needing_seed(specs: list[dict]) -> bool:
    return any(svc.get("needs_seed")
               for spec in specs for svc in (spec.get("services") or []))

def services_needing_model(specs: list[dict]) -> bool:
    return any(svc.get("needs_model")
               for spec in specs for svc in (spec.get("services") or []))

def _check_env_kinds(man: dict, needed: set[str]) -> str | None:
    """Refuse a harness that cannot operate on the environment the dataset needs.

    Refused rather than attempted, because the failure mode otherwise is a harness
    that runs, scores zero on every task, and looks like a weak harness. The manifest
    is the harness's own statement of what it can do; the platform's job is to hold it
    to that rather than to find out by measurement.
    """
    declared = set(man.get("env_kinds") or ["files"])
    unknown = declared - set(ENV_KINDS)
    if unknown:
        return (f"harness.json declares env_kinds {sorted(unknown)}, which the platform "
                f"does not provide; known kinds are {list(ENV_KINDS)}")
    missing = needed - declared
    if missing:
        return (f"this dataset needs the environment kind(s) {sorted(missing)}, and "
                f"harness {man.get('name')!r} declares only {sorted(declared)}. Running "
                f"it anyway would score zero on every task for a reason that has "
                f"nothing to do with the harness.")
    return None

def _warn_missing_overlay(harness_src: Path, envs: dict, dataset: str) -> None:
    """Say so, with the command that fixes it, when a declared runtime is missing.

    One warning per run, not per task: the fact is about the harness, and 52 copies of
    it would bury the rest of the log. Only for a run that will use a container -- a
    `files` run uses the platform's interpreter, which usually has what is needed, and
    warning there would be noise about a case that is not broken.
    """
    if "exec" not in _required_env_kinds(envs):
        return
    install = harness_runtime.install_spec(harness_src)
    if install is None:
        return
    root = harness_runtime.overlays_root() / install[0][:16]
    try:
        built = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        built = []
    if built:
        return
    console.note(
        f"harness {harness_src.name!r} declares {install[1]!r}, and this run will use a "
        f"container -- but no dependency overlay is configured, so the harness will run "
        f"on each image's own interpreter. If an image has no such package the harness "
        f"will die on its first import, and that is recorded as `harness_failed` rather "
        f"than as a score. Configure it once with: python3 tools/configure_harness.py "
        f"--harness {harness_src} --dataset {dataset}",
        level="warn")

def _report_invalid(res: dict | None, dataset: str) -> None:
    """Say out loud which tasks the platform could not measure, and why.

    Attribution is the whole content of the `invalid` category: the note names the
    dataset and the service, because the reader's next question is whose fault it is
    and the answer is not "the harness" (§2.5.7).
    """
    for tid, why in sorted(((res or {}).get("invalid") or {}).items()):
        where = f", service {why.get('service')!r}" if why.get("service") else ""
        console.note(
            f"{tid}: invalid for dataset {dataset!r} at stage "
            f"{why.get('stage')!r}{where} -- {why.get('detail')}", level="warn")

def _report_harness_failed(res: dict | None, dataset: str) -> None:
    """Say out loud when a harness was measured but left nothing to read.

    This is not `invalid`: the task *was* scored, so the number stands. What is being
    reported is the attribution -- and it is the attribution a reader needs, because a
    run whose harness never started looks exactly like a run whose method decided not to
    edit: score 0.000, no trace, no complaint. Measured: `loop` inside a Terminal-Bench
    image died on its first model call with `ModuleNotFoundError: No module named
    'openai'`, exit code 1, 0-byte trace, and the only thing the record said was 0.000.

    The stderr tail is printed rather than summarised: the harness is code the platform
    did not write, so the platform cannot explain the failure, only carry it.
    """
    res = res or {}
    for tid, why in sorted((res.get("harness_failed") or {}).items()):
        code = why.get("exit_code")
        tail = " ".join((why.get("stderr") or "").split())
        # **按 `reason` 播报,不硬编码。** 判据现在有两种形状(轨迹为空 / 有轨迹但没有
        # `usage` 块),而"中途崩"不是"没留下轨迹":实测那三道题各写了 12-14 万字节的轨迹。
        # 一句写死的 "left no trace" 会把有证据的失败说成没有证据。
        console.note(
            f"{tid}: the harness failed (exit code {code}) -- "
            f"{why.get('reason') or 'no reason recorded'}. The score stands, but it is "
            f"the platform's verdict on the artifact, not evidence about the harness."
            + (f" stderr: {tail[-400:]}" if tail else ""),
            level="warn")

    # **A harness that died after it started.** The branch above answers "did it start?"
    # by design, so a non-zero exit with a *non-empty* trace said nothing -- and that is
    # the more common shape: measured on `headless-terminal`, exit 1, four steps of real
    # work, `loop.ended = crashed`, and the reason
    # (`RuntimeError: 3 attempts failed, last: Connection error`) reached neither the log
    # nor the method's evidence. A reader cannot tell that apart from "the model wrote
    # bad code" without it.
    for tid, out in sorted((res.get("harness_output") or {}).items()):
        code = (out or {}).get("exit_code")
        if not code or tid in (res.get("harness_failed") or {}):
            continue
        tail = " ".join(((out or {}).get("stderr") or "").split())
        console.note(
            f"{tid}: the harness exited {code} after it started, so its score is the "
            f"platform's verdict on whatever an unfinished run left behind"
            + (f" -- {tail[-300:]}" if tail else ""),
            level="warn")

#: The environment kinds, taken from the module that validates a dataset's
#: declaration of them. One list, so the driver and the data layer cannot drift into
#: disagreeing about what a harness may declare it supports.
ENV_KINDS = datasets.ENV_KINDS
