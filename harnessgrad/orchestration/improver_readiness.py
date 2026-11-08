"""Improver declaration and tool support policy, independent of run resources.

The caller installs the requested environment outputs before resolving a declaration.
It owns process state, gate timing and refusal exit codes.
"""
from __future__ import annotations
from dataclasses import dataclass
import os
import sys
from pathlib import Path
from harnessgrad import config as platform_config
from eval.run.integrity import PLATFORM_ROOT

def cli_provider(improver: dict) -> dict:
    """What provider a CLI improver's *own* config names: `base_url` and `env_key`.

    Derived rather than asked for, because the CLI reads its model and its endpoint from
    its own config (`tools/improver.py:declared_model` already reads the model that way).
    A `model_provider` whose definition has a `base_url` is a custom/local endpoint and can
    be probed; a name with no definition (e.g. `OPENAI`) is the CLI's built-in and cannot.

    **`env_key` comes back too, because probing with the wrong credential is how a valid
    run gets refused at the door.** The probe below used the *agent's* key for the
    *improver's* endpoint. Those are two budgets by design (`.env.example`), and when they
    name different providers the probe authenticates with a credential the endpoint does
    not accept, gets 401, and reports "this endpoint cannot emit tool calls" -- a refusal
    about the wrong fact. Measured 2026-10-06: it passed only because this deployment
    happens to set `HG_AGENT_API_KEY` and `DEEPSEEK_API_KEY` to the same value.

    **`~/.codex/config.toml` is codex's file, so only a codex improver may be read for it.**
    Any `kind == "cli"` improver used to come through here, and with the `dsh` entry resolved
    this returned the custom provider out of the operator's codex config
    (`https://naiccc.com/v1`) -- so the gate below would have probed *that* endpoint, one
    belonging to someone else, and written its answer onto the curve as a fact about
    `deepseek-flash`. An improver that declares its own `provider` (dsh declares
    `deepseek-official`) uses that CLI's built-in endpoint, which is exactly the
    "cannot be probed from here" case the caller already records as *unprobed*.
    """
    if improver.get("provider") or improver.get("argv_style", "codex") != "codex":
        return {}
    home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    cfg = Path(home) / "config.toml"
    if not cfg.is_file():
        return {}
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    provider = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("model_provider") and "=" in stripped:
            provider = stripped.partition("=")[2].strip().strip('"').strip("'")
    if not provider:
        return {}
    section = f"[model_providers.{provider}]"
    inside = False
    found: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped == section
            continue
        if not inside or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        if name in ("base_url", "env_key"):
            found[name] = value.strip().strip('"').strip("'")
    return found


def cli_provider_base_url(improver: dict) -> str:
    """The endpoint a CLI improver will call, or `""` when it uses a built-in provider."""
    return cli_provider(improver).get("base_url", "")


def probe_tool_calls(base_url: str, model: str, *, provider=cli_provider) -> tuple[bool | None, str]:
    """Ask the endpoint whether it returns structured tool_calls. Never raises.

    Reuses `tools/probe_tool_calls.py`'s request shapes so the door and the CLI agree on
    what "supports tool calls" means. `responses` decides, because that is the wire a codex
    improver uses; `chat` is reported in the detail for a reader who needs to know which
    path failed. A declared but unset CLI credential returns None (unprobed),
    never a capability verdict or permission to borrow the task-agent's key.
    """
    try:
        sys.path.insert(0, str(PLATFORM_ROOT))
        import tools.probe_tool_calls as probe
    except Exception as exc:                                             # noqa: BLE001
        return False, f"the probe itself could not be imported: {exc}"
    # Only the selected CLI credential may authorize this request. A tokenless
    # custom endpoint gets a nonsecret placeholder; no other role is a fallback.
    key_env = provider({}).get("env_key", "")
    if key_env:
        key = os.environ.get(key_env, "")
        if not key:
            return None, (f"credential-unset: CLI provider env_key {key_env!r} is unset; "
                          "tool-call capability is unprobed")
    else:
        key = "local"
    try:
        resp_ok, resp_why = probe._calls_responses(base_url, model, key, 120.0)
        chat_ok, chat_why = probe._calls_chat(base_url, model, key, 120.0)
    except Exception as exc:                                             # noqa: BLE001
        return False, f"the probe raised: {type(exc).__name__}: {exc}"
    detail = f"responses: {resp_why} | chat: {chat_why}"
    return resp_ok, detail


@dataclass(frozen=True)
class ImproverRequest:
    name: str | None
    model: str | None
    effort: str | None

    @property
    def carries_its_own(self) -> bool:
        return (self.name or "").strip().lower() in ("none", "noop", "null")

    def environment_patch(self) -> dict[str, str | None]:
        patch = {}
        if self.carries_its_own:
            patch["HG_IMPROVER_NAME"] = None
        elif self.name:
            patch["HG_IMPROVER_NAME"] = self.name
        if self.model:
            patch["HG_IMPROVER_MODEL"] = self.model
        if self.effort:
            patch["HG_IMPROVER_EFFORT"] = self.effort
        return patch


