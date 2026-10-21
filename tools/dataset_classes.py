#!/usr/bin/env python3
"""Classify the published datasets, deriving everything a program can decide.

Why a program and not a table someone maintains
-----------------------------------------------
A classification rots the moment it is a hand-written list beside code that can contradict
it. So the axes split into three kinds, and only the middle one is a judgement:

1. **Derived** -- read out of the adapter and the agent-facing CLI. The environment kind, the
   verifier kind, the action surface (the verbs the shipped CLI exposes), the task counts and
   the split sizes. If any of these changed, re-running this tool produces the new answer;
   nothing has to be remembered. The decisive one is `interaction`: a dataset whose CLI
   exposes a `do`-style verb makes the agent act repeatedly and observe each result, and one
   whose CLI only submits an artefact cannot. That is a fact about the shipped program, so it
   is derived rather than declared -- a declared "this is a closed loop" flag would be the
   first field to go stale.

2. **Judged** -- the capability axis, and nothing else: one `(demand, surface)` pair per
   published dataset, declared once in `CAPABILITY`. There is no program that can decide
   whether `chessbench_puzzles` is "reasoning" or "games", and pretending otherwise buys a
   rule nobody follows. What a program *can* enforce is that the vocabulary stays closed and
   total, so this tool refuses a published dataset with no entry or an entry outside the
   vocabulary, and it refuses a demand thin enough to be a private name for one dataset.

   The axis has two levels because one flat list mixed four different kinds of statement.
   `demand` is what the task asks of the Harness, which is the level a transfer claim is
   phrased on ("improvements move within evidence and not to environment"). `surface` is
   where that demand is exercised, which is what tooling is chosen from. The flat list had
   `code_generation`, `text_to_sql`, `spreadsheet` and `terminal` as four top-level peers --
   four surfaces of two demands -- and `tool_calling` beside `tool_dialogue`, which restated
   an axis the derivation below already owns.

   The vocabulary also names demands the registry does not cover yet, and it says so: an
   uncovered demand is declared in `UNCOVERED_DEMANDS` with the surfaces it is expected to
   need. Declaring it is not covering it, and the two are kept apart on purpose, because a
   demand that is silently absent reads exactly like a demand that is absent for no reason.

3. **Measured** -- cost, difficulty and headroom. Only a run knows these; they live in the
   curve points and in `tools/task_difficulty.py`'s per-dataset JSON, never in this file.

The judged axis also carries a third level that answers a different question from the first
two. `demand` and `surface` say what a *task* asks for; `exercises` says which *harness
component* a dataset makes score-relevant, as a set, because a task can stress several at
once. Scaffold reuse does not establish functional coverage, so `EXERCISES` records
which functions each dataset stresses and `UNCOVERED_FUNCTIONS` names those below
the coverage floor.

The function vocabulary is not invented here. It is the union of the decompositions the
survey corpus already reports -- `2507.21504`'s Agent Capabilities, `2601.01743`'s agent
components, the Springer meta-taxonomy's W-pillar, `2601.05111`'s method dimensions and
`2503.09648`'s TrustAgent -- reconciled with the functions this repository's own pages name:
`docs/writing_a_harness.md` ("tools, prompts, control flow, memory, number of model calls")
and `report/09_experiments.tex` ("context builders, tool policies, retry rules, memory
structures, and stopping conditions"). Where a name appears only in the literature it is
still listed, because the point is coverage rather than vocabulary agreement.

Usage:

    python3 tools/dataset_classes.py                # the table
    python3 tools/dataset_classes.py --json         # the same, for another program
    python3 tools/dataset_classes.py --demand reasoning
    python3 tools/dataset_classes.py --function verification
    python3 tools/dataset_classes.py --surface terminal
    python3 tools/dataset_classes.py --adversarial
    python3 tools/dataset_classes.py --interaction closed_loop
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.scaffolds import registry  # noqa: E402

#: Level 1 of the judged axis: the demand the task places on the Harness. Closed, and printed
#: in this order. A demand names something the Harness must provide, which is why a transfer
#: claim can be stated over it: the Harness either carries external evidence across steps or
#: it does not, and that is a property of the artefact under improvement rather than of a
#: benchmark's subject matter.
DEMANDS = ("evidence", "reasoning", "tool_use", "artifact", "environment", "memory")

#: Demands the vocabulary names but the registry does not cover yet, with the surfaces each is
#: expected to need. A key here means "no adapter", and the check below holds both directions:
#: a demand with no adapter must be declared here, and a demand declared here must have no
#: adapter. A gap entry must be removed when adapters cover that demand.
#:
#: `memory` asks the Harness to maintain a representation of information that arrives over
#: time and changes. That is not `evidence`, which asks it to select from a static set: no
#: question in HotpotQA is invalidated by a later observation, while every screened memory
#: candidate turns on knowledge update, temporal reasoning, preference drift, conflict
#: resolution, or selective forgetting. It is not a condition like `ADVERSARIAL` either,
#: because an adversary adds stress to an unchanged capability while memory changes what the
#: Harness has to provide: a Harness with no store cannot attempt these tasks at all, and can
#: still attempt every evidence task ever onboarded.
#:
#: The two surfaces are `authored_store`, where the Harness builds and maintains the store
#: itself, and `supplied_history`, where the interaction history is provided and the demand is
#: to retain it. Five candidates were screened against the platform's hard constraints in
#: `docs/survey/notes/harnessgrad_memory_domain_report.md` and none is onboarded as of this
#: revision: LoCoMo-MC10, PersonaMem v1, LaMP, MemoryAgentBench (deterministic sub-tasks), and
#: LongMemEval's retrieval-recall variant. LongMemEval's QA headline, HaluMem, StructMemEval
#: and LaMP-QA stay refused: their headline is an LLM judge, or in HaluMem's case a product of
#: two judge scores.
UNCOVERED_DEMANDS: dict[str, tuple[str, ...]] = {
    "memory": ("authored_store", "supplied_history"),
}

#: The harness functions a dataset can make score-relevant: if you changed that component in a
#: harness, could this dataset's score move? A set per dataset rather than one value, because a
#: task that needs multi-step tool use stresses the tool interface and the control loop at once.
#:
#: `context` is assigned only where the assembly is a real decision (many candidate documents,
#: a growing trajectory, a large structured state) rather than to every dataset that has a
#: prompt, or the column would say nothing. `memory` here is *within-task* retention, which is
#: a different thing from the `memory` demand above: jericho, scienceworld and tau_bench all
#: require holding state across a horizon, and none of them grades a store the harness authored
#: and carried across sessions. That distinction is the reason both vocabularies exist.
#:
#: `verification` is assigned where testing the harness's own output before submitting is
#: rewarded -- the code, SQL and spreadsheet datasets. A harness that runs the tests it can see
#: and fixes what fails scores higher there, and does nothing extra on a QA row.
#:
#: `verification` covers both objects the corpus names: checking an artifact against a check
#: (`2605.23950`'s "verification", `2601.01743`'s "critics") and re-examining the harness's own
#: reasoning (`2503.16416`'s Self-Reflection). It is one function here rather than two because a
#: separate `reflection` failed the test this axis applies to every entry: assigned to exactly
#: the reasoning datasets, it covered one demand and carried nothing the demand axis did not
#: already have. Every other function crosses at least two demands, and `_check_functions`
#: refuses one that does not. It is the same defect as `tool_calling` beside `tool_dialogue` one
#: level down -- two names for one fact -- and the remedy is the same: one name, with a
#: definition that says what it spans.
#:
#: Two properties of this axis must not be confused with each other, or with the demand axis.
#: Every entry is a claim of *sensitivity* -- change this component and the score can move --
#: and not a claim that the dataset *isolates* the component. No dataset here isolates any of
#: them, which is why `2503.16416` can call self-reflection "a critical gap" while ten datasets
#: are sensitive to it: a task that checking helps on is not a measurement of checking. And an
#: entry is *harness-relative*: whether changing a component moves the score depends on whether
#: the harness under test has that component at all, so one dataset can carry a different set
#: for a different harness family. The demand axis has neither property, because it is a
#: property of the dataset alone.
#: **Not defined here any more.** The ten names are the same decomposition the harness side
#: needs, so the list lives in `harnessgrad/components.py` and this is the re-export --
#: `tools/` imports `harnessgrad/` and never the reverse, so that is the legal direction. Two
#: copies is how a taxonomy rots: the moment one grows, every claim phrased over the other is
#: silently about a different set. `harnessgrad/components.py` also says why the same list
#: answers both "which component does a dataset exercise" and "which component did a round's
#: edit touch".
from harnessgrad.components import COMPONENTS as FUNCTIONS  # noqa: E402

EXERCISES: dict[str, tuple[str, ...]] = {
    "kumo": ("context", "tools", "control", "verification"),
    "alfworld": ("context", "tools", "control", "recovery"),
    "agentdojo": ("context", "tools", "control", "safety"),
    "bfcl_v3": ("tools",),
    "chessbench_puzzles": ("context", "control"),
    "ds1000": ("tools", "verification"),
    "gsm8k": ("context", "verification"),
    # `aime` arrives from Harbor's adapter rather than from a fresh survey, and it stresses the
    # same two components as the other single-shot exact-match sets: the problem and the answer
    # format both have to survive the context, and the final answer has to be checked against
    # the stated shape. It adds no new function coverage, which is the expected result of a
    # port -- the point of this one is a second source family over a metric already in use.
    "aime": ("context", "verification"),
    "reasoning_gym": ("context", "verification"),
    "jericho": ("context", "tools", "control", "memory"),
    "musique": ("context",),
    "twowikimultihopqa": ("context",),
    "livecodebench": ("tools", "verification"),
    # Harbor delivers the complete conversation at task start; the score tests
    # selecting evidence from that static history, not incremental retention.
    "locomo": ("context",),
    "appworld": ("context", "tools", "control", "recovery"),
    "bird_minidev": ("context", "tools", "verification"),
    "physicsquestions": ("context", "verification"),
    "scienceworld": ("context", "tools", "control", "memory", "recovery"),
    "sportsunderstanding": ("context", "verification"),
    "spreadsheetbench": ("context", "tools", "control", "verification"),
    "strategyqa": ("context", "verification"),
    "tau_bench": ("context", "tools", "control", "memory", "recovery"),
    "terminal_bench": ("context", "tools", "control", "recovery", "verification"),
    "triviaqa": ("context",),
    "gaia": ("context", "tools", "control"),
    # Refused datasets carry the functions their task shape would stress, for the same reason
    # they carry a demand: the judgement is about the benchmark, not about our contract.
    "api_bank": ("tools",),
    "fever": ("context",),
    "hotpotqa": ("context",),
    "nestful": ("tools",),
    "sotuqa": ("context",),
    "tau2_bench": ("tools", "control"),
}

#: How few datasets make a harness function covered rather than merely touched.
MIN_MEMBERS_PER_FUNCTION = 2

#: Functions below that floor, with the reason and the shape of the gap. Same two-way rule as
#: `UNCOVERED_DEMANDS`: a function under the floor must appear here, and a function here must be
#: under the floor, so closing a gap deletes the entry in the same change.
#:
#: A function's coverage counts adapters across demands. The remaining
#: gaps below distinguish missing task requirements from ordinary runtime accounting.
#:
#: Deliberately absent from `FUNCTIONS`: perception. Computer-use and GUI benchmarks need a
#: browser or a VM (`docs/survey/README.md` excludes both), and the platform has no multimodal
#: harness or image-bearing action space, so a `perception` function here would be a permanent
#: red entry for an infrastructure decision rather than a dataset gap.
UNCOVERED_FUNCTIONS: dict[str, str] = {
    "delegation": "no adapter requires the harness to split work across sub-agents or route "
                  "between models; tau2_bench needs an independent user simulator, which the "
                  "platform supplies rather than the harness",
    "reliability": "no adapter is built so that consistency across trials is what is scored; "
                   "the platform takes repeated trials, but no task is constructed for pass^k",
    "cost": "every run is costed, but no task is budget-bounded, so spending less changes the "
            "ledger rather than the score",
    "safety": "one adapter (agentdojo), and it carries the adversary as a condition flag on an "
              "ordinary tool-dialogue task rather than as a surface of its own",
}

#: Level 2 of the judged axis: the surface the demand is exercised on. Kept because a SQL task
#: and a spreadsheet task stress different tools even though both deliver an artefact, but a
#: surface is a leaf, so a surface with one member is a fact about coverage rather than a
#: defect in the taxonomy.
#:
#: The judged entry for a dataset is its single `(demand, surface)` pair. One mapping rather
#: than two so the levels cannot drift apart: there is no way to add a dataset to a demand and
#: forget its surface, which a second dict keyed by the same names would allow.
CAPABILITY: dict[str, tuple[str, str]] = {
    "kumo": ("reasoning", "planning_and_search"),
    "alfworld": ("environment", "embodied_text"),
    "agentdojo": ("tool_use", "tool_dialogue"),
    "bfcl_v3": ("tool_use", "tool_calling"),
    "chessbench_puzzles": ("reasoning", "planning_and_search"),
    "ds1000": ("artifact", "code_generation"),
    "gsm8k": ("reasoning", "reasoning"),
    # Same `(demand, surface)` as `gsm8k`, `physicsquestions` and `strategyqa`: competition
    # mathematics answered by a single integer, so the demand is reasoning and the surface it
    # is exercised on is reasoning rather than a tool or an artefact. The surface stays a leaf
    # because it belongs to `reasoning` alone.
    "aime": ("reasoning", "reasoning"),
    "reasoning_gym": ("reasoning", "reasoning"),
    "jericho": ("environment", "interactive_fiction"),
    "musique": ("evidence", "information_seeking"),
    "twowikimultihopqa": ("evidence", "information_seeking"),
    "livecodebench": ("artifact", "code_generation"),
    "locomo": ("evidence", "long_context"),
    "appworld": ("tool_use", "tool_dialogue"),
    "bird_minidev": ("artifact", "text_to_sql"),
    "physicsquestions": ("reasoning", "reasoning"),
    "scienceworld": ("environment", "science_simulation"),
    "sportsunderstanding": ("reasoning", "reasoning"),
    "spreadsheetbench": ("artifact", "spreadsheet"),
    "strategyqa": ("reasoning", "reasoning"),
    "tau_bench": ("tool_use", "tool_dialogue"),
    "terminal_bench": ("environment", "terminal"),
    "triviaqa": ("evidence", "information_seeking"),
    "gaia": ("evidence", "information_seeking"),
    # Refused datasets still have an entry: the classification is about what the benchmark
    # measures, and a refusal is a statement about this platform's contract, not about it.
    "api_bank": ("tool_use", "tool_calling"),
    "fever": ("evidence", "information_seeking"),
    "hotpotqa": ("evidence", "information_seeking"),
    "nestful": ("tool_use", "tool_calling"),
    "sotuqa": ("evidence", "information_seeking"),
    "tau2_bench": ("tool_use", "tool_dialogue"),
}

#: A condition, not a capability, and therefore not a level of the judged axis. `agentdojo`
#: runs bank, email and travel tool dialogues with an injected adversary, so its demand and
#: surface are the ordinary ones and what distinguishes it is the threat model. Carrying it as
#: its own demand would have said nothing about what the Harness must provide, while occupying
#: a top-level bucket with a single member: a demand named after the dataset it contains.
#:
#: As a flag it composes, and the claim it supports is the sharper one -- an improvement that
#: survives the ordinary dialogue may still fail here, which is a transfer result rather than
#: a category.
ADVERSARIAL = frozenset({"agentdojo"})

#: A CLI verb that means "act, then observe the result". Its presence is the difference
#: between a dataset a scaffold can iterate inside and one it can only answer once.
#:
#: The vocabulary is closed and each entry names where it came from: `alf`, `scw` and `jer`
#: expose `do "<action>"`, and `dojo` exposes `call '<json>'`. A dataset that ships a *new*
#: action verb therefore fails the check below rather than being silently misclassified --
#: which is what happened to `agentdojo` when this list held only `do`.
ACT_VERBS = ("do", "call")

#: The judged value, declared, and asserted equal to the derived one by `classify_all()`.
#: Two independent sources that must agree: if a shipped CLI changes its surface, or someone
#: edits this map, the tool stops and someone looks. A single source would have hidden the
#: `agentdojo` mistake above; the derived side is what caught it.
EXPECTED_INTERACTION = {
    "agentdojo": "closed_loop",
    "kumo": "closed_loop",
    "alfworld": "closed_loop",
    "bfcl_v3": "single_shot",
    "chessbench_puzzles": "single_shot",
    "ds1000": "single_shot",
    "fever": "single_shot",
    "hotpotqa": "single_shot",
    "gsm8k": "single_shot",
    # Derived, not declared: the QA scaffold's shipped CLI exposes only answer submission, so
    # `classify()` reads `single_shot` off the program and this entry has to agree with it.
    "aime": "single_shot",
    "jericho": "closed_loop",
    "musique": "single_shot",
    "reasoning_gym": "single_shot",
    "twowikimultihopqa": "single_shot",
    "livecodebench": "single_shot",
    "locomo": "single_shot",
    "bird_minidev": "single_shot",
    "physicsquestions": "single_shot",
    "scienceworld": "closed_loop",
    "sportsunderstanding": "single_shot",
    "spreadsheetbench": "single_shot",
    "strategyqa": "single_shot",
    "tau_bench": "closed_loop",
    "terminal_bench": "closed_loop",
    "appworld": "closed_loop",
    "triviaqa": "single_shot",
    "gaia": "closed_loop",
}

#: How few datasets make a demand a private name for one benchmark rather than a category.
#: Enforced below so the flat list's failure mode -- thirteen buckets, seven of them holding
#: one dataset each -- cannot come back one dataset at a time.
MIN_MEMBERS_PER_DEMAND = 2

_ACT_PATTERN = re.compile(
    r"^\s*(?:python3\s+\S+\s+)?(?:" + "|".join(ACT_VERBS) + r")\b", re.M)

#: How a shipped CLI declares its verb set. Both spellings are declarations *in the program*,
#: which is why they are read instead of the prose in the module docstring: `dojo` documents
#: "tools, call, and submit" in a sentence and lists them in `choices`, and the sentence is not
#: a thing a program can rely on.
_VERB_PATTERNS = (
    re.compile(r"choices\s*=\s*\[([^\]]*)\]"),
    re.compile(r"""command\s*==\s*["']([a-z_]+)["']"""),
)


