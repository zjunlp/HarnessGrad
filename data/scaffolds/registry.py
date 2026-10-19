"""Dataset registry. INTERFACE.md §8.1: the interface is dataset-agnostic.

A dataset may declare a train/eval split:

    SPLIT = {"train": [...task ids...], "eval": [...task ids...]}

`load_split` returns it, or `None` when the dataset does not declare one. It is
deliberately `None` and not a guess. Both obvious guesses are wrong in a way that
is invisible afterwards: treating every task as train hands the method the exam,
and treating every task as eval withholds the diagnostics the method is supposed
to work from. `None` means "this dataset cannot be used for the claim", and the
driver says so out loud rather than picking one.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from pathlib import Path

#: **What a module in `data/` is for.** A dataset declares `KIND = "dataset"`; anything that
#: does not is a **fixture** -- the platform's own test material. The default is the quiet
#: one on purpose: a new dataset that forgets to declare itself is hidden from the chooser
#: rather than silently offered as something a reader can run, and the fix is one line.
#:
#: This mirrors what `methods/` already does with `method.json`'s `kind`, and for the same
#: reason: measured 2026-10-06, the Launch form's dataset list offered `demo`, `probe_set`
#: and `broken_service` beside `terminal_bench` and `tau_bench`, so a reader could not tell
#: which of the five were datasets and which were the platform's own scaffolding. The files
#: stay -- `demo` alone is referenced by 21 test modules -- they are simply not offered.
KIND_DATASET = "dataset"
KIND_FIXTURE = "fixture"


# Fixtures keep their historical module names; adapters have no old-path shims.
_FIXTURE_NAMES = frozenset({"demo", "probe_set", "broken_service",
                            "verify_demo", "verify_exec", "verify_service"})


def module_name(name: str) -> str:
    """Canonical adapter module, or the explicitly retained top-level fixture."""
    return f"data.{name}" if name in _FIXTURE_NAMES else f"data.datasets.{name}"


def kind_of(name: str) -> str:
    """`"dataset"` or `"fixture"`, from the module's own declaration."""
    module = importlib.import_module(module_name(name))
    kind = getattr(module, "KIND", KIND_FIXTURE)
    return kind if kind in (KIND_DATASET, KIND_FIXTURE) else KIND_FIXTURE


def published() -> tuple[str, ...]:
    """Every dataset a reader is meant to choose from, in name order.

    Programs that offer a choice -- the Launch form, the read API's option list -- read this
    rather than globbing `data/`, so "which datasets exist" has one answer.
    """
    out = []
    for path in sorted((Path(__file__).resolve().parents[1] / "datasets").glob("*.py")):
        if path.stem in ("registry", "__init__"):
            continue
        try:
            if kind_of(path.stem) == KIND_DATASET:
                out.append(path.stem)
        except Exception:                                     # noqa: BLE001
            # A module that will not import is not a dataset a reader can pick, and
            # `load()` reports its own errors when someone actually runs it.
            continue
    return tuple(out)


_VALID = ("train", "eval")


def load(name: str) -> tuple[list[dict], dict[str, str]]:
    """Return (tasks, scorable) for a dataset name."""
    module = importlib.import_module(module_name(name))
    return module.load()


def load_split(name: str) -> tuple[list[dict], dict[str, str], dict | None]:
    """Return (tasks, scorable, split). `split` is None when undeclared.

    Validates the declaration here rather than at the point of use, because the
    failure mode of a bad split is a run that completes and reports a number.
    Every task must land on exactly one side, and the sides must not overlap --
    an overlap means the method is scored on something it studied.

    **A declaration is a claim about the benchmark, so both sides must be filled.** A
    split the *run* supplies is a different thing and is validated by `validate_split`
    with only the run's own side required; see that function for why.
    """
    tasks, scorable = load(name)
    module = importlib.import_module(module_name(name))
    split = getattr(module, "SPLIT", None)
    if split is None:
        return tasks, scorable, None
    return tasks, scorable, validate_split(name, tasks, split)


