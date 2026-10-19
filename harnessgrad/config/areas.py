"""The platform's configuration surface, declared once.

One frozen dataclass per *area* of configuration -- the agent's model, the method's
model and budget, the improver, the environment's own model, the sandbox, egress, the
runtime overlay/registry, and storage. Each field carries, in its metadata, the exact
environment variable it is read from (`"env"`), one line saying what it does (`"doc"`),
how the raw string becomes a value (`"kind"`), and whether it is a credential that must
never be written down (`"secret"`).

The areas are grouped by **what they configure**, not by which module reads them: the
agent's model is read by `driver.py`, `harnessgrad/identity.py` and `eval/run/runner.py`,
and it is one thing. Before this package existed the 38 variables below were read with
`os.environ.get(...)` at 30-odd points of use, so nothing on disk said what the surface
*was* and a run could observe two different values of the same variable.

The names and the defaults are the ones the point-of-use reads had. `kind` is how the
loader keeps that exact behaviour:

* `"str"`      -- a string whose default is a concrete string; an unset variable gets
                  the default, a set-but-empty variable stays `""` (this preserves
                  `os.environ.get("HG_X", "d")`).
* `"opt"`      -- an optional string; unset **or empty** is `None` (this preserves
                  `os.environ.get("HG_X") or None`, which is how the platform spells
                  "not configured").
* `"opt_raw"`  -- an optional string that keeps `""` distinct from unset (one call site
                  passes the value straight to `Path(...)` and the two differ).
* `"int"`      -- `int(raw)`, with the default when unset (this preserves
                  `int(os.environ.get("HG_X", "0"))`, including its `ValueError` on a
                  non-numeric value).
* `"int_opt"`  -- `int(raw)` but an empty string falls back to the default (this
                  preserves `int(os.environ.get("HG_X", "0") or "0")` and
                  `int(os.environ.get("HG_X") or 900)`).
* `"float"`    -- `float(raw)`, with the default when unset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _var(env: str, default: Any, doc: str, *, kind: str = "str",
         secret: bool = False) -> Any:
    """One declared configuration field.

    Returns a `dataclasses.field` whose metadata is the declaration: the variable name,
    the default, one line of prose, the parse kind, and whether the value is a secret.
    Keeping the variable name *in* the field is what lets `dump()` and `describe()` be
    derived from the dataclasses instead of restating them in a second table that drifts.
    """
    return field(default=default,
                 metadata={"env": env, "doc": doc, "kind": kind, "secret": secret})


@dataclass(frozen=True)
class AgentConfig:
    """The model that solves the tasks.

    Read by `driver.py` (at the door), `harnessgrad/identity.py` (what a curve point
    says ran) and `eval/run/runner.py` (whether a containerised harness needs the internet).
    One agent, one area: a score without this model is comparable to nothing.
    """

    backend: str = _var(
        "HG_AGENT_BACKEND", "mock",
        "How the agent is reached: `mock` (no network, no cost) or `openai` "
        "(any OpenAI-compatible endpoint).")
    model: str | None = _var(
        "HG_AGENT_MODEL", None,
        "The model that answers the tasks; recorded as every curve point's "
        "`identity.agent_model`.", kind="opt")
    base_url: str = _var(
        "HG_AGENT_BASE_URL", "",
        "Base URL of the endpoint serving the agent's model (unused for `mock`).")
    api_key: str | None = _var(
        "HG_AGENT_API_KEY", None,
        "Credential for the agent endpoint; recorded only as present or absent.",
        kind="opt", secret=True)


@dataclass(frozen=True)
class MethodConfig:
    """The improver-facing model and its budget.

    The *method's* model is deliberately separate from the agent's (`.env.example`):
    the agent's score is the measurement, the method's quality shapes only what gets
    proposed. Read by `methods/editor.py`, `harnessgrad/channel.py`,
    `harnessgrad/methods.py` and `tools/improver.py`.
    """

    model: str | None = _var(
        "HG_METHOD_MODEL", None,
        "The model the method thinks with; never the agent's, and never injected into "
        "the harness.", kind="opt")
    base_url: str | None = _var(
        "HG_METHOD_BASE_URL", None,
        "Base URL of the endpoint the method's client calls.", kind="opt")
    api_key: str | None = _var(
        "HG_METHOD_API_KEY", None,
        "Credential for the method's endpoint; recorded only as present or absent.",
        kind="opt", secret=True)
    timeout_s: int = _var(
        "HG_METHOD_TIMEOUT_S", 180,
        "Seconds before one method model call is abandoned.", kind="int")
    parse_retries: int = _var(
        "HG_METHOD_PARSE_RETRIES", 2,
        "Attempts to get a parseable reply from the method's model.", kind="int")
    history_rounds: int = _var(
        "HG_METHOD_HISTORY_ROUNDS", 0,
        "How many past rounds the channel keeps for the method; 0 means every round.",
        kind="int_opt")
    dump: str | None = _var(
        "HG_METHOD_DUMP", None,
        "Directory the method dumps its raw model exchanges into, when set.",
        kind="opt")
    source_limit: int = _var(
        "HG_METHOD_SOURCE_LIMIT", 200000,
        "Bytes of harness source the method may read in one call.", kind="int")
    source_total: int = _var(
        "HG_METHOD_SOURCE_TOTAL", 60000,
        "Total bytes of harness source staged into the method's channel.", kind="int")
    skill_limit: int = _var(
        "HG_METHOD_SKILL_LIMIT", 24000,
        "Bytes of skill text staged into the method's channel.", kind="int")


@dataclass(frozen=True)
class ImproverConfig:
    """Which tool composes the edits, and how it is driven.

    A method is a decision rule *and* an improver. `--improver` reaches this area
    through the environment (`driver.py` publishes the flag, the method's subprocess
    reads it), which is exactly the layer that produced the `HG_METHOD_BASE_URL`
    override defect: one layer set it and another read it.
    """

    selection: str | None = _var(
        "HG_IMPROVER", None,
        "Name or path of the improver a method should drive, as its subprocess sees it.",
        kind="opt")
    name: str | None = _var(
        "HG_IMPROVER_NAME", None,
        "The improver `--improver` selected, published for the method's subprocess.",
        kind="opt")
    model: str | None = _var(
        "HG_IMPROVER_MODEL", None,
        "Overrides the improver's own model; recorded beside the improver's identity.",
        kind="opt")
    effort: str | None = _var(
        "HG_IMPROVER_EFFORT", None,
        "Codex-family reasoning effort: low | medium | high | xhigh.", kind="opt")
    args: str = _var(
        "HG_IMPROVER_ARGS", "",
        "Extra argv appended to a CLI improver's command, parsed as a shell line.")
    skill: str | None = _var(
        "HG_IMPROVER_SKILL", None,
        "Path to the skill text the improver is given, overriding the default.",
        kind="opt")
    timeout_s: int = _var(
        "HG_IMPROVER_TIMEOUT_S", 1800,
        "Seconds before one CLI improver session is killed.", kind="int")
    skip_tool_gate: str | None = _var(
        "HG_IMPROVER_SKIP_TOOL_GATE", None,
        "`1` skips the door check that a CLI improver's endpoint emits structured "
        "tool_calls.", kind="opt")
    codex_home_source: str | None = _var(
        "HG_CODEX_HOME_SOURCE", None,
        "Codex home whose credentials are borrowed for a CLI improver (default "
        "`~/.codex`).", kind="opt_raw")
    api_key: str | None = _var(
        "DEEPSEEK_API_KEY", None,
        "The default improver registry entry's credential, named by that registry "
        "entry's `api_key_env`; recorded only as present or absent.",
        kind="opt", secret=True)


@dataclass(frozen=True)
class EnvironmentConfig:
    """The environment's own model -- the third budget (INTERFACE.md 2.5.7).

    Only needed by a dataset whose service declares `needs_model` (typically a
    simulated user). Handed to service containers only; the harness never sees it.
    """

    model: str | None = _var(
        "HG_ENV_MODEL", None,
        "The model a service thinks with; required when a service declares "
        "`needs_model`.", kind="opt")
    base_url: str | None = _var(
        "HG_ENV_BASE_URL", None,
        "Base URL of the endpoint that serves the environment's model.", kind="opt")
    api_key: str | None = _var(
        "HG_ENV_API_KEY", None,
        "Credential for the environment's model; recorded only as present or absent.",
        kind="opt", secret=True)
    seed: str | None = _var(
        "HG_ENV_SEED", None,
        "Seed handed to services that declare `needs_seed`; a per-task seed is derived "
        "from it.", kind="opt")


@dataclass(frozen=True)
class SandboxConfig:
    """The mount-namespace boundary the harness and the method run inside."""

    bwrap: str | None = _var(
        "HG_BWRAP", None,
        "Path to the bubblewrap binary, so the installer and the platform check the "
        "same one.", kind="opt")


@dataclass(frozen=True)
class EgressConfig:
    """The proxy a task's *check* may use to reach the internet (never the harness)."""

    proxy: str | None = _var(
        "HG_EGRESS_PROXY", None,
        "Proxy URL handed to a task's check, so a check that bootstraps a test runner "
        "can reach it.", kind="opt")
    bind: str = _var(
        "HG_EGRESS_BIND", "172.17.0.1",
        "Interface `tools/egress_forwarder.py` publishes the proxy on.")
    port: int = _var(
        "HG_EGRESS_PORT", 17890,
        "Port `tools/egress_forwarder.py` publishes the proxy on.", kind="int")
    upstream: str = _var(
        "HG_EGRESS_UPSTREAM", "127.0.0.1:17891",
        "The host proxy `tools/egress_forwarder.py` forwards to.")


