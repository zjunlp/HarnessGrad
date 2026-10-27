#!/usr/bin/env python3
"""How a method drives an external coding-agent improver.

**Why this is a module and not a method.** Until 2026-10-04 this code was
`methods/codex/run.py`, and `methods/` is supposed to hold the decision rules a paper
proposes. "Run the improver the platform resolved" is not a rule: it is the *improver layer*,
the same job `editor.py` does for a chat endpoint, and a method that is nothing but that
layer is a tool wearing a method's name -- it would also make `method = codex` a true sentence
about a run, when codex is a tool and the method is whichever rule wrapped it. So it moved
here. **No method in `methods/` calls it today**, and that is the honest state: the reference
method (`llm_improver`) delegated to it for one afternoon, and the delegation was withdrawn
because the registry's *default* improver is codex -- a CLI -- so delegating silently changed
what an ordinary run does. The trade-off is recorded as an open decision in
`INTERFACE.md` §4.49; the code is kept because it is the only implementation of the
improver layer and every fact in the list below was paid for once already.

**The split of responsibility.** The platform resolves the improver
(`tools/improver.py`), binds its runtime trees read-only into the method sandbox, and hands
the method its identity in the request (`req["improver"] = {name, path, version, model,
sha256}`). What a method must still supply is everything method-shaped: the evidence staged
where the tool can read it, the prompt, the timeout, and the report. That is this file.

**Measured facts this code carries**, each from a real failure:

* The command comes from the **request**, not from PATH. Measured: three attempts at round 1,
  three `exit 1`, and `codex method: no usable improver`, because the sandbox hides the
  platform and `tools/improver.py` is not on the method's disk. On this machine PATH also
  points at a codex whose native binary is missing, which is why the resolver exists at all.
* `CODEX_HOME` is redirected into the workspace, so a run does not append to the operator's
  own codex history.
* `responses_experimental_transport = "https"` is appended to the copied config: measured, a
  proxy that does not carry WebSockets makes codex retry, warn and time out
  (`Falling back from WebSockets to HTTPS transport`).
* Changed files are decided by **content**, not by git status. Measured: a one-line edit came
  back as `22 file(s) changed`, nineteen of them ours, and the wrong one was quoted to the
  reader.
* The transcript is written to `<workspace>/improver.log`, which the platform copies into the
  record (`harnessgrad/records.py:_keep_method_logs`): the question "why did the improver
  change nothing?" is normally asked after the run has deleted its scratch directory.
"""
from __future__ import annotations

import os
import signal
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_tree import copy_harness
from bun_workspace import stage_candidate
import editor                                              # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: Directories that are never part of the harness: the platform's channel, caches, and the
#: tool's own session state.
#: The platform's own plumbing inside a candidate tree. A change to any of these cannot
#: alter what the harness does when it is measured, so it is not a candidate edit:
#: `_NOT_HARNESS` decides what `_changed_files` reports, and `changed` decides whether the
#: round is spent measuring the tree at all.
#:
#: `.gitignore` is here because of a measured waste, not a principle: codex was handed a
#: tree whose `.gitignore` the platform had just rewritten, edited a line of it, and the
#: round measured a tree that behaves exactly like its parent.
_NOT_HARNESS = frozenset({"_harnessgrad", "__pycache__", ".git", ".codex-home", ".state",
                          "evidence", "node_modules", ".venv", ".gitignore"})