def validate_split(name: str, tasks: list[dict], split: dict,
                   *, require_nonempty: tuple[str, ...] = _VALID) -> dict:
    """Validate a mapping and return it normalized to both sides.

    This runs on splits that did **not** come from the dataset's own declaration: one the
    user supplied with `--split <file>`, or one this module derived from a dataset fact.
    Those differ from a declaration in one way that matters:

    * a **declaration** says "this benchmark has a study side and an exam side", so an
      empty side is a bug in the dataset and `require_nonempty` is both sides;
    * a **run** evaluates exactly one side (INTERFACE.md §2.6), and `--side eval --split
      official` is the report run -- the benchmark's whole evaluation set, with no study
      material in this pool at all. Requiring a filled `train` side there would make the
      most comparable run on the platform impossible to express, so the caller names the
      sides it needs filled.
    """
    unknown = set(split) - set(_VALID)
    if unknown:
        raise ValueError(
            f"dataset {name!r}: split has unknown side(s) {sorted(unknown)}; "
            f"expected only {list(_VALID)}")

    ids = [t["task_id"] for t in tasks]
    known = set(ids)
    groups = {side: list(split.get(side, [])) for side in _VALID}
    for side, members in groups.items():
        missing = set(members) - known
        if missing:
            raise ValueError(
                f"dataset {name!r}: split[{side!r}] names unknown task(s) "
                f"{sorted(missing)}")

    overlap = set(groups["train"]) & set(groups["eval"])
    if overlap:
        raise ValueError(
            f"dataset {name!r}: task(s) {sorted(overlap)} are on both sides of "
            f"the split; a task the method studies cannot also be one it is "
            f"scored on")

    covered = set(groups["train"]) | set(groups["eval"])
    if covered != known:
        raise ValueError(
            f"dataset {name!r}: task(s) {sorted(known - covered)} are on neither "
            f"side of the split; they would be silently neither trained on nor "
            f"scored")

    empty = [side for side in require_nonempty if not groups[side]]
    if empty:
        raise ValueError(f"dataset {name!r}: split has an empty side: {empty}")
    return groups


