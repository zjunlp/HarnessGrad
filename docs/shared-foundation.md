# Shared foundation for harness workflows

HarnessGrad has an established experiment protocol and is exploring customization and personal evolution. They may reuse mechanisms but must retain separate evidence access, execution policies and selection rules. The current experiment implementation and its isolation boundary remain authoritative.

## Workflow responsibilities

The shared layer provides reusable mechanisms; each workflow remains responsible for its own inputs, execution and decision to adopt a candidate.

| Concern | Experiments | Customization | Personal harness integration |
| --- | --- | --- | --- |
| Input and feedback | Existing study channel and measured history | Explicitly selected local text and caller-labeled feedback | Future integration must define user-selected sources and access policy |
| Proposal mechanisms | Existing method entry points and experiment-specific selection | Reusable proposal stages composed for the supplied surfaces | Can call shared proposal stages with its own feedback and callback |
| Execution and evaluation | Existing sandbox, datasets, scoring and receipts | No task execution or effectiveness measurement in the proposal command | Not supplied by the shared stages |
| Candidate adoption | Existing experiment measurement and selection | Candidate JSON and diff for review; no automatic application | Future integration must define review, adoption and recovery |

The [repository map](architecture.md#area-map) identifies the current module owners. Remaining module aliases support existing callers during migration; removed imports are listed in the [migration guide](import-migration.md). Experiment provenance remains bound to the platform content that produced it.

## First extracted unit

`harnessgrad.core.components` owns the existing component declaration and change-attribution implementation. The former `harnessgrad.components` import now fails with migration guidance; use the core path. A refusal guard prevents fallback to a stale editable installation. Attribution reports paths a manifest cannot place; it does not grant or restrict edits.

`harnessgrad.core.harness.describe_harness` reads a harness manifest without executing it and returns a description of declared identity, environment kinds and component-to-file mappings. `attribute_changes` applies the existing attribution rules. This API does not admit a harness, establish that a runtime loaded a component, or prove any improvement. Identity and components come from the same parsed manifest read. A caller must still supply a trusted directory and manage concurrent changes; a filesystem read is not an authenticated snapshot.

`harnessgrad.core.snapshot` owns the existing portable Harness snapshot format and verification. It can be reused independently of experiment scheduling; adoption and recovery remain workflow decisions.

`harnessgrad.core.code_metrics.measure` provides the existing static Python diagnostics to any workflow without executing a Harness. Its output is a name-based approximation, not evidence that code ran or that a candidate improved.

## Method declarations

`harnessgrad.core.methods.discover_methods(methods_root)` inspects existing `method.json` declarations and `run.py` presence without importing methods, contacting models or invoking experiments. `describe_method` inspects one directory. Missing or malformed metadata is reported, and absent `requires` remains unknown rather than being treated as no requirements. A declaration records what the current method port says, not whether it can operate in a new workflow. In particular it does not express every method's scored-history requirements. The existing Console catalogs and experiment selection path are unchanged. `harnessgrad method list`, `harnessgrad method list --json`, and `harnessgrad method show skillopt` expose the same read-only declarations for any workflow; they do not launch a method or validate model credentials.

## Method exchange

`harnessgrad.core.method_transport` is the workflow-neutral JSON wire encoding and exited-process response decoder used by the existing experiment method caller. It accepts a request already assembled by its caller and a completed process result; it neither launches a process nor provides access to evidence. Each workflow must define its own request projection, executor, isolation, timeout, and candidate validation. The experiment caller still owns its existing sandbox, credential handling, runtime preparation, protected-file checks and error handling. Successful JSON values and error truncation retain the legacy behavior, including non-object JSON; workflows must validate outputs before use.

## Reusable SkillOpt proposal stage

`methods.policy_ports.skillopt_fast.propose_fast` contains the existing port's fast reflection, merging and edit ranking. The experiment adapter still selects its measured parent, classifies tasks from real study scores, supplies the existing evidence projection and editor, applies the existing protected-region patch rules, and leaves candidate selection to later platform measurement. The extracted function accepts explicit failure/success batches and injected callbacks, without requiring numeric scores or importing benchmark code. A different workflow can supply its own authorized feedback and callbacks; this is a proposal stage, not a complete SkillOpt search or an approval to modify live user files. The existing sandbox mounts `methods/`, so it can load the extracted module without exposing `harnessgrad` or private evaluator data. The matching `methods.policy_ports.skillopt_patch.apply_skill_patch` provides the original in-memory bounded edits and slow-guidance protection without importing the experiment runtime. It does not write or publish a candidate, and it is not a generic path authorization boundary.

## Reusable ACE proposal stage

`methods.policy_ports.ace_proposal.propose_playbook` extracts the existing ACE port's reflection, usage tagging and ADD-only curation into a callback-driven function. Its caller supplies authorized evidence, a model callback and the existing native playbook parser; no numeric score, task execution or file publication is supplied by this stage. The experiment adapter still loads the same pinned parser, selects its own parent, copies the candidate and lets later platform measurement determine selection. This is a proposal stage, not a complete ACE training loop or a guarantee that unscored feedback produces an improvement.

## Reusable EvoHarness memory and skill stage

`methods.policy_ports.evo_harness_proposal.propose_skills` extracts the existing port's per-failure reflection, topic/general skill compilation, source-ID checks and managed-library limits. The experiment adapter still selects the measured parent, identifies nonperfect tasks, supplies study evidence and performs candidate writing and subsequent platform measurement. `harnessgrad.improvement.stages.evo_harness_proposal` adapts the same stage to caller-labeled feedback and an explicit model callback. It returns model-generated memory records and a candidate skill library in memory, and refuses model attempts to replace caller-supplied feedback IDs. Those records are not verified facts, and no candidate is evaluated or applied. The caller controls data access, model transport, evaluation and adoption. This is a partial adaptation, not the full EvoHarness algorithm or evidence of improvement.

## Reusable GEPA instruction proposal

`harnessgrad.improvement.gepa_proposal` adapts the GEPA port's instruction reflection prompt and response validation to caller-selected failure feedback. It returns an unevaluated instruction and diff; it does not run GEPA's scored parent selection or Pareto frontier, execute a task, or adopt a candidate. The experiment port uses the same prompt and response helper while retaining its existing selection, evidence and measurement. The [pre-refactor control](../tests/fixtures/policy_ports/pre_refactor_gepa.json) records one scripted prompt, candidate and report from the LightRSI fix.

## Customization proposal composition

`harnessgrad.improvement` exposes `Feedback`, `index_feedback`, `is_relative_path`, `selected_surfaces` and `text_diff` as reusable input and text contracts, and `Proposal`, `MemorySkillProposal`, `skillopt_proposal`, `ace_proposal` and `evo_harness_proposal` as the shared in-memory API. For example, `from harnessgrad.improvement import Feedback, skillopt_proposal` gives a workflow the feedback type and a proposal stage. A workflow supplies its own feedback and model callback; each stage loads its method mechanism only when called. The stages do not collect personal sessions, evaluate or apply changes. Offline customization input collection and review use the contracts without importing the methods package or model transport. The former `harnessgrad.customization.contracts` import now directs callers to the shared contracts. Customization composes these stages at its workflow edge; experiment implementations remain separate.

The experiment port bridge also loads each extracted stage on demand. Loading an unrelated policy does not require the ACE, SkillOpt or EvoHarness proposal modules. Its generic path check retains the fix-branch implementation, while the three affected policies continue to own their original parent selection, evidence projection and candidate measurement.

`harnessgrad.customization.proposals.propose_customization` is an in-memory composition boundary for customization: the caller supplies selected feedback, a model callback and skill and/or instruction text. It uses SkillOpt's fast reflection/editing for skill files and ACE's reflection/curation for a separate instruction file. No method selection is required from the end user. The returned candidate contains a diff and stage provenance; it is not measured, automatically adopted or written to disk. This is an initial fixed composition of two existing port stages, not a complete method, intelligent method router, deployment workflow or evidence of improvement. A failure raises rather than returning a partial candidate. The caller must control what feedback is passed to the model, budget model calls, validate and evaluate candidates, and handle user review and publication. The `harnessgrad customize collect` command reads explicitly selected files offline; `harnessgrad customize propose` supplies an explicit endpoint callback and logical call limit. `harnessgrad customize propose --with-memory` optionally adds the shared memory/skill stage; `--refine-instruction` adds a failure-focused instruction rewrite. Neither option requires a method choice. `harnessgrad customize review` checks proposal consistency offline, can compare selected source files, and can optionally attribute changed paths using declared components. `harnessgrad customize patch` exports a source-checked patch without applying it. Neither evaluates the candidate. See [customization proposals](customization.md). Experiment method paths and policies are unchanged.

## Offline composition example

From a source checkout, run `PYTHONPATH=. python examples/shared_improvement.py` to compose the shared skill-edit, memory/skill and instruction stages on one synthetic feedback item. The example uses fixed scripted replies in place of a model and prints candidate JSON; it does not read personal data, call a service, write a Harness, evaluate a task or adopt a candidate. Real integrations must provide their own authorized feedback, model callback, evaluation and adoption policy.

## Pre-refactor policy controls

Offline controls compare the ACE and SkillOpt experiment ports with [recorded output from the pre-refactor main revision](../tests/fixtures/policy_ports/README.md). They cover selected scripted prompts, candidate files and reports, not every configuration or full experiment execution.

## Dataset runtime paths retained as contracts

The dataset provider, runtime preparation and runtime identity live at `harnessgrad.datasets.provider`, `harnessgrad.datasets.runtime` and `harnessgrad.datasets.runtime_identity`. The old provider and identity imports refuse with migration guidance; `harnessgrad.dataset_runtime` now refuses with migration guidance; production adapters use the canonical runtime path. The runtime identity's H0 subprocess uses the new module name while its serialized envelope remains unchanged. ChessBench, MATH, MATH-500, LoCoMo, Reasoning Gym, StrongREJECT, AgentDojo, ALFWorld, Jericho, ScienceWorld and both SpreadsheetBench versions record declared data source, loader and scorer separately. Older combined identities remain valid historical records, and records missing the separated evidence cannot silently be reused as equivalent. The source-only [ChessBench](../tests/fixtures/chessbench_provider_identity/README.md), [MATH](../tests/fixtures/math_scoring_identity/README.md), [LoCoMo](../tests/fixtures/locomo_scoring_identity/README.md), [SpreadsheetBench](../tests/fixtures/spreadsheet_scoring_identity/README.md), [Reasoning Gym](../tests/fixtures/reasoning_gym_scoring_identity/README.md), and [StrongREJECT](../tests/fixtures/strongreject_scoring_identity/README.md) identity fixtures record older identities without containing scores. See the [import migration guide](import-migration.md).

The provider's import change alters its source bytes and thus ChessBench's loader fingerprint. It does not by itself indicate a changed CSV or scoring algorithm; H0 and cache admission still conservatively treat the changed loader as a different component. The old dataset-runtime import now refuses with migration guidance; other adapters without separated identities do not gain a comparability claim from the import change.

## Measurement identity during refactoring

The experiment platform content digest includes paths and bytes under `harnessgrad/`, `tools/`, `docs/` and other protected roots. Refactoring these files changes that identity even when execution and scoring semantics are preserved. Historical receipts remain unchanged, and a refactored checkout must not silently reuse an old baseline, cached score or checkpoint whose existing admission checks reject its provenance. A fresh compatible baseline or explicitly verified migration is necessary; passing focused offline tests does not establish comparability across platform identities. The integrity controls check that the relocated implementations themselves, as well as the retained compatibility files, remain inside the platform snapshot; a change to a relocated implementation changes the content digest.

## Boundaries for later work

- **Experiments** retain the current driver, method subprocess protocol, isolated feedback channel, scoring and receipts. Historical runs must not be rewritten.
- **Customization** may eventually obtain domain material or build environments under its own explicit execution and evidence policy. It cannot silently reuse benchmark-private material.
- **Personal evolution** is not implemented here. A future integration needs user-selected sources, per-harness capability checks and candidate review and recovery.
- A method's proposal logic is distinct from its selection strategy. Existing methods may require staged, scored experiment history. Do not invent scores, pass private data or advertise a partial method execution as the full published algorithm.
- Methods currently run with only `methods/` and explicitly declared files visible in their sandbox. Moving their dependencies into `harnessgrad.core` requires an explicit packaging, sandbox visibility and compatibility design; importing the package from a method directly would break that boundary.

## References examined

- [Harness² Substrate](https://github.com/google-research/harness2/blob/6868817518876de8fd170c6a8354daca273b6191/harness2/substrate/base.py): separates editable surface, materialization, execution and trajectory reading. Its contracts are a design reference, not a dependency.
- [Harbor agent capabilities](https://github.com/laude-institute/harbor/blob/main/src/harbor/agents/capabilities.py): declares capabilities separately from the execution of a trial. A declaration should not be treated as an observed result.
- [PluginRSI plugin contracts](https://github.com/syr-cn/PluginRSI/blob/bafab39e4c62b7f0aa252f2ffd891886b45c82f8/src/pluginrsi/contracts.py): separates role, skill, tool and memory interfaces. HarnessGrad's current component attribution does not imply these interfaces exist in every external harness.