#: The rules that hold **whatever the rule's own prompt says**.
#:
#: These used to sit at the end of `PROMPT`, and `propose` chose `prompt or PROMPT` -- so a
#: rule that supplied a prompt of its own (DGM's "target this failure", HyperAgents',
#: Meta-Harness' frontier) silently replaced the whole instruction, hard rules included.
#: Measured (2026-10-05, `codex-dgm`, codex driving a local 9B): DGM handed codex a targeted
#: prompt, codex deleted 625 of the harness's 635 lines, and the replacement read
#: `sys.argv[1]` exactly where the platform passes `--task`. Every task in two rounds died
#: at startup, and the run recorded two `0.000` points for a harness that never ran once.
#:
#: The rule's prompt is *what to change*; these are *what may not break*. They are not
#: alternatives, so they are not passed as one.
HARD_RULES = """\
Hard rules (the platform rejects a candidate that breaks any of them):
  * `harness.json` must not be modified.
  * Do not add or widen `env_kinds`.
  * The entrypoint named in `harness.json` must still exist and still run. The platform
    invokes it as `<entrypoint> --task <task.json> --workdir <dir>`: it must keep both
    flags, exit 0, and write `answer.txt` and `trace.jsonl` into the workdir.
  * **A file nothing loads changes nothing.** If you add a module, the entrypoint must reach
    it: import it, or write your new logic *into* the entrypoint. The platform computes the
    entrypoint's import closure and records whether your change is inside it; a candidate
    whose every changed file is outside it cannot have changed the harness's behaviour,
    whatever score it happens to get. Measured (`v9`, 2026-10-05): one candidate added a
    635-line backup and an 88-line rewrite that nothing imported, left `agent.py` byte
    identical, and scored 0.200 against the 0.100 it started from -- a number about noise,
    not about the edit.
  * If your change is large enough that you are unsure it still starts, **run it**:
    `<entrypoint> --task <a tiny task you wrote> --workdir <a temp dir>` must exit 0 and
    write `answer.txt`. A candidate that cannot start is rejected, and the round is spent
    for nothing.
  * Do not run any task from the dataset. You may write and run your own tests of the
    harness, and you may run the harness's entrypoint on a task you construct yourself.

**Be bold. The harness is a starting point, not a baseline to protect.** It is the thing
being improved, and a round that leaves it alone is a round that was paid for and returned
nothing. If your diagnosis implicates a mechanism -- how a reply is parsed, how commands are
de-duplicated, when the run is told to finish, how much of the budget is spent before
anything is written, how much of the context one command's output may consume -- then change
*that mechanism*, in as many lines as the change honestly needs. Open the function you are
about to change and make it do what your diagnosis says it must.

**Do not answer a structural defect with a constant.** Editing one number is the weakest
response available, and so far it is the only one tried. Measured on this platform (one local
9B, ten Terminal-Bench tasks, the `loop` base harness): `FINISH_NUDGE_STEPS` 4 -> 8 scored
0.200 -> 0.000; a second nudge added inside the same window changed nothing; `OUTPUT_LIMIT`
4000 -> 20000 scored 0.100 -> 0.000, because that limit exists to stop a single command's
output from filling the model's context. Three constants, three failures. When a number looks
wrong, the mechanism around it is usually what is actually wrong -- and a mechanism is what
the platform is asking you to change.

When you are done, print one short paragraph: what you changed and why it should raise
the score. If you conclude that nothing should change, make no edits and say so.

**The submission is the file on disk, not the analysis.** A turn that ends with a diagnosis
and no edit is not a proposal: the platform measures the tree you leave behind, so a correct
diagnosis you did not apply scores exactly like no diagnosis at all. Apply your change with
your editing tool *first*, then say what you changed. Measured (`v7`, 2026-10-05): six
attempts across two rules each named a real defect in the harness -- one named the duplicate
JSON key that drops the model's second `Action`, another named the stuck-detection that only
fires on a command repeated *immediately* -- and not one of them wrote a file. The diagnoses
were right; nothing was submitted.
"""

PROMPT = """\
You are improving a **harness** -- the structured execution layer around a model that
turns a task into model calls and decides when to stop. Your job is to produce a better
harness for the tasks described in `evidence/tasks/`.

Read `evidence/SKILL.md` first: it defines the harness contract, where the evidence is,
what a valid change is, and which failure modes are known to matter on this platform.

Everything you may read is under `evidence/`:

  evidence/SKILL.md            how to improve a harness (read this first)
  evidence/round.json          what the incumbent scored, per task
  evidence/traces/*.jsonl      the incumbent's real conversations, one per task
  evidence/tasks/*.json        the instruction, the grading, and the verifier's verdict
  evidence/history/*.json      earlier rounds' summaries
  evidence/states/             complete harness trees from earlier rounds

The harness you are changing is the current directory. Edit it in place.

""" + HARD_RULES