def _declared_verbs(source: Path) -> set[str]:
    """The verbs a CLI declares in its own argument handling.

    An empty set is a hard failure rather than a default: a third spelling means a human has
    to look, and a silent `single_shot` is exactly the mistake this axis already made once.
    """
    text = source.read_text(encoding="utf-8", errors="replace")
    verbs: set[str] = set()
    for pattern in _VERB_PATTERNS:
        for match in pattern.finditer(text):
            verbs |= {v.strip().strip("\"'") for v in match.group(1).split(",") if v.strip()}
    return {v for v in verbs if v}


def _first(mapping):
    return mapping[list(mapping)[0]] if isinstance(mapping, dict) and mapping else None


def _cli_scripts(module, task_id: str) -> list[Path]:
    """The agent-facing programs a dataset ships.

    The rule is the layout convention this repository actually follows, not a file extension
    and not the executable bit -- measured: `data/scaffolds/qa/cli/qa` is mode 0600 while
    `data/datasets/alfworld/cli/alf` is 0711, so the bit cannot decide, and `dojo.py` carries a `.py`
    extension while `alf` does not, so neither can a suffix. Every agent-facing program here
    lives under a `cli/` directory or is materialized under a `bin/` directory.
    """
    out = []
    for entry in (module.SETUP.get(task_id) or {}).get("files", []):
        rel = entry.get("path", "")
        source = entry.get("from")
        if not source:
            continue
        if "bin" in Path(rel).parts or "cli" in Path(source).parts:
            path = Path(source)
            if path.is_file():
                out.append(path)
    return out


