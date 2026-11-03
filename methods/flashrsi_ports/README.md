# FlashRSI policy ports for HarnessGrad

This directory supplies ten **FlashRSI-policy-port** methods missing from the
starting HarnessGrad catalogue: `ace`, `adas`, `arbor`, `darwinx`, `evo_harness`,
`evoprompt`, `gepa`, `modular_rsi`, `opro`, and `skillopt`. Existing AHE, RRSI and
Meta-Harness ports are not replaced or renamed.

These are runnable search-policy adaptations, **not native reproductions or
published benchmark results**. Each method has its own branch, archive/selection
policy, proposal stages and scope gates. Shared code handles the artifact
boundary and the existing editor; it does not turn the ten strategies into one
unconditional diagnose-and-edit prompt.

**What a run needs, measured 2026-11-02 (so a reader does not find it out by
launching one).** None of the ten runs model-free: every port calls the method
editor, which refuses without `HG_METHOD_BASE_URL` / `HG_METHOD_API_KEY` /
`HG_METHOD_MODEL` (`methods/editor.py`), and `HG_AGENT_BACKEND=mock` mocks only the
*harness* agent, not the method's editing model. Separately, the six instruction
methods (`opro`, `gepa`, `evoprompt`, `ace`, `adas`, `skillopt`) need a declared
instruction file and refuse before any model call on a harness that ships none —
including the reference `base_harness/loop` — with "declare instruction_path in
`HG_FLASHRSI_CONFIG`; no unique loaded instruction file is available". Pair them
with a harness that has a prompt/skill file, or set `instruction_path`.

The source is RUC-NLPIR/FlashRSI commit
`e9c7667945f959d62da27204649a82f94d1b6705`. `PROVENANCE.json` records the
archive SHA-256 and per-file hashes. Unmodified original strategies are retained
as `.reference` files next to their original prompt modules. Apache-2.0 license
text is in `vendor/LICENSE.Apache-2.0`. The original strategy references are not
imported or executed. ACE's ADD-only helper is imported unchanged.

## Why the schedule changes

FlashRSI algorithms call `await _evaluate(candidate, tasks)` inside their own
loop. HarnessGrad's `INTERFACE.md` §4 prohibits a method from evaluating benchmark
tasks; the platform owns execution, scoring and budget. Importing the complete
FlashRSI Algorithm unchanged would violate that boundary.

Instead, one method invocation reads `_harnessgrad/round.json`, `history/`,
`states/` and public task/trace pages. It selects a measured parent and returns
one or more complete candidates. The platform measures them, then calls the
method again with the resulting measured history. Search memory lives in each
point's `method_reported.flashrsi_port`, not an untracked temporary file.

Only `protocol.studied_score` / `studied_per_task` supply fitness. Exam points,
non-finite scores, refused rounds, invalid task sets and missing historical trees
are never silently substituted into a search. A historical tree that has been
omitted by `HG_METHOD_HISTORY_ROUNDS` refuses the proposal with a named reason;
unset that cap for full-archive policies.

A separate dev capability is not available in the ordinary method protocol.
Methods whose native admission uses dev therefore use a **declared study-score
adaptation**, not a renamed or secretly exposed held-out score. Native reference
answers are not shown to the optimizer. This materially changes the experiment
and is declared in each `method.json` fidelity profile.

## Per-method stage map

| Port | Native stages and source | Runtime implementation | Explicit difference |
|---|---|---|---|
| OPRO | `opro/strategy.py.reference`: `_propose`, `_render_history`, stable `_best` | Accumulate every measured instruction, threshold/sort history, propose a bounded novel batch, restore earliest global best | JSON instruction reply; platform study feedback and state budget |
| ADAS | `adas/strategy.py.reference`: `_propose` and two reflections before admission | Every measured design remains an archive account; propose from strict best, edit then reflect twice in successive copies | Shared-editor edits, study replaces dev, no native Agent tool-session preservation |
| ACE | `ace/strategy.py.reference`: `_reflect`, `_curate`, `_adopt`, `_validate` | Reflect once, apply helpful/harmful tags, native ADD-only parser, dedup/budget gate, append to loaded instruction; online keeps all valid additions, offline restores best | No target retry during reflection; `max_reflection_rounds=1` enforced; study replaces periodic dev |
| GEPA | `gepa/strategy.py.reference`: `_iterate`, `_select`; native `_population.py` | Exact per-task frontier/tie pruning, task-ownership-weighted parent, nonperfect-task reflection, strict or nondecreasing paired admission | Full-set study result substitutes fresh minibatch screen and dev pass; native unsupported merge remains refused |
| EvoPrompt | `evoprompt/strategy.py.reference`: `_initialize`, `_ga`, `_de`, `_member` | Measured population, wheel/tournament/random parents, crossover/mutation prompts, standard/top-k survivors, DE positional donors and strict replacement | Population phases span calls; truncated standard generations retain unmeasured old slots; final winner is global best |
| DarwinX | `darwinx/strategy.py.reference`: `_select`, `_assess` | Per-task positive gain, regression mass, solved invariant; accepted-only mutation parents; complementary rejected donors may merge; exploration/cumulative gain | Existing full-set platform measurement is not fresh screen/confirmation; repeated parent trials are not invented |
| Arbor | `arbor/strategy.py.reference`: `_coordinate`, `_execute`, `_backpropagate` | Bounded expand/run/prune/stop coordinator; run only depth>=2 leaves; execute from trunk; measured reflection and bottom-up synthesis | One study measurement replaces native train screen + dev gate; adapted JSON role output. **Two declared differences the coordinator has:** (a) `expand` is allowed on any unpruned node below `max_depth`, where native allows only `root`/`direction`/`done`/`merged` (`runtime.py:1193-1196` against `strategy.py.reference:187-191`) — the port's leaf lifecycle has `pending`/`needs_retry` in place of native's `direction`, so the two sets are not the same words; (b) native's rejection-feedback list is not carried into the next proposal, so a rejected action is retried blind rather than with its reason |
| ModularRSI | `modular_rsi/strategy.py.reference`: `_contrast`, `_evolve_branch`, `_execution_gate` | Explicit disjoint file modules, independent root branches, contrast actual repeated platform trials, scoped edits, integration from branch contents | Requires `--trials >=2` **and** an explicit `modules` map (both refuse with a named reason); no synchronous runtime gate or new role-directed rollouts; one fixed-set update per module epoch |
| SkillOpt | `skillopt/strategy.py.reference`: `_reflect`, `_consolidate`, `_slow_update`, `_select` | Success/failure role separation, merge/rank bounded exact edits, protected slow region, slow-guidance/meta-memory phase | `epoch_batches` schedules port epochs; no fresh same-task epoch comparison or separate dev gate |
| EvoHarness | `evo_harness/strategy.py.reference`: `_reflect`, `_compile`, `_skills` | One-shot failure memories; topic then general native `SKILL.md`; current-task provenance and cross-task minimum; managed ownership/budget checks | Fixed platform task set replaces disjoint online batches; structured compiler emits JSON-valid-YAML frontmatter |