#: What the round is asking for. **One sentence used to do this job and it was wrong twice.**
#:
#: It said "Propose **one** change to this parent, or say no_change" -- 54 characters, 0.1% of a
#: 58,160-character instruction, placed after 40% of raw traces and 50% of harness source with
#: the hard rules after that. Two problems, both measured:
#:
#: * *one change* is a size limit nothing justified. Every rule in this repository carried it
#:   (`dgm`, `hyperagents`, `llm_improver`, `ahe`, `harnessx`, `sica_ci`, `tthe`, `dream_rsi`;
#:   only `rrsi` said "edit(s)"). A diagnosis that implicates a mechanism and the two call
#:   sites that use it is not one change.
#: * *or say no_change* is a **free exit**. Taken five times in one run (`v7`), while the
#:   platform's "the submission is the file on disk" paragraph sat 3,649 characters later.
#:
#: So the ask lives here now -- one place, for every rule -- and it says how much, says the
#: edit must land, and makes `no_change` cost a named mechanism.
CHANGE_REQUEST = """\
## What to produce

Propose the changes your diagnosis requires -- as many independent edits as it takes, in as
many files as it takes -- and **apply them to the tree**. The platform measures the tree you
leave behind, so an edit that is still in your head, or in a new file that nothing imports, is
not a proposal. Prefer the mechanism your diagnosis actually implicates over a number that
makes the symptom smaller.

If the evidence genuinely shows that no change would help, say `no_change` **and name the
mechanism you checked** that already handles the failure. "Nothing looked wrong" is not a
diagnosis, and a round that returns it leaves the platform with nothing to learn from.
"""


def instruction(prompt: str | None, evidence: str = "", facts: str = "") -> str:
    """The rule's prompt, wrapped in the platform's own instruction. **The order is content.**

    A rule builds its prompt around its own hypothesis ("target this failure", "build on
    round 3"), so the rules that keep the artifact measurable cannot be left to it to
    remember. Returns `PROMPT` when the rule supplied none.

    Measured (`v4`-`v10`, 58,160 characters assembled): the ask was 0.1% of the prompt and sat
    *after* 89.7% of raw evidence, with the hard rules after that. A small model reads what it
    is told to read last. So the order is: what to produce, what may not break, where the
    evidence is, and only then the rule's own reasoning -- and any rule that appends its own
    "Propose one change" sentence instead of relying on `CHANGE_REQUEST` is adding a second,
    weaker ask in a place the model reads last.
    """
    if not prompt:
        return PROMPT
    # **The order is the point**: what happened, what to produce, what may not break, where
    # the evidence is, and only then the rule's own reasoning. The old order put 89.7% of raw
    # evidence first and the ask last, at 0.1% of the prompt, where a small model reads it
    # least -- and it took the free `no_change` exit five times in one run.
    parts = [facts, CHANGE_REQUEST, HARD_RULES, evidence, prompt.rstrip()]
    return "\n\n".join(x for x in parts if x)


def applies(req: dict) -> bool:
    """Is the improver this run resolved a **command**, rather than an endpoint?

    True when the operator named one (`HG_IMPROVER`) or the declared kind is `cli`.
    Older records without a kind use `path` and absence of `base_url`. A CLI may itself
    use a custom endpoint. The distinction matters because the two are driven
    completely differently: an endpoint is a chat call (`editor.ask`), a CLI is a
    subprocess in the candidate tree.
    """
    if os.environ.get("HG_IMPROVER"):
        return True
    improver = req.get("improver") or {}
    if improver.get("kind"):
        return improver["kind"] == "cli" and bool(improver.get("path"))
    return bool(improver.get("path")) and not improver.get("base_url")


