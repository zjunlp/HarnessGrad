"""Boundary rules for the platform. INTERFACE.md §6.

The rules changed when methods moved out of the platform and into the harness
repositories. They are now sharper, and they are the load-bearing claim of the
whole design:

    The platform never contains a method, never imports one, and never writes
    to one. It reaches a method only through the entrypoints declared in the
    harness's own manifest.

Rules kept as prose get broken the first time someone is in a hurry. These run
in milliseconds and they fail loudly.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Everything that is the platform. `base_harness/` is NOT here: those are the
# artifacts under measurement, and they are allowed to contain anything.
#
# `harnessgrad/` is the implementation and `driver.py` is the entrypoint that wires it
# up. The rules below are about what the *platform* does, so they read all of it: when the
# implementation moved out of `driver.py`, a check pinned to that one path would have
# failed a refactor that changed no behaviour -- which is R6's own argument, written down
# before this move happened.
PLATFORM_PACKAGES = ["eval", "ckpt", "data", "harnessgrad"]
PLATFORM_FILES = ["driver.py"]


def _platform_sources() -> list[Path]:
    out: list[Path] = []
    for pkg in PLATFORM_PACKAGES:
        d = ROOT / pkg
        if d.is_dir():
            out.extend(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)
    out.extend(ROOT / f for f in PLATFORM_FILES if (ROOT / f).exists())
    return out


def _platform_text() -> str:
    """The platform's own sources as one string, for rules stated as properties."""
    return "\n".join(p.read_text() for p in _platform_sources())


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_r1_platform_does_not_import_a_method():
    """A method that lives in the platform is frozen at import time, and a
    method frozen at import time cannot be measured while it rewrites itself."""
    bad: list[str] = []
    for path in _platform_sources():
        src = path.read_text()
        for mod in _imports(path):
            if mod in ("base_harness", "methods"):
                bad.append(f"{path.relative_to(ROOT)} imports {mod}")
        # What must not happen is a *dynamic import of a method*, so the check reads
        # the argument of each `importlib.import_module(...)` rather than looking for
        # the word "harness" anywhere in the file. The loose version flagged
        # `data/scaffolds/registry.py` the moment a comment there used the word -- the file
        # imports `data.datasets.<name>`, which is a dataset, and always has. A check whose
        # false positives are fixed by rewording comments is a check that gets deleted
        # rather than trusted.
        #
        # The registry used to spell that import as an f-string, so it carried a quote and
        # slipped past the "opaque argument" rule. Rewriting it to `module_name(name)` --
        # the same dataset, now through a named resolver -- made the rule fire. The
        # exemption below is therefore earned rather than granted: the argument is allowed
        # only when it is `module_name(...)`, and only after `test_registry_resolver_...`
        # below has shown that resolver returns a `data.datasets.*` name. Renaming it to
        # reach a method fails both halves.
        for call in re.finditer(r"importlib\.import_module\(([^)]*)\)", src):
            arg = call.group(1).strip()
            # The capture stops at the first `)`, so a nested call arrives truncated
            # (`module_name(name`); match the resolver by prefix rather than equality.
            resolved_dataset = arg.startswith("module_name(")
            if "method" in arg or "base_harness" in arg or (
                    not resolved_dataset and '"' not in arg and "'" not in arg):
                bad.append(f"{path.relative_to(ROOT)} dynamically imports "
                           f"{'a method' if arg else 'an opaque module'}: "
                           f"import_module({arg})")
    assert not bad, (
        "the platform must reach a method only through the manifest entrypoints "
        "(INTERFACE.md R1): " + "; ".join(bad)
    )


def test_the_registry_resolver_only_ever_names_a_dataset():
    """The exemption in R1 above is earned here, not asserted there.

    `test_r1_platform_does_not_import_a_method` lets `import_module(module_name(name))`
    through because that resolver answers the dataset question. This pins the property
    that makes it safe: whatever it returns, it is under `data.` and never names the
    method or harness trees. Rename or repoint the resolver at `methods` and R1 stops
    being exempt for it, because this test fails first.
    """
    from data.scaffolds import registry

    assert registry.module_name("fever") == "data.datasets.fever", \
        "an adapter must resolve under data.datasets"
    assert registry.module_name("demo") == "data.demo", \
        "a retained top-level fixture must keep resolving at the top level"
    for name in list(registry.published()) + ["demo"]:
        resolved = registry.module_name(name)
        assert resolved.startswith("data."), (name, resolved)
        assert "methods" not in resolved and "base_harness" not in resolved, (name, resolved)