def split_sha(split: dict) -> str:
    """A digest of a resolved split, so two runs can be told apart by their task sets.

    Recorded on every run. Two points on one axis are comparable only if they scored the
    same tasks, and "the same dataset" does not say that once a run may supply its own
    split -- the same rule §2.1 already applies to the sampling policy.
    """
    payload = json.dumps({side: sorted(split[side]) for side in _VALID},
                         sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def is_eval_id(task_id: str) -> bool:
    """The platform's default rule for which side a task is on.

    A deterministic third, by hash of the task id -- hashed rather than sliced off the
    sorted list so that adding or removing a task does not reshuffle which side every
    other task is on. A split that moves under the benchmark is a split whose past
    scores cannot be compared with its future ones.
    """
    return hashlib.sha256(task_id.encode()).digest()[0] % 3 == 0


def default_split(tasks: list[dict]) -> dict:
    """The platform's default division, named rather than implied.

    Every adapter used to write this rule for itself, which made "the platform's default"
    indistinguishable from "what this benchmark does". It is a protocol choice, so
    `--split default` names it, a dataset's own declaration overrides it, and the run
    records which of the two it used.
    """
    ids = [t["task_id"] for t in tasks]
    return {"train": [i for i in ids if not is_eval_id(i)],
            "eval": [i for i in ids if is_eval_id(i)]}


#: The attribute a dataset uses to state the benchmark's own evaluation set.
OFFICIAL_EVAL_ATTR = "OFFICIAL_EVAL"

#: The attribute a dataset uses to say where its oracle's reference artifact came from.
ORACLE_SOURCE_ATTR = "ORACLE_SOURCE"


def oracle_source(name: str) -> tuple[str | None, str | None]:
    """`(ORACLE_SOURCE, ORACLE_COVERAGE)` for a dataset, as the run record needs them.

    Gate 1 is the oracle control: the dataset's own reference artifact, submitted, expected
    to score 1. That artifact used to be the benchmark's released answer by definition; since
    the platform may also **author** one (validated by the released tests and held out of the
    agent's view), a run has to record which kind it was, or two curves on one axis can mean
    two different things about the check. This is the same argument `split_sha` makes for the
    division, and the same rule about silence: `ORACLE_SOURCE` is **required** by
    `tools/dataset_lint.py`, so `None` here means a refused dataset (which has no oracle) or
    a fixture -- never a runnable dataset that declined to say.

    `ORACLE_COVERAGE` matters only for an authored reference, and it is the honest half: an
    authored oracle that exists for three tasks proves the check is reachable on three tasks,
    and a reader who cannot see that would read the control as covering the whole pool.
    """
    module = importlib.import_module(module_name(name))
    return (getattr(module, ORACLE_SOURCE_ATTR, None),
            getattr(module, "ORACLE_COVERAGE", None))


def official_split(name: str, tasks: list[dict]) -> dict:
    """The benchmark's own evaluation set, taken from the dataset's declaration.

    `OFFICIAL_EVAL` is a fact about the benchmark, not an opinion about this run: the task
    ids in this pool that belong to the set the benchmark's published numbers are computed
    on. `None` declares that **nothing in the pool is study material** -- every task in it
    belongs to the benchmark's evaluation set. It deliberately does *not* claim the pool is
    the whole of that set: `ds1000`'s pool is the official test split minus the rows
    measured as unrunnable on this host, so its declaration is `None` and its pool is still
    a subset. The one thing `None` must never mean is "the pool is also the training set",
    which is why an adapter that loads study material cannot use it.

    The attribute being **absent** is a different statement ("this dataset has not said")
    and is refused rather than guessed, because a guessed evaluation set produces a number
    that looks official and is not.
    """
    module = importlib.import_module(module_name(name))
    if not hasattr(module, OFFICIAL_EVAL_ATTR):
        raise ValueError(
            f"dataset {name!r} does not declare {OFFICIAL_EVAL_ATTR}, so `--split official` "
            f"has no meaning for it. Declare it in the adapter (the ids of the benchmark's "
            f"own evaluation set, or None when the pool holds no study material) or use "
            f"`--split declared` / `--split default`.")
    declared = getattr(module, OFFICIAL_EVAL_ATTR)
    ids = [t["task_id"] for t in tasks]
    eval_ids = list(ids) if declared is None else list(declared)
    # Hoisted, not inline: `i not in set(eval_ids)` inside the comprehension rebuilds the
    # set once per task. Measured on TriviaQA's pool (19,944 ids, 17,944 of them eval):
    # 9.75 s for this one call, which `QASpec.build()` pays at import.
    eval_set = set(eval_ids)
    return {"train": [i for i in ids if i not in eval_set], "eval": eval_ids}


# --------------------------------------------------------------- environments ---

#: The environment kinds the platform knows how to provide. `files` is a directory on
#: the host and the harness runs beside it in a namespace; `exec` is a container and
#: the harness runs inside it (INTERFACE.md §2.5). Kept here rather than in the driver
#: because a dataset is where a kind is *named*, and the driver imports it.
ENV_KINDS = ("files", "exec")

NETWORKS = ("none", "bridge", "services")
PLACEMENTS = ("inside", "beside")

#: Fields an environment may carry. Unknown keys are refused rather than ignored: a
#: mistyped `work_dir` that silently did nothing would leave a task running in the
#: wrong place while the dataset said otherwise.
ENV_FIELDS = ("kind", "image", "workdir", "network", "placement", "services", "web",
              "state", "cpus", "memory_mb", "agent_timeout_s", "verify_timeout_s", "agent_runtime")

#: Where a task's state lives (INTERFACE.md §2.5.9). `mount` is a host directory bound
#: at `workdir`; `container` is the container's own filesystem, snapshotted after the
#: harness stops so the check can run somewhere the harness never was.
STATES = ("mount", "container")

#: The default resource envelope. Declared per environment because the real task set
#: spans 1-4 cpus, 2-8 GB and 600-12000 s (measured), and a platform that ran those on
#: one default would report a harness failing a task it was never given time to attempt.
ENV_DEFAULTS = {"cpus": 2, "memory_mb": 4096,
                "agent_timeout_s": 3600, "verify_timeout_s": 3600}

#: A service's fields (INTERFACE.md §2.5.7). Every one has a consequence in
#: `eval/service/services.py`; none is decoration.
SERVICE_FIELDS = ("name", "image", "port", "health", "record", "needs_seed",
                  "needs_model", "command", "entrypoint", "cpus", "memory_mb")

#: A service name becomes a hostname on the task's internal network *and* a path
#: component under `/recordings`, so it is restricted to a DNS label. Refused rather
#: than sanitized: a name that is silently rewritten is not the name the harness was
#: told to call, and the harness would fail to resolve it for a reason nothing states.
SERVICE_NAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\Z")

#: Where the task's files land inside an `exec` environment when the dataset does not
#: say. Duplicated from `eval/containers/container.py` on purpose: the registry must be able to
#: state a normalized env without importing the container driver, which needs Docker.
DEFAULT_WORKDIR = "/app"


def _check_env(spec, tid: str, name: str) -> None:
    if not isinstance(spec, dict):
        raise ValueError(f"dataset {name!r}: ENV[{tid!r}] must be a dict")

    # Checked before the unknown-field rule so that naming a Dockerfile gets the
    # explanation rather than "unknown field 'dockerfile'". The two say very different
    # things: one is a typo, the other is a deliberate design boundary.
    if spec.get("dockerfile"):
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}] names a Dockerfile. Building is an "
            f"import step, not a run step, so a dataset does not reference one: "
            f"import it once and name the pinned image it produced (INTERFACE.md "
            f"§2.5.5).\n    python tools/import_env.py --dockerfile <path>")

    unknown = set(spec) - set(ENV_FIELDS)
    if unknown:
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}] has unknown field(s) {sorted(unknown)}; "
            f"known fields are {list(ENV_FIELDS)}")

    kind = spec.get("kind", "files")
    if kind not in ENV_KINDS:
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}] has kind {kind!r}; "
            f"known kinds are {list(ENV_KINDS)}")

    # Checked before the files/exec split so that a `files` env carrying `state` gets
    # the "only an exec environment uses this" message rather than being waved through.
    state = spec.get("state", "mount")
    if state not in STATES:
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}]['state'] must be one of {list(STATES)}, "
            f"got {state!r}")

    for field, minimum in (("cpus", 1), ("memory_mb", 64),
                           ("agent_timeout_s", 1), ("verify_timeout_s", 1)):
        value = spec.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or value < minimum:
            raise ValueError(
                f"dataset {name!r}: ENV[{tid!r}][{field!r}] must be a number >= "
                f"{minimum}, got {value!r}")

    if state == "container" and spec.get("services"):
        # Refused rather than half-supported. A recording is a **bind mount**, and
        # `docker commit` does not capture bind mounts -- so the check would look for
        # evidence that silently did not travel. Nothing in the real task set needs both
        # (measured: `mcp_servers` is empty for all 89), so this is a boundary, not a gap.
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}] asks for state='container' together with "
            f"services. A service's recording travels by bind mount and a container "
            f"snapshot does not capture one, so the check would read an empty recording "
            f"and the failure would look like the harness's. Not supported yet.")

    if kind == "files":
        extra = set(spec) - {"kind", "network", "agent_timeout_s", "verify_timeout_s", "agent_runtime"}
        if extra:
            raise ValueError(
                f"dataset {name!r}: ENV[{tid!r}] is kind 'files' but carries "
                f"{sorted(extra)}, which only an 'exec' environment uses. A field "
                f"that does nothing is worse than a missing one: the dataset would "
                f"read as though it had asked for a container.")
        if "network" in spec and spec["network"] != "none":
            raise ValueError("files network policy must be 'none' or omitted")
        if "agent_runtime" in spec:
            runtime = spec["agent_runtime"]
            if not isinstance(runtime, dict) or set(runtime) != {"package_dir", "files"} \
                    or not isinstance(runtime["package_dir"], str) \
                    or not Path(runtime["package_dir"]).is_absolute() \
                    or not isinstance(runtime["files"], list) or not runtime["files"]:
                raise ValueError("files agent_runtime needs an absolute package_dir and nonempty files")
        return

    if "agent_runtime" in spec:
        raise ValueError("agent_runtime package projections require a files environment")

    web = spec.get("web")
    if web is not None:
        if not isinstance(web, dict) or set(web) - {"search", "fetch"}:
            raise ValueError(f"dataset {name!r}: ENV[{tid!r}]['web'] must be a dict with "
                             "boolean search/fetch fields")
        if not all(isinstance(web.get(k), bool) for k in ("search", "fetch")):
            raise ValueError(f"dataset {name!r}: ENV[{tid!r}]['web'] needs boolean search/fetch fields")
        if not any(web.get(k) for k in ("search", "fetch")):
            raise ValueError(f"dataset {name!r}: ENV[{tid!r}]['web'] must enable search or fetch")

    image = spec.get("image")
    if not isinstance(image, str) or not image.strip():
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}] is kind 'exec' and needs a non-empty "
            f"`image`. Name an image that is already on the local daemon; this "
            f"platform does not pull or build during a run (INTERFACE.md §2.5.5).")

    workdir = spec.get("workdir", DEFAULT_WORKDIR)
    if not isinstance(workdir, str) or not workdir.startswith("/"):
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}]['workdir'] must be an absolute path "
            f"inside the container, got {workdir!r}. A relative one would resolve "
            f"against the image's WORKDIR and land the task somewhere the dataset "
            f"did not name.")

    network = spec.get("network", "none")
    if network not in NETWORKS:
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}]['network'] must be one of "
            f"{list(NETWORKS)}, got {network!r}")

    placement = spec.get("placement", "inside")
    if placement not in PLACEMENTS:
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}]['placement'] must be one of "
            f"{list(PLACEMENTS)}, got {placement!r}")

    services = spec.get("services") or []
    if not isinstance(services, list):
        raise ValueError(f"dataset {name!r}: ENV[{tid!r}]['services'] must be a list")
    seen: set[str] = set()
    for svc in services:
        _check_service(svc, tid, name)
        # Uniqueness here rather than at start time: two services with one name would
        # resolve to whichever container won the race, so the harness would reach an
        # environment the dataset did not describe and nothing would say so.
        if svc["name"] in seen:
            raise ValueError(
                f"dataset {name!r}: ENV[{tid!r}] declares two services named "
                f"{svc['name']!r}; a name is a hostname on the task's network, so "
                f"duplicates are ambiguous rather than merely untidy")
        seen.add(svc["name"])

    # Both directions of the same contradiction. Declaring services with no network to
    # reach them on, and asking for a network with nothing to put on it, are equally
    # likely to be a typo -- and silently upgrading one of them would leave the dataset
    # describing a run that did not happen (INTERFACE.md §2.5.7).
    if services and network != "services":
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}] declares services but sets "
            f"network={network!r}. A service is reachable only on the task's internal "
            f"network, so this is a contradiction; write network='services'.")
    if web and network != "services":
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}] declares web access but sets network={network!r}. "
            "The host gateway is published only on the task's internal network; write "
            "network='services'.")
    if network == "services" and not services and not spec.get("web"):
        raise ValueError(
            f"dataset {name!r}: ENV[{tid!r}] sets network='services' but declares no "
            f"services. There is nothing to be reachable, and the network would only "
            f"be an isolation the dataset did not ask for.")