def classify(name: str) -> dict:
    module = registry.importlib.import_module(registry.module_name(name))
    refused = getattr(module, "REFUSED", None)
    demand, surface = CAPABILITY.get(name, (None, None))
    row = {
        "dataset": name,
        "demand": demand,
        "surface": surface,
        "adversarial": name in ADVERSARIAL,
        "exercises": list(EXERCISES.get(name, ())),
        "refused": bool(refused),
        "env_kinds": sorted({v.get("kind") for v in module.ENV.values()}) if getattr(module, "ENV", None) else [],
        # A refusal declares no tasks on purpose, so the contract axes are inapplicable.
        "verifier_kinds": [],
        "interaction": None,
        "action_surface": [],
        "tasks": 0,
        "train": 0,
        "eval": 0,
    }
    if refused:
        row["interaction"] = "n/a (refused)"
        return row
    tasks = module.load()[0]
    task_id = tasks[0]["task_id"]
    # Prefer the scaffold's declared kind: `VERIFY` is a lazy one-entry-per-task map,
    # so iterating it built ~83k verifier dicts to recover a single distinct value.
    # A module that declares no kind still walks its real map, which is small.
    declared_kinds = getattr(getattr(module, "_SPEC", None), "VERIFIER_KIND", None)
    row["verifier_kinds"] = ([declared_kinds] if declared_kinds
                             else sorted({v["kind"] for v in module.VERIFY.values()}))
    scripts = _cli_scripts(module, task_id)
    surface_names = []
    acts = False
    for script in scripts:
        verbs = _declared_verbs(script)
        if not verbs:
            raise SystemExit(
                f"{name}: {script} declares no verb set that this tool can read. Its CLI uses "
                f"a spelling not covered by _VERB_PATTERNS, so the interaction axis cannot be "
                f"derived for it. Add the spelling (and decide which of its verbs mean "
                f"'act') rather than defaulting the dataset to single_shot.")
        surface_names.append(f"{script.name}[{','.join(sorted(verbs))}]")
        if verbs & set(ACT_VERBS):
            acts = True
    row["action_surface"] = surface_names
    if "exec" in row["env_kinds"]:
        row["interaction"] = "closed_loop"
    elif acts:
        row["interaction"] = "closed_loop"
    else:
        row["interaction"] = "single_shot"
    row["tasks"] = len(tasks)
    row["train"] = len(module.SPLIT["train"])
    row["eval"] = len(module.SPLIT["eval"])
    declared = EXPECTED_INTERACTION.get(name)
    if declared != row["interaction"]:
        raise SystemExit(
            f"{name}: the shipped program says {row['interaction']!r} (env "
            f"{row['env_kinds']}, action surface {surface_names}) but EXPECTED_INTERACTION "
            f"says {declared!r}. Neither side is overwritten automatically: decide which is "
            f"right, then fix the other.")
    return row


