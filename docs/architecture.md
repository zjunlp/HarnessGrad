# Repository architecture

This is the repository map for the current source tree. Start with the
[area map](#area-map), then follow the links to implementation files. The
[interface specification](../INTERFACE.md) defines the measurement contract;
this page describes where its implementation lives and which way dependencies
should point.

Navigate: [areas](#area-map) · [execution runtime](#runtime-navigation) ·
[datasets](#dataset-navigation) · [import edges](#observed-import-edges-and-exceptions) ·
[known structural problems](#known-structural-problems).

`A -> B` means **A depends on B**, not that data flows from A to B. A
**leaf/contract** provides shared definitions or low-level helpers without
importing repository consumers. A **consumer** composes those definitions or
executes work. These are module-level roles: a package name alone does not
establish a boundary. The allowed directions below are maintenance rules;
[observed exceptions](#observed-import-edges-and-exceptions) are called out
explicitly rather than presented as a clean, enforced layering.

The enforced dependency directions, the observed import edges and their reviewed
exceptions are recorded separately in [import direction](import-direction.md),
with an automated guard in `tests/quality/test_import_direction.py`.

## Area map

| Area | Purpose and entry points | Role | Allowed dependency direction |
| --- | --- | --- | --- |
| [Root entry points](../driver.py) | `driver.py` parses arguments and composes dataset loading, scheduling, measurement and recording. [install.sh](../install.sh) provisions/checks prerequisites. | Consumers/composition roots. | `driver -> harnessgrad` orchestration/loops, `eval`, dataset registry, `ckpt` and operational helpers. Lower layers must not depend on the driver to implement ordinary execution. |
| [harnessgrad/runtime/](../harnessgrad/runtime/) | Eight-stage event types, recording, schema/semantic validation and the legacy-method compatibility wrapper; start at [events.py](../harnessgrad/runtime/events.py) and [validator.py](../harnessgrad/runtime/validator.py). | Leaf/contracts, despite the word “runtime.” | Consumers -> these contracts; their modules may use sibling definitions and external libraries, never `eval`, driver, tools or method implementations. See the package-initialization caveat below. |
| [harnessgrad/config/](../harnessgrad/config/) | Typed configuration declarations in [areas.py](../harnessgrad/config/areas.py), loading/snapshots in [loader.py](../harnessgrad/config/loader.py). | Leaf/contracts. | Runtime and orchestration -> config; config -> its own declarations and standard library. No imports back into runtime consumers. |
| [Top-level harnessgrad/*.py](../harnessgrad/) | Platform orchestration: [environments.py](../harnessgrad/environments.py), [methods.py](../harnessgrad/methods.py), [channel.py](../harnessgrad/channel.py), [records.py](../harnessgrad/records.py), identity and budget integration. | Consumers of `eval/` as an area, with contract aliases and standalone helpers mixed in. **Not a leaf package.** | Orchestration -> `eval`, config/contracts, `ckpt`, dataset registry and lower-level orchestration helpers. Do not infer permission for `eval ->` these orchestration modules. |
| [harnessgrad/loops/](../harnessgrad/loops/) | Scheduling in [mode_a.py](../harnessgrad/loops/mode_a.py) and [eval_side.py](../harnessgrad/loops/eval_side.py); Mode B coordination also lives in `driver.py`. There is no `loops/mode_b.py`. | Consumers. | Driver -> loops -> orchestration helpers, `eval.run.runner`, candidate validation and `ckpt`. Runtime primitives must not import schedules. |
| [harnessgrad/store/](../harnessgrad/store/) | Run-record validation, reading and certification in [schema.py](../harnessgrad/store/schema.py). | Leaf/record contract and reader. | Record consumers, validators and web read API -> store; store -> standard library, not execution/scheduling code. |
| [eval/](../eval/) | **Platform execution runtime, despite the name**: task execution, environments, model gateway, verification/scoring, admission, progress and traces. Start at [runner.py](../eval/run/runner.py). | Consumer of low-level contracts/helpers; provider to orchestration. Not a test suite. | Orchestration -> `eval`; `eval` -> runtime peers, config, `ckpt` and external execution backends. Keep method algorithms and scheduling above it. Existing imports from `tools` are listed below. |
| [data/](../data/) | **29 dataset adapters** with private assets, platform fixtures, and a separate **scaffolds/** directory for shared platform code. Start at [registry.py](../data/scaffolds/registry.py) and [qa/](../data/scaffolds/qa/). | Mixed: scaffold/contracts, adapter consumers and runtime-consuming controls. **Not a leaf directory.** | Driver/tools -> registry -> selected `data.datasets.<name>` adapter; adapters -> shared scaffolding. Controls may call `eval`; existing adapter/reporting coupling is listed below. Runtime should consume task/verifier declarations instead of discovering datasets itself. |
| [methods/](../methods/) | External improvement methods and controls, plus shared [protocol.py](../methods/protocol.py), editor/application helpers and per-method `run.py` entry points. | Consumers of method/harness exchange contracts; supplies candidates, not platform scores. | Platform invokes methods through subprocess entry points and JSON/artifacts. Methods -> their own helpers, supplied workspace/channel and declared model/tool dependencies; shared runtime compatibility contracts are available. Do not import method algorithms into the referee. |
| [base_harness/](../base_harness/) | Runnable starting artifacts: `loop`, `loop_plain`, `loop_rrsi_parse`, CLI/Codex/DSH wrappers, and `oracle` control. Each has `harness.json` and `agent.py`. See [writing a harness](writing_a_harness.md). | Consumers of task/model/file contracts; measured artifacts, not platform libraries. | Platform stages and executes manifest entry points; harnesses use their own code/dependencies and supplied task/environment contracts. No platform imports of harness implementation code. |
| [eval/ckpt/](../eval/ckpt/) | [git_state.py](../eval/ckpt/git_state.py) stages, commits, restores and materializes harness states and manifests. | Low-level helper leaf, inside the runtime since 2026-10-27 (it was a top-level `ckpt/`). | Driver, orchestration and execution -> `eval.ckpt` -> standard library/external Git. No reverse imports into runtime or orchestration. |
| [tools/](../tools/) | Operator CLIs/services for validation, datasets, model services, setup, reporting and inspection. [serve_ui.py](../tools/serve_ui.py) is the retained operations service; the UI is [`console/`](../console/). | Mostly consumers, with shared runtime helpers mixed in. Not a leaf area. | Tools -> platform APIs/contracts and artifacts; services may launch driver subprocesses. Runtime reuse of selected helpers already exists, so do not assume a strict one-way `tools -> eval` boundary. |
| [harnessgrad/improvers/](../harnessgrad/improvers/) | [improvers.json](../harnessgrad/improvers/improvers.json) catalogs improver configuration; [skill.md](../harnessgrad/improvers/skill.md) is the default method-facing guidance. | Configuration/reference leaves, not Python implementations. | Resolver/channel code -> these data files; these files do not import platform code. |
| [adapters/](../adapters/) | Historical external-method replay/compilation route, with a separate RRSI measurement integration in [harnessgrad_domain/](../adapters/harnessgrad_domain/). These are **not dataset adapters**. | Historical consumers; domain integration is a runtime consumer. | Replay/compiler scripts -> external artifacts and their own helpers. Domain integration -> driver setup, dataset registry and `eval.run.runner`; core execution must not depend on this route. See [its status](../adapters/README.md). |
| ~~`web/`~~ | **Deleted 2026-10-19** (commit `99163538`, 22,807 lines): the SPA and its read API were replaced by [`console/`](../console/), which reads the registry and `runs/` directly. The rows below that still name `web/server/launch.py` describe the deleted tree. | -- | Execution code never depended on the frontend; nothing in `eval/` or `harnessgrad/` imports it. |
| [site-redirect/](../site-redirect/) | Two static pages that keep the old project-page URL working: `index.html` sends `zjunlp.github.io/HarnessGrad/` to the new site, `404.html` maps a deep link onto the same path there. The site itself is **not in this repository** -- it is [`zjunlp/HarnessGrad-Hub`](https://github.com/zjunlp/HarnessGrad-Hub) since 2026-10-27, history included. | Leaf artifacts, no build step, no imports. | Nothing depends on them; nothing here changes a measurement. |
| [tests/](../tests/) | [quality/](../tests/quality/) covers platform boundaries/integrations; [runtime/](../tests/runtime/) covers event/budget APIs; [blockers/](../tests/blockers/) and [fixtures/](../tests/fixtures/) hold focused checks and test material. Web also has colocated tests. | Test consumers. | Tests -> subjects under test and fixtures. Production code should not import test helpers; one existing tooling exception is listed below. |
| [reports/](../reports/) | Audit reports, evidence/receipt JSON, test transcripts and review summaries, **moved out of the repository root**. | Audit/evidence leaves, not executable libraries. | Reviews/documentation -> evidence artifacts. Some runtime/admission paths consume evidence files explicitly; those are file/receipt dependencies, not Python imports. Preserve provenance and avoid treating historical reports as current measurements. |
| [docs/](./) and root documentation | Architecture, setup, contributor guidance, contract proposals and onboarding/audit material; [README](../README.md), [INTERFACE](../INTERFACE.md), [DOWNLOADS](../DOWNLOADS.md) and [AGENTS](../AGENTS.md) are root references. | Documentation/reference contracts and evidence. | Readers/contributors -> documentation; documentation links to source/evidence. Schemas and evidence loaded as files are explicit artifact contracts, not executable dependencies. |
| `legacy/` | **Absent in this checkout.** Historical external-method code remains under `adapters/`; compatibility wrappers are under `harnessgrad/runtime/`. | No current area to classify. | Do not invent a dependency or migration destination under this name. |

## Runtime navigation

The top-level package remains `eval`. Its 32 implementations are grouped by
concern; package initializers do not import implementations:

| Package | Modules | Responsibility |
| --- | --- | --- |
| [`eval/run/`](../eval/run/) | [`runner.py`](../eval/run/runner.py), [`candidate.py`](../eval/run/candidate.py), [`metrics.py`](../eval/run/metrics.py), [`integrity.py`](../eval/run/integrity.py), [`task_contract.py`](../eval/run/task_contract.py) | Task execution, candidate validation, measurement and shared task contracts |
| [`eval/containers/`](../eval/containers/) | [`container.py`](../eval/containers/container.py), [`container_state.py`](../eval/containers/container_state.py), [`sandbox.py`](../eval/containers/sandbox.py), [`harness_runtime.py`](../eval/containers/harness_runtime.py) | Container execution/state, host sandbox and harness dependency overlays |
| [`eval/admission/`](../eval/admission/) | [`pilot_admission.py`](../eval/admission/pilot_admission.py), [`artifact_admission.py`](../eval/admission/artifact_admission.py), [`runner_admission.py`](../eval/admission/runner_admission.py), [`scorer_admission.py`](../eval/admission/scorer_admission.py), [`scorer_bundle.py`](../eval/admission/scorer_bundle.py), [`scorer_backend.py`](../eval/admission/scorer_backend.py), [`lifecycle_receipt.py`](../eval/admission/lifecycle_receipt.py) | Pilot, artifact, runner and scorer admission evidence and lifecycle receipts |
| [`eval/service/`](../eval/service/) | [`services.py`](../eval/service/services.py), [`modelgate.py`](../eval/service/modelgate.py), [`unix_model_relay.py`](../eval/service/unix_model_relay.py), [`separated_stdio.py`](../eval/service/separated_stdio.py), [`stepwise_transport.py`](../eval/service/stepwise_transport.py) | Service lifecycle, model gateway, relay and stepwise/stdio transports |
| [`eval/observe/`](../eval/observe/) | [`console.py`](../eval/observe/console.py), [`progress.py`](../eval/observe/progress.py), [`trace.py`](../eval/observe/trace.py), [`tracing.py`](../eval/observe/tracing.py), [`phases.py`](../eval/observe/phases.py) | Phase vocabulary, console/progress reporting and trace writing/reading |
| [`eval/policy/`](../eval/policy/) | [`diagnostic_policy.py`](../eval/policy/diagnostic_policy.py), [`runtime_policy.py`](../eval/policy/runtime_policy.py), [`appworld_reporting.py`](../eval/policy/appworld_reporting.py), [`gaia_preflight.py`](../eval/policy/gaia_preflight.py), [`gaia_snapshot_attestation.py`](../eval/policy/gaia_snapshot_attestation.py) | Diagnostic/runtime policies, AppWorld reporting and GAIA metadata review |
| [`eval/verification/`](../eval/verification/) | [`verifier.py`](../eval/verification/verifier.py) | Execute declared checks and interpret verdicts |

The grouping follows the [pre-move AST audit](../reports/eval-package-refactor-e1.md):
`phases` is a dependency-free vocabulary consumed by observation modules;
`harness_runtime` depends on container execution; `stepwise_transport` is a
transport helper; `verifier` composes environment, service and runtime-policy
helpers. `runner` remains the execution composition root. Package-level cycles
still reflect existing cross-concern imports, especially container state using
run contracts and verification. This move does not impose a new dependency DAG.

### Compatibility import paths

All production importers use canonical implementation paths. The following old
paths remain **thin compatibility shims**, each aliasing the same module object
as the corresponding implementation. Private names, module state and patch
targets therefore remain shared rather than copied with a wildcard re-export.
`containers` and `service` deliberately avoid taking over the old module names
`eval.container` and `eval.services`.

| Old paths (all are shims) | Canonical prefix (same module basename) |
| --- | --- |
| `eval.runner`, `eval.candidate`, `eval.metrics`, `eval.integrity`, `eval.task_contract` | `eval.run.` |
| `eval.container`, `eval.container_state`, `eval.sandbox`, `eval.harness_runtime` | `eval.containers.` |
| `eval.pilot_admission`, `eval.artifact_admission`, `eval.runner_admission`, `eval.scorer_admission`, `eval.scorer_bundle`, `eval.scorer_backend`, `eval.lifecycle_receipt` | `eval.admission.` |
| `eval.services`, `eval.modelgate`, `eval.unix_model_relay`, `eval.separated_stdio`, `eval.stepwise_transport` | `eval.service.` |
| `eval.console`, `eval.progress`, `eval.trace`, `eval.tracing`, `eval.phases` | `eval.observe.` |
| `eval.diagnostic_policy`, `eval.runtime_policy`, `eval.appworld_reporting`, `eval.gaia_preflight`, `eval.gaia_snapshot_attestation` | `eval.policy.` |
| `eval.verifier` | `eval.verification.` |

The existing `trace` adapter itself is unchanged in `eval.observe.trace` and
continues to re-export the canonical reader in `eval.observe.tracing`.
The four old module CLIs (`eval.gaia_preflight`, `eval.harness_runtime`,
`eval.scorer_bundle`, `eval.separated_stdio`) delegate to their canonical modules
when invoked with `python -m`. `eval/diagnostic_receipt.schema.json` keeps its
artifact path. GAIA's source-identity-v1 required-path set keeps its historical
paths: those shim files still exist, and full tree manifests include both shims
and implementations without changing historical-manifest validation.

The separate [eight-stage runtime guide](eight_stage_runtime.md) describes the
event protocol implemented by `harnessgrad/runtime/`. That contract layer is
distinct from the execution engine in `eval/`.

## Dataset navigation

The count **29** comes from `data/datasets/*.py` modules declaring
`KIND = "dataset"`, not from counting directories or assuming every Python file
is an adapter. It includes refused/unavailable integrations; it is not a claim
that 29 datasets are ready to run. [registry.published()](../data/scaffolds/registry.py)
discovers declarations through imports, while the web read API's
[launch-option scanner](../web/server/launch.py) parses each module's AST for a
`KIND = "dataset"` assignment instead. `docs/dataset-onboarding.md` §2 names
`registry.published()` as the one answer to "which datasets exist", and the AST
scanner exists only because the Launch form must not import a module to describe
it: the earlier zero-argument-`load()` test it replaced dropped the real dataset
`musique` (whose `load` is keyword-only) and offered platform fixtures.

The dataset adapter modules are:

| Dataset adapter | Dataset adapter | Dataset adapter | Dataset adapter |
| --- | --- | --- | --- |
| [agentdojo](../data/datasets/agentdojo.py) | [alfworld](../data/datasets/alfworld.py) | [api_bank](../data/datasets/api_bank.py) | [appworld](../data/datasets/appworld.py) |
| [bfcl_v3](../data/datasets/bfcl_v3.py) | [bird_minidev](../data/datasets/bird_minidev.py) | [chessbench_puzzles](../data/datasets/chessbench_puzzles.py) | [ds1000](../data/datasets/ds1000.py) |
| [fever](../data/datasets/fever.py) | [gaia](../data/datasets/gaia.py) | [gsm8k](../data/datasets/gsm8k.py) | [hotpotqa](../data/datasets/hotpotqa.py) |
| [jericho](../data/datasets/jericho.py) | [livecodebench](../data/datasets/livecodebench.py) | [musique](../data/datasets/musique.py) | [nestful](../data/datasets/nestful.py) |
| [physicsquestions](../data/datasets/physicsquestions.py) | [reasoning_gym](../data/datasets/reasoning_gym.py) | [scienceworld](../data/datasets/scienceworld.py) | [sotuqa](../data/datasets/sotuqa.py) |
| [sportsunderstanding](../data/datasets/sportsunderstanding.py) | [spreadsheetbench](../data/datasets/spreadsheetbench.py) | [strategyqa](../data/datasets/strategyqa.py) | [tau2_bench](../data/datasets/tau2_bench.py) |
| [tau_bench](../data/datasets/tau_bench.py) | [terminal_bench](../data/datasets/terminal_bench.py) | [triviaqa](../data/datasets/triviaqa.py) | [twowikimultihopqa](../data/datasets/twowikimultihopqa.py) |

Shared infrastructure is separated under `data/scaffolds/`: registry/split handling,
[dataset.schema.json](../data/dataset.schema.json), [qa/base.py](../data/scaffolds/qa/base.py)
and QA verification/prediction helpers, [command_control.py](../data/scaffolds/command_control.py),
and the [command oracle harness](../data/scaffolds/command_oracle_harness/).
[demo.py](../data/demo.py), [probe_set.py](../data/probe_set.py) and
[broken_service.py](../data/broken_service.py) explicitly declare fixtures.
Shared code belongs only in `data/scaffolds/`; adapters may import it and their own assets, never another adapter. Same-stem directories such as `data/datasets/gaia/` contain supporting assets/scripts;
their presence does not add another dataset to the module count.

## Observed import edges and exceptions

The source tree was checked using Python AST imports (including relative and
function-local imports), with dynamic dataset loading and subprocess/file
boundaries inspected separately. The following concrete edges explain why a
single directory-level dependency DAG would be misleading:

- **Execution consumers:** `harnessgrad/environments.py -> eval.containers.container` and
  `eval.containers.harness_runtime`; `harnessgrad/records.py -> eval.run.metrics` and
  `eval.observe.tracing`; `harnessgrad/channel.py -> eval.observe.tracing`.
  Loops call `eval.run.runner`. These modules are above execution, not leaves.
- **Leaf initialization caveat:** source files in `harnessgrad/runtime/`,
  `config/` and `store/` do not import repository consumers. However,
  [harnessgrad/__init__.py](../harnessgrad/__init__.py) eagerly exports
  `trusted_runner`, which imports `eval.run.candidate`. An ordinary import of a
  leaf subpackage executes that parent initializer. Their source-level leaf
  status therefore does not imply an execution-free transitive import.
- **Top-level exceptions:** `protocol.py`, `events.py`, `validator.py` and
  `compat.py` re-export `harnessgrad.runtime` APIs; `budget_dispatch.py`,
  `code_metrics.py` and `policies.py` are standalone helpers. Treating all
  `harnessgrad/*.py` as consumers or all as contracts would both be inaccurate.
- **Runtime/tool back-edges:** `eval.run.candidate -> tools.validate_harness`,
  `eval.run.runner -> tools.improver`, and `eval.run.runner`/`container_state ->
  tools.web_gateway`. Tools also import runtime APIs. These reusable helpers
  currently live in the CLI directory.
- **Dataset/runtime coupling:** `data/datasets/appworld.py -> eval.policy.appworld_reporting`;
  `data/scaffolds/command_control.py` and several dataset `control.py` scripts call
  `eval.run.runner`. The registry dynamically imports `data.datasets.<name>`. Thus neither
  all of `data/` nor the whole adapter/control surface is a leaf.
- **Methods and tests:** method scripts use shared local modules via their
  script search paths; `methods/compatibility.py` imports the runtime contract
  wrapper. [tools/validate_method.py](../tools/validate_method.py) imports
  `methods.protocol`, not a method algorithm.
  [tools/check_triviaqa_provenance.py](../tools/check_triviaqa_provenance.py)
  imports a test helper, an existing exception to the test-consumer direction.
- **Web boundary:** `web/server/readapi.py -> harnessgrad.store.schema`;
  [launch.py](../web/server/launch.py) reads catalog/config files and invokes
  `driver.py --help` rather than importing the driver. The SPA consumes the
  resulting HTTP records or fixture records.

[Boundary tests](../tests/quality/test_boundaries.py) check specific platform
rules, including avoiding imports of method/harness implementations. They do
not establish every architectural direction in this map.

## Known structural problems

- **`adapters/` historical route:** its replay/compiler modules remain in an
  active-looking top-level directory. Its README marks the route unmaintained,
  while `harnessgrad_domain/` is explicitly excepted as the RRSI measurement
  integration. The name also collides conceptually with dataset adapters in
  `data/`.
- **`data/` separation:** shared registry/QA/control code lives in `data/scaffolds/`;
  fixtures stay at the top level; 29 adapters and their private assets live in `data/datasets/`.
  Moving or classifying the directory as a unit obscures their different roles
  and the existing control/reporting dependencies on runtime.
- **`eval/` naming and cross-concern dependencies:** seven subpackages now
  separate the implementations, with old paths retained as compatibility shims.
  The package name still suggests evaluation-only code although it contains the
  execution engine; the move preserves existing cross-concern dependencies.
- **Trace compatibility names:** `eval.observe.trace` remains the existing
  compatibility adapter for `eval.observe.tracing`; its old top-level path is
  also a shim. New callers should use `eval.observe.tracing`.
- **`harnessgrad` half-leaf, half-consumer naming:** contracts/configuration
  and orchestration share one package; public aliases further blur the roles.
  The parent initializer additionally pulls execution code into leaf imports.
- **Root cleanup now under `reports/`:** audit/evidence files now live there;
  cleanup commit `ad57926` updates references to that location. Root-level
  report paths are historical; `reports/` is the evidence destination. Audit material also remains in
  `docs/`, so the move has not created a complete documentation/evidence split.
  Generated `runs/`, dataset payloads and web build output are separate from
  this source map; see [.gitignore](../.gitignore).
