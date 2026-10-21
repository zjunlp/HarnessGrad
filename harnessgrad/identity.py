"""What a measurement says it is.

The identity block is the part of a curve point a reader uses to decide whether two points
are comparable at all: which harness (by content hash), which models answered, which
improver composed the edits, on which side of the split, measured where. It is assembled
from a manifest, the models the run actually used, and whatever the harness declared about
itself in its own trace.

Pure on purpose -- no docker, no HTTP, and no filesystem beyond what it is handed, with one
deliberate exception that `_skill_sha` documents: the skill text is the platform's own input
to the method, it is a single fixed path, and a point that does not name it cannot be
compared with a point taken after it was edited. The improver used to be a module-level
global here that `main()` assigned into; it is an argument now, so "what does this point
claim" is answerable without running a program, and a caller that forgets it gets an
identity with no improver rather than one run's improver leaking into another's record.
"""

from __future__ import annotations

import hashlib

from harnessgrad import FRAMEWORK_VERSION, PLATFORM_ROOT
from harnessgrad.config import current as current_config

def _model_of(declared: dict | None) -> str | None:
    """Which model actually answered, and where that answer came from.

    The harness's declaration wins over the platform's environment, because the
    harness is the only party that knows. When both exist and disagree, both are kept
    (`agent_model_conflict` below) -- resolving it silently in either direction is how
    a record ends up disagreeing with the run it describes.

    The fallback is the declared `AgentConfig.model` field. Before the configuration
    layer this line read `os.environ.get("HG_AGENT_MODEL")` directly; the variable and
    the default are unchanged, they are simply declared in `harnessgrad/config` now.
    """
    declared = declared or {}
    return declared.get("agent_model") or current_config().agent.model or None

def _backend_of(declared: dict | None) -> str:
    declared = declared or {}
    return (declared.get("agent_backend")
            or current_config().agent.backend or "mock")

def _conflict(declared: dict | None) -> dict:
    """Both values, when the harness and the platform disagree about the model."""
    declared = declared or {}
    from_harness = declared.get("agent_model")
    from_env = current_config().agent.model
    if from_harness and from_env and from_harness != from_env:
        return {"harness": from_harness, "platform_env": from_env,
                "note": "the harness did not use HG_AGENT_MODEL; the declaration is "
                        "what ran"}
    return {}

def _skill_sha() -> str | None:
    """The sha256 of the skill the platform offered the method this round, or nothing.

    `improver` says *which tool* composed the candidate. It says nothing about what that
    tool was told, and the skill is the platform's own input to the method -- the same
    improver given a different skill produces different edits. Measured reason this field
    exists: `improvers/skill.md` gained a fourth failure mode between two runs, both points
    named the same method and the same improver version, the edits changed, and nothing in
    either record said why. That is a comparison a reader cannot make, which is the whole
    job of this block.

    Read per call, not cached at import, because the platform may edit its own skill between
    rounds of one run -- self-improvement on the method side is a case this platform exists
    to measure, and a hash frozen at import would report the first round's skill forever.

    `None` when the file is absent, which is a real state: a run with no default skill stages
    no `SKILL.md`, and the field is then omitted rather than hashed over an empty file.
    """
    try:
        return hashlib.sha256(
            (PLATFORM_ROOT / "improvers" / "skill.md").read_bytes()).hexdigest()
    except OSError:
        return None


#: The fields of a resolved improver that belong on a curve point. `ok` and `reason` are
#: bookkeeping for the process that resolved it and are dropped: a record should carry what
#: was used, not the outcome of looking for it.
#:
#: **`kind` was missing, and its absence quietly disabled the whole CLI layer.** The method
#: decides whether to drive a CLI improver or call an endpoint by reading the improver block
#: out of the staged `round.json` (`methods/editor.py:cli_improver_identity`), and it asked
#: for `kind == "cli"`. `resolve` sets `kind` -- the gate reads it -- but this filter dropped
#: it, so the staged block never had one, the answer was always "not a CLI", and every method
#: fell back to the raw chat endpoint. Measured (`v2-dgm`, 2026-10-05): the method process sat
#: in `do_poll` on a socket to the model endpoint while `codex` was resolved, probed for tool
#: calls, and never spawned; the run's candidate was a 9B whole-file rewrite, which is what a
#: chat model asked to "propose one change" does. `improver.kind` is the difference between
#: "an RSI rule driving codex" and "an RSI rule driving a bare model", and the record has to
#: carry it for the method to know which it is.
#: `base_url` is here for one reason: it is the **only** field that separates an endpoint
#: from a CLI once `resolve` has run, because an endpoint carries `path` = its base URL
#: (`tools/improver.py:resolve`). Without it in the record, the method-side test
#: "does this improver carry a path and no base_url?" -- the fallback `cli_improver.applies`
#: has always documented -- could not tell them apart, and every endpoint improver was
#: classified as a CLI. Measured (`ds-dgm`, 2026-10-05): `--improver deepseek` reached
#: `cli_improver._improver_command`, which did `Path("https://api.deepseek.com/v1").exists()`,
#: and the round died with "the platform named an improver at ... which does not exist inside
#: this sandbox" -- a true statement about a URL. It is a URL, not a secret: the key is
#: copied from `api_key_env` and never written here.
#:
#: `argv_style` is identity rather than plumbing. It is *which CLI this is* -- codex and dsh
#: do not take the same command line -- and it has to reach the method, because the method is
#: what builds that command line. Filtered out of the request, a dsh run would have been
#: handed codex's `exec --sandbox ... -C` and died looking like a method bug; recorded on the
#: point, it is also what tells two improvers sharing one model apart.
IMPROVER_IDENTITY_FIELDS = ("name", "kind", "argv_style", "base_url", "version", "sha256",
                            "model", "requested", "modified", "path")