def _check_judged_axis(rows: list[dict]) -> None:
    """The judged axis has to stay closed, total, and coarse enough to be a taxonomy.

    Every refusal here is a statement about the vocabulary, so it names the dataset and what
    to add to `CAPABILITY`. Silence is what this replaces: the flat list grew two singleton
    buckets per new benchmark because nothing objected.
    """
    unknown = [r["dataset"] for r in rows
               if not r["demand"] or not r["surface"] or r["demand"] not in DEMANDS]
    if unknown:
        raise SystemExit(
            f"these published datasets have no judged entry, or a demand outside the "
            f"vocabulary: {unknown}. Add them to CAPABILITY in {Path(__file__).name}.")
    missing = sorted(set(CAPABILITY) - {r["dataset"] for r in rows})
    if missing:
        raise SystemExit(f"CAPABILITY names datasets that are not published: {missing}")

    # A surface belongs to exactly one demand, or it is not a leaf and the levels overlap.
    owners: dict[str, set[str]] = {}
    for demand, surface in CAPABILITY.values():
        owners.setdefault(surface, set()).add(demand)
    shared = {s: sorted(d) for s, d in owners.items() if len(d) > 1}
    if shared:
        raise SystemExit(
            f"these surfaces appear under more than one demand: {shared}. A surface is a leaf, "
            f"so either merge the demands or split the surface.")

    # A declared gap has to stay a gap, and an undeclared gap has to be declared.
    stray = sorted(set(UNCOVERED_DEMANDS) - set(DEMANDS))
    if stray:
        raise SystemExit(f"UNCOVERED_DEMANDS names demands outside DEMANDS: {stray}.")
    for demand, surfaces in UNCOVERED_DEMANDS.items():
        if not surfaces:
            raise SystemExit(
                f"UNCOVERED_DEMANDS[{demand!r}] declares no planned surface. A gap with no "
                f"recorded shape is the thing this file exists to prevent.")
    counts = {d: sum(1 for r in rows if r["demand"] == d) for d in DEMANDS}
    silent = sorted(d for d in DEMANDS if not counts[d] and d not in UNCOVERED_DEMANDS)
    if silent:
        raise SystemExit(
            f"these demands hold no datasets and are not declared uncovered: {silent}. A "
            f"demand the vocabulary names but nothing covers has to say so, or coverage "
            f"reads as complete when it is not. Add it to UNCOVERED_DEMANDS with the surfaces "
            f"it is expected to need, or drop it from DEMANDS.")
    stale = sorted(d for d in UNCOVERED_DEMANDS if counts[d])
    if stale:
        raise SystemExit(
            f"these demands are declared uncovered but now hold datasets: {stale}. Delete "
            f"them from UNCOVERED_DEMANDS in the same change that onboards the adapter; a "
            f"recorded gap outliving its cause becomes a wrong claim.")

    # A function that lives inside one demand is that demand under another name.
    narrow = sorted(f for f in FUNCTIONS if f not in UNCOVERED_FUNCTIONS
                    and len({r["demand"] for r in rows if f in r["exercises"]}) < 2)
    if narrow:
        raise SystemExit(
            f"these functions sit inside a single demand: {narrow}. A function covering one "
            f"demand carries nothing the demand axis does not already have, which is why "
            f"`reflection` was folded into `verification`: it held exactly the reasoning "
            f"datasets. Broaden the assignment, or fold the function into one it crosses.")

    # And a covered demand has to be a category rather than a private name for one dataset.
    thin = {d: n for d, n in counts.items() if n and n < MIN_MEMBERS_PER_DEMAND}
    if thin:
        raise SystemExit(
            f"these demands hold fewer than {MIN_MEMBERS_PER_DEMAND} datasets: {thin}. The "
            f"flat vocabulary this replaced ended with seven one-dataset buckets; a new "
            f"benchmark either belongs to an existing demand or the demand is wrong.")