def _improver_command(req: dict) -> list[str]:
    """Which command to run: the operator's, or the one the platform resolved."""
    explicit = os.environ.get("HG_IMPROVER")
    if explicit:
        return shlex.split(explicit)
    given = (req.get("improver") or {}).get("path")
    if given:
        if not Path(given).exists():
            raise SystemExit(
                f"the platform named an improver at {given!r}, which does not exist inside "
                f"this sandbox. That is a platform bug (the improver's runtime trees were "
                f"not bound), not a broken method.")
        return [given]
    # Ask the resolver rather than guessing at PATH: measured on the development machine,
    # PATH points at a codex whose native binary is missing and `codex --version` crashes.
    # Reachable only without a sandbox -- a sandboxed method cannot see `tools/`.
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "improver.py"), "--which"],
                          capture_output=True, text=True, timeout=300)
    resolved = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if proc.returncode != 0 or not resolved:
        raise SystemExit(
            "no usable improver. Run `python3 tools/improver.py --list` to see what is on "
            "this machine, or set HG_IMPROVER.")
    return [resolved]


def _cli_argv(req: dict, candidate: Path, task: str) -> list[str]:
    """Build the CLI's own argv: its vocabulary, then the task.

    codex is the shipped improver, so it is the default when an entry declares no style --
    but the styles are **named**, not guessed from the command's name, because the two are
    not variations of one command line. codex takes a subcommand, a sandbox mode, a working
    directory and a model override; dsh takes a profile, an optional patch overlay and the
    task, and reads its working directory from the process -- which is why `propose` runs it
    with `cwd=candidate`. Passing codex's flags to dsh is not a degraded round, it is a
    failure to start, reported as a method bug.
    """
    style = str((req.get("improver") or {}).get("argv_style") or "codex")
    command = _improver_command(req)
    extra = shlex.split(os.environ.get("HG_IMPROVER_ARGS", ""))
    if style == "codex":
        argv = command + ["exec", "--skip-git-repo-check",
                          "--sandbox", "danger-full-access",
                          "-C", str(candidate)]
        if model := os.environ.get("HG_IMPROVER_MODEL"):
            argv += ["-c", f'model="{model}"']
        if effort := os.environ.get("HG_IMPROVER_EFFORT"):
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        return argv + extra + [task]
    if style == "dsh":
        return command + extra + [task]
    raise SystemExit(
        f"the resolved improver {(req.get('improver') or {}).get('name')!r} declares "
        f"argv_style {style!r} and this method has no argument builder for it. A CLI's argv "
        f"is not guessable -- add a branch here rather than passing flags it may ignore.")


def _seed_codex_home(workspace: Path, env: dict) -> Path:
    """Copy codex's own state into the workspace and point `CODEX_HOME` at it."""
    home = workspace / ".codex-home"
    home.mkdir(exist_ok=True)
    source_home = Path(os.environ.get("HG_CODEX_HOME_SOURCE", Path.home() / ".codex"))
    for name in ("config.toml", "auth.json"):
        if (source_home / name).is_file():
            shutil.copy2(source_home / name, home / name)
    cfg = home / "config.toml"
    text = cfg.read_text() if cfg.exists() else ""
    if "responses_experimental_transport" not in text:
        cfg.write_text(text + '\nresponses_experimental_transport = "https"\n')
    env["CODEX_HOME"] = str(home)
    return home