def _check_service(svc, tid: str, name: str) -> None:
    where = f"dataset {name!r}: ENV[{tid!r}]['services']"
    if not isinstance(svc, dict):
        raise ValueError(f"{where} entries must be dicts")

    unknown = set(svc) - set(SERVICE_FIELDS)
    if unknown:
        raise ValueError(
            f"{where} has unknown field(s) {sorted(unknown)}; known fields are "
            f"{list(SERVICE_FIELDS)}")

    for field, minimum in (("cpus", 1), ("memory_mb", 64)):
        if field in svc:
            value = svc[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value < minimum:
                raise ValueError(
                    f"{where}: service `{field}` must be a number >= {minimum}, "
                    f"got {value!r}")

    svc_name = svc.get("name")
    if not isinstance(svc_name, str) or not SERVICE_NAME_RE.match(svc_name):
        raise ValueError(
            f"{where}: service name {svc_name!r} must be a DNS label -- lowercase "
            f"letters, digits and internal hyphens. It becomes a hostname the harness "
            f"resolves and a directory under /recordings.")

    image = svc.get("image")
    if not isinstance(image, str) or not image.strip():
        raise ValueError(
            f"{where}: service {svc_name!r} needs a non-empty `image`, pinned to a "
            f"digest by the same rule as the task's (INTERFACE.md §2.5.4). A service "
            f"is a container and never a host process (§2.5.8), so that a harness "
            f"cannot read or kill the thing grading its interaction.")

    # Docker exec form: command replaces CMD, entrypoint replaces ENTRYPOINT.
    # Omission preserves the image defaults; shell strings are never split or run.
    if "command" in svc:
        command = svc["command"]
        if not isinstance(command, list) or not command or not all(
                isinstance(arg, str) and "\x00" not in arg for arg in command) \
                or not command[0].strip():
            raise ValueError(
                f"{where}: service {svc_name!r} `command` must be a non-empty "
                "list of NUL-free strings with a non-empty first argument")
    if "entrypoint" in svc:
        entrypoint = svc["entrypoint"]
        if not isinstance(entrypoint, str) or not entrypoint.strip() \
                or "\x00" in entrypoint:
            raise ValueError(
                f"{where}: service {svc_name!r} `entrypoint` must be a non-empty "
                "NUL-free executable string")

    port = svc.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError(
            f"{where}: service {svc_name!r} needs `port` as an integer in 1..65535, "
            f"got {port!r}. It is the port the harness is told to call.")

    # Required, not defaulted to "is the port open". A port that accepts a connection
    # is not a service that is ready, and the whole point of the check is that the
    # harness must not start against a service that is still initialising -- a score
    # decided by a race is not a measurement (INTERFACE.md §2.5.7).
    health = svc.get("health")
    if not isinstance(health, dict):
        raise ValueError(
            f"{where}: service {svc_name!r} needs a `health` check. Without one the "
            f"platform cannot tell a service that is ready from one that accepts a "
            f"connection and is not, and the task's score becomes a race it runs "
            f"against its own infrastructure. Shape: "
            f"{{'argv': [...], 'timeout_s': 30}}")
    argv = health.get("argv")
    if not isinstance(argv, list) or not argv or not all(
            isinstance(a, str) for a in argv):
        raise ValueError(
            f"{where}: service {svc_name!r} `health` needs `argv` as a non-empty list "
            f"of strings, run inside the service container")
    timeout = health.get("timeout_s", 60)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError(
            f"{where}: service {svc_name!r} `health` timeout_s must be positive, "
            f"got {timeout!r}")

    for flag in ("record", "needs_seed", "needs_model"):
        if not isinstance(svc.get(flag, False), bool):
            raise ValueError(
                f"{where}: service {svc_name!r} `{flag}` must be a bool, got "
                f"{svc.get(flag)!r}")


def _normalize_env(spec: dict) -> dict:
    """The canonical form, so two spellings of one environment compare equal."""
    if spec.get("kind", "files") == "files":
        return {"kind": "files", **{key: spec[key] for key in
                ("network", "agent_timeout_s", "verify_timeout_s", "agent_runtime") if key in spec}}
    services = []
    for svc in spec.get("services") or []:
        services.append({
            "name": svc["name"], "image": svc["image"], "port": svc["port"],
            "health": {"argv": list(svc["health"]["argv"]),
                       "timeout_s": svc["health"].get("timeout_s", 60)},
            "record": svc.get("record", False),
            "needs_seed": svc.get("needs_seed", False),
            "needs_model": svc.get("needs_model", False),
            **({"command": list(svc["command"])} if "command" in svc else {}),
            **({"entrypoint": svc["entrypoint"]} if "entrypoint" in svc else {}),
            **{field: svc[field] for field in ("cpus", "memory_mb") if field in svc},
        })
    normalized = {"kind": "exec",
            "image": spec["image"],
            "workdir": spec.get("workdir", DEFAULT_WORKDIR),
            "network": spec.get("network", "none"),
            "placement": spec.get("placement", "inside"),
            "services": services,
            "state": spec.get("state", "mount"),
            **{f: spec.get(f, d) for f, d in ENV_DEFAULTS.items()}}
    if spec.get("web") is not None:
        normalized["web"] = {"search": bool(spec["web"]["search"]),
                              "fetch": bool(spec["web"]["fetch"])}
    return normalized


def _selected_tasks(name: str, tasks: list[dict], task_ids) -> list[dict]:
    """Validate an explicit selection without reading any declaration values."""
    if task_ids is None:
        return tasks
    if isinstance(task_ids, (str, bytes)):
        raise ValueError(f"dataset {name!r}: task_ids must be an iterable of task IDs")
    selected = list(task_ids)
    if any(not isinstance(tid, str) for tid in selected):
        raise ValueError(f"dataset {name!r}: selected task IDs must be strings")
    if len(set(selected)) != len(selected):
        raise ValueError(f"dataset {name!r}: duplicate selected task IDs")
    unknown = set(selected) - {t["task_id"] for t in tasks}
    if unknown:
        raise ValueError(f"dataset {name!r}: selected unknown task(s) {sorted(unknown)}")
    chosen = set(selected)
    return [task for task in tasks if task["task_id"] in chosen]


def load_envs(name: str, *, task_ids=None) -> dict[str, dict]:
    """Resolve selected ENV values, falling back to DEFAULT_ENV or files.

    None preserves the full-pool behavior. An explicit selection validates global
    declaration keys but never materializes environments for unselected tasks.
    The result remains an ordinary dict.
    """
    tasks, _ = load(name)
    selected = _selected_tasks(name, tasks, task_ids)
    module = importlib.import_module(module_name(name))
    default = getattr(module, "DEFAULT_ENV", None) or {"kind": "files"}
    declared = getattr(module, "ENV", None)
    if declared is None:
        declared = {}

    if not isinstance(default, dict):
        raise ValueError(f"dataset {name!r}: DEFAULT_ENV must be a dict")
    _check_env(default, "<default>", name)

    ids = {t["task_id"] for t in tasks}
    unknown = set(declared) - ids
    if unknown:
        raise ValueError(f"dataset {name!r}: ENV names unknown task(s) {sorted(unknown)}")

    envs = {}
    for task in selected:
        tid = task["task_id"]
        spec = declared.get(tid, default)
        _check_env(spec, tid, name)
        envs[tid] = _normalize_env(spec)
    return envs


# ------------------------------------------------------- tasks and verifiers ---

#: The verifier kinds the platform knows how to run. Unknown kinds are refused at
#: load time rather than at scoring time: a dataset that only fails once a run has
#: been paid for is a dataset nobody can debug.
VERIFIER_KINDS = ("answer", "command")

#: Verifier fields. `reward_file` is Terminal-Bench's convention: the check writes the
#: score into a file and exits 0 either way, so the exit code carries nothing
#: (INTERFACE.md §2.5.9). Parsed as a number, so a fraction is a score.
VERIFIER_FIELDS = ("kind", "argv", "cwd", "inputs", "pass_when", "stdout",
                   "timeout_s", "expected", "env", "reward_file", "evidence_file",
                   "image")

#: Keys that must never appear in a task dict, because `run_one` writes the whole
#: dict to `task.json` and hands it to the harness. Putting the grade or the initial
#: state in there would hand the harness what it is being measured against.
#:
#: `env` is here for consistency rather than secrecy: an environment is declared in
#: the module-level `ENV`/`DEFAULT_ENV` like `SETUP` and `VERIFY` are, so one that
#: appears inside a task would be silently *not* the environment that ran, while the
#: harness read it as though it were.
FORBIDDEN_IN_TASKS = ("verify", "verifier", "setup", "expected", "scorable", "env")


def _check_no_secrets(task: dict, name: str) -> None:
    leaked = [k for k in task if k in FORBIDDEN_IN_TASKS]
    if leaked:
        raise ValueError(
            f"dataset {name!r}: task {task.get('task_id')!r} carries {leaked}, which the "
            f"harness would receive verbatim: every task dict is written to task.json "
            f"and passed on the command line. The grade and the initial state belong in "
            f"VERIFY and SETUP (INTERFACE.md §2.4).")


def _check_setup(spec, tid: str, name: str) -> None:
    if not isinstance(spec, dict):
        raise ValueError(f"dataset {name!r}: SETUP[{tid!r}] must be a dict")
    if ((spec.get("tool_relay") or {}).get("record")) is not None:
        from data.scaffolds.runtime_extensions import validate_record
        validate_record(spec["tool_relay"]["record"])
    if (spec.get("tool_relay") or {}).get("model") is not None:
        from data.scaffolds.runtime_extensions import validate_judge
        validate_judge(spec["tool_relay"]["model"], allow_trial_seed=True)
    if "workers" in spec:
        from data.scaffolds.runtime_extensions import validate_workers
        validate_workers(spec["workers"])
        for worker in spec["workers"]:
            _check_setup({"files": worker.get("files", [])}, tid, name)
    if 'mailbox' in spec:
        from data.scaffolds.runtime_extensions import validate_mailbox
        if 'workers' not in spec:
            raise ValueError('mailbox requires workers')
        validate_mailbox(spec['mailbox'])
    if "runtime_prompt" in spec and (
            not isinstance(spec["runtime_prompt"], str)
            or not spec["runtime_prompt"].strip()):
        raise ValueError(
            f"dataset {name!r}: SETUP[{tid!r}]['runtime_prompt'] must be a nonempty string")
    files = spec.get("files") or []
    if not isinstance(files, list):
        raise ValueError(f"dataset {name!r}: SETUP[{tid!r}]['files'] must be a list")
    for entry in files:
        if not isinstance(entry, dict) or not entry.get("path"):
            raise ValueError(
                f"dataset {name!r}: SETUP[{tid!r}] has an entry without a `path`")
        # Escape refused, not clamped. A `../` that silently became `.` would set up a
        # task whose inputs are not where the dataset said they were.
        parts = Path(str(entry["path"])).parts
        if str(entry["path"]).startswith("/") or ".." in parts:
            raise ValueError(
                f"dataset {name!r}: SETUP[{tid!r}] path {entry['path']!r} escapes the "
                f"working directory")
        if "content" not in entry and "from" not in entry:
            raise ValueError(
                f"dataset {name!r}: SETUP[{tid!r}] entry {entry['path']!r} has neither "
                f"`content` nor `from`")


def _check_verifier(spec, tid: str, name: str) -> None:
    if not isinstance(spec, dict):
        raise ValueError(f"dataset {name!r}: VERIFY[{tid!r}] must be a dict")
    from data.scaffolds.scorer_isolation import declared_class
    declared_class(spec)
    if "result_max_bytes" in spec:
        limit = spec["result_max_bytes"]
        if "container_runtime" not in spec:
            raise ValueError("result_max_bytes requires container_runtime")
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("result_max_bytes must be positive or null")
    if "container_runtime" in spec:
        from data.scaffolds.runtime_extensions import validate_container_runtime
        validate_container_runtime(spec["container_runtime"])
        if spec.get("kind") != "command" or not spec.get("result_contract"):
            raise ValueError("container_runtime requires command and result_contract")
    if "trial_protocol" in spec:
        from data.scaffolds.runtime_extensions import validate_trials
        validate_trials(spec["trial_protocol"])
    if "judge" in spec:
        from data.scaffolds.runtime_extensions import validate_judge as validate
        validate(spec["judge"])
        if spec.get("kind") != "command" or "result_contract" not in spec:
            raise ValueError("judge requires command verification with result_contract")
    if "result_contract" in spec:
        from data.scaffolds.runtime_extensions import validate_result as validate
        validate(spec["result_contract"])
    kind = spec.get("kind")
    if kind not in VERIFIER_KINDS:
        raise ValueError(
            f"dataset {name!r}: VERIFY[{tid!r}] has kind {kind!r}; "
            f"known kinds are {list(VERIFIER_KINDS)}")
    if kind == "answer" and not isinstance(spec.get("expected"), str):
        raise ValueError(f"dataset {name!r}: VERIFY[{tid!r}] needs a string `expected`")
    if kind == "command":
        argv = spec.get("argv")
        if not isinstance(argv, list) or not argv or not all(
                isinstance(a, str) for a in argv):
            raise ValueError(
                f"dataset {name!r}: VERIFY[{tid!r}] needs `argv` as a non-empty list of "
                f"strings")
        for entry in (spec.get("inputs") or []):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"dataset {name!r}: VERIFY[{tid!r}] inputs must be dicts")
            # An input names either a path relative to the workdir (`path`, the original
            # contract) or an absolute location inside the container (`dst`), because a
            # check that expects its tests at `/tests` cannot get them from a relative
            # path (INTERFACE.md §2.5.9).
            target = entry.get("dst") or entry.get("path")
            if not target:
                raise ValueError(
                    f"dataset {name!r}: VERIFY[{tid!r}] input has neither `path` nor "
                    f"`dst`")
            if entry.get("dst") and not str(entry["dst"]).startswith("/"):
                raise ValueError(
                    f"dataset {name!r}: VERIFY[{tid!r}] input `dst` must be absolute, "
                    f"got {entry['dst']!r}; it is a path inside the container")
            if "content" not in entry and "from" not in entry:
                raise ValueError(
                    f"dataset {name!r}: VERIFY[{tid!r}] input {target!r} has neither "
                    f"`content` nor `from`")
        reward = spec.get("reward_file")
        if reward is not None and (not isinstance(reward, str)
                                   or not reward.startswith("/")):
            raise ValueError(
                f"dataset {name!r}: VERIFY[{tid!r}]['reward_file'] must be an absolute "
                f"path inside the container, got {reward!r}")
        if reward and spec.get("pass_when", "exit0") != "exit0":
            raise ValueError(
                f"dataset {name!r}: VERIFY[{tid!r}] declares both `reward_file` and "
                f"`pass_when`; the reward comes from the file, so `pass_when` would be "
                f"a second answer to the same question")
        evidence = spec.get("evidence_file")
        if evidence is not None:
            if not isinstance(evidence, str) or not evidence.startswith("/"):
                raise ValueError(
                    f"dataset {name!r}: VERIFY[{tid!r}]['evidence_file'] must be an absolute "
                    f"path inside the container, got {evidence!r}")
            if reward and Path(evidence).parent != Path(reward).parent:
                raise ValueError(
                    f"dataset {name!r}: VERIFY[{tid!r}] reward_file and evidence_file "
                    "must share a parent directory; separate output mounts are not supported")
        if spec.get("pass_when", "exit0") not in ("exit0", "exit0_stdout"):
            raise ValueError(
                f"dataset {name!r}: VERIFY[{tid!r}] pass_when must be 'exit0' or "
                f"'exit0_stdout'")