def _improver_identity(improver: dict | None) -> dict:
    """The improver's identity, or nothing at all.

    Passed in rather than read from a module global: "which improver composed these edits"
    is a property of the point being written, and a global made it a property of whichever
    run happened to be in the same process. An empty dict means the run carried no improver
    -- the reference methods do their own editing -- and the field is then absent rather
    than empty, because a point must never claim an improver it did not use.
    """
    improver = improver or {}
    return {k: improver[k] for k in IMPROVER_IDENTITY_FIELDS if k in improver}


def _improver_runtime(improver: dict | None) -> tuple[str, ...]:
    """The trees on disk the improver needs in order to *run*.

    The other half of the same resolution, and deliberately not part of what a point
    says: this is what the method's sandbox has to bind so that the method can execute
    the tool the platform picked (`tools/improver.py:runtime_trees`;
    `harnessgrad/methods.py:_method_sandbox_plan`). A tuple rather than a list because it
    is handed straight to the sandbox plan, and a plan that changes between the hash
    check and the call is the kind of thing this platform exists to notice.
    """
    return tuple(str(path) for path in ((improver or {}).get("runtime") or []))



#: The keys a tree may use to describe what it was copied from. Closed on purpose: the point
#: is a lineage (which project, which revision), not a copy of the upstream manifest.
_BASE_FIELDS = ("name", "version", "repo", "commit", "sha256", "manifest", "path")


def _harness_base(man: dict) -> dict:
    """The declared lineage of a candidate tree, or `{}` when it declares none.

    Empty rather than `None`-valued: a point must not claim a lineage it did not have, and a
    field whose value is `null` reads as "the platform looked and found nothing", which is a
    different statement from "this harness was written here".
    """
    base = (man or {}).get("base")
    if not isinstance(base, dict):
        return {}
    return {k: str(base[k]) for k in _BASE_FIELDS if isinstance(base.get(k), (str, int))}