def _seed_dsh_home(workspace: Path, env: dict) -> Path:
    """Give dsh a home of its own, inside the workspace.

    Two reasons, and the second is the one that bites. The method sandbox binds the
    improver's trees **read-only** -- a tool a method runs is not state it may write -- and
    dsh writes sessions, caches and storages under its home, so pointing `DSH_HOME` at the
    real `~/.dsh` would have every one of those writes fail. And inside the namespace there
    is nothing under `$HOME` to fall back on, which is the same failure `_seed_codex_home`
    already exists for.

    What has to come along: `profiles/`, because without a bundle layer `--profile headless`
    has nothing to boot, and the credentials. Measured: `profiles/` is 1.3 MB, so a copy per
    round is cheaper than making the sandbox's read-only bind conditional.

    **`symlinks=True`, and that is not a detail.** `profiles/node_modules` is a pnpm link
    farm -- measured here, 277 symlinks, each pointing into a package's `node_modules`
    inside the DSH checkout -- and `copytree` dereferences symlinks by default. So the
    ordinary call copied the *targets*, every one of which carries further `node_modules`,
    and the copy was unbounded. Measured (`alfworld-dshsmoke3`, 2026-10-16): round 1 sat in
    this call for over ten minutes, 132 s user and 510 s system time, with thousands of
    nested `_copytree` frames and nothing on disk to show for it. The seeded home is meant
    to be a small writable overlay; copying links as links makes it one. The links resolve,
    because the DSH checkout is bound read-only into the same namespace.

    `settings.yaml` comes too, so the improver runs in the operator's environment -- but the
    model it uses is not read from there: the launcher pins `agent-default-model` with
    `--patch`, which is applied after the profile's user layer, so the recorded model cannot
    be overridden by ambient state.
    """
    home = workspace / ".dsh-home"
    home.mkdir(exist_ok=True)
    # **The declared home wins over the ambient one.** `DSH_HOME` is exported by this tool
    # environment, so the driver and every method inherit it -- which meant the improver read
    # the operator's settings file (complete with third-party `llm-pi-ai` providers nobody
    # chose for it) and that "which model edits the harness" was a property of the shell. The
    # registry entry names the home this tool should use; the fallbacks keep the older
    # behaviour reachable for an improver that has not been given one.
    source = Path(os.environ.get("HG_IMPROVER_DSH_HOME")
                  or os.environ.get("DSH_HOME")
                  or (Path.home() / ".dsh")).expanduser()
    for name in ("settings.yaml", ".credentials.yaml"):
        if (source / name).is_file():
            shutil.copy2(source / name, home / name)
    if (source / "profiles").is_dir():
        shutil.copytree(source / "profiles", home / "profiles", symlinks=True,
                        dirs_exist_ok=True)
    env["DSH_HOME"] = str(home)
    return home


#: What a CLI subprocess may keep, besides the one credential its entry names. An allowlist
#: rather than a denylist of secret-shaped names, because the point is a boundary and a
#: boundary that has to recognise every way a secret can be spelled is not one. `PATH` and
#: `HOME` because a program cannot start without them; `TMPDIR`/`TMP`/`TEMP` because node and
#: pnpm both use them; `LANG`/`LC_*`/`TZ`/`TERM` because encoding and timestamps end up in
#: logs; the proxy variables because a deployment behind one would otherwise watch the
#: improver fail to reach its own endpoint and read it as a method bug.
_ENV_KEEP = ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
             "TERM", "SHELL", "USER", "LOGNAME", "NODE_OPTIONS", "NODE_PATH",
             "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")


def _restrict_env(env: dict, improver: dict) -> list[str]:
    """Leave the improver exactly one credential, and say what was withheld.

    A CLI improver runs with whatever the method inherited, and the method inherits the
    driver's environment -- which holds the agent's key and every provider's alongside the
    improver's. So a dsh improver *could* reach an endpoint nobody chose for it, and "no
    forbidden models, no other endpoints" rested on it not trying. This makes that structural
    rather than aspirational.

    **Only for an entry that declares `api_key_env`** -- one that has opted into naming the
    single credential it needs. codex does not, and its provider config can name any variable,
    so it keeps the environment it has always had; narrowing it here would be a change nobody
    asked for, made in the one place least able to notice.

    Returns the names dropped. The round records them, so the boundary is visible in the
    receipt instead of being an invisible difference between two improvers.
    """
    declared = str(improver.get("api_key_env") or "")
    if not declared:
        return []
    kept = [name for name in _ENV_KEEP if name in env]
    withheld = sorted(name for name in env if name not in kept and name != declared)
    for name in withheld:
        del env[name]
    return withheld