`methods/flashrsi_ports/policies.py` contains independently testable selectors.
`runtime.py` reconstructs state and proposes candidate artifacts. Every new
method uses `methods/editor.py`; no port imports `eval`, the dataset registry,
the driver, or a private task key.

## Model / resource configuration

Use the existing method endpoint variables:

```bash
export HG_METHOD_BASE_URL=http://127.0.0.1:<your-reviewed-endpoint>/v1
export HG_METHOD_API_KEY=<key>
export HG_METHOD_MODEL=<model>
export HG_FLASHRSI_CONFIG='{"instruction_path":"prompts/system.md"}'
```

These commands are a configuration example, not authorization to contact a paid
provider or run a benchmark. Structured roles (reflection, ranking, curation,
coordination and instruction generation) call the existing editor with
`endpoint_only=True`. They require a configured JSON-capable endpoint; a CLI
improver's checkout-to-edit response cannot represent those schemas. Structured roles provide their declared prompt/skill resources and study feedback
explicitly, so an instruction-only update does not inline an unrelated 200 KB
runtime. Source-edit roles use the existing CLI-or-endpoint editor dispatch; an
endpoint edit still obeys the existing complete-source size guard. All observed usage comes
from that editor. Missing usage remains unknown; no port assigns a fake zero.

Prompt methods require a loaded `instruction_path`. Without an explicit path,
exactly one of `prompts/system.md`, `prompt.md`, `CLAUDE.md` or `AGENTS.md` must
exist. Ambiguity or absence refuses rather than editing a dead file or guessing
inside `agent.py`. A declared instruction can still be unused by an arbitrary
harness; this declaration is an operator responsibility, so the tests exercise
an actual harness that loads it. Skill methods edit `skills/**` resources; they
require a harness that actually loads native skills (the integrated MiniAgent is
one such harness).

`HG_FLASHRSI_CONFIG` is a JSON object. Defaults mirror the source where the
platform can implement them. Unsupported native knobs fail explicitly instead of
being accepted and ignored. Parameters used by the port are in `DEFAULTS` inside
`runtime.py`. Examples:

```bash
# DE needs >=4 measured population positions.
export HG_FLASHRSI_CONFIG='{"instruction_path":"prompt.md","variant":"de","population_size":4}'
# Module file ownership is explicit and disjoint.
export HG_FLASHRSI_CONFIG='{"modules":{"control":["agent.py"],"prompt":["prompt.md"]}}'
# SkillOpt's slow update cadence is explicit port configuration.
export HG_FLASHRSI_CONFIG='{"epoch_batches":2,"edit_budget":4}'
```

Each submitted batch is capped by the remaining platform state budget. Search
methods reserve the last state for **actual deployment of the best already
measured tree**, rather than only writing `kept_round` beside an inferior current
candidate. A run with only one state after H0 cannot both propose and receive a
post-measurement final-selection callback; it is a single-candidate diagnostic,
not a completed native search. Online additive ACE and EvoHarness deliberately
retain valid updates through score regressions. ModularRSI relies on the next
platform measurement for its execution gate; its native synchronous final gate
is not reproduced.

## Validation

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/quality/test_{ace,adas,arbor,darwinx,evo_harness,evoprompt,gepa,modular_rsi,opro,skillopt}_rule.py \
  tests/quality/test_flashrsi_method_integration.py \
  --basetemp=/tmp/hg-flashrsi-methods-tests
python3 tools/method_lint.py
```

The integration test uses a local scripted OpenAI-compatible endpoint and a
human-authored demo fixture. It drives the **real method sandbox**, platform
candidate measurement and final state restore. Expected scores are 0 → 1 → 0 → 1:
the final tree is the previously measured best, not an improver-reported number.
No real model, benchmark suite, GPU endpoint or paid provider is called.