def load_tasks(name: str, *, task_ids=None) -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
    """Return selected (tasks, setups, verifiers) as a list and ordinary dicts.

    None keeps full-pool loading. Explicit task_ids validate global identities,
    then read only selected SETUP/VERIFY values, including lazy mappings.

    `SCORABLE` is not a second mechanism: a dataset that declares it and no `VERIFY`
    gets `{"kind": "answer", "expected": ...}` for each task, which is precisely what
    the platform did before verifiers existed. The equivalence is deliberate, so that
    adding this layer changed nothing for the datasets that already worked.
    """
    tasks, scorable = load(name)
    module = importlib.import_module(module_name(name))
    selected = _selected_tasks(name, tasks, task_ids)
    setup_declarations = getattr(module, "SETUP", None)
    if setup_declarations is None:
        setup_declarations = {}
    declared = getattr(module, "VERIFY", None)

    for task in tasks:
        _check_no_secrets(task, name)

    ids = {t["task_id"] for t in tasks}

    if declared is None:
        missing = [t["task_id"] for t in selected if t["task_id"] not in scorable]
        if missing:
            raise ValueError(
                f"dataset {name!r}: no VERIFY declared and SCORABLE is missing "
                f"{missing}; those tasks would have no grade at all")
        verifiers = {t["task_id"]: {"kind": "answer", "expected": scorable[t["task_id"]]}
                     for t in selected}
    else:
        unknown = set(declared) - ids
        if unknown:
            raise ValueError(
                f"dataset {name!r}: VERIFY names unknown task(s) {sorted(unknown)}")
        absent = ids - set(declared)
        if absent:
            raise ValueError(
                f"dataset {name!r}: task(s) {sorted(absent)} have no VERIFY entry")
        verifiers = {}
        for task in selected:
            tid = task["task_id"]
            spec = declared[tid]
            _check_verifier(spec, tid, name)
            verifiers[tid] = {"kind": "answer", "expected": scorable.get(tid, ""),
                              **spec} if spec.get("kind") == "answer" else dict(spec)

    setup_ids = set(setup_declarations)
    unknown_setup = setup_ids - ids
    if unknown_setup:
        raise ValueError(f"dataset {name!r}: SETUP names unknown task(s) "
                         f"{sorted(unknown_setup)}")
    setups = {}
    for task in selected:
        tid = task["task_id"]
        if tid in setup_ids:
            spec = setup_declarations[tid]
            _check_setup(spec, tid, name)
            setups[tid] = spec

    return selected, setups, verifiers