def test_r2_platform_never_writes_into_a_method_directory():
    """The platform must not edit the improvement mechanism or the acceptance
    rule. If it did, the platform would be a participant in the comparison
    rather than the referee -- and 'did the method rewrite its own search rule?'
    would stop being an observation about the method."""
    offenders: list[str] = []
    for path in _platform_sources():
        src = path.read_text()
        for needle in ('"method/', "'method/", "/method/improve", "/method/accept"):
            if needle in src:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert not offenders, (
        "the platform must not reference or write method internals "
        "(INTERFACE.md R2): " + "; ".join(offenders)
    )


def test_r3_platform_reaches_methods_only_through_the_manifest():
    """The entrypoint is re-read every round, so a method that replaces its own
    entrypoint is still reachable next round."""
    platform = _platform_text()
    assert 'man.get("improve_entrypoint")' in platform, (
        "the platform must take the improve entrypoint from harness.json"
    )
    assert "subprocess.run" in platform, (
        "the entrypoint must be invoked as a process, never imported"
    )
    assert "_call_entrypoint" in platform


def test_r4_harness_state_never_contains_platform_channel_or_pycache():
    """Committing the framework-method channel or bytecode makes a no-op round
    mint a new sha, so the control curve appears to move. This bug existed once;
    the rule exists so it cannot come back silently.

    Asserted against `IGNORED_HARNESS_STATE` rather than a hand-copied pair: the principle
    is "the instrument's own state is not harness state", and a second copy of two of its
    rules is the copy that goes stale when the class grows -- `*.bak` joined the constant
    for exactly that reason, and
    `test_states.py::test_the_instruments_own_state_never_moves_the_harness_identity` is
    the behavioural half of this rule.
    """
    from ckpt.git_state import IGNORED_HARNESS_STATE  # noqa: PLC0415

    src = (ROOT / "ckpt" / "git_state.py").read_text()
    assert "_harnessgrad/" in IGNORED_HARNESS_STATE
    assert "__pycache__/" in IGNORED_HARNESS_STATE
    for rule in IGNORED_HARNESS_STATE:
        assert f'"{rule}"' in src, f"harness .gitignore must exclude {rule}"


def test_r5_a_method_hands_over_a_harness_directory_not_a_claim():
    """The bug this prevents, and why the contract is shaped this way.

    The first version had a step name a commit id. An external method's states
    live in *its own* git, so the platform could not resolve them; it measured
    whatever the working tree held, and two distinct harnesses scored
    identically while the curve looked plausible.

    Requiring a directory instead of a name removes the failure by construction:
    a method that hands over a harness cannot have that harness be something
    else by the time it is measured."""
    platform = _platform_text()
    assert 'step.get("harness_dir")' in platform, (
        "a step must name a directory holding a complete harness"
    )
    assert "if not src or not Path(src).is_dir()" in platform, (
        "a step naming no readable harness must be skipped with a reason, "
        "never measured anyway"
    )
    assert "reset_hard(work, h0_sha)" in platform, (
        "each candidate is swapped into a pristine base, so candidates cannot "
        "leak into one another"
    )


def test_r6_an_unmeasurable_candidate_is_never_given_a_platform_score():
    """RRSI's harness is a module tree with no executable entry point; another
    method may hand over something that does not satisfy the contributor spec.
    The platform reports the state and the method's number as the method's, and
    never presents a number it did not produce.

    The alternative failure is worse than it looks: writing such a harness over
    the platform's own entrypoint made the platform run the *method's* harness
    against the *platform's* task set, and both scored zero for a reason that had
    nothing to do with either.

    The assertions span the whole rendering surface, not one file. When the table
    moved into `eval/observe/console.py` the first two stayed in the driver and the third
    moved, and a check pinned to a single path would have called that a
    regression -- or, worse, passed while the rendering it guards was deleted.
    """
    platform = _platform_text()
    rendering = platform + (ROOT / "eval" / "observe" / "console.py").read_text()
    assert "measured_by_platform" in platform, (
        "every curve point must say whether the platform produced its score"
    )
    # 断言的是「记下了**具名的问题**」而不是某一句措辞。以前只有一句
    # "does not satisfy docs/writing_a_harness.md (no harness.json)" —— 它只覆盖了四种
    # 拒绝里的一种(manifest 不在),另外三种(manifest 解析不了、entrypoint 没了、
    # Python 编译不过、env_kinds 被放宽)到了读者那里都是一个 0.000。
    assert "candidate.validate" in platform and "candidate_problems" in platform, (
        "候选必须在**平台自己这一侧**被复检,并且把问题记进点里"
    )
    assert "not measured" in rendering, (
        "an unmeasured point must not be printed as if it were a score"
    )
    assert "method_reported" in rendering, (
        "an unmeasured point must still show the number the method claimed, "
        "labelled as the method's"
    )