def _check_functions(rows: list[dict]) -> None:
    """Every dataset names the harness functions it stresses, and every function is covered.

    The rule this replaces was silence. Onboarding was ordered by scaffold cost, and nothing
    ever asked whether the resulting set could tell one harness component from another; the
    measured answer was that three of ten could not be told apart at all.
    """
    unknown = [(r["dataset"], f) for r in rows for f in r["exercises"] if f not in FUNCTIONS]
    if unknown:
        raise SystemExit(
            f"these datasets exercise functions outside the vocabulary: {unknown}. Add the "
            f"function to FUNCTIONS in {Path(__file__).name}, or fix the dataset's entry.")
    silent = [r["dataset"] for r in rows if not r["exercises"]]
    if silent:
        raise SystemExit(
            f"these published datasets name no harness function: {silent}. A dataset that "
            f"stresses nothing in particular cannot support a harness-design claim; if that is "
            f"really true of it, say so by naming the one it stresses least badly.")
    stray = sorted(set(UNCOVERED_FUNCTIONS) - set(FUNCTIONS))
    if stray:
        raise SystemExit(f"UNCOVERED_FUNCTIONS names functions outside FUNCTIONS: {stray}.")

    counts = {f: sum(1 for r in rows if f in r["exercises"]) for f in FUNCTIONS}
    thin = sorted(f for f in FUNCTIONS if counts[f] < MIN_MEMBERS_PER_FUNCTION)
    declared = sorted(UNCOVERED_FUNCTIONS)
    if thin != declared:
        undeclared = [f for f in thin if f not in UNCOVERED_FUNCTIONS]
        stale = [f for f in declared if f not in thin]
        raise SystemExit(
            f"the function coverage floor is out of step with what is declared. Under the "
            f"floor and undeclared: {undeclared}. Declared but now at or above the floor: "
            f"{stale}. Below the floor means {MIN_MEMBERS_PER_FUNCTION} datasets; entries are "
            f"deleted in the same change that closes the gap.")