@dataclass(frozen=True)
class ImproverReadiness:
    declaration: dict
    tool_calls: bool | None
    detail: str
    refused: bool = False


def resolve_readiness(request: ImproverRequest, *, reporter, load_config,
                      resolve_config, provider_url=cli_provider_base_url,
                      probe=probe_tool_calls) -> ImproverReadiness:
    """Resolve identity and apply the CLI tool gate with its original soft errors.

    Resolution exceptions become an unresolved declaration; provider and reporting
    errors propagate. Probe transport errors are handled by probe_tool_calls alone.
    """
    improver: dict = {}
    carries_its_own = request.carries_its_own
    if carries_its_own:
        reporter.note("improver none  ← the method carries its own; nothing recorded")
    else:
        try:
            improver = resolve_config(load_config(), version="",
                                        requested=request.name,
                                        model_override=request.model)
        except Exception as exc:                                # noqa: BLE001
            improver = {"ok": False,
                        "reason": f"could not read the improver config: {exc}"}
    if improver and improver.get("ok"):
        reporter.note(
            f"improver {improver['name']} {improver['version']}"
            + (f" · model {improver['model']}" if improver.get("model") else "")
            + f" · sha {improver['sha256'][:12]}"
            + ("  ← declared MODIFIED" if improver.get("modified") else ""))
    elif improver:
        reporter.note(
            f"no improver resolved ({improver.get('reason')}); a method that carries its "
            f"own improver still runs, but this run carries no improver identity",
            level="warn")
        improver = {}

    # **A CLI improver needs a model that emits standard tool_calls, and that is a gate --
    # not a convention.** Measured (2026-10-04): codex pointed at the local 9B connected,
    # spent 8,699 tokens and did nothing, because the endpoint returned the model's call as
    # `<tool_call><function=...>` **text** and codex needs structured calls on
    # `wire_api = "responses"`. The requirement had been raised before and was written down
    # nowhere (`grep -rn tool_call docs/ INTERFACE.md` was empty), so it kept being
    # rediscovered instead of enforced. `tools/probe_tool_calls.py` is the check.
    #
    # What it can and cannot decide: a **custom** provider in the CLI's own config names a
    # `base_url`, so that endpoint can be probed and refused. A hosted provider's endpoint
    # is the CLI's built-in and cannot be reached from here -- then the result is recorded
    # as *unprobed*, which is a fact rather than a silent pass.
    # `HG_IMPROVER_SKIP_TOOL_GATE=1` overrides, for an operator who knows an endpoint's
    # tool support better than a one-shot probe does.
    improver_tool_calls: bool | None = None
    improver_tool_calls_detail = ""
    if improver.get("ok") and improver.get("kind") == "cli":
        base_url = provider_url(improver)
        if base_url:
            if platform_config.current().improver.skip_tool_gate == "1":
                improver_tool_calls_detail = "gate skipped by HG_IMPROVER_SKIP_TOOL_GATE"
                reporter.note("improver tool-call gate skipped by request", level="warn")
            else:
                improver_tool_calls, improver_tool_calls_detail = probe(
                    base_url, improver.get("model") or "")
                if improver_tool_calls is None:
                    reporter.note(
                        f"refusing to run: improver {improver.get('name')!r} tool-call "
                        f"capability is unprobed ({improver_tool_calls_detail}). "
                        "Configure the declared CLI credential or explicitly set "
                        "HG_IMPROVER_SKIP_TOOL_GATE=1 to run without this probe.",
                        level="error")
                    return ImproverReadiness(improver, None, improver_tool_calls_detail, refused=True)
                if not improver_tool_calls:
                    reporter.note(
                        "refusing to run: the improver "
                        f"{improver.get('name')!r} would use model "
                        f"{improver.get('model')!r} at {base_url}, and that endpoint does "
                        f"not return structured tool_calls "
                        f"({improver_tool_calls_detail}).\n"
                        "  A CLI improver edits files by calling tools; a model whose calls "
                        "come back as text cannot drive it, and the run would spend its "
                        "budget producing nothing.\n"
                        "  Fix the endpoint (server-side parsing: `--enable-auto-tool-choice "
                        "--tool-call-parser <name>`), or set HG_IMPROVER_SKIP_TOOL_GATE=1 "
                        "to run anyway.",
                        level="error")
                    return ImproverReadiness(improver, improver_tool_calls, improver_tool_calls_detail, refused=True)
        else:
            improver_tool_calls_detail = ("the CLI's provider is built in, so its endpoint "
                                          "cannot be probed from here")
        reporter.note(
            "improver tool calls: "
            + ("yes" if improver_tool_calls else
               ("no" if improver_tool_calls is False else "unprobed"))
            + f" ({improver_tool_calls_detail[:90]})")

    return ImproverReadiness(improver, improver_tool_calls, improver_tool_calls_detail)