def test_r7_harness_location_is_declared_not_assumed():
    """A harness is a directory, not necessarily the workspace root. Without a
    declared path the platform silently overwrote its own entrypoint with the
    method's harness files, which have no manifest of their own."""
    import json
    for name in ("loop",):
        man = json.loads((ROOT / "base_harness" / name / "harness.json").read_text())
        assert "path" in man, f"{name}/harness.json must declare where the harness is"


def test_r8_edit_kind_is_recorded_but_never_interpreted():
    """Seven published methods were checked and none makes the *kind* of an edit
    machine-readable: AHE declares `constraint_level: middleware|tool_impl|
    tool_desc|skill|prompt` in its prompt and no code reads it. A vocabulary that
    exists only in prose cannot be analysed.

    The platform records what the method calls the change and does nothing else
    with it. Interpreting it would mean the platform had an opinion about which
    kinds of change count, which is the referee becoming a participant."""
    platform = _platform_text()
    assert '"edit_kind"' in platform, "a curve point must carry the declared kind"
    assert 'point["edit_kind"] = step.get("edit_kind")' in platform, (
        "the value is copied verbatim from the method, not derived"
    )
    # No vocabulary may be baked into the platform: not a whitelist, not a
    # mapping, not a default. Recording is the whole job.
    for forbidden in ("VALID_EDIT_KINDS", "EDIT_KIND_MAP", "edit_kind in ("):
        assert forbidden not in platform, (
            f"{forbidden!r} means the platform is interpreting edit kinds; "
            "it must only record them"
        )


def test_r9_the_reference_harness_satisfies_the_contributor_contract():
    """The spec in docs/writing_a_harness.md is only real if the validator
    enforces it and the reference harness passes. A spec with no case that
    satisfies it is a wish."""
    import json
    import subprocess
    import sys

    for name in ("loop",):
        root = ROOT / "base_harness" / name
        man = json.loads((root / "harness.json").read_text())
        for field in ("name", "version", "entrypoint"):
            assert man.get(field), f"{name}: missing {field}"
        entry = root / man.get("path", ".") / man["entrypoint"]
        assert entry.is_file(), f"{name}: entrypoint {entry} does not exist"

    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_harness.py"),
         str(ROOT / "base_harness" / "loop"), "--smoke"],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        "the reference harness must pass its own validator:\n" + proc.stdout
    )


def test_r10_the_record_names_the_model_that_actually_ran():
    """`identity.agent_model` is the field that makes a score comparable to
    anything, and the platform got it wrong in the most confusing way available:
    it recorded `mock` while a live model answered every task, because the harness
    had been renamed and this line had not.

    Checked by reading both sides rather than by trusting either: the variable the
    harness reads must be the variable the platform records.

    It used to check a literal line. That was the wrong shape once the *harness*
    became allowed to declare what ran it -- an agent CLI never reads `HG_AGENT_MODEL`
    at all, so the platform's environment is only the fallback. The property is
    unchanged and now stated directly: the platform falls back to the variable the
    harness reads, and prefers the harness's own declaration when there is one.
    """
    harness_src = (ROOT / "base_harness" / "loop" / "agent.py").read_text()
    platform = _platform_text()

    assert "HG_AGENT_MODEL" in harness_src, "the harness reads HG_AGENT_MODEL"
    # **The declaration, not the source text.** This grepped the platform's code for the
    # literal `os.environ.get("HG_AGENT_MODEL")` -- right while that read was written out
    # at its point of use, wrong the moment the configuration became a declaration
    # (stage 2): the variable is a *field* now, and a test that greps for the old spelling
    # fails on a refactor that preserved the behaviour exactly. What the property needs is
    # that the platform still reads that name, and the declaration is where it says so.
    from harnessgrad import config as platform_config
    assert "HG_AGENT_MODEL" in platform_config.ENV_NAMES, (
        "the platform must still read the variable the harness reads, or a harness that "
        "declares nothing gets no model on its record"
    )
    # And the declaration must still carry the fallback, not only the name: a field whose
    # default is a constant would ignore the environment entirely.
    assert any(f["variable"] == "HG_AGENT_MODEL"
               for f in platform_config.describe()), platform_config.ENV_NAMES[:5]
    assert "identity_declared" in platform, (
        "the platform must prefer what the harness says actually ran: an agent CLI "
        "reads its model from its own config, so the environment can be ignored "
        "entirely and the record would describe a run that did not happen"
    )
    assert "agent_model_conflict" in platform, (
        "when the harness and the platform disagree about the model, both values "
        "must be recorded rather than one silently winning"
    )
    for stale in ("HARNESSGRAD_AGENT_MODEL", "HARNESSGRAD_BASE_URL",
                  "HARNESSGRAD_API_KEY", "HARNESSGRAD_LOOP_BACKEND"):
        assert stale not in platform, (
            f"stale variable name {stale!r} survives in the platform; the harness "
            "renamed it and the two sides have drifted"
        )