def classify_all() -> list[dict]:
    rows = [classify(name) for name in registry.published()]
    _check_judged_axis(rows)
    _check_functions(rows)
    return rows


def _table(rows: list[dict]) -> str:
    head = (f"{'dataset':20s} {'demand':11s} {'surface':20s} {'interaction':12s} "
            f"{'env':8s} {'verify':9s} {'tasks':>6s} {'train':>6s} {'eval':>6s}")
    out = [head, "-" * len(head)]
    for r in rows:
        env = ",".join(r["env_kinds"]) or "-"
        ver = ",".join(r["verifier_kinds"]) or "-"
        mark = " *" if r["adversarial"] else ""
        out.append(f"{r['dataset']:20s} {str(r['demand']):11s} {str(r['surface']):20s} "
                   f"{str(r['interaction']):12s} {env:8s} {ver:9s} "
                   f"{r['tasks']:>6d} {r['train']:>6d} {r['eval']:>6d}{mark}")
    closed = [r["dataset"] for r in rows if r["interaction"] == "closed_loop"]
    once = [r["dataset"] for r in rows if r["interaction"] == "single_shot"]
    out += [
        "",
        f"closed_loop ({len(closed)}): {', '.join(closed)}",
        f"single_shot ({len(once)}): {', '.join(once)}",
        "",
        "demands:",
    ]
    for demand in DEMANDS:
        names = [r["dataset"] for r in rows if r["demand"] == demand]
        if not names:
            continue
        surfaces = []
        for surface in dict.fromkeys(r["surface"] for r in rows if r["demand"] == demand):
            n = sum(1 for r in rows if r["surface"] == surface)
            surfaces.append((n, surface))
        surfaces.sort(key=lambda item: (-item[0], item[1]))
        out.append(f"  {demand:12s} {len(names):2d}  "
                   f"[{', '.join(f'{s} {n}' for n, s in surfaces)}]")
    for demand, surfaces in UNCOVERED_DEMANDS.items():
        out.append(f"  {demand:12s} {0:2d}  [planned: {', '.join(surfaces)}]"
                   f"   (declared, no adapter yet)")
    out += ["", "harness functions (which component a dataset makes score-relevant):"]
    fcounts = {f: sum(1 for r in rows if f in r["exercises"]) for f in FUNCTIONS}
    for f in sorted(FUNCTIONS, key=lambda f: (-fcounts[f], f)):
        names = [r["dataset"] for r in rows if f in r["exercises"]]
        note = "  <-- declared gap" if f in UNCOVERED_FUNCTIONS else ""
        out.append(f"  {f:13s} {fcounts[f]:2d}  {', '.join(names) if names else '-'}{note}")
    adversarial = [r["dataset"] for r in rows if r["adversarial"]]
    out.append(f"  {'adversarial':12s} {len(adversarial):2d}  {', '.join(adversarial) or '-'}"
               f"   (* in the table above; a condition, not a demand)")
    return "\n".join(out)