@dataclass(frozen=True)
class RuntimeConfig:
    """Overlay, registry and build settings -- where a harness's dependencies come from."""

    overlay_root: str | None = _var(
        "HG_OVERLAY_ROOT", None,
        "Where dependency overlays are built and read; default is under "
        "`XDG_CACHE_HOME`.", kind="opt")
    overlay_builder_image: str | None = _var(
        "HG_OVERLAY_BUILDER_IMAGE", None,
        "Image `tools/configure_harness.py` builds an overlay inside.", kind="opt")
    registry_mirrors: str | None = _var(
        "HG_REGISTRY_MIRRORS", None,
        "Comma-separated package-registry mirrors used when building overlays.",
        kind="opt")


@dataclass(frozen=True)
class StorageConfig:
    """Where a run's own records and a method's runs are found."""

    run_root: str | None = _var(
        "HG_RUN_ROOT", None,
        "Where a reader-adapter finds a method's own runs.", kind="opt")
    record_dir: str = _var(
        "HG_RECORD_DIR", "/recordings/orders",
        "Where the demo service fixture writes its recordings.")
    pull_timeout_s: int = _var(
        "HG_PULL_TIMEOUT_S", 900,
        "Seconds before a dataset image pull is abandoned.", kind="int_opt")


@dataclass(frozen=True)
class RunConfig:
    """The run's own parameters -- from argv, not the environment.

    This is the second half of "what was in force": the environment says which models
    and budgets, and the command line says which harness, dataset, side and sampling
    policy. It is recorded in the same `config` block so a reader can answer the whole
    question from one place. `RunConfig` is not built by `load()` because argv is not an
    environment variable; `run_config(args, run_id)` builds it explicitly.
    """

    run_id: str
    harness: str
    dataset: str
    side: str
    sampling: str
    rounds: int
    trials: int
    jobs: int
    sandbox: bool
    mode: str
    seed: str | None = None
    runs_root: str = "runs"
    work_root: str = "../harnessgrad_work"
    method_entrypoint: str | None = None
    # Optional trusted dispatcher binding.  These are argv-derived facts and
    # therefore belong beside the run identity, not in the environment snapshot.
    budget_profile: str | None = None
    budget_ledger: str | None = None
    budget_arm: str = "agent"
    budget_profile_hash: str | None = None