def test_r11_the_two_models_are_separately_configured_and_separated():
    """A harness has a model and a method may have its own; they are different
    models doing different jobs and must not collapse into one.

    Two properties, and the second is the one that needs enforcing: they are
    configured under different names, and the harness cannot reach the method's
    credentials. Separate configuration without enforcement is not separation --
    the harness inherits the whole environment by default, so it could spend the
    method's quota or be pointed at the method's endpoint, and a resulting failure
    would look like a property of the harness.
    """
    env_example = (ROOT / ".env.example").read_text()
    for name in ("HG_AGENT_MODEL", "HG_AGENT_BASE_URL", "HG_AGENT_API_KEY",
                 "HG_METHOD_MODEL", "HG_METHOD_BASE_URL", "HG_METHOD_API_KEY"):
        assert name in env_example, (
            f"{name} is not documented in .env.example; a variable nobody knows "
            "about is a variable nobody sets"
        )

    runner = (ROOT / "eval" / "run" / "runner.py").read_text()
    assert "HG_METHOD_" in runner, (
        "the harness's environment must be filtered; without it the harness "
        "inherits the method's credentials"
    )
    assert "harness_env()" in runner, "the filter must actually be applied"


def test_r12_the_two_models_are_recorded_separately():
    """The record has to keep them apart too, or the separation exists only in the
    configuration. `agent_model` is what produced the score; a method's model is
    not, and conflating them would make one method's weak improver look like a
    weak harness."""
    platform = _platform_text()
    assert '"agent_model"' in platform and '"method_model"' in platform, (
        "both models belong on the curve point, under distinct names"
    )

def test_a_method_declares_its_model_through_the_protocol():
    """`identity.method_model` is only readable if methods answer the question.

    The platform records the harness's model itself and can only be *told* the
    method's. When the reference methods forgot, every curve said `n/a` and a
    moved score could not be attributed to a better improver or a luckier harness
    -- the exact confusion the two-model split exists to prevent.
    """
    import io
    import os
    import sys

    sys.path.insert(0, str(ROOT / "methods"))
    import protocol

    def emitted(payload, model=None):
        old_out, old_model = sys.stdout, os.environ.get("HG_METHOD_MODEL")
        buf = io.StringIO()
        try:
            sys.stdout = buf
            if model is None:
                os.environ.pop("HG_METHOD_MODEL", None)
            else:
                os.environ["HG_METHOD_MODEL"] = model
            protocol.emit(dict(payload))
        finally:
            sys.stdout = old_out
            if old_model is None:
                os.environ.pop("HG_METHOD_MODEL", None)
            else:
                os.environ["HG_METHOD_MODEL"] = old_model
        return json.loads(buf.getvalue())

    import json  # noqa: E402  (kept next to its only use)

    assert emitted({}, model="deepseek-flash")["method_model"] == "deepseek-flash"
    assert emitted({}, model=None)["method_model"] is None
    # A method that names its own model wins: this is a declaration, not a fact.
    assert emitted({"method_model": "mine"}, model="env")["method_model"] == "mine"
    # Silence is read as "no change", never as "changed": a checkpoint that
    # differs by nothing is how a control curve starts to move.
    assert emitted({})["changed"] is False