def select(rows: list[dict], *, demand: str | None = None, surface: str | None = None,
           function: str | None = None, adversarial: bool = False,
           interaction: str | None = None, runnable_only: bool = False) -> list[dict]:
    """Apply the CLI's filters as a pure function over already-classified rows.

    Extracted so the filter semantics can be tested without paying for a fresh
    classification per combination. `classify_all()` reads every dataset, and one
    dataset per task set is not cheap; running the whole command once per demand and
    per surface turned a taxonomy check into fifteen minutes of suite time. The CLI
    and the tests now share this function, so the tested path is the shipped path.
    """
    out = list(rows)
    if demand:
        out = [r for r in out if r["demand"] == demand]
    if surface:
        out = [r for r in out if r["surface"] == surface]
    if function:
        out = [r for r in out if function in r["exercises"]]
    if adversarial:
        out = [r for r in out if r["adversarial"]]
    if interaction:
        out = [r for r in out if r["interaction"] == interaction]
    if runnable_only:
        out = [r for r in out if not r["refused"]]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--demand", choices=DEMANDS, help="only this demand")
    parser.add_argument("--function", choices=FUNCTIONS,
                        help="only datasets that stress this harness function")
    parser.add_argument("--surface", help="only this surface")
    parser.add_argument("--adversarial", action="store_true",
                        help="only datasets carrying an injected adversary")
    parser.add_argument("--interaction", choices=["closed_loop", "single_shot"])
    parser.add_argument("--runnable-only", action="store_true",
                        help="drop the datasets that refuse")
    args = parser.parse_args(argv)

    selection = {"demand": args.demand, "surface": args.surface, "function": args.function,
                 "adversarial": args.adversarial, "interaction": args.interaction,
                 "runnable_only": args.runnable_only}
    rows = select(classify_all(), **selection)
    if args.json:
        print(json.dumps(rows, indent=1, sort_keys=True))
    else:
        print(_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