@dataclass(frozen=True)
class JudgeConfig:
    """An independently configured verifier model; never forwarded to the actor."""
    model: str | None = _var("HG_JUDGE_MODEL", None, "Default verifier judge model; dataset overrides take precedence.", kind="opt")
    base_url: str | None = _var("HG_JUDGE_BASE_URL", None, "Verifier judge endpoint.", kind="opt")
    api_key: str | None = _var("HG_JUDGE_API_KEY", None, "Verifier-only credential.", kind="opt", secret=True)
    memoryagentbench_model: str | None = _var(
        "HG_MAB_JUDGE_MODEL", None, "MemoryAgentBench judge override; otherwise global model, then source official default.", kind="opt")
    strongreject_model: str | None = _var(
        "HG_STRONGREJECT_JUDGE_MODEL", None, "StrongREJECT judge override; otherwise global model, then official default.", kind="opt")
    tau3_model: str | None = _var(
        "HG_TAU3_JUDGE_MODEL", None, "Tau3 judge override; otherwise global model; no implicit default.", kind="opt")
    memoryagentbench_max_output_tokens: int | None = _var(
        "HG_MAB_JUDGE_MAX_OUTPUT_TOKENS", None, "MemoryAgentBench judge output limit override (1–16384); otherwise source official default.", kind="int_opt")

    def resolve_model(self, dataset: str, *, default: str | None = None) -> str:
        """Select once on the host, then freeze the result in VERIFY.judge."""
        overrides = {"memoryagentbench": self.memoryagentbench_model,
                     "strongreject": self.strongreject_model, "tau3_bench": self.tau3_model}
        if dataset not in overrides:
            raise ValueError(f"unknown judge configuration dataset: {dataset}")
        model = overrides[dataset] or self.model or default
        if model is None:
            raise ValueError(f"configure a judge model for {dataset} or HG_JUDGE_MODEL")
        if (not isinstance(model, str) or not model.strip() or model != model.strip()
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in model)):
            raise ValueError("judge model must be a model ID without control characters or surrounding whitespace")
        return model


@dataclass(frozen=True)
class Config:
    """Every area of configuration, as one value.

    `current()` returns one of these; `dump()` turns it into the plain dict that goes
    into `run_meta.json`.
    """

    agent: AgentConfig
    method: MethodConfig
    improver: ImproverConfig
    environment: EnvironmentConfig
    sandbox: SandboxConfig
    egress: EgressConfig
    runtime: RuntimeConfig
    storage: StorageConfig
    judge: JudgeConfig = field(default_factory=JudgeConfig)


__all__ = ["AgentConfig", "MethodConfig", "ImproverConfig", "EnvironmentConfig",
           "SandboxConfig", "EgressConfig", "RuntimeConfig", "StorageConfig",
           "RunConfig", "Config"]