def _identity(man: dict, sha: str, mode: str, method_model: str = "n/a",
              declared: dict | None = None, improver: dict | None = None,
              method: str | None = None, jobs: int | None = None,
              trials: int | None = None, gpu_budget: int | None = None) -> dict:
    """Who produced this number.

    There is no platform-side method identity any more: the method IS the
    implementation of the entrypoints inside the harness repo. So the harness
    version is the method version, and `editable_surface_touched` (below) is how
    the platform reports what that method did to itself.
    """
    return {
        "harness_name": man["name"], "harness_version": man["version"],
        "harness_sha": sha,
        "improve_entrypoint": man.get("improve_entrypoint"),
        "accept_entrypoint": man.get("accept_entrypoint"),
        "run_mode": mode,
        "framework_version": FRAMEWORK_VERSION,
        # Read from the same names the harness reads, or the record disagrees with
        # the run: this line said `mock` while a live deepseek-flash answered every
        # task, because the variable had been renamed in the harness and not here.
        # A curve point that misreports its model is worse than one that omits it.
        #
        # `HG_AGENT_*` is *our* convention, and it is the whole story only for a
        # harness we wrote. An agent CLI reads its model from its own config file or a
        # flag, so the platform's environment can be entirely ignored -- which is how
        # the record starts lying. A harness may therefore declare what ran it, in its
        # trace (`eval/observe/tracing.py:_last_identity`), and the declaration wins.
        "agent_model": _model_of(declared),
        "agent_backend": _backend_of(declared),
        "agent_model_source": ("harness" if (declared or {}).get("agent_model")
                               else "platform-env"),
        "method_model": method_model,
        # **Which improver produced the candidate.** A method is a decision rule *and*
        # an improver, and until this field existed the platform recorded only the rule's
        # model -- so an improver upgrade was indistinguishable from a better method. The
        # version and the hash are the parts that cannot be reconstructed later: several
        # codex builds coexist on one machine (measured: 0.125.0 broken, 0.139.0, 0.156.1)
        # and `latest` is a request, not an identity.
        **({"improver": _improver_identity(improver)} if improver else {}),
        # **Is the method the harness it edits?** HarnessX/harness²-style self-editing is a
        # different claim from a method that edits somebody else's tree, and until this field
        # existed a curve point could not say which one it was: `improver.name` and
        # `harness_name` were both present, but a reader had to know to compare them, and the
        # comparison is exactly the paper's independent variable (does the improver share the
        # artefact's own surface?). Recorded as a boolean on every point so a table can group
        # by it without re-deriving the rule.
        "self_referential": bool(improver) and (
            _improver_identity(improver).get("name") == man.get("name")),
        # **What the candidate tree was copied from.** A harness we ship is a candidate, not a
        # referee, and a candidate that started as a copy of a real framework has a lineage a
        # reader needs in order to read its rounds: `dsh`-style trees copy an upstream checkout
        # and are then edited in place, so "the harness got better" and "the harness started
        # from a much larger codebase" have to be distinguishable. Declared by the tree itself
        # in `harness.json` under `base`; restricted to the fields that identify a lineage, so
        # a manifest cannot smuggle a whole tree through this field.
        **({"harness_base": base} if (base := _harness_base(man)) else {}),
        # Which instructions the improver was given, kept apart from which improver it was.
        **({"skill_sha": s} if (s := _skill_sha()) else {}),
        # **Which method produced this candidate.** The point carried `method_model` and the
        # improver's name, but never the *method*: the only place it appeared was
        # `run_meta.method_entrypoint`, a path. So a curve copied out of its run directory --
        # which is how a curve is read, plotted and compared -- could not say whether round 1
        # came from `llm_improver` or from `rrsi`, and two methods in one figure were
        # indistinguishable except by their scores. The method reports its own name in every
        # step (`methods/editor.py:report(method=...)`), so this is that name, read from the
        # step rather than guessed from the entrypoint's directory.
        **({"method": method} if method else {}),
        **({"harness_identity": declared} if declared else {}),
        # **The conditions the number was produced under.** `run_meta.json` already records
        # `jobs` and `trials`, but a curve point is the unit that gets copied, plotted and
        # compared, and it did not carry them -- so "0.83" from a six-way run and "0.83" from
        # a forty-way run were indistinguishable inside the point itself. `trials` was also
        # half-present: the point carries `n_trials`, while the reference check reads
        # identity, so a reference and a candidate could disagree on trials and nothing said
        # so. Both are emitted only when known, like every other optional field here.
        **({"jobs": int(jobs)} if jobs is not None else {}),
        **({"trials": int(trials)} if trials is not None else {}),
        # The **global** measurement budget this point was produced under, when a batch shared
        # one: `jobs` is this run's own ceiling, while this is the capacity every concurrent
        # run on the machine was admitted against (`harnessgrad/slots.py`). They answer
        # different questions -- "how many at once here" versus "how many at once anywhere" --
        # and a reader needs both to reproduce the contention.
        **({"gpu_budget": int(gpu_budget)} if gpu_budget is not None else {}),
        # **The harness's own runtime budget, promoted out of the declaration.**
        #
        # `HG_AGENT_MAX_STEPS=16` and `=32` run the same harness file: one trajectory
        # stops at 16 steps and the other reaches 32. Measured (2026-10-04, local 9B on
        # `build-pmars`): 32 steps at ~35 s/step is ~1100 s, past the task's own 900 s
        # agent budget, so the run is killed; 16 steps finishes. Both used to produce
        # curve points that differed in *no* field, which makes "the same harness
        # measured twice" and "two harnesses" the same record.
        #
        # Promoted rather than left nested so a reader can ask one question --
        # `identity.harness_config.max_steps` -- without knowing the declaration's
        # shape. Absent when the harness declares nothing, which is a fact ("this
        # harness does not say") and not a zero.
        **({"harness_config": c} if (c := (declared or {}).get("config")) else {}),
        **({"agent_model_conflict": c} if (c := _conflict(declared)) else {}),
    }