def _stage_evidence(workspace: Path, req: dict) -> Path:
    """Put the platform's channel where the improver will look for it.

    `workspace/_harnessgrad/` already holds the round summary, the traces, the task pages
    (with the verifier's verdict) and the earlier harness states -- staged by the driver,
    which is the only thing that knows what this run measured and what it is allowed to
    show. This copies it under the name the prompt uses rather than moving it, so a method
    that wants the original paths still has them. `states/` becomes a symlink: those are
    complete harness trees and copying them would double the cost of every round.
    """
    src = Path(req.get("channel") or (Path(req["workspace"]) / "_harnessgrad"))
    dest = workspace / "evidence"
    if dest.exists():
        shutil.rmtree(dest)
    if src.is_dir():
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("states"))
        states = src / "states"
        if states.is_dir():
            (dest / "states").symlink_to(states, target_is_directory=True)
    else:
        dest.mkdir(parents=True, exist_ok=True)

    # The skill arrives **with the channel**: the platform stages `improvers/skill.md` as
    # `_harnessgrad/SKILL.md` precisely because a method cannot read the platform's copy
    # from inside its sandbox. `HG_IMPROVER_SKILL` still wins -- an operator pointing at a
    # skill is a deliberate act -- and the platform path is the `--no-sandbox` fallback.
    # This is the file-based twin of `editor.skill_block`, which does the same job for a
    # chat call: one staged skill, two ways of handing it over.
    if not (dest / "SKILL.md").is_file():
        override = os.environ.get("HG_IMPROVER_SKILL")
        skill = Path(override) if override else (ROOT / "harnessgrad" / "improvers" / "skill.md")
        if skill.is_file():
            (dest / "SKILL.md").write_text(skill.read_text(encoding="utf-8"),
                                           encoding="utf-8")
    return dest


def _is_harness_file(rel: Path) -> bool:
    return not any(part in _NOT_HARNESS for part in rel.parts) \
        and not rel.name.endswith(".pyc")


def _changed_files(candidate: Path, pristine: Path) -> list[str]:
    """Which files differ from the pristine harness, **by content**.

    Not `git status`: measured, a one-line edit to `agent.py` came back as
    `22 file(s) changed`, nineteen of them files this platform wrote, and the wrong one was
    quoted back to a reader as the method's change.
    """
    changed = []
    for path in sorted(candidate.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(candidate)
        if not _is_harness_file(rel):
            continue
        other = pristine / rel
        if not other.is_file() or other.read_bytes() != path.read_bytes():
            changed.append(str(rel))
    for path in sorted(pristine.rglob("*")):
        if path.is_file() and _is_harness_file(path.relative_to(pristine)):
            rel = path.relative_to(pristine)
            if not (candidate / rel).is_file():
                changed.append(str(rel))
    return sorted(set(changed))


def _summary_of(transcript: str) -> str:
    """The improver's own sentence: the last substantial line it printed that reads as prose.

    Skipped: its logging (`tokens used`, timestamps, a session id), our diagnostics, anything
    short enough to be a label rather than a reason -- and, since a measurement, **tool
    output**. `v7-dgm` recorded a child's reason as

        -rw-r--r-- 1 xubuqiang xubuqiang    12 Oct  5 15:32 requirements.txt

    which is `ls -l`: 74 characters, starting with none of the prefixes the old filter knew,
    so a directory listing reached the curve where the method's stated hypothesis belongs.
    Prose is letters, spaces and ordinary punctuation; terminal output and source code are
    not, and the ratio separates them cleanly (measured on that transcript: 1.000 for each
    real diagnosis, 0.794 for the `ls -l` line, 0.584 for a line of Python).
    """
    for line in reversed((transcript or "").strip().splitlines()):
        text = line.strip()
        if len(text) < 40 or text.startswith(("ERROR", "warning", "fake", "codex",
                                              "tokens used", "session id")):
            continue
        readable = sum(c.isalpha() or c.isspace() or c in ".,;:'\"()" for c in text)
        if readable / len(text) < 0.90:
            continue
        return text[:400]
    return ""


def propose(req: dict, parent: Path, *, prompt: str | None = None,
            evidence: str = "", facts: str = "") -> dict:
    """Drive the improver on **this tree** and return what moved. No report, no decision.

    Split out of `run` so a method with a rule of its own can use the improver as the
    *proposal* engine: it picks the parent (its archive's choice), hands the prompt its rule
    wants, gets back a candidate tree, and then decides what to do with it -- keep it, reject
    it, or submit it beside the ones its siblings produced. `run` is now the trivial rule on
    top of this ("accept whatever it produced and report").

    `prompt` is the **rule's** instruction when one is given (DGM's "target this failure",
    Meta-Harness' frontier), and `PROMPT` when it is not. Either way the improver also gets
    `evidence/` -- staged here -- so it can read the traces itself, and either way the
    platform's `HARD_RULES` are appended (`instruction`), because a rule's prompt says what
    to change and not which contract the result must still satisfy.
    """
    workspace = Path(req["workspace"])
    candidate = Path(req.get("candidate") or (workspace / "candidate"))
    if req.get("exclusive_candidate"):
        if os.path.lexists(candidate):
            raise FileExistsError(f"Refusing to replace an existing candidate: {candidate}")
        # copytree(exist_ok=False) also refuses a concurrent creation after this
        # check. Exclusive mode must never enter the legacy deletion branch.
    elif candidate.exists():
        shutil.rmtree(candidate)
    # A git repo, because codex's own `apply_patch` works through one, and because a harness
    # *is* a git repo on this platform (`ckpt/git_state.py`).
    copy_harness(parent, candidate, ignore=shutil.ignore_patterns(".git"))
    selftest = stage_candidate(candidate, req.get("harness_runtime"))
    subprocess.run(["git", "init", "-q", str(candidate)], check=True)
    # **Only when the harness has none.** This used to write unconditionally, and the
    # harness tree the platform stages already carries a `.gitignore` of its own
    # (`ckpt/git_state.py` writes a five-line one, starting with a blank line). The
    # rewrite therefore made every candidate differ from its parent in a file the
    # *platform* had just written, `_changed_files` reported it as the improver's edit,
    # and a proposal that changed nothing at all was handed to the round as a new
    # candidate and measured. Measured twice (`v3-smoke`, `v4-dgm` round 1): codex's
    # only "change" to the harness was this line, and the curve recorded a new
    # `harness_sha` for a tree that behaves identically.
    if not (candidate / ".gitignore").exists():
        (candidate / ".gitignore").write_text(
            "_harnessgrad/\n__pycache__/\n*.pyc\n.state/\n")

    _stage_evidence(candidate, req)
    if selftest:
        guide = ("Prepared dependencies are attached before this turn; do not install packages.\n"
                 "Workspace imports resolve into this candidate; third-party packages are shared read-only.\n"
                 "Run public source tests from the candidate root (OpenCode examples):\n"
                 "  .state/self-test unit packages/core test/tool-read-filesystem.test.ts\n"
                 "  .state/self-test typecheck packages/core\n"
                 "Other packages/test files may be selected. Each call has a 120-second limit, "
                 "no network and no inherited credentials. Test failures/timeouts are not passes.\n"
                 "The runtime dependency cache may omit root development dependencies: full typecheck "
                 "can report missing types/tools even on the original source. Record that separately "
                 "from candidate regressions; do not install packages to hide it.\n"
                 "These self-tests do not replace independent platform acceptance.\n")
        (candidate / "evidence").mkdir(exist_ok=True)
        (candidate / "evidence/SELF_TEST.md").write_text(guide)
        facts += "\nDependencies are ready. See evidence/SELF_TEST.md for the offline self-test entry."

    # **Which CLI this is, is declared, not assumed.** This used to append codex's argv
    # unconditionally, with a comment saying a second CLI would earn a proper argument
    # builder "rather than a guess". `dsh` is that second CLI, and it is not a variation of
    # codex's command line: it takes a profile and a patch overlay, and reads its working
    # directory from the process. `_cli_argv` builds both vocabularies and refuses an
    # unknown one, because a CLI handed flags it does not know either ignores them or dies
    # -- and both look like a method bug rather than a wrong argv.
    argv = _cli_argv(req, candidate, instruction(prompt, evidence=evidence, facts=facts))

    timeout = int(os.environ.get("HG_IMPROVER_TIMEOUT_S", "1800"))
    started = time.time()
    env = dict(os.environ)
    # **Narrow the environment first, then seed into it.** The order matters twice over: the
    # restriction must see the inherited environment (that is what it is narrowing), and the
    # seeding must run after it so the `DSH_HOME` it sets is the one the improver gets --
    # restricting afterwards would delete the very variable that points at the seeded home.
    style = str((req.get("improver") or {}).get("argv_style") or "codex")
    withheld = _restrict_env(env, req.get("improver") or {})
    # The CLI's own state, seeded into the workspace because the sandbox binds the
    # improver's trees read-only and nothing under `$HOME` is reachable from inside it.
    if style == "dsh":
        _seed_dsh_home(workspace, env)
    else:
        _seed_codex_home(workspace, env)

    transcript = ""
    try:
        if req.get("exclusive_candidate"):
            # The single-rewrite entry owns a fresh tree and a real process budget.
            # The outer PID namespace also kills descendants that change sessions.
            proc = _run_bounded(argv, timeout=min(timeout, 600), cwd=candidate, env=env)
        else:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                  cwd=str(candidate), env=env)
        transcript = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        def decoded(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        transcript = decoded(exc.stdout) + decoded(exc.stderr)
        code = -1
    (workspace / "improver.log").write_text(transcript[-200_000:], encoding="utf-8")

    changed = _changed_files(candidate, parent)
    protected = [f for f in changed if Path(f).name == "harness.json"]
    summary = _summary_of(transcript)
    reported = {
        "improver": (req.get("improver") or {}).get("name")
                    or os.environ.get("HG_IMPROVER") or "cli (resolved)",
        "improver_path": (req.get("improver") or {}).get("path") or "",
        "exit_code": code,
        "seconds": round(time.time() - started, 1),
        "files_changed": changed,
        "note": summary,
        # What the boundary withheld, so a reader can tell two improvers apart by what they
        # *could* reach and not only by what they did. Empty for an improver that declares no
        # `api_key_env`, which is every improver that has not opted into being narrowed.
        "env_withheld": withheld,
    }
    return {"candidate": candidate, "changed": changed, "files": changed,
            "protected": protected, "exit_code": code, "seconds": reported["seconds"],
            "note": summary, "reported": reported, "transcript": transcript}


def _run_bounded(argv, *, timeout, cwd, env):
    """Method-local equivalent of the platform group deadline (platform is hidden)."""
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, errors="replace", start_new_session=True,
                          cwd=cwd, env=env) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def run(req: dict, base: Path, *, method: str) -> int:
    """Drive the improver and report what moved -- the trivial rule on top of `propose`.

    Everything here is improver-shaped and nothing is rule-shaped, which is the point: a
    method with a decision rule of its own calls `propose` when it has already decided to
    spend the round, and whatever it does with the result is its own business.
    """
    result = propose(req, base)
    candidate, changed = result["candidate"], result["changed"]
    protected, reported = result["protected"], result["reported"]
    code, summary = result["exit_code"], result["note"]
    if code != 0 and not changed:
        reported["error"] = (f"the improver exited {code} and changed nothing; see "
                             f"improver.log in the run's method logs")
    elif protected:
        # Recorded, not raised: the platform's own `candidate.validate` decides whether the
        # tree is still a harness, and a method that edited a file it was told not to is a
        # fact for the record rather than a reason to lose the round.
        reported["error"] = (f"the improver edited a protected file "
                             f"({', '.join(protected)})")

    return editor.report(
        req, method=method, harness_dir=candidate, changed=bool(changed), files=changed,
        hypothesis=summary, edit_kind="cli_improver", method_reported=reported,
        acceptance_rule=(
            "the improver the platform resolved produces the edits; the platform measures "
            "and judges the result. This layer makes no decision about whether to spend the "
            "round or whether to keep the candidate -- that belongs to the method"),
        label=(f"{reported['improver']}: {summary[:70]}" if summary
               else f"{reported['improver']}: no change"))
