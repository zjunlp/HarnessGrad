# Shared-foundation offline control

This record describes selected synthetic controls for the directory and shared-mechanism refactor. It is not a benchmark result, an equivalence proof for every experiment, or permission to reuse a baseline from another platform identity.

## Follow-up: real Qwen GSM8K smoke comparison

On 2026-11-17, with explicit user authorization, isolated copies of LightRSI fix `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` and refactor `92b05df4` each ran the existing Loop + DGM + GSM8K recipe against the same locally served Qwen3.5-9B model. Both used a copied dataset, separate run and work directories, one worker, one trial, seed 0, `random_5`, one improvement round and the same explicit Loop limits of eight steps and 1,024 output tokens. The runs were sequential and did not reuse each other's run directory or H0 records.

Both runs reported complete coverage and measured five tasks in each of rounds 0 and 1, with zero invalid, failed-harness or timed-out tasks. The selected task IDs, per-task scores, and initial and candidate Harness hashes matched; the score was 0.2 in both rounds of both runs. Each run recorded one editor model call and an `agent.py` candidate change. These are two observed runs under one small configuration, not evidence of method effectiveness or equivalence across other models, datasets and runtimes. The original local run records and logs remain outside the repository under `/tmp/hg-qwen-compare-2S3WQUfS/`; they are not shipped as public evidence. The two runs have different platform content identities, and this subsequent documentation update changes that identity again. Do not silently reuse either run as an H0 baseline for the updated tree.

## Source and scope

On 2026-11-16, the original main checkout was at `1eedb4c5be7104025c8eb89c3782323b7cbdf0b6`. The refactor checkout was at `d988eec67242a7a94c1bdaedcc9cb402f829b260`, with an uncommitted `docs/test-suite.md` whose SHA-256 was `652d4de373614285f44a64bc93b021e78f9262f86e33bddb404d886ea337e1cb`. These documentation files participate in platform identity; the result must not be attributed to the unmodified commit or this later documentation revision.

The five shared test files below passed **23 tests** on each checkout, using separate external temporary directories:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/hg-refactor-e2e-control \
  tests/quality/test_resume_train_e2e.py \
  tests/quality/test_experiment_integration.py \
  tests/quality/test_native_method_integration.py \
  tests/quality/test_check_first_use.py \
  tests/quality/test_evidence_runtime_integration.py
```

The controls cover authored demo tasks and mock execution, resume and record wiring, configuration expansion, a scripted local method endpoint with the method sandbox, and synthetic evidence projections. They do not call a real model or run a real benchmark. The two checkouts passing the same tests is useful regression evidence, not a byte-for-byte comparison of all run records.

The retained synthetic records from `test_resume_appends_instead_of0` and `test_resume_with_nothing_to_do0` were also compared directly between the two checkouts. The selected projections matched for all three and two curve points respectively: task selection and per-task scores, aggregate scores and coverage/failure counts, scorer-isolation and verification context, candidate tree hash, method metadata and completion status. Both scenarios used the authored demo and noop control; their observed scores were zero. Timestamps, costs, paths and platform content identity were not compared. The temporary records are not shipped as historical run receipts.

The refactor invocation also included `tests/quality/test_migration_provenance_integrity.py`, producing **25 passed** in total. The applicable historical source was fetched separately as described in the [test guide](../test-suite.md#historical-migration-source-check); that test checks pinned evidence bytes, not runtime behavior.

## Follow-up after experiment and customization layering

On 2026-11-16, the same five synthetic test files were rerun on clean refactor commit `afa0c1c748ef013c9ac375877d87e578b9ec5490`, together with the historical-source, ACE/SkillOpt and EvoHarness pre-refactor compatibility controls. The combined invocation passed **29 tests**. The prior main-checkout controls above remain at `1eedb4c5be7104025c8eb89c3782323b7cbdf0b6`.

The original refactor history containing the cited commit IDs is retained locally under `refs/harnessgrad/backups/refactor-pre-consolidation-20261116`; that ref is not automatically published with the consolidated branch. The two newly generated synthetic resume records were compared with the retained main-checkout records using the same selected fields described above. The three-point and two-point projections matched, including candidate hash and completion status. The policy controls also compare selected scripted prompts, candidate contents and reports to captured pre-refactor fixtures; they do not exercise every method configuration. These checks remain synthetic and do not establish full real-dataset or real-model compatibility.

## Consolidated commit checks

After grouping the local history into ten commits, each resulting commit was checked in a temporary detached worktree with its own external pytest temporary directory. The original pre-consolidation history remains locally under `refs/harnessgrad/backups/refactor-pre-consolidation-20261116`. At the time of consolidation, before this follow-up documentation update, the final committed tree matched the original pre-consolidation tree exactly. This validation document had an additional uncommitted update during the checks.

| Commit | Selected controls | Result |
| --- | --- | --- |
| `96d0ccb1` | Documented command paths and flags | 6 passed |
| `081ec05c` | Shared SkillOpt and ACE stages | 6 passed |
| `8e4d5214` | Execution and provenance import compatibility | 15 passed |
| `a43bc760` | Customization CLI and review | 22 passed |
| `dc8f5e16` | EvoHarness shared stage and pre-refactor comparison | 8 passed |
| `a3d431e9` | Harness setup entrypoints | 21 passed |
| `a38deb2d` | Relocated implementation integrity | 1 passed |
| `0caf5381` | Test tooling and result-analysis entrypoints | 36 passed |
| `9e2e4c95` | Experiment module compatibility and shared example | 8 passed |
| `13142088` | Customization patch and CLI | 26 passed |

These are targeted controls for commit usability, not complete suites for each commit. The temporary checkout was removed after verifying it was clean; the original logs remained outside the repository at the time of this check. These commit IDs describe the consolidated local history and will change if it is rewritten again.

## Cross-layer offline regression

On 2026-11-16 the clean refactor checkout at `73762daefd20a52c98def4490618889a42948ab9` ran the following selected controls: **579 passed**, with 13 warnings. The set includes synthetic experiment resume and sandbox controls, pre-refactor policy comparisons, customization and patch handling, import compatibility, test tooling and platform integrity. It does not invoke a real model or benchmark. The checked tree and the following documentation update have different platform content identities.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/hg-layered-regression-control \
  tests/quality/test_{core_harness,core_methods,core_method_transport,improvement_import_isolation,shared_improvement_example,customization_inputs}.py \
  tests/quality/test_{customization_proposals,customization_review,customization_patch,customization_cli,policy_port_pre_refactor_compat,evo_harness_pre_refactor_compat}.py \
  tests/quality/test_{evo_harness_shared,ace_rule,skillopt_rule,evo_harness_rule,method_lint,endpoint_client_offline}.py \
  tests/quality/test_{experiment_package_compat,execution_package_compat,python_runtime_package_compat,provenance_package_compat,evidence_package_compat,analysis_entrypoints}.py \
  tests/quality/test_{visualization_entrypoints,harness_setup_entrypoints,testing_entrypoints,experiments,experiment_presets,experiment_integration}.py \
  tests/quality/test_{custom_splits,resume_train_e2e,native_method_integration,evidence_runtime_integration,check_first_use,ci_select}.py \
  tests/quality/test_{ci_workflow,quality_receipts,import_direction,integrity,migration_provenance_integrity}.py
```

Use a fresh external `--basetemp` for each run. The historical-source test requires the recorded source commit described in the [test guide](../test-suite.md#historical-migration-source-check); without it that check skips. This selected set excludes the known baseline failures and controls requiring the unavailable Loop wheel described below; it is not a whole-suite result.

## Loop dependency preparation attempt

On 2026-11-16 an explicitly network-enabled preparation was attempted with `tools/prepare_loop_test_runtime.py --allow-downloads`, using a newly owned cache and runtime store under `/tmp`. The package fetch failed after three retries with a TLS handshake EOF; no managed Loop runtime was produced. The failed preparation directory was removed. A globally installed `openai==2.21.0` was visible, but it is not a replacement for the original locked wheels and isolated runtime required by these controls. The real-wheel Loop tests therefore remain unverified in this environment.

## LightRSI correctness branch integration

The local refactor head `2fa1982e` was merged with `fix/lightrsi-platform-correctness` commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8`. That branch also brings its main-branch ancestors through `8264a916`; the merge does not alter the separate main checkout. Budget-dispatch changes were placed in the canonical execution module, and OpenClaw setup changes in the canonical setup module. Their contents match the fix branch, apart from the OpenClaw repository-root depth required by its new directory. Structured method termination and usage are retained alongside the shared JSON decoder.

Selected offline controls passed in four runs: **110** for import boundaries, H0 subset, treatment identity, method admission and HTTP budget; **222** for budget, evidence, credential and resume behavior; **184** for Harness setup, packaged resources and customization compatibility; and **204** for editor, source/evidence isolation, improver and related controls. These counts overlap and are not whole-suite coverage. The first editor run exposed that `/usr/bin/python3` on this host was 3.10, below the project's 3.12 minimum; the test now uses an isolated copy of the supported interpreter's standard library when necessary, without mounting installed site packages. The final editor run passed.

These controls use authored fixtures, mock execution or scripted loopback endpoints. They do not establish compatibility for every real dataset, model, external Harness or deployment. The separately requested identity-semantics change and dataset-provider migration are not represented by this merge control.

## Dataset provider import migration

After the merge, the provider implementation was moved to `harnessgrad.datasets.provider`. A byte comparison against the merge commit's previous implementation matched after normalizing the one repository-root depth adjustment. A selected offline run of the provider, adapter, identity, import-boundary, wheel-resource, CI-selection and integrity tests reported **329 passed, 40 skipped, 2 failed**. It used an external pytest temporary directory and no real benchmark or model.

The failures are not counted as passing controls. `test_provider_session_freezes_actual_requests_and_resets_afterward` expects a runtime re-admission on a cached catalog read; the same cache return and assertion are present at the merge commit before this move. `test_every_top_level_entry_is_classified` found ignored `improver-*-{full,receipt,tail}.log` files left in this checkout by earlier controls. The logs were retained, not removed to make the check pass. These observations do not establish a full baseline run or prove those failures harmless.

At this migration stage the ChessBench profile still combined provider and scoring source. Its identity changed with the provider path, and old identities remained valid but did not compare equal. This migration validation did not establish the later separation described below.

## ChessBench provenance separation

The subsequent change introduces separate declared data-source, loader and scorer identities for ChessBench; old combined scoring identities remain verifiable but are not inferred equivalent. The source digest is a declaration, not a claim that a CSV was read or verified. No historical receipt, benchmark input or scorer algorithm was changed.

Selected offline controls passed in overlapping runs: **141 passed** for the component model, a mock-driver H0/resume scenario, ChessBench profiles, H0 preparation, caching, identity validation, import boundaries and the installed wheel; **160 passed** for component validation, mock-driver wiring, reused-evidence lineage, privacy-safe evidence export, H0 subset and preparation, and legacy ChessBench identity controls. The mock-driver scenario uses the authored demo dataset and mock actor. These controls do not exercise a real ChessBench dataset, model, container or benchmark score. Existing full-suite and locked Loop wheel limitations still apply.

## MATH provenance separation

MATH and MATH-500 now record the catalog's declared upstream source/revision, loader source identity and native command-scorer identity separately. The declaration does not assert that locally prepared rows came from the upstream release, and no upstream artifact digest is claimed. The scorer identity covers the command scorer and the modules it imports; actual dependency artifacts and selected private-input commitments remain separately recorded. A source-only fixture retains the previous combined scoring identities. The adapter and scoring algorithm files were not changed.

Synthetic controls test independent loader, scorer and source changes, historical identity validation, missing-evidence refusal, concurrent declaration changes, H0/cache paths and installed-wheel discovery. The offline selection reported **375 passed, 2 skipped, 6 deselected**. After strengthening declaration-drift and malformed-input handling, the focused MATH, H0, evidence, documentation and wheel selection reported **95 passed**. The skips require an explicitly prepared Math-Verify runtime; five deselections are real native equivalence controls requiring that runtime, and one is the known provider-cache baseline assertion. No genuine dataset, model or benchmark run was performed. These overlapping controls do not establish full-suite or live scorer equivalence.

## LoCoMo provenance separation

LoCoMo now records its declared data source/digest, input loader and deterministic NLTK-based scorer/report implementation separately. Its scorer component includes a hash of the declared upstream Harbor scorer provenance; this is not an attestation of downloaded upstream bytes or of the installed NLTK version. The admitted runtime artifact and selected-input commitments remain separate. The old combined identity is retained as a public source-only fixture and cannot silently compare equal to the new profile. Adapter, scorer and aggregation algorithm files were not changed.

An offline selection reported **338 passed, 3 skipped, 5 failed**; the five synthetic command controls could not start because no prepared LoCoMo execution runtime was available. A focused selection not requiring that runtime reported **108 passed, 3 skipped**, including synthetic Harbor differential, source/loader/scorer drift, historical identity, export and installed-wheel controls. The skips cover two controls requiring an explicitly prepared runtime and an optional released-data audit. No released conversation, model or benchmark was used; these overlapping controls do not establish full-suite or real-runtime acceptance.

## Scoring and verification import retirement

The old `harnessgrad.scoring_identity` and `harnessgrad.verification_context` imports now refuse with canonical replacement paths; repository callers use `harnessgrad.provenance`. Fresh-process checks cover both import orders, and installed-wheel checks verify that the old paths cannot silently resolve to another editable checkout. Selected offline controls passed in two overlapping runs: **160 passed** for import boundaries, H0, identity, mock-driver and wheel checks; **172 passed** for cache, evidence, H0 planning, record projections and reuse. These selected controls do not establish complete-suite acceptance or validate real benchmark executions. Other provenance compatibility imports remain until their isolated-runtime and mount contracts are migrated separately.

## Native judge import retirement

JobBench's native runtime now imports the unchanged judge identity implementation through `harnessgrad.provenance.native_judge_identity`; the old import refuses with migration guidance. An isolated `python -I -B` child control reaches judge validation without loading Harbor or starting a benchmark, and installed-wheel checks exercise the canonical module and entrypoint. Selected offline controls reported **111 passed, 5 skipped** across judge selection, native dispatch, loopback gateway, import boundaries and wheel installation; a further focused run reported **18 passed, 1 skipped**. The skipped actual H0 subprocess requires an explicitly prepared full Harbor artifact. These are synthetic controls, not a real Harbor, Docker, model or benchmark evaluation, and the test sets overlap.

## Harness and H0 identity import retirement

The remaining `harnessgrad.identity` and `harnessgrad.h0_identity` aliases now refuse with canonical provenance paths. The implementations and record formats were not changed. Synthetic controls reported **224 passed** across H0 admission, mock driver, identity/configuration, import boundaries and wheel installation, followed by **93 passed** for editor, slot and root-integrity checks. Both import orders are checked in fresh processes. These controls do not replace full-suite or real-benchmark validation.

## Test archive isolation

Direct editor tests previously lacked the method working directory supplied by the driver and wrote CLI archive logs into the checkout root. The affected test helpers now supply their own pytest temporary directories; production archive behavior is unchanged. An offline run covering editor, TTHE, CLI archive, usage, terminal budget and improver controls reported **214 passed**. The names, sizes and modification times of pre-existing root `improver-*.log` files were unchanged before and after that run. The 48 earlier test logs were subsequently moved, byte-checked and retained with a restore manifest in a private sibling directory outside the checkout. The checkout-wide integrity controls then reported **13 passed**. The external archive is local evidence, not a published project artifact.

## Budget import retirement

The budget dispatcher and its driver binding now use `harnessgrad.execution` exclusively; the former top-level imports refuse with replacement guidance. The dispatcher implementation and budget rules are unchanged. Selected offline controls covering reservations, request budgets, terminal statuses, H0 costs, driver bindings, import boundaries, integrity and the installed wheel reported **116 passed**. They use authored requests, mock processes and local test gateways, not real model or benchmark calls; full-suite coverage is not implied.

## Evidence import retirement

Evidence manifests, exports, statistics and receipt verification now use `harnessgrad.evidence_reports`; the former top-level paths refuse with replacement guidance. Record schemas, privacy rules and report implementations are unchanged. Selected synthetic controls reported **198 passed** across manifest and receipt validation, privacy-safe export, comparisons, Console evidence API/browser integration, H0 costs, import boundaries, integrity and installed wheels. The tests do not run a model or a benchmark and are not full-suite coverage.

## Experiment import and split entrypoint retirement

Experiment configuration, completion and split APIs now use `harnessgrad.experiment`; the former top-level imports and `python -m harnessgrad.splits` refuse with replacement guidance. `harnessgrad split` and `python -m harnessgrad.experiment.splits` use the same existing implementation. Selected offline controls covering experiment configuration, synthetic splits, mock-driver integration, import boundaries, integrity and the installed wheel reported **130 passed**. A further **22 passed** for static preset and release checks. No real benchmark or model was run; these are not full-suite controls.

## Execution coordination import retirement

Bubblewrap selection, shared slots and run namespace coordination now use `harnessgrad.execution`; the former top-level imports refuse with replacement guidance. Their implementations, resource-lock format and namespace behavior are unchanged. Selected offline controls reported **114 passed** across cross-process slot capacity and recovery, namespace reservation, Bubblewrap selection, driver admission, import boundaries, integrity and installed wheels. These controls use owned temporary paths and synthetic requests; they do not establish complete-suite or real-benchmark compatibility.

## Candidate and Python runtime import retirement

Candidate admission and managed Python helpers now use `harnessgrad.execution` and `harnessgrad.execution.python_runtime`; former top-level imports refuse with replacement guidance. Source fingerprinting, dependency preparation and admission implementations were not changed. The isolated H0 subprocess and installation/validation tools were migrated too.

An offline selection reported **177 passed, 3 errors**: the three Loop image-runtime fixtures could not prepare their locked dependency set because the owned offline cache lacked `annotated-types==0.8.0`. A broader synthetic selection reported **219 passed, 12 skipped, 5 failed**. Three failures were pre-existing synthetic actor tasks missing the now-required goal; the fixtures were updated and those three tests subsequently **passed**. The other two required an unavailable locked MiniAgent SDK wheel (`openai==2.21.0`) and an unprepared OpenClaw runtime. A further partially selected tool/harness run reported **64 passed, 1 skipped, 4 deselected, 9 errors**; all nine errors required the same unavailable MiniAgent wheel. A focused native-preparation and setup run reported **31 passed**. These runs overlap. No dependency was downloaded or substituted; the results do not establish real-runtime or full-suite acceptance.

## Test-selection tool layout

The scope and change-lane implementations moved from `tools/test_scope.py` and `tools/test_lane.py` to `tools/testing/scope.py` and `tools/testing/lane.py`; the old files were removed so pytest does not collect their `test_files` helpers. The implementations match the previous source after normalizing repository-root depth and documented command paths. An authored change-lane plan was byte-identical before and after, and the GSM8K plan matched after excluding its random temporary directory. A first selection produced **178 passed, 1 failed** due to one stale historical documentation path; after correction, focused documentation and entrypoint controls reported **31 passed**, and wheel, integrity, selection and layout controls reported **109 passed**. Pytest collection of `tools/` reported no tests (exit code 5, the expected no-tests result). These are offline controls and overlap.

The remaining runner, selector and sweep entrypoints and their cost inventory were subsequently consolidated under `tools/testing/`; the former top-level scripts were removed and the CI workflow updated. The GSM8K plan matched its previous output except for its random temporary directory, and an authored CI selection matched exactly. Selected offline guards, quality-receipt, documentation, entrypoint and wheel controls reported **232 passed**. An additional real subprocess control fed a one-file authored plan to `run_selected.py`; the canonical runner completed that file with an empty `unexecuted` list. No model or benchmark was invoked.

## Analysis entrypoint consolidation

The eight result-reporting commands now use the existing `tools/analysis/` implementations directly; the top-level forwarding scripts were removed and documentation, wheel checks and the contributor registry path were updated. Synthetic `plot_curve`, `run_summary` and `results_matrix` outputs matched the pre-removal commands byte for byte. An initial selected run reported **377 passed, 8 failed**: seven test invocations still constructed the removed summary path, and an AppWorld synthetic fixture omitted its existing `_verifier` helper. After correcting those test paths and fixture, the same selection reported **385 passed**. A further selection reported **68 passed, 2 failed, 1 error**: the two failures are the existing subprocess-decoding audit and results-table expectation documented in the test guide; the error was a ChessBench sealed-runtime fixture whose currently present scorer source did not match its pinned size. No pinned scorer or benchmark artifact was changed. Contributor and documentation controls then reported **43 passed**. These offline selections overlap and do not establish full-suite or real-benchmark acceptance.

## Public shared improvement API

`harnessgrad.improvement` now exposes feedback and the existing SkillOpt, ACE and EvoHarness proposal stages through a single lazy API. The old customization-specific contract alias refuses with the shared replacement path. Import controls verify that importing feedback or stage declarations does not import method implementations; executing one stage loads only its selected mechanism. Selected synthetic customization, isolation, shared-stage and installed-wheel controls reported **119 passed**; after updating the scripted example to use the public API, focused checks reported **70 passed**. The runs overlap and do not measure model output, execute a benchmark or establish candidate effectiveness.

## Lazy experiment policy stages

The experiment policy bridge now loads extracted ACE, SkillOpt and EvoHarness proposal stages on demand instead of loading all three at startup. Its generic `_safe` function matches the LightRSI fix-branch source AST exactly. A top-level function comparison against `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` found only the previously extracted `ace`, `skillopt`, `apply_skill_patch` and `evo_harness` functions changed; all other shared functions and top-level assignment values matched. Selected offline policy, pre-refactor fixture, isolated-import, mock-integration and wheel controls reported **62 passed**. The fixtures cover selected scripted scenarios and do not establish complete algorithmic equivalence or real benchmark performance.

## Dataset runtime identity relocation

`dataset_runtime_identity` moved to `harnessgrad.datasets.runtime_identity`; the H0 child now invokes that module and its working directory points to the same repository root. The implementation matches the previous source after normalizing that one root-depth adjustment. A pre-move synthetic identity fixture is reproduced byte-for-byte and its original digest still validates. Fresh-process controls cover both import orders and the retired module command.

A selected offline run reported **413 passed, 1 skipped, 1 deselected, 1 failed**. The deselected provider-cache assertion is the existing baseline discrepancy documented in the test guide; the failure was the import-boundary test's list of subprocess owners, corrected to include the relocated H0 preflight. The full boundary/integrity and focused migration recheck then reported **33 passed**; documentation and contributor checks reported **43 passed**. The initial selection included authored provider/runtime fixtures, H0 and cache admission, mock driver and installed-wheel controls. The sets overlap and do not establish real benchmark or full-suite compatibility.

## Dataset runtime implementation relocation

Dataset runtime preparation moved to `harnessgrad.datasets.runtime`, with the old path temporarily retained as an alias for source-pinned adapters. After normalizing the repository-root depth, the implementation matches its prior bytes. Metadata-only snapshots for eight selected datasets retained identical provider/execution declarations and scoring identities. The ChessBench provider import itself changed, so its separate loader fingerprint changes; this does not assert a change in its data or scorer. No adapter scorer source was edited.

An initial import-control run reported **16 passed, 2 failed** because the newly authored subprocess test generated an invalid root expression; after correcting the test, its **3 checks passed**. The broader offline runtime, provider, H0, identity, cache, import and wheel selection reported **363 passed, 12 skipped, 1 deselected**; the deselected provider-cache test is the documented baseline discrepancy. Adapter metadata, synthetic native-dispatch, documentation and contributor controls additionally reported **162 passed, 1 skipped**. These sets overlap and do not establish full-suite or real-benchmark compatibility.

## Visualization entrypoint consolidation

The optional demo and trend renderers now run from `tools/visualization/`; the former top-level forwarding scripts were removed. A synthetic demo PNG matched the pre-removal output byte for byte, including its historical creator metadata; the existing trends SVG also matched its receipt using `--check`. Selected offline renderer, CI-selection, import, integrity, documentation and installed-wheel controls reported **156 passed**. These figures describe recorded or synthetic inputs and are not new benchmark measurements.

## Cross-layer regression after integration

On 2026-11-17, the clean refactor tree at `48ff66b24e867a1eb203ecbf29c3714fa64be2fb` ran the following selected offline controls: **530 passed**, with 13 warnings. The selection covers the shared proposal API and pre-refactor policy fixtures, customization, dataset identity, mock experiment and resume, candidate boundaries, Workbench service/selection/binding, evidence API, CI tooling and installed wheels. The LightRSI fix commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` was confirmed as an ancestor. The original main checkout's README had no staged or unstaged diff at this check. This record itself was added after the checked tree and therefore changes the platform content identity.

```bash
PYTHONDONTWRITEBYTECODE=1 HG_REVIEWED_ENVIRONMENT_ONLY=1 python3 -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/hg-cross-layer-final-20261117 \
  tests/quality/test_{core_harness,core_methods,core_method_transport,improvement_import_isolation,shared_improvement_example,customization_inputs,customization_proposals,customization_review,customization_patch,customization_cli,customization_components,evo_harness_shared,policy_port_pre_refactor_compat,evo_harness_pre_refactor_compat,policy_stage_loading}.py \
  tests/quality/test_{measurement_components,measurement_components_driver,dataset_runtime_package,experiment_integration,custom_splits,resume_train_e2e,source_candidate_stage,run_namespace,native_method_integration}.py \
  tests/quality/test_{workbench_editor_drivers,workbench_selection_boundary,workbench_service,workbench_binding_recheck,console_scope_preview,console_evidence_api}.py \
  tests/quality/test_{ci_select,ci_workflow,quality_receipts,import_direction,integrity,testing_entrypoints,testing_layout,analysis_entrypoints,visualization_entrypoints,wheel_adapter_resources,clean_install_contract}.py
```

These are authored fixtures, mock Harnesses, or local scripted services; no real model or benchmark was invoked. Passing them does not resolve the documented baseline failures, missing locked wheels or the unprepared external runtimes, and is not full-suite acceptance.

## Fix-branch differential controls

On 2026-11-17, a separate temporary source archive of LightRSI fix commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` was compared with refactor commit `7389c2c6d85cd69ec77090b471af90da9743caed`. Both ran an authored two-task `demo` with the mock Harness, a fixed seed and the no-op method. Their two rounds had identical selected projections: side, score kind, score, per-task values, coverage and scorer isolation. Both scored the two synthetic tasks as zero; this is a control, not benchmark evidence.

The unchanged `tests/quality/test_native_method_integration.py` was then run independently in both trees using its local scripted model service and authored demo fixture. Both controls passed and produced scores `0 → 1 → 0 → 1`. The selected per-round measurements, policy reports, task IDs and candidate tree hashes matched across trees. A private local copy of these synthetic projections is retained outside the checkout. These controls cover only the selected mock/no-op and OPRO scenarios; they do not prove equivalence for other methods, datasets, platforms or live models. The source archive lacks Git checkout metadata and the runs intentionally have different platform identities; neither result is reused as an experimental baseline.

The public package index was also probed without credentials while investigating the missing locked wheels, but its TLS connection failed in this environment. No global SDK, replacement wheel or user cache was substituted, so the genuine Loop/MiniAgent wheel controls remain unverified.

## Post-merge module commit consolidation

Three adjacent pairs of unpushed, same-module commits were combined: execution coordination/candidate runtime, test selection/runner tools, and dataset runtime identity/preparation. The fix-branch merge remains intact. Before this documentation update, the consolidated tree exactly matched the original `cfc671d990be169e233adaffaee894a10a5844c0` tree (`57b9c80c587bec7bc9a1ebee3e8660100aa16f26`), so the existing behavior checks remain attributable to identical files. The original local history, including historical validation commit IDs, is retained at `refs/harnessgrad/backups/refactor-before-module-consolidation-20261117`; this local ref is not automatically published with the branch.

Each combined commit was separately checked in an owned temporary detached worktree with offline settings and external pytest directories: **122 passed** for execution runtime imports (`bcd6f210`), **178 passed** for test tooling (`c2ae6191`), and **211 passed, 1 deselected** for dataset runtime/identity (`947b19a4`). The deselected provider-cache assertion is the baseline discrepancy documented in the test guide. Logs and summaries are retained locally outside the checkout, and the temporary worktrees were removed. These are selected synthetic controls, not full-suite or real-benchmark acceptance.

## Limits

The checks above do not establish complete-suite acceptance, compatibility with arbitrary external Harness installations, or runtime behavior for every dataset and container. Known baseline failures and the unavailable locked Loop SDK wheel are recorded in the [test guide](../test-suite.md#existing-failures-checked-during-shared-layer-refactoring). Changing the checkout after these controls changes the platform digest. Preserve existing receipts and follow the platform's admission rules when comparing or resuming runs.

## SpreadsheetBench provenance separation

The v1 and v2 source-only profiles now separate declared workbook origin, loader and scorer roles. V2's upstream evaluator metadata is a declaration, and its archive has no asserted upstream digest; neither is proof of downloaded bytes. Verifier inputs and the actual Python and LibreOffice/UNO environments remain separately bound. Scorer, adapter, runtime and workbook files were not changed. Source-only historical profile fixtures remain digest-verifiable and are not silently equated with the new profiles.

On 2026-11-17, selected offline controls for both adapters, component admission, H0, CI selection, import boundaries, documentation and installed wheels reported **188 passed, 12 skipped** with 13 pre-existing syntax warnings. The skips require prepared external runtimes. This selection uses authored fixtures and does not establish real workbook scoring or full-suite acceptance. A prior focused selection reported **110 passed, 5 skipped**; after adding declaration mutation controls the focused recheck reported **126 passed**. Selections overlap.

## Adapter runtime import migration

LoCoMo and SpreadsheetBench v1/v2 now import `harnessgrad.datasets.runtime` directly. A source-only pre-migration fixture records the separate measurement identities and an AST normalized for this import spelling. The current AST matches that fixture; the declared dataset and scorer identities stay identical, while each adapter loader fingerprint changes. Cache and H0 admission still conservatively recognize the loader change. Other adapters continue to use the temporary alias pending their own review.

The focused offline selection reported **121 passed**. A broader selection covering the migrated adapters, runtime identity, H0, component admission, installed wheels, CI and import boundaries reported **256 passed, 14 skipped, 1 failed**, with 13 syntax warnings. The failure is the previously recorded `test_provider_session_freezes_actual_requests_and_resets_afterward` cache-admission assertion; neither its test nor the provider implementation changed in this migration. The skips require external prepared runtimes. These overlapping controls use synthetic inputs and do not establish real benchmark or full-suite acceptance.

## Reasoning Gym provenance separation

The historical pool digest is now a declared source identity without an assertion about acquisition or the generator of those bytes. Its adapter and QA scaffold are loaded by the scorer as well as by the loader, so shared files are conservatively included in both roles. The installed scoring package remains bound by the execution runtime identity; verifier inputs remain separately committed. No scoring implementation or historical pool was changed. The previous source-only combined profile remains verifiable and does not silently compare equal to the new components.

On 2026-11-17, a selected offline run of authored provenance and pool-lifecycle controls, snapshot-transport controls using local services, H0, CI, import boundaries, documentation and installed-wheel tests reported **209 passed**, with 13 syntax warnings. A prior focused selection reported **115 passed**. These selections overlap, do not invoke a real benchmark or model, and do not establish full-suite or actual Reasoning Gym runtime acceptance.

## Task study command organization

Task difficulty and sensitivity commands now live under `tools/studies/`; Python imports and current documentation use that location. The former top-level scripts were removed. A byte comparison against the immediately preceding revision matched each script after normalizing its root-depth calculation and displayed command path. Difficulty reads existing records; sensitivity may run a study and remains an explicit operator command.

The initial selected offline run, including synthetic statistics, mock-Harness sensitivity execution, import checks and installed-wheel controls, reported **188 passed, 1 failed**; the failure listed four documentation references to the removed paths. After correcting those references, the focused command, statistics, CI and documentation selection reported **140 passed**. These selections overlap; they did not invoke a real model or benchmark, and do not establish full-suite acceptance.

The subsequent CI-selector review added explicit study-tool consumers, installed-wheel coverage for dataset identity profiles, and shared SpreadsheetBench provenance tests for either adapter. Unknown study tools defer to the full test plan. Focused selector/workflow controls reported **149 passed**; a broader offline selection including tooling, synthetic studies, provenance, import boundaries and installed wheels reported **235 passed**, with 13 syntax warnings. These selections overlap and do not run real benchmarks or models.

## Observation import migration

The former top-level `eval.console`, `eval.progress`, `eval.trace`, `eval.tracing` and `eval.phases` imports now refuse with their `eval.observe` replacement paths. The canonical implementations and the `eval.observe.trace` adapter are unchanged. Controls exercise both fresh-process and installed-wheel import behavior; existing platform imports are checked against canonical paths.

A selected offline run reported **115 passed, 1 skipped, 2 failed**. Both failures were Loop-backed progress end-to-end controls that stopped while preparing the locked `openai==2.21.0` dependency, which was unavailable in the offline cache; neither reached the event assertions. The broader offline rerun excluding those two controls reported **135 passed, 1 skipped, 2 deselected**, with 13 syntax warnings. It includes synthetic progress/trace tests, mock experiment integration, source-boundary checks and an installed wheel. The skip is the absent historical legacy adapter. These sets overlap and do not establish real Loop runtime or full-suite acceptance.

The same migration subsequently retired four top-level policy aliases: `eval.diagnostic_policy`, `eval.runtime_policy`, `eval.appworld_reporting` and `eval.gaia_snapshot_attestation`. The GAIA preflight command alias and historical source-attestation rules remain in place. Policy, GAIA snapshot and import-boundary controls reported **261 passed, 1 skipped** with 13 syntax warnings. A separate overlapping offline selection including mock experiments and installed-wheel import controls reported **246 passed, 1 skipped**. The skip is the absent historical legacy adapter; no real model or benchmark was invoked.

The same grouped migration also retired six non-CLI admission aliases and four non-CLI service aliases, with canonical replacements under `eval.admission` and `eval.service`. Standalone scorer-bundle and separated-stdio command entrypoints remain. Admission, model gateway, Unix relay, stepwise transport, import and documentation controls reported **426 passed, 1 skipped** with 13 syntax warnings. A separate overlapping selection including mock experiments and installed-wheel controls reported **311 passed, 1 skipped**. The skip is the absent historical legacy adapter. These controls do not run a real model or benchmark.

Four non-CLI run-module aliases (`eval.candidate`, `eval.metrics`, `eval.integrity`, `eval.task_contract`) were also retired in favor of `eval.run`; the GAIA-required `eval/runner.py` and related historical paths remain. Candidate, aggregation, task-contract, integrity, import and documentation controls reported **245 passed, 1 skipped** with 13 syntax warnings. An overlapping selection of mock experiments, H0, GAIA snapshot admission and installed-wheel controls reported **159 passed, 1 skipped**. The skip is the absent historical legacy adapter. These are offline controls, not real benchmark or full-suite acceptance.

## Public shared input contracts

The improvement package now exposes named feedback indexing, relative-path checking, surface selection and deterministic text-diff functions. Customization and its CLI use these contracts instead of underscored names; strict JSON hooks remain available from the contracts module. An AST comparison against the preceding revision matched the shared contract, stages, customization and CLI after normalizing these names and function docstrings. This does not establish that a generated proposal is effective.

The initial focused run reported **79 passed, 7 failed** because the CLI still imported the previous private JSON names. An attempted correction introduced an incorrect JSON keyword and the next run reported **81 passed, 6 failed**. After both were fixed, the focused run reported **87 passed**. A broader offline run covering customization, shared stages, pre-refactor method fixtures, import boundaries and wheel recipe assets reported **145 passed** with 13 syntax warnings; installed-wheel adapter and customization controls subsequently reported **25 passed**. The selections overlap and use authored feedback or scripted callbacks, not a real model or benchmark.

## Shared static Harness diagnostics

`code_metrics` moved from the top-level package to `harnessgrad.core.code_metrics`; the former import now directs callers to the canonical path. The implementation matched the pre-move source byte for byte. This remains a static, name-based approximation and is not a score or evidence of execution. The moved implementation remains inside the platform integrity snapshot.

Selected offline candidate-diagnostic, import and documentation controls reported **91 passed** with 13 syntax warnings. An overlapping selection including integrity, mock experiments, H0, customization and installed-wheel controls reported **117 passed** with the same warnings. No real model or benchmark was invoked; these controls do not establish full-suite acceptance.

## Experiment scoring policy organization

The recorded scoring and reference-comparability policies moved to `harnessgrad.experiment.policies`; the implementation was byte-identical to the prior source. The former import now directs callers to the canonical path, and the moved file remains in integrity snapshots. These policies remain experiment-specific and are not acceptance decisions for other workflows.

The focused offline policy, identity, lifecycle, import and documentation selection reported **148 passed** with 13 syntax warnings. A second selection including H0, mock experiments and installed-wheel controls reported **78 passed, 1 failed**: the frozen-reference test omitted the verification context now required by its contract. Running that same test in an independently archived pre-move `be141d50` source tree produced the identical failure. The authored reference fixture was updated to provide matching synthetic contexts and now also explicitly checks that missing context is reported; the relevant reference, policy, H0 and mock-experiment recheck reported **65 passed**. These overlapping controls do not invoke a real model or benchmark and do not establish full-suite acceptance.

## Portable Harness snapshot organization

The portable snapshot implementation moved byte-for-byte to `harnessgrad.core.snapshot`. Its v1 schema and content addressing remain unchanged, and the former Python import gives the replacement path. A synthetic artifact written by the pre-move implementation is reproduced byte for byte and can be loaded, checked out and verified after migration. Console and export loaders prefer the new location, support older platform trees containing only the previous file, and refuse an invalid or dangling current path instead of silently falling back. Existing artifact receipts are not rewritten.

Selected offline snapshot, descriptor-relative restoration, Console, exporter and CI controls reported **156 passed**. A broader overlapping selection covering candidate recovery, synthetic training resume, integrity, import boundaries and installed wheels reported **267 passed**, with 13 syntax warnings. These checks use authored source trees and do not establish recovery for every historical checkout or full-suite acceptance.

## Scoped readiness layering

The shared `Readiness` value and status vocabulary moved to `harnessgrad.core.readiness`; normalization of an already-collected dataset provision status moved to `harnessgrad.datasets.provisioning`. The former import directs callers to these two owners. The type and normalization function matched the pre-move ASTs; their status semantics and serialized dictionary are unchanged. Both new implementations remain included in platform integrity snapshots.

Selected offline readiness, dataset lint, import and documentation controls reported **57 passed**; an overlapping selection including driver readiness, synthetic mock experiments, integrity and installed-wheel controls reported **146 passed**. Each run emitted 13 syntax warnings. No real model, benchmark or installation was invoked; these controls are not full-suite acceptance.

## Dataset-owned task Python organization

Dataset task Python binding moved to `harnessgrad.datasets.task_python`; registry and execution consumers use the new path, while the old import directs callers to it. The implementation is byte-identical to the prior source. Its actor-role validation, environment projection, public receipt and existing dataset-runtime identity format are unchanged; platform source identity changes with the migration.

The first focused invocation failed during test collection because two test modules still used a combined old import; after updating them, **10 passed, 3 deselected**. A broader offline selection covering synthetic task bindings, runtime identities, trial execution, H0, mock experiments, integrity and installed wheels reported **145 passed, 4 deselected**, with 13 syntax warnings. Three deselections are Spreadsheet actor integration controls not exercised in this run; the fourth is the known provider-cache assertion documented in the test guide. No real model or benchmark was invoked; these controls do not establish full-suite or real actor-runtime acceptance.

## StrongREJECT provenance separation

The declared Harbor task inventory and upstream CSV, loader and scorer are now represented separately. The inventory's digest covers public declarations, not verified local task bytes. The adapter is shared by loading and verification and remains in both implementation fingerprints; the scorer also binds declared upstream evaluator/template metadata and local scorer and transport sources. Judge model, parameters and endpoint hash remain in scoring identity. Loaded declarations must match the current source manifest; missing or mismatched old component evidence is not silently equated. The adapter, verifier and scoring implementation were not changed.

Initial source controls found that two official-source checks raised because no dataset root was configured; the final offline runs used an explicitly empty temporary root, under which those checks correctly skip. An H0 configuration-only test also had stale runtime assumptions and a stale mock signature; its runtime preflights are now mocked for that configuration test, not for experiment execution. The focused selection then reported **173 passed, 5 skipped**. A broader overlapping selection covering authored verifier privacy, historical profiles, H0, evidence reuse, CI, imports and installed wheels reported **260 passed, 5 skipped**, with 68 syntax warnings. The skips require the pinned StrongREJECT task pool or upstream source files. No real model, judge or benchmark was invoked; these controls do not establish real-source or full-suite acceptance.

A subsequent authored end-to-end control binds the separated identities to a synthetic StrongREJECT verifier case and a local HTTP judge double. It checks correct/refusal scores, malformed-judge unmeasured handling, and exclusion of synthetic private text and credentials from public identities/results. Its first run failed because the test had not created its temporary work root; after fixing that setup, **9 passed**. A broader overlapping run including the original StrongREJECT suite, H0, evidence reuse, import and installed-wheel controls reported **269 passed, 45 skipped** with 68 syntax warnings. The skips require the unprepared pinned task pool or upstream source files. No real model or benchmark was invoked; a local scripted judge is not evidence of real judge performance.

## Cross-layer regression after provenance and package migrations

On 2026-11-17, the clean source tree at `60ff81bb725c99641592c9768da249f121b18230` ran a selected offline cross-layer suite: **741 passed**, with 13 syntax warnings. The selection covers shared core and improvement APIs, customization, pre-refactor method fixtures, independent measurement identities, scoring and reference policies, mock experiments and resume, authored StrongREJECT local-judge controls, MATH/LoCoMo/SpreadsheetBench/Reasoning Gym provenance, snapshot recovery and export, Workbench services, CI tooling, import/integrity boundaries and installed wheels. The LightRSI fix commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` was confirmed as an ancestor. This validation record was added afterward and changes the platform content identity.

```bash
PYTHONDONTWRITEBYTECODE=1 HG_REVIEWED_ENVIRONMENT_ONLY=1 python3 -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/hg-cross-layer-current-20261117 \
  tests/quality/test_{core_harness,core_methods,core_method_transport,code_metrics,readiness_layers,improvement_import_isolation,shared_improvement_example,customization_inputs,customization_proposals,customization_review,customization_patch,customization_cli,customization_components,evo_harness_shared,policy_port_pre_refactor_compat,evo_harness_pre_refactor_compat,policy_stage_loading}.py \
  tests/quality/test_{measurement_components,measurement_components_driver,scoring_identity,scoring_policies,eval_reference_block,dataset_runtime_package,dataset_task_python_migration,experiment_integration,custom_splits,resume_train_e2e,source_candidate_stage,run_namespace,native_method_integration}.py \
  tests/quality/test_{strongreject_provenance,locomo_provenance,math_managed_execution,spreadsheet_provenance,reasoning_gym_provenance,run_snapshots,console_snapshot_rounds,export_harnesses}.py \
  tests/quality/test_{workbench_editor_drivers,workbench_selection_boundary,workbench_service,workbench_binding_recheck,console_scope_preview,console_evidence_api}.py \
  tests/quality/test_{ci_select,ci_workflow,quality_receipts,import_direction,integrity,testing_entrypoints,testing_layout,analysis_entrypoints,studies_entrypoints,visualization_entrypoints,wheel_adapter_resources,clean_install_contract}.py tests/test_round_snapshot_fd.py
```

These are authored fixtures, mock Harnesses or local scripted services. No real model or benchmark was invoked. This selection does not resolve the documented baseline failures, missing locked wheels or unprepared external runtimes and does not establish full-suite acceptance.

## README customization entrypoint

The customization section now links to installation and endpoint configuration, shows an authored feedback example and the existing collect/propose/review commands, and links to source-checked patch export. The personal-evolution section remains a link to its protocol rather than an implementation guide. Selected offline documentation-command, link and customization CLI controls reported **81 passed**, including a scripted local endpoint and selected-source review. No external model or user Harness was accessed.

## Run and trace validation commands

The read-only run-record and Harness-trace validators now live in `tools/validation/`; their former top-level scripts were removed. Each implementation matches the previous source after normalizing the repository-root depth and displayed command path. Existing Harness sources and their historical descriptive comments were not edited. CI maps the new commands to their audited consumers and defers unknown validation tools to the full plan.

The focused offline command, trace, record and documentation controls reported **56 passed, 1 skipped**. A broader overlapping selection including CI, import boundaries, integrity and wheel controls reported **225 passed, 1 skipped** with 13 syntax warnings. The initial staging command did not stage all new files after `git mv`; after correcting staging, the command/CI/installed-wheel recheck reported **138 passed, 1 skipped**. No real model or benchmark was invoked, and these controls do not establish full-suite acceptance.

## Fix-branch control after later migrations

At refactor commit `b05b813fdd2fe8ac7fe735268bc112d467e4b2be`, the unchanged authored OPRO integration test (source SHA-256 `42d0be1ced6a3b2e2232671ed1ffcf691d2b5242678703f2dff3e0ed385da54d`) passed against the mock demo and local scripted model endpoint. All four round projections matched the previously recorded control against LightRSI fix commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8`: score, per-task values, score side/kind, measured/invalid counts, scorer isolation, method report, selected task IDs and Harness tree hash. The sequence remained `0 → 1 → 0 → 1`, with the measured best candidate promoted. This is an authored one-task control, not a benchmark score or proof of equivalence for other methods, datasets, platforms or live models. Platform identity and run paths differ; historical measurements are not reused.

## First-use and endpoint diagnostics organization

The first-use checker and explicit tool-call probe now live under `tools/diagnostics/`; experiment improver readiness imports the latter from that path. After normalizing the repository-root depth and displayed command path, both scripts matched their prior source. The probe is not invoked simply by importing or requesting help. The diagnostic CLI still contacts only the caller-selected endpoint when explicitly run.

Selected offline first-use, local loopback credential/probe, documentation and import controls reported **57 passed** with 13 syntax warnings. A broader overlapping selection including driver readiness, CI, integrity and installed-wheel checks reported **231 passed**, with the same warnings. Tests used synthetic inputs and local scripted services; no external model or real benchmark was invoked.

## Provider-session cache test correction

The previously documented provider-session failure expected runtime re-admission on a cached catalog read, although the cache intentionally returns without launching a worker. The synthetic test now explicitly checks that behavior, then changes the selected storage configuration to force a new worker request, verifies admission of the same frozen artifact, and verifies that a failed admission refuses that request. Provider and runtime implementation behavior was not changed; this does not claim cached reads recheck artifact bytes.

The focused provider and cache controls reported **50 passed**. A broader overlapping offline selection covering runtime identity, provider protocol, declaration caching, H0, mock experiments, import boundaries and integrity reported **167 passed** with 13 syntax warnings. These controls do not exercise a real dataset or establish full-suite acceptance.

## Unmapped tool changes in CI selection

The CI selector previously returned only generic guards when a `tools/` path had no matching test basename, and treated any file under `tools/visualization/` as covered by the existing renderer check. Unknown tool or visualization paths now produce the existing full-suite/deferred plan instead of implying a narrow selection is sufficient; known visualization paths also select the installed-wheel control. This changes CI selection only, not experiment execution or scoring.

Synthetic selector, workflow, runner and visualization controls reported **163 passed**. A broader overlapping offline tooling, import, integrity and installed-wheel selection reported **223 passed**, with 13 syntax warnings. A deferred plan is not an executed full suite; the workflow records it for the nightly/full lane.

## Shared tool-call probe

The explicit Chat Completions and Responses capability probes now live in `harnessgrad.execution.tool_call_probe`; both the diagnostic command and experiment improver preflight call this owner. This removes that preflight's dependency on a CLI script. Probe function syntax, request definitions and transport handling matched the prior source after normalizing function names. Importing the module does not contact an endpoint.

Four authored loopback scenarios compared the prior and current diagnostic commands directly, including both supported wires, partial support and a synthetic HTTP failure. Request paths, authorization headers, JSON bodies, output JSON and exit codes matched. Selected offline credential, readiness, CLI and import controls reported **62 passed**; an overlapping run including mock experiments, integrity, CI and installed wheels reported **241 passed**, both with 13 syntax warnings. No external model or benchmark was invoked, and these controls do not establish compatibility with all providers.

## Harness validation ownership

The existing Harness validator, source/filesystem identity and explicitly requested smoke implementation now live in `harnessgrad.execution.harness_validation`. Candidate admission, driver, orchestration and Console use that owner rather than importing a CLI implementation. `tools/validate_harness.py` remains the CLI and aliases the same module object for ordinary Python imports; path-loaded callers can still access its public functions. The implementation matched the prior source except for the repository-root depth required by its new location. Three authored valid, syntax-error and missing-entrypoint CLI cases produced identical output and exit status before and after the move.

The initial offline selection reported **166 passed, 1 skipped, 1 failed** because the import-boundary test had not registered the moved validator's existing explicit-smoke subprocess capability. After registering that owner, the broader overlapping candidate, submission, source-identity, mock experiment, import, integrity and installed-wheel selection reported **321 passed, 1 skipped**, with 13 syntax warnings. No real model or benchmark was invoked; these controls do not establish full-suite or every native runtime's acceptance.

## Web gateway ownership

The Web gateway implementation moved byte-for-byte to `eval.service.web_gateway`; driver, runner and container state use the canonical module. `tools/web_gateway.py` remains a CLI and same-module import alias, including for the source-pinned native-research relay, whose source was not changed. Fresh-process, path-loaded, isolated relay import and installed-wheel controls check the retained entrypoints and shared patch targets. No real Web provider was contacted.

The first selected run reported **79 passed, 1 deselected, 1 failed** because the import graph had not listed the gateway's existing optional `crawl4ai` import; the dependency declaration was corrected. The broader run then reported **132 passed, 1 deselected, 4 failed**. All four failures reproduced in an independently archived pre-move source tree: three cleanup controls used tasks without the required goal, and a container-state control expected an older no-observation behavior. The authored task fixtures and observation assertion were updated without changing runtime behavior. The affected local controls reported **36 passed**; the broader recheck reported **150 passed, 1 deselected**, with 13 syntax warnings. After refining the state fixture to use a valid no-service network, a focused recheck reported **9 passed**. The deselected Docker integration was not run. These overlapping controls do not establish full-suite, live provider or real container acceptance.

## Improver resolver ownership

Improver discovery and resolution moved to `harnessgrad.improvers.resolver`. Driver, runner, readiness and Console use the canonical owner. `tools/improver.py` remains the command and ordinary imports share the same module object, preserving patch targets and the method's existing CLI path. The implementation matched its prior source except for repository-root depth. The platform and method sandbox visibility rules were not changed.

Old and new CLIs produced identical results and exit codes for an authored endpoint configuration, an authored local version-reporting executable and a missing entry. Focused ownership, local credential, readiness, Workbench and import controls reported **93 passed**; an overlapping selection including DSH path controls, mock experiments, integrity, CI and installed wheels reported **255 passed**. Both selections emitted 13 syntax warnings. No external model or benchmark was invoked; controls do not establish behavior for arbitrary real installations.

## Run-summary ownership

Run-summary implementation moved byte-for-byte to `harnessgrad.evidence_reports.run_summary`. Driver and completion logic now use the evidence owner, while `tools/analysis/run_summary.py` remains the command and ordinary Python import aliases the same module. Path-loaded and `runpy` callers retain `main`. Two authored run-summary CLI comparisons matched the prior JSON and text output byte for byte; no historical receipt was changed.

Selected offline summary, completion, mock-driver, import and documentation controls reported **98 passed**. An overlapping selection including H0 cost projections, unknown usage, mock experiments, integrity and installed wheels reported **198 passed**. Both emitted 13 syntax warnings and used authored records or local mocks, not a real model or benchmark; they do not establish full-suite acceptance.

## Native research Web relay import migration

The native research Web relay now imports `eval.service.web_gateway` directly. Its syntax matches the previous source after normalizing the import path; gateway and relay behavior were not otherwise edited. A source-only fixture records a synthetic pre-move verification context. The current context differs because relay program bytes are committed, so existing contexts are not silently reused. The retained `tools/web_gateway.py` command/import remains available for other callers.

An authored relay process served a synthetic search over a local Unix socket and closed with a clean receipt. Selected identity, relay, CI and import controls reported **143 passed** with 13 syntax warnings. A broader overlapping offline selection including Web gateway, verification workflows, task cleanup, native-research runtime declarations, integrity and installed-wheel isolated relay help reported **265 passed, 1 deselected** with 13 syntax warnings. The deselected Docker integration was not run. No real dataset, model or external Web provider was invoked; these checks do not establish real-container or full-suite acceptance.

## Native research conversion ownership

The existing native research conversion implementation now lives in `data.scaffolds.native_research.conversion`; the isolated preparation worker imports it directly and uses the canonical dataset runtime. The original `tools/prepare_native_research.py` command and ordinary Python imports remain, delegating to the same module; path-loaded and `runpy` entrypoints retain access to `main`. The conversion implementation matches the previous source except for repository-root depth. An authored HLE JSONL was converted by both old and new commands; the resulting manifest and case files matched byte for byte. Existing prepared datasets and receipts were not modified.

Selected offline ownership, synthetic conversion, import and documentation controls reported **50 passed** with 13 syntax warnings. An overlapping broader selection including authored native research, local Web relay, identity, H0, mock experiments and installed-wheel worker checks reported **137 passed, 2 deselected**, with 13 syntax warnings. The two deselected structured-judge sandbox controls require a prepared execution runtime and were not run. A final entrypoint recheck reported **15 passed**. No real model, benchmark data or external source was invoked; these controls do not establish full-suite acceptance.

## Platform dataset-runtime import migration

Registry, command-verifier, public native verifier and tool-relay platform modules now import `harnessgrad.datasets.runtime` directly. Their ASTs matched the previous source after normalizing only the import path. A source-only baseline verifies unchanged scoring and measurement components for ChessBench, MATH/MATH-500, LoCoMo, SpreadsheetBench v1/v2, Reasoning Gym and StrongREJECT with an authored judge setting. The platform content identity still changes; dataset adapters and the temporary old alias were not removed.

Selected offline provider, runtime identity, verification and CI controls reported **198 passed** with 13 syntax warnings. A broader overlapping selection including H0, authored relay, mock experiments, integrity and installed-wheel checks reported **345 passed** with 15 warnings. A final import-boundary and identity recheck reported **9 passed** with 2 warnings. No real dataset or model was invoked; these checks do not establish full-suite acceptance.

The subsequent provider-worker import migration uses the same canonical runtime and changes no other worker syntax. The prior source-only baseline confirms that ChessBench's declared data source and scorer identity remain equal while its loader fingerprint changes because it includes the worker source; component comparison refuses silent reuse. Other previously opted-in dataset identities remain unchanged in this control. A cold isolated Python worker returns the expected protocol refusal for an authored invalid request, both from the checkout and installed wheel. Focused controls reported **82 passed**; a broader overlapping provider, runtime identity, H0, evidence, mock experiment, import, integrity and wheel selection reported **331 passed**, both with 15 syntax warnings. No real dataset or model was invoked; this does not establish real ChessBench runtime or full-suite acceptance.

The subsequent native QA import migration updates its loader and preparation code to the same canonical runtime. The two source ASTs match their prior versions after normalizing import paths. MATH and MATH-500 declared data and scorer identities remain unchanged, while their loader fingerprints change and old loader evidence is not silently reused. An isolated Python conversion worker processed an authored JSONL in both checkout and installed-wheel controls. Focused controls reported **44 passed** with 15 syntax warnings; a broader overlapping native QA, H0, provider identity, mock experiment and wheel selection reported **325 passed, 2 skipped, 5 deselected**, with 15 warnings. The skipped and deselected MATH equivalence controls require an explicitly prepared Math-Verify runtime. No real dataset or model was invoked; full-suite and real-runtime acceptance remain unproven.

Spreadsheet actor binding and shared native-benchmark declaration validation subsequently switched to the canonical dataset runtime; both modules match their previous AST after normalizing only import paths. The source-only baseline still matches the opted-in dataset data and scorer identities; platform content identity changes independently. The installed-wheel control uses an authored actor binding, not an admitted real runtime.

Focused actor, Harbor policy, judge, identity and import controls reported **111 passed, 3 deselected**. A broader overlapping offline selection including both spreadsheet versions, Harbor declaration checks, H0, mock experiments and wheel controls reported **367 passed, 12 skipped, 3 deselected**, with 15 syntax warnings. The skipped and deselected spreadsheet controls require prepared external runtime artifacts or were not exercised in this run. No real benchmark or model was invoked; real runtime and full-suite acceptance remain unproven.

Native Harbor's dependency preparation and exact runtime-admission helpers subsequently switched to the canonical dataset runtime import. Their ASTs match the prior sources after normalizing import paths; selected source-only dataset identities remain as expected. An isolated child with an authored unbound runtime is refused before importing Harbor.

Focused synthetic Harbor, identity and import controls reported **78 passed, 6 skipped**. A broader offline selection reported **307 passed, 18 skipped, 1 failed** because a newly written installed-wheel subprocess test had an invalid nested string. After correcting that test, the installed-wheel and Harbor/identity recheck reported **42 passed, 6 skipped**. Skips require the explicitly prepared full Harbor artifact. These overlapping controls do not execute a real Harbor trial, model or benchmark and do not establish full-suite acceptance.

## Native research runtime import migration

The native research adapter, component binding and special worker helpers now use the canonical dataset runtime. Their ASTs match the prior sources after normalizing import paths. These files are included in a verifier source-directory commitment, and `special_runtime.py` is also included in SWE/Harvey execution fingerprints: an authored pre-move context now compares as changed and the special-worker bootstrap fingerprint changes while the other fingerprinted modules remain equal. These are platform-code identity changes, not a claim that upstream task data or the installed dependency artifact changed. Historical receipts remain untouched.

Selected offline source-identity, native preparation, managed-worker and import controls reported **159 passed** with 15 syntax warnings; a smaller source-copy isolation check reported **16 passed**. A broader overlapping native research, local relay, H0, mock experiment and installed-wheel selection reported **256 passed, 2 deselected** with 15 warnings. An attempted additional wheel-test edit did not apply because its insertion marker differed; after applying it, the focused source, managed-worker and installed-wheel recheck reported **10 passed**. The deselected structured-judge controls require a prepared runtime. No real model or benchmark was invoked, and these checks do not establish full-suite or real SWE/Harvey runtime acceptance.

## Command-control runtime import migration

The shared command-control helper now imports `harnessgrad.datasets.runtime` directly. Its AST matches the previous source after normalizing that import. The old Git blob ID is retained as a source-only fixture: GAIA's existing source-tree comparison reports the changed platform code and refuses silent evidence reuse. This does not represent a change to task data or the scorer algorithm, and historical receipts were not rewritten.

Selected offline bound-verifier, GAIA source-comparison, dataset-identity and CI controls reported **236 passed** with 15 syntax warnings. A broader overlapping selection including H0, mock experiments, native-research checks, import/integrity boundaries and an installed-wheel admission-refusal control reported **362 passed**, also with 15 warnings. No real model or benchmark was invoked; this does not establish real GAIA or full-suite acceptance.

## Full test discovery and runtime directory

On 2026-11-17, the clean refactor tree at `44d69c76` completed offline `pytest --collect-only -q -p no:cacheprovider tests`: **11,924 tests collected, no collection errors**. Collection establishes discoverability and importability only; it is not execution or a full-suite pass.

On the same tree, `PYTHONDONTWRITEBYTECODE=1 HG_REVIEWED_ENVIRONMENT_ONLY=1 python3 -m pytest -q -p no:cacheprovider --basetemp=/tmp/hg-runtime-suite-current-20261117 tests/runtime` reported **70 passed**. The directory covers budget, event protocol, import paths and local scripted gateway controls; it did not invoke a real model or benchmark. The documented external-runtime and full-suite limitations remain.

## GAIA preparation runtime import migration

GAIA preparation and its isolated Parquet worker now import the canonical dataset runtime. Their ASTs match the previous sources after normalizing import paths. The pre-move source-only fixture confirms that both Git blob identities change, so GAIA's full source-tree comparison refuses silent evidence reuse. New preparation records contain the current worker SHA-256; old records and task data are not rewritten. An isolated authored invalid binding is refused before `pyarrow` is imported.

The first focused selection reported **240 passed, 1 failed** because the new refusal test expected `ValueError` instead of the existing `DatasetRuntimeUnavailable`/`RuntimeError`. After correcting the test, the focused selection reported **241 passed** with 15 syntax warnings. A broader overlapping offline selection including preparation, source comparison, H0, mock experiments and installed-wheel isolated worker checks reported **369 passed**, also with 15 warnings. No gated GAIA data, external model or real Parquet runtime was invoked; these controls do not establish full-suite or real GAIA acceptance.

## CostBench runtime helper import migration

CostBench's execution-binding helper and preparation controller now use the canonical dataset runtime. Their ASTs match the previous sources after normalizing import paths. An authored source-only baseline confirms that preparation-source and verifier-directory commitments change, so old code evidence is not silently reused; this does not establish a change to upstream task data. The batch report, verifier, scoring modules and upstream source declaration matched their prior SHA-256 values and were not edited. Historical preparation artifacts and receipts remain untouched.

Selected offline preparation, runtime-binding, source-identity, CI and import controls reported **171 passed, 11 skipped**. An overlapping broader selection including authored scorer/report checks, H0, mock experiments, integrity and installed-wheel binding controls reported **324 passed, 31 skipped**; both emitted 15 syntax warnings. The skipped checks require prepared CostBench data or runtime artifacts. No real model or benchmark was invoked; these controls do not establish real CostBench runtime or full-suite acceptance.

## Reasoning Gym preparation runtime import

The explicit Reasoning Gym preparation script now imports the canonical dataset runtime. Its AST matches the previous source after normalizing that import. The source-only identity baseline confirms that the historical pool declaration and scorer identity remain unchanged while the loader fingerprint changes, and component comparison refuses silent reuse. The scorer-bearing adapter and the historical pool/receipts were not modified.

Focused offline identity, authored pool-management, loopback transport and import controls reported **60 passed**. An overlapping selection covering runtime identity, H0, mock experiments, integrity and an installed-wheel preflight-refusal control reported **290 passed**; both emitted 15 syntax warnings. No actual Reasoning Gym pool, external model or benchmark was invoked; these checks do not establish real-runtime or full-suite acceptance.

## Experiment record module migration

The record writer was moved byte-for-byte from `harnessgrad/records.py` at `9e8fc747` to `harnessgrad/experiment/records.py`; production consumers now use the latter. The legacy name remains a temporary alias to the same module object, including its process-local cache and patch points. Both files remain covered by platform integrity snapshots. This source-tree change changes the platform content identity; it does not rewrite historical measurements or claim they are equivalent to new ones.

A focused offline selection initially reported **93 passed, 1 failed** because the new module's import boundary omitted its existing function-local `stat` dependency. After correcting the declared boundary, its full import-direction suite reported **16 passed**. The installed-wheel checks reported **2 passed**, including the alias identity check. The selection covered synthetic record components, completion, resume, native-method integration and integrity; it did not execute real models or benchmark datasets.

The subsequent retirement of the record alias migrates repository tests and makes the old import raise a migration error. A production import-boundary check prevents reintroduction, and the installed wheel checks the refusal. Full test discovery found **11,931 tests** with no collection errors after a mixed import was corrected. A selected offline run reported **236 passed, 1 failed**: a resume test detected a platform content identity change while the checkout was being edited concurrently. After edits stopped, the resume end-to-end file passed **2 tests**; the canonical import guard and metadata projection checks passed **10 tests**. These do not constitute a full-suite or real-benchmark run.

## Customization component inspection

`harnessgrad customize inspect` exposes the shared Harness manifest description through a read-only CLI. It reads the bounded manifest only and reports declarations, not verified behavior. Offline customization, input and import-isolation controls reported **46 passed**. The installed-wheel checks passed as part of a run with **17 passed, 1 failed**: the new entrypoint dependency was absent from the import-boundary declaration. After adding the explicit `harnessgrad.core.harness` dependency, the full import-direction suite reported **16 passed**. No model or benchmark was used.

## Additional record consumer controls

On clean refactor commit `c4a29f30`, a selected offline run covering AppWorld reporting, candidate application, editor, GAIA reporting, improver cost, method channel, usage retention, scoring identity, timeout and unmeasured-trial consumers reported **231 passed, 1 error**. The error was fixture setup for `test_a_changed_tree_is_not_a_cache_hit`: the genuine locked Loop SDK dependency `annotated-types==0.8.0` was unavailable in the offline cache. No synthetic substitute or download was used; this test remains unverified. The run does not establish complete-suite or real benchmark correctness.

## Scoring and preparation runtime imports

ChessBench stepwise control, CostBench reporting, and JobBench renderer preparation/worker imports now use the canonical dataset runtime. A pre-change synthetic source fixture records normalized AST and ChessBench component identities: only the import syntax changed; the data source and loader identities stayed equal while scorer and scoring identities changed because the controller bytes are part of scoring provenance. The alias remains for other callers. These checks neither reclassify historical measurements nor establish real-dataset equivalence.

The first offline selection reported **63 passed, 14 skipped**, including JobBench's missing-runtime refusal, Chess controller tests and CostBench scoring controls. The skips require genuine optional environments. A second selection reported **51 passed, 1 error**: the Chess native-runtime control requested pinned source bytes that had moved since its original capture. Two historical source files were recovered at their exact manifest SHA-256 values and retained as test-only fixtures with provenance; the failed control then reported **1 passed**. The pinned scorer manifest and production scoring code were not altered. The 51 passing controls included the import-direction and installed-wheel checks. No model or benchmark was run.

## Tau preparation and ScienceWorld operator imports

The Tau2 and Tau3 preparation entrypoints and ScienceWorld operator control use the canonical dataset runtime. An authored pre-change AST fixture verifies the import is the only syntax difference. The Tau source-admission control verifies exactly the two changed preparation-file hashes differ, accepts the current identity and rejects the pre-change identity. No dataset or scorer source was changed.

Selected offline preparation and operator tests reported **64 passed, 7 skipped**; the skipped controls require explicitly provisioned native artifacts. Import-direction and installed-wheel tests reported **18 passed**, with 15 pre-existing syntax warnings. No real benchmark or model was run.

## Scoring identity mismatch diagnostic

A scoring-profile mismatch now reports that the recorded profile or implementation differs and comparability is unknown, rather than asserting that the judge, endpoint or protocol changed. This only changes the diagnostic text: identity validation and refusal decisions remain the same. A synthetic scorer-hash-only change exercises that distinction. Scoring/provenance controls reported **85 passed**; H0 subset, resume statistics and evidence-comparability controls reported **66 passed**. No real model or benchmark was used.

## Reasoning Gym adapter runtime import

The Reasoning Gym adapter imports the canonical runtime path. A pre-change source-only fixture verifies the normalized adapter AST is unchanged, the data source declaration stays equal, and the loader, scorer and scoring identities change because the adapter is shared by both roles. Historical and current captures are refused as interchangeable; no historical receipt is rewritten.

Selected source identity, managed-lifecycle and native QA controls reported **80 passed, 7 skipped**; the skips require optional prepared runtimes. Import-direction, installed-wheel, runtime identity and measurement-record controls reported **66 passed** with 15 pre-existing syntax warnings. The controls use synthetic data and did not run the benchmark or a model.

## Service adapter runtime imports

AgentDojo, ALFWorld, AppWorld, Jericho and ScienceWorld adapters now import the canonical dataset runtime. Source-only controls compare their normalized ASTs to pre-change fixtures; no dataset or scorer identities were invented for these adapters, which do not currently opt into the independent profiles. The platform content identity changes. A metadata-privacy test's exact import allowlist was updated without removing its file, network or process guards.

The first selection reported **97 passed, 8 skipped, 16 failed** because that allowlist still named the old runtime import. With the allowlist updated, the same selection reported **113 passed, 8 skipped**. Import-direction, installed-wheel and provider-registry checks reported **26 passed** with 15 pre-existing syntax warnings. Skips require native runtime artifacts; no model or released benchmark was run.

## Remaining adapter runtime imports

DS-1000, LiveCodeBench, Tau2 and Tau3 now use the canonical runtime import. Authored pre-change fixtures verify the normalized AST of each adapter remains unchanged. These four adapters do not opt into independent data-source or scorer identities; no cross-version equivalence is inferred. Tau preparation source-admission verifies both adapter hashes changed and rejects the old source identity. Production Python imports no longer require the old alias, which remains for tests and external integrations.

The first offline selection reported **96 passed, 23 skipped, 1 failed**. The failed Tau2 readiness test expected the detail to mention a missing manifest, but the current environment first reports a missing managed runtime; the same test failed with the same detail on a separate clean source archive at pre-change commit `7cf6309f`. Neither the production readiness order nor the assertion was changed. The import-boundary, wheel, provider and Tau preparation selection reported **75 passed, 5 skipped**, with 15 pre-existing syntax warnings. Skips need optional runtime artifacts; no model or released benchmark was run.

## Dataset runtime test import migration

Seventy-five test modules and fixtures, including isolated subprocess source strings, now import the canonical dataset runtime. The old alias remains for the dedicated compatibility checks. Full test discovery reported **11,955 collected with no errors**; a selected offline runtime, provider, H0 and Tau preparation suite reported **203 passed, 27 skipped**. Skips require optional native environments. No model or released benchmark was used.

The old `harnessgrad.dataset_runtime` module now refuses import with guidance to `harnessgrad.datasets.runtime`; production and test imports were migrated. Fresh-process controls cover both import orders, the isolated registry and provider worker, and installed-wheel refusal. A selected suite reported **153 passed** with 15 pre-existing syntax warnings. The first full collection found two mixed-import sites; after updating them, full collection reported **11,955 tests with no errors**. The two associated provider suites reported **35 skipped** because their prepared optional environments were not selected. No model or released benchmark was run. This is an intentional source API change; external users must update imports, and historical receipts remain unchanged.

## Dataset registry maintenance grouping

Blocker validation, reviewed-proposal application and semantic registry merge implementations now live under `tools/registry_ops`; their former CLI and Python import paths remain identity-preserving entrypoints. A source comparison against the previous tree confirmed their implementation text changed only where repository root and checker locations had to follow the move. No registry file was modified.

Synthetic command equivalence, refusal and historical-evidence controls, source import boundaries, platform integrity and installed-wheel checks reported **58 passed** with 15 pre-existing syntax warnings. The validation did not invoke `--apply`, a model or a benchmark.

The test selector treats `tools/registry_ops/` and its three former top-level entrypoints as shared core paths. Previously, a change to the moved applier could select only its basename-matched test and omit other registry consumers. A synthetic-path control verifies that all seven paths fall back to the full-suite selection; the existing CI policy still defers that full path to nightly or manual dispatch. Selector, registry maintenance and dataset-documentation controls reported **163 passed**.

## Onboarding and provenance-control selection

The read-only blocker checker reported a valid index of 56 datasets, and `tools/datasets_doc.py --check` confirmed the checked-in data documentation matches adapter declarations. Documentation links and commands, CLI and customization review/patch controls reported **76 passed**. The LightRSI fix commit remains an ancestor of the refactor branch.

CI selection now includes runtime-consumer source/identity migration controls for their dataset adapters and maps the corresponding fixture directory to its consumer. The ChessBench native-source fixture is mapped to both of its test consumers. Unknown fixtures still fall back to the full suite. Selector and migration controls reported **139 passed**; these selections do not by themselves run a benchmark or establish real-runtime compatibility.

## Experiment method-attempt accounting

`MethodAttempts` now lives in `harnessgrad.experiment.method_attempts`; its implementation is byte-for-byte identical to the prior `harnessgrad/method_attempts.py` at `dcff21ea`. The former import refuses with migration guidance, and the new implementation is covered by platform integrity snapshots. This is a source API and platform content identity change, not a migration of existing attempt ledgers.

Selected synthetic LightRSI method-admission, terminal budget, treatment resume, request budget, import-boundary, integrity and installed-wheel controls reported **73 passed** with 15 pre-existing syntax warnings. They do not establish real-model or full-benchmark equivalence.

## Experiment method evidence channel

Experiment-specific method evidence projection now lives in `harnessgrad.experiment.channel`. Its implementation is byte-for-byte identical to the former `harnessgrad/channel.py` at `21237083`. The old import refuses with migration guidance; the relocated implementation is included in integrity snapshots. This changes platform content identity and the source API, not the recorded channel format or historical evidence.

Synthetic channel, private-case projection, verification-context, trace, state, import-boundary, integrity and installed-wheel checks reported **116 passed** with 15 pre-existing syntax warnings. Scripted native-method and shared-customization controls reported **27 passed**. These controls do not establish real-model or full-benchmark equivalence.

## Method runner relocation

Method sandbox execution, readiness checks and platform-tamper exception ownership now live in `harnessgrad.execution.method_runner`. The implementation is byte-for-byte identical to `harnessgrad/methods.py` at `542cc7d6`. The old module remains a same-object alias, including its globals and exception class; production call sites use the new import. Both implementation and compatibility file are covered by platform integrity. The platform content identity changes, while existing historical receipts remain unchanged.

Synthetic method transport, readiness, invocation admission, native-method integration, import-boundary, integrity and installed-wheel checks reported **103 passed** with 15 pre-existing syntax warnings. Fresh-process checks of both import orders and patch-point identity reported **2 passed**. No real model or benchmark was run.

The former `harnessgrad.methods` import now refuses with migration guidance; repository tests and subprocess snippets use `harnessgrad.execution.method_runner`. Full test discovery reported **11,964 tests with no collection errors**. A focused offline selection reported **145 passed, 1 failed** with 15 pre-existing syntax warnings. The failed local-endpoint sandbox control expected one synthetic call but observed zero; a separate clean archive at pre-change commit `4d8cdae8` reproduced the same failure. No method behavior or assertion was changed to mask it. The installed-wheel and fresh-process checks cover the new path and refusal. No real model or released benchmark was run.

The previously failing local-endpoint method sandbox test was diagnosed further: its synthetic channel lacked `round.json`, so the editor correctly refused missing platform round evidence before dispatch. The fixture now includes a synthetic train-side round record. The previously failing test passes and verifies one actual loopback request; the complete endpoint, import-migration, transport and readiness selection reported **64 passed**. No production logic, real endpoint, model or benchmark was changed or used.

The previously failing Tau2 readiness fixture assumed a missing case manifest would be reported before managed-runtime admission, but the actual controller admits the runtime first. Its test now uses an explicitly absent temporary runtime store and checks refusal without creating that store. A separate synthetic preparation test verifies that, once bundle inspection is reached, a missing case manifest is rejected before runtime inspection. No preparation or scoring implementation changed. The Tau preparation and runtime selection reported **65 passed, 7 skipped**; the previously failing adapter migration selection now reports **97 passed, 23 skipped**. Skips require optional native artifacts.

## Orchestration readiness grouping

Run readiness checks now live in `harnessgrad.orchestration.readiness`; the shared readiness fact stays in `harnessgrad.core.readiness`. The former import remains an identity-preserving alias for existing callers. AST comparison against `fc4bf28f` confirms the code is identical after removing a repeated `shlex` import and the first of two identical `_split_check` definitions. The relocated implementation is protected by platform integrity; platform content identity changes, and historical receipts are not rewritten.

The first selected readiness, run planning, import, integrity and wheel suite reported **112 passed, 1 failed** because the new import-boundary declaration omitted an existing function-local Python runtime dependency. After recording that precise dependency, the complete import-direction suite reported **16 passed**. Fresh-process tests for both import orders and patch-point identity reported **2 passed**. Tests used synthetic fixtures and did not run a model or released benchmark.

The former `harnessgrad.readiness` import now refuses with migration guidance; tests and production callers use `harnessgrad.orchestration.readiness`. Full test discovery reported **11,967 collected with no errors**. A selected readiness, Console, import, integrity and wheel suite reported **156 passed, 5 failed** with 15 pre-existing syntax warnings. The five release preflight controls stopped during genuine Loop SDK preparation because locked `openai==2.21.0` was unavailable offline; their intended completion assertions remain unverified. No synthetic SDK replacement or download was used.

## Orchestration environment grouping

Run environment selection and reporting moved to `harnessgrad.orchestration.environments`. The implementation is byte-for-byte identical to `harnessgrad/environments.py` at `634d4198`; the former name temporarily aliases the same module. Production imports use the canonical path and both files remain in the platform integrity snapshot. Platform content identity changes; historical receipts remain unchanged.

An initially broad test selection was interrupted after discovering it included an unauthorised real-Docker control. Before interruption it reported **113 passed, 1 deselected**, but the selection did not complete. Docker event inspection showed that the control created and destroyed the named test container `hg-test-overlay-commit` and tagged and untagged `hg-test-overlay-snap`; no prior removal event for those names was observed in the inspected event window. The later explicitly selected offline controls reported **82 passed, 1 deselected**, with 15 pre-existing syntax warnings. They cover environment records, failure reporting, module alias state, import boundaries, integrity and installed wheels. No real model or released benchmark was run.

The Docker overlay test that ran inadvertently has been corrected to use a unique container name and image tag per invocation. It no longer deletes fixed names before starting, and only cleans a container or image after its own creation succeeded. Two synthetic subprocess-double checks cover successful cleanup and a failed create; **2 passed**. These checks did not invoke Docker. This changes test isolation only, not experiment execution or scoring.

The former `harnessgrad.environments` import now refuses with migration guidance; internal callers use `harnessgrad.orchestration.environments`. Offline controls for environment records, failure reporting, fresh-process import refusal, architecture, integrity and installed wheels reported **82 passed, 1 deselected** with 15 pre-existing syntax warnings. Full collection reported **11,971 tests with no errors**. The real Docker test was not run in this validation.

## Cross-layer regression after orchestration and runtime migrations

On clean refactor commit `a507933e6ca215fb0a902810b708b33b08922666`, the same selected offline cross-layer command listed under “Cross-layer regression after provenance and package migrations” was repeated with the external basetemp `/tmp/hg-cross-layer-after-refactor-20261117`. It reported **760 passed**, with 15 pre-existing syntax warnings. The selection covers shared improvement and customization, scripted pre-refactor method controls, mock experiment and resume, provenance, Workbench services, CI, import and integrity boundaries, clean installation and installed wheels. LightRSI fix commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` remains an ancestor. This record is added after the checked commit and changes the platform content identity.

The selection uses authored fixtures, mock Harnesses and scripted local services. It does not run real models, released benchmarks or the full test suite, and does not resolve the missing genuine Loop SDK/native runtime dependencies documented above.

## OPRO synthetic differential after method-channel relocation

At clean refactor commit `19e76ca66da7067f976efb1bac3065d978643fa5`, the authored OPRO integration control passed using a mock Harness and scripted loopback endpoint. The test source SHA-256 `42d0be1ced6a3b2e2232671ed1ffcf691d2b5242678703f2dff3e0ed385da54d` matches the version at LightRSI fix commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8`. Its four round projections match the previously captured fix-branch control for round, score, per-task values, side, score kind, scored/invalid/Harness-failure counts, scorer isolation, method report, selected task IDs and Harness tree hash; scores remain `0 → 1 → 0 → 1`.

This is a one-task authored control, not evidence of equivalence for other methods, datasets, platforms or live models. Run paths and platform identities differ, and no historical score is reused. The new control was run with `PYTHONDONTWRITEBYTECODE=1 HG_REVIEWED_ENVIRONMENT_ONLY=1 python -m pytest -q -p no:cacheprovider tests/quality/test_native_method_integration.py --basetemp=/tmp/hg-opro-after-orchestration-20261117`; it reported **1 passed**.

## ACE, SkillOpt and EvoHarness fix-branch differential

A temporary source archive of LightRSI fix commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` and the clean refactor tree at `d20a89ba` ran the same authored pre-refactor policy controls. The test files and public synthetic reference fixtures were copied into the temporary fix-branch archive; `tests/quality/native_helpers.py` has the same SHA-256 (`18f9fead4266a9fc296097528c1613533010db1e8320b97bd7cc559bc5b38431`) in both trees. Each run reported **4 passed** against the same historical fixtures: ACE and SkillOpt prompts, candidate contents and reports, plus EvoHarness topic/general compilation and unknown-source refusal.

This establishes equality only for those scripted cases; it does not establish full method equivalence, real-model quality or benchmark results. No dataset, model or external endpoint was invoked, and no historical experiment record was modified.

The previously recorded Responses overlay failure controls now supply a synthetic `goal`, as required by the existing task contract. Both timeout and nonzero-exit cases passed, retaining their assertions on capture preservation and task cleanup. The full synthetic Responses overlay and Bun runtime contract selection reported **48 passed**. This changes only a test fixture; no production runtime, provider or model was invoked.

## AgentDojo source and scoring provenance

AgentDojo now opts into separate declared data source, loader and scorer identities. The source envelope records the pinned archive declaration and prepared-manifest digest; it does not assert downloaded or selected task bytes were verified. Its adapter, bootstrap and bridge are shared by loader and verifier, so they appear in both fingerprints. The upstream manifest covers code and suite data together and is conservatively included in scorer identity; admitted runtime and selected verifier inputs remain separately bound. Pin disagreement, changed declarations and concurrent shared-source mutation fail closed. Older records without the new evidence remain readable but are not automatically comparable. No dataset or scoring implementation was edited.

Source-only, privacy, execution-boundary, identity, architecture, integrity and installed-wheel controls reported **172 passed, 4 skipped**, with 15 pre-existing syntax warnings. A subsequent source-race control passed, and a selection covering AgentDojo source checks, H0 preparation, evidence and resume reported **82 passed**. Skips require optional native runtime artifacts. The checks did not run AgentDojo tasks or a model and do not establish full real-runtime equivalence.

A further source-only AgentDojo control resolves the actual profile through selected-run planning, writes it into an authored curve point and H0 configuration, and checks the H0 identity copies. Missing historical components and an authored loader-source change are refused; the latter leaves the declared source and scorer identities unchanged. The AgentDojo, H0, evidence and resume selection reported **82 passed**. Runtime and verification preflights in this control are explicit doubles, so it does not establish that a real AgentDojo environment is prepared or that any benchmark task ran.

An authored export control using the actual AgentDojo profile confirmed that a changed loader is identified as a comparison mismatch while the declared-source group remains equal, and that the source label and raw component hashes are withheld from the export. A clean wheel installation, after removing its build source tree, resolved and aligned the AgentDojo profile without the dataset or SDK. The combined export and clean-install selection reported **4 passed**.

## ALFWorld source and scoring provenance

ALFWorld now opts into separate declared data-source, loader and scorer identities. The source envelope identifies the four upstream archive declarations, not verified local game bytes. The adapter, replay helper and scorer-input binding are shared between loading and verification and are conservatively fingerprinted in both roles; selected game inputs and native runtime remain separately bound. A synthetic file relocation with identical bytes and the same role preserves the component identities, while the platform identity remains separate. Historical records lacking the identities are not automatically comparable. No dataset adapter or scoring algorithm was changed.

Source-only identity, managed-runtime boundaries, architecture, integrity, wheel and clean-install controls reported **107 passed, 1 skipped**, with 15 pre-existing syntax warnings. A subsequent identity, relocation, H0, evidence and resume selection reported **81 passed**. The skipped control requires optional prepared native artifacts. No real ALFWorld task, model or Docker operation was run.

The AgentDojo and ALFWorld source-profile flow controls now share one parameterized test. For both profiles it resolves the actual source-only identity through selected-run planning, an authored curve point and H0 configuration; missing historical components and a loader-only source change are refused while the data-source and scorer identities remain equal. Runtime and verification preflights are explicit doubles. CI dataset selection includes this shared consumer. The shared flow, both source-profile suites and CI selector reported **134 passed**; no real task, runtime or model was invoked.

On clean commit `6da82a74`, full test collection reported **11,988 tests with no errors**. The evidence-export control is now shared by AgentDojo and ALFWorld: it checks that a loader change makes comparison fail, the unchanged data-source group remains equal, and raw source labels and hashes are not exported. CI selections for either adapter include the H0 flow, evidence export, wheel and clean-install controls. The selected export, CI and H0 flow suite reported **125 passed**; it used authored records only and did not run a real model or dataset.

## Jericho source and scoring provenance

Jericho now opts into separate declared data-source, loader and scorer identities. Its source envelope records the original archive URL and declared SHA-256 without inventing an upstream commit or claiming that a selected ROM was verified. The adapter, replay environment and scorer-input binding participate in both loading and verification and are conservatively fingerprinted in both roles. Selected ROM inputs and the admitted runtime remain separate. A synthetic file relocation with identical bytes and role preserves component identities, while the platform identity remains separate. Historical records without these identities are not automatically comparable; no adapter or scoring implementation was changed.

Synthetic source and runtime-boundary, H0 flow, evidence export, CI, architecture, integrity, wheel and clean-install checks reported **200 passed, 1 skipped**, with 15 pre-existing syntax warnings. A subsequent source-profile suite including the identical-file relocation control reported **5 passed**. The skipped control requires optional prepared native artifacts. No real ROM, model or Docker operation was run.

## ScienceWorld source and scoring provenance

ScienceWorld now opts into separate declared data-source, loader and scorer identities. The source envelope fingerprints a normalized projection of public test/study task declarations, the declared gold-path archive and world-JAR digest; comments and JSON formatting do not change that identity. This is a declaration, not proof that an upstream archive or particular world was used. The adapter, environment helper and world lookup are shared by loading and verification and are conservatively fingerprinted in both code roles. Selected task inputs and actual runtimes remain separately bound. Loaded fold/study caches, package version and step budget must agree with the declaration; changed shared source or input declarations during capture fail closed. Historical records lacking these identities are not automatically comparable. No dataset adapter or scoring algorithm was changed.

Synthetic source, cache-consistency, runtime-boundary, H0 flow, evidence export, CI, architecture, integrity, wheel and clean-install checks reported **195 passed, 2 skipped**, with 15 pre-existing syntax warnings. Skips require optional prepared native artifacts. No real ScienceWorld task, JVM, model or Docker operation was run.

Fresh-process source-only controls now import the real ScienceWorld adapter and load its public static fold for default, `dev` and `test` study settings. Each setting successfully captures and aligns the provenance profile with the adapter's loaded caches without creating a dataset root or starting a JVM. The ScienceWorld source-profile and shared H0 flow tests reported **15 passed**. These checks do not run ScienceWorld tasks or verify a prepared runtime.

## AppWorld provenance boundary

The AppWorld source review found that the public encrypted-bundle declaration cannot by itself identify the private evaluator and package executed inside the selected verifier image. No incomplete scoring profile was added. A source-only control compiles the existing verifier declaration with authored values and verifies that changing the image reference or selected split changes the private verification-context commitment without exposing the raw image reference or task goal. This commitment proves equality of captured declarations, not the actual contents or admission of an image. AppWorld reporting, verification-context and evidence controls reported **36 passed**. No AppWorld adapter, task data, model or Docker service was invoked.

The source-only test fixtures for AgentDojo, ALFWorld, Jericho and ScienceWorld now share a small helper for copying their declared implementation files into an owned temporary tree. Dataset-specific extra manifests remain explicit; production profiles and measurement behavior were not changed. The four provenance suites, shared H0 flow and CI selection controls reported **152 passed** without model, benchmark or Docker execution.

## Shared source declaration parsing

AgentDojo, ALFWorld, Jericho and ScienceWorld now use `harnessgrad.provenance.source_declarations.literal_assignments` for their common top-level literal parsing. It never imports or executes the inspected source and rejects missing, duplicate or nonliteral declarations; chained assignments require explicit opt-in. Dataset-specific validation and role boundaries remain with each profile. A pre-change capture compared after extraction showed identical data-source, loader, scorer and scoring identities for all four datasets. Platform content identity changes and historical records remain unchanged.

Synthetic parser, four provenance suites, H0 flow, runtime migration, evidence export, import-boundary, integrity, installed-wheel and clean-install controls reported **97 passed** with 15 pre-existing syntax warnings. No dataset, model or Docker service was run.

AgentDojo source-profile capture now refuses when an already loaded adapter's revision or suite version differs from the source declaration being fingerprinted. A fresh-process control imports the actual adapter with an import blocker for the optional SDK and an absent dataset root; the profile still aligns without loading either. A source-only comparison confirms unchanged data-source, loader, scorer and scoring identities for matching declarations. The selected AgentDojo, metadata-privacy, H0 and runtime-migration controls reported **97 passed**; the final AgentDojo source suite reported **9 passed**. No AgentDojo task or model was run.

Reasoning Gym and LoCoMo source profiles now use the shared top-level literal reader. Source-only captures before and after the change matched exactly for data-source, loader, scorer and scoring identities in both datasets; the platform content identity changes independently. The parser, historical-profile, H0, evidence, import, integrity and clean-install controls reported **82 passed** with 15 pre-existing syntax warnings. No model, dataset or Docker service was run.

Three H0/runtime identity preflight subprocesses now explicitly use strict text decoding. Their outputs are identity-bearing JSON, so invalid bytes remain a failure rather than being replaced; this is the existing default behavior made explicit, not a new acceptance rule. A local malformed-byte control confirmed implicit and explicit strict decoding both reject the same invalid output. Synthetic H0, dataset-runtime identity, source-profile and measurement-record controls reported **94 passed**. The broader decoding inventory guard still fails: its expected count is stale and 22 unreviewed call sites remain; no blanket exemption or count-only update was made.

OpenCode version probing, Harness installer toolchain versions and the DS-1000 interpreter-target JSON now explicitly retain their existing strict text decoding. These are identity/configuration probes: replacing malformed bytes could produce a different accepted identity. Synthetic installer, OpenCode runtime-path and DS-1000 preparation controls reported **69 passed**. The decoding inventory guard still fails on its stale counts and **19 unreviewed sites**; no blanket exception or count-only update was made. No model, dataset download or Docker operation was invoked.

The decoding audit now distinguishes `replace`, byte-preserving `surrogateescape`, and individually reviewed strict identity/configuration probes. An explicit but unreviewed `strict`, `ignore` or dynamic error mode is reported rather than silently accepted; nine existing strict sites have specific reasons and are checked for drift. Synthetic scanner and audit-diagnostic controls reported **6 passed**. The full inventory audit still fails on its stale inventory and **19** unresolved sites; no count-only update or blanket exemption was applied. Production code and decoding behavior did not change in this audit update.

Native Harbor import probing, native QA/research preparation workers and the Tau preparation supervisor now explicitly retain their existing strict decoding. Their individually reviewed strict sites remain in the decoding audit; malformed worker output is not silently substituted. Before/after source-only captures showed MATH and MATH-500 data-source and scorer identities unchanged, with loader identities changing because the preparation source bytes changed. The Tau preparation supervisor source identity also changed; earlier source identities are still rejected rather than rewritten. No scoring implementation changed.

Synthetic worker, runtime, provenance, H0, decoding-policy, architecture and integrity controls reported **79 passed, 10 skipped**, followed by **49 passed** with 15 pre-existing syntax warnings. Skips require optional prepared runtimes. The full decoding guard remains failing on stale inventory counts and **15** unreviewed sites; no count-only update or blanket exception was applied. No released benchmark, model, download or Docker service was run.

ScienceWorld's trusted reference-action control and the Reasoning Gym pool-generation and download workers now explicitly retain their existing strict decoding: malformed structured output is rejected, not silently altered. The scoring implementation is unchanged. A before/after source-only comparison showed ScienceWorld data-source, loader and scorer identities unchanged; Reasoning Gym's data-source and scorer identities stayed equal while its loader identity changed with the preparation source bytes. The pre-existing migration fixture continues to verify the exact original AST after normalizing this one reviewed strict-decoding annotation.

Synthetic preparation, source-provenance, control and decoding-policy checks reported **56 passed, 2 skipped**; skips need prepared native artifacts. The full decoding guard still fails because its call inventory is stale and **12** sites remain unreviewed. No dataset, model, network download or Docker service was run.

GAIA Parquet preparation and the JobBench renderer controller now explicitly retain their existing strict decoding for structured worker output. A source-only comparison confirms the GAIA controller and JobBench preparation controller identities changed while their worker bytes and optional scoring-profile states remain unchanged; this is code/preparation provenance, not a change to task data. The historical JobBench migration check normalizes exactly this reviewed strict-decoding annotation before comparing its previous AST.

Synthetic preparation, source-identity and strict-policy controls reported **34 passed**; architecture, integrity and installed-wheel checks reported **31 passed** with 15 pre-existing syntax warnings. The full decoding guard remains failing because the inventory is stale and **10** sites remain unreviewed. No real dataset, model, network download or Docker service was run.

Six source-bound scoring/native-runtime decoding sites were reviewed individually without changing their bytes: Reasoning Gym's QA wrapper, the isolated UNO worker and four native inventory probes. An authored invalid-byte control confirmed that a Reasoning Gym child can leave a reward file but its nonzero wrapper exit is treated as unmeasured under `require_exit_zero`. A synthetic UNO failure control confirmed that a decoding error occurs before candidate workbooks are copied back. Native inventory probes retain strict refusal to avoid accepting altered module, font, dependency or version identities. These are specific recorded exceptions, not a claim that malformed output is readable.

The scorer/native identity, source provenance and decoding-exception controls reported **41 passed, 1 skipped**; the skip requires an optional native runtime. The full decoding guard remains failing because its inventory counts are stale and **four** other calls remain unreviewed. No scorer source, model, benchmark, LibreOffice process or Docker service was changed or run.

## Decoding inventory reconciliation

The remaining four implicit-strict sites were individually reviewed with authored controls: invalid output makes AgentDojo and Tau3 readiness unavailable, prevents CostBench publication, or stops the manual ALFWorld control before scoring. Source-bound code was retained. The source inventory was then compared across original main `1eedb4c5`, LightRSI fix `a0f837fd` and the refactor: all have 138 matched calls; four moved from tools into the framework. Normalized AST call comparison accounted for every call, with only the expected working-directory parent-depth change after module relocation. No untracked Python files were present in the scanned scope. The full method and limitations are in [decoding-inventory.md](decoding-inventory.md).

Only after that review was the stale per-area count updated. The complete decoding guard, policy diagnostics and authored readiness/publication/scorer controls reported **16 passed**, with one pre-existing syntax warning. No real model, dataset, LibreOffice process or Docker service was run; the guard is a source audit rather than a complete runtime safety proof.

After adding the public inventory review to the documentation allowlist, the documentation-link, decoding-diagnostic and complete decoding-guard selection also reported **30 passed** with the same pre-existing syntax warning.

## Consolidated history validation

The candidate commit `aa3a3f17bf3609337089f548d4c753466fe26a9e` had a tree byte-for-byte identical to the prior refactor head `1e19890df33474fe9c21e08de9c4d8ecdfc35714` and retained both `a0f837fd` (LightRSI fix) and `1eedb4c5` (original local main) as ancestors. Its offline cross-layer selection reported **910 passed, 16 warnings** without real models, benchmark datasets or Docker. This documentation update subsequently changes the platform content identity. Genuine Loop SDK and other native runtimes, and full-suite acceptance, remain unverified. The cited original development history is retained locally but is not automatically published.

The original checkout’s locally recorded `origin/main` at `8264a916bd9a6f3fbe7b0ef49437efc6be6f0fe1` is an ancestor of LightRSI fix `a0f837fd` and therefore of the consolidated branch. This verifies commit ancestry, not that the remote has not advanced since that local observation. A separate offline regression selection on the consolidated branch for namespaced historical traces, improver audit ledgers and difficulty-report model pooling reported **11 passed**; it does not exercise actual services or establish full experiment equivalence.

A further LightRSI-focused offline selection on the consolidated branch covered CLI archive cost recovery, argument credential redaction, usage evidence and private improver artifacts: **33 passed**. Tests used synthetic subprocesses and temporary files; they do not establish real provider accounting or live CLI behavior.

## Retired container aliases

The unused `eval.container_state` and `eval.sandbox` aliases now refuse imports with the canonical `eval.containers` destinations. The GAIA v1 required source path `eval/container_state.py` remains present; its changed bytes change current platform/source identity, and historical identities are not rewritten. The implementation and scorer are unchanged. Offline import, documentation and layering controls reported **64 passed, 1 skipped, 102 deselected**, with 15 syntax warnings; separate synthetic GAIA source and missing-required-path controls reported **10 passed**. These checks do not run a real container, model or benchmark and do not establish full experiment equivalence.

After the alias retirement, the authored OPRO mock experiment and ACE, SkillOpt and EvoHarness scripted compatibility controls reported **5 passed** with a local scripted endpoint and no real model or benchmark. A separate GAIA source comparison collected the actual parent and retirement commit Git trees with a synthetic equal runtime binding: it identified both changed legacy paths and refused source compatibility. This is a provenance control, not an execution or scorer equivalence claim.

A subsequent post-retirement offline selection exercised historical measurement evidence, multi-round reuse and resume, treatment drift refusal and H0 subset views with authored mock tasks: **36 passed**. Temporary run, work and slot directories were isolated from existing runs. These controls do not establish equivalence for live models or released benchmarks.

A separate detached checkout of LightRSI fix `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` ran the corresponding authored evidence-reuse, treatment-resume and H0-subset test files: **34 passed**. The refactor's corresponding selection reported **36 passed**, including two additional separated-identity cases; the other test changes are canonical import paths. Both runs used isolated temporary paths and mock tasks. Passing the same assertions does not by itself prove byte-for-byte equivalence of every result or cover real benchmarks.

The `eval.verifier` alias was subsequently retired in the same way; its only in-repository test patch target now uses `eval.verification.verifier`. Its old file remains required by GAIA source-identity v1, while the verifier implementation and scorer are unchanged. Import, Jericho exception-handling, documentation and synthetic GAIA controls reported **70 passed, 1 skipped**; the expanded GAIA changed-path check reported **9 passed**, and canonical-import, Jericho provenance and decoding controls reported **12 passed** with 3 syntax warnings. No real model or benchmark was invoked.

The remaining `eval.container` alias was also replaced by a migration refusal; its old path remains required by GAIA source-identity v1. Import, GAIA, documentation and Jericho exception controls reported **72 passed, 1 skipped** with 2 syntax warnings. A separate mocked container-state test first reported **4 failures** because its task fixture supplied `prompt` but omitted the existing required `goal` or `instruction`; the same fixture spelling exists at the LightRSI fix baseline. Updating only the authored fixture to include `goal` produced **4 passed**. The test mocks container operations and does not execute Docker, a model or a benchmark; no container implementation or scorer was changed.

The former `eval.runner` alias now directs imports to `eval.run.runner`. Internal QA-control and outcome tests patch or import the canonical implementation; the unchanged historical file path remains required by GAIA source-identity v1. Canonical import, QA-control, outcome and documentation checks reported **79 passed, 1 skipped** with 2 syntax warnings. A separate authored OPRO mock experiment, synthetic GAIA changed-path and mocked container-state selection reported **15 passed**. No real model, dataset or Docker service was used. Preserved descriptions may still mention the historical path.

The four remaining top-level evaluation aliases (`gaia_preflight`, `harness_runtime`, `scorer_bundle` and `separated_stdio`) now direct Python imports to their canonical modules. Their original `python -m` commands and the standalone stdio script still delegate to those implementations. The offline import-boundary, old-versus-new CLI help and documentation selection reported **66 passed, 1 skipped**, with 2 syntax warnings. Help parity does not establish equivalence for every command invocation; no real model, dataset or Docker service was used.

For the retained command delegation, additional offline controls used a real synthetic scorer bundle: both old and canonical commands returned the same valid receipt, then rejected a tampered file. Both harness-runtime commands also returned the same diagnostic for an authored manifest. The evaluation migration controls reported **41 passed, 1 skipped**; the namespace scorer's operational command remains unexercised by these two added checks.

An offline clean-wheel control now also checks all nine newly retired evaluation imports against their canonical implementations from an isolated installed wheel, and checks that the four retained module commands start from that installation. The complete reviewed-resource and installed-wheel test file reported **2 passed**; it builds from tracked source with no index access and exercises authored local inputs only.

## Default test discovery

A plain repository-root pytest collection originally found **12,037 tests** but failed on two authored, intentionally broken exercise files under `data/authored/`. Default discovery now targets `tests/` and `console/uat/`: the same **12,037 tests** collect without errors. An explicitly named exercise remains selectable and produces its authored assertion failure. Focused Console mock and evaluation migration controls reported **49 passed, 1 skipped**. Collection is not execution or full-suite acceptance.

The later consolidated candidate `e4a402e7591b90e88a40cbadcac926a7c77202ef` had the same tree as pre-consolidation `62b1ebf9`, retained both integration ancestors and reported **208 passed, 1 skipped** across evaluation imports, test selection, documentation and installed-wheel controls. A subsequent documentation-only update records this result and changes platform content identity. Three full-length cited commits outside the original retained development history (`60ff81bb725c99641592c9768da249f121b18230`, `aa3a3f17bf3609337089f548d4c753466fe26a9e`, and `b05b813fdd2fe8ac7fe735268bc112d467e4b2be`) are locally reachable through `refactor/shared-foundation-validation-evidence-20261117`; that branch and `refactor/shared-foundation-history-20261117` are not automatically published.

The retained stdio command was additionally exercised through both old and canonical entrypoints with an authored invalid test payload. Both produced the same unmeasured refusal; the control intercepts the fixed input and verdict paths and does not execute a candidate or a sandbox. **1 passed.**

## GEPA proposal extraction

A scripted GEPA port control was captured from LightRSI fix `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` before extracting its instruction-prompt and reply-validation helpers. The extracted port reproduced the same prompt, candidate files and report byte-for-byte after temporary-path normalization. The copied compatibility test and existing GEPA selector tests reported **3 passed** on both trees; the first baseline attempt had failed because its new fixture directory had not yet been created, then passed after creating it. The shared proposal accepts explicitly supplied failure feedback and does not perform GEPA parent selection, task scoring or adoption. Broader scripted GEPA, other policy compatibility, customization, CI, documentation and installed-wheel controls reported **187 passed**, followed by **1 passed** for the updated fixture selection. No real model or benchmark was invoked; one scripted case cannot establish full algorithm equivalence.

Four additional authored GEPA boundary captures from the same LightRSI fix matched the extracted port after normalizing temporary paths: no failing task, duplicate instruction, empty instruction and non-string instruction. The compatibility and selector selection reported **7 passed** on the fix baseline; the extracted branch reported **13 passed** including shared-proposal controls. No real model or benchmark was used.

## Optional instruction refinement in customization

Customization can optionally apply the shared GEPA proposal role after instruction curation when explicit failure feedback is present. The same logical-call budget covers the additional call; invalid input, exhaustion or invalid model output returns no partial candidate. Offline review checks stage order and required inputs, and patch export remains source-checked. The option composes with memory proposals; it is absent by default. The scripted local-endpoint, review, patch, documentation and installed-wheel selection reported **111 passed**; the separate GEPA, ACE, SkillOpt, EvoHarness and mock OPRO experiment regression reported **13 passed**. These are synthetic controls, not real-model effectiveness or benchmark evidence.

The shared GEPA API also rejects a caller callback returning a non-object with a controlled error; this validation is at the shared API boundary and does not change the experiment port's reply handling. Shared proposal, fix-baseline GEPA compatibility, selector, customization CLI and review controls reported **57 passed** with scripted offline inputs.

## Dataset maintenance tool ownership

Dataset lint and generated inventory implementations moved under `tools.dataset_maintenance`; top-level commands and ordinary imports remain supported. Each relocated source is byte-identical to its prior file after adjusting only repository-root depth. Old and canonical commands produced identical exit status and output for an authored empty-data GSM8K lint and inventory `--check`. The offline tool, CI, documentation and installed-wheel selection reported **176 passed, 1 deselected**. The deselected all-datasets lint fails with DS1000 data missing in an explicitly empty data root on both the pre-move baseline and the migrated tree; an earlier selection reported **174 passed, 1 failed** for that reason. No dataset download, model or Docker was invoked; these checks do not establish full-suite or real-dataset acceptance.

The dataset taxonomy subsequently moved into the same maintenance package. Its source matches the previous source except for repository-root depth; the old command/import still delegates to the same module. The blocker checker reads the moved literal declarations, and one current repository-evidence path was updated to the new location without rewriting historical run records. An empty-data full taxonomy selection reported **23 failed, 8 passed, 2 errors** because Terminal-Bench was absent; the same tests on the pre-move baseline had the identical counts and failing nodes. The independent maintenance, blocker, CI, documentation and installed-wheel controls reported **175 passed**, and eight taxonomy controls independent of the missing data passed. No dataset was fetched, no model or Docker was used, and real-dataset coverage remains unverified.

The blocker taxonomy reader now supports a supplied historical checkout whose taxonomy remains at the old path, while a present invalid canonical file is refused rather than bypassed. An actual detached pre-move checkout validated successfully against its original registry (**56 datasets, 11 historical records**); the first validation wrapper incorrectly expected a `valid` field in the Python API result and was corrected before the successful check. Historical-root, current-root, blocker and maintenance controls reported **34 passed**. No historical record was rewritten.

The dataset scaffold generator now lives in `tools.dataset_maintenance.new_dataset` with its original command and import retained. Its source matches the previous file after normalizing only repository-root depth. In a temporary isolated checkout, the generated QA, command and files scaffolds, local and mock-platform controls, maintenance entrypoints, CI selector and installed-wheel controls reported **158 passed**. Those generator tests created and removed only their own authored fixtures in the isolated checkout; no released dataset, model or Docker service was used.

Dataset download management and runtime preparation subsequently moved under `tools.dataset_maintenance`; their old scripts and imports remain usable. Their relocated source bytes match the previous implementations except for repository-root depth. Old and canonical commands produced identical output and exit status for GSM8K metadata listing, missing-data check and an invalid runtime check. Offline maintenance, local-file transfer, runtime-tool, CI, documentation and wheel controls reported **246 passed**, followed by **13 passed** for expanded CI and entrypoint checks. No released dataset download, model call or Docker service was run.

The read-only metadata listing, missing-data check and invalid runtime check are now also regression tests comparing old and canonical commands in an empty external directory; they assert that neither data nor runtime storage is created. Maintenance entrypoint and runtime-tool controls reported **87 passed**. Dataset-specific preparation wrappers retain their existing locations where runtime or source contracts still refer to them.

Dataset lint, documentation and split inventory now use the canonical download-management import; an import-boundary control prevents production callers from reintroducing the old maintenance aliases. With an explicitly empty data root, the first offline selection reported **100 passed, 4 failed** in split-inventory checks; the same four nodes failed on the pre-change baseline, where the generated split view also differed from the committed document. A repeat excluding those baseline failures reported **100 passed, 4 deselected**, with 2 syntax warnings. No released dataset, model or Docker service was run.

The dataset download, documentation and runtime-tool unit tests now import their canonical maintenance modules directly; dedicated compatibility controls still exercise the old imports and commands. An offline selection including those tests and installed-wheel checks reported **123 passed**. Other callers remain on the compatibility entrypoints where their runtime-specific tests need separate review.

The split inventory's source-only fallback now reads a literal `SPLIT_SOURCE` from an unambiguous lazy attribute branch without executing the adapter. This fixes the Jericho row when its provider runtime is unavailable; ambiguous conditions, decorated or rebound functions, and nonliteral values remain unknown. With an explicitly empty data root, the previously failing split-inventory controls and adjacent maintenance/documentation selection reported **67 passed**; four additional static ambiguity controls passed. `tools/split_inventory.py --check` now matches the committed 56-dataset document under that empty root. Dataset adapters, task data and scorers were not changed.

The split-inventory implementation was moved into `tools.dataset_maintenance` with its old command and import retained. Its bytes match the prior source except for repository-root depth. In an empty data root, split inventory, maintenance, CI, documentation, installed-wheel and import-boundary controls reported **176 passed** with 2 syntax warnings; the canonical-import recheck reported **34 passed**, also with 2 warnings. No released dataset, model or Docker service was used.

After the maintenance-tool moves, an empty-data selection covering measurement components, H0 source profiles, Jericho and StrongREJECT provenance, split inventory and maintenance entrypoints reported **64 passed**. A source comparison against LightRSI fix `a0f837fd` also found no tracked changes under `data/**/verify/**`, native QA scoring modules or `eval/run/metrics.py`. These controls do not establish full real-dataset or scorer-runtime equivalence.

Dataset maintenance entrypoint controls now also compare old and canonical `python -m` help and load every old entrypoint by file path with a temporary empty data root. The complete entrypoint selection reported **31 passed**; no data directory was created. These checks supplement, but do not replace, testing every operational command or external runtime.

## Cross-layer recheck after GEPA and maintenance changes

At clean commit `b0e40f3fe481ffec00e2941eeed31f76b1a31ae2`, a detached checkout ran the earlier cross-layer selection expanded with GEPA, dataset maintenance, split inventory and authored scaffold controls, using empty external data and isolated slot and pytest directories. It reported **875 passed, 1 failed**, with 15 syntax warnings. The failure was the import-direction guard: the newly extracted `methods.policy_ports.gepa_proposal` dependency had not been explicitly listed for the shared improvement stage. After adding only that concrete edge to the reviewed dependency set, the complete import-direction and improvement-import-isolation selection reported **23 passed**, with 15 warnings; the GEPA and guard focused selection reported **16 passed**, with 2 warnings. The 875-test selection was not rerun after this test-policy change, so this is not a claim of 876 passes. The checked commit is retained in the local validation-evidence branch; no real dataset, model or Docker service was run.

After that correction, the same expanded cross-layer selection was rerun in a detached checkout at `371af6c3b8cb7d87e8198a3eb0214b396abe1d1b` and reported **876 passed, 15 warnings**. The test checkout, empty data root, slot directory and pytest basetemp were isolated; the temporary checkout was removed after the run. This record was added afterward and changes the platform content identity. The checked commit is retained by the local validation-evidence branch. No real model, released benchmark data or Docker service was run; genuine Loop SDK, other native runtimes and full-suite acceptance remain unverified.

The selection was the earlier cross-layer command in this document, expanded with `test_gepa_shared_proposal.py`, `test_gepa_pre_refactor_compat.py`, `test_gepa_rule.py`, `test_dataset_maintenance_layout.py`, `test_datasets_fetch.py`, `test_split_inventory.py` and `test_new_dataset.py`, with `UV_OFFLINE=1`, `UV_PYTHON_DOWNLOADS=never`, `PIP_NO_INDEX=1`, `HG_REVIEWED_ENVIRONMENT_ONLY=1` and separately owned data, slot and pytest temporary directories.

The clean installed-wheel control now also compares old and canonical module commands for GSM8K metadata listing, missing-data check and an invalid runtime check. Exit codes and output matched, and the external data directory remained absent; the wheel/resource selection reported **2 passed** without package-index access.

## Driver preflight ownership

The `--check-only` / `--prepare-only` workflow now lives in `harnessgrad.orchestration.preflight`; `driver._readiness` supplies the current driver callbacks and remains the entrypoint. Normalizing only those callback bindings makes the moved function body AST match the prior source. The pre-move and post-move focused CLI/closure selection each reported **23 passed**. A broader readiness, namespace, custom split, scenario, architecture and installed-wheel selection reported **174 passed** with 15 syntax warnings. A callback-binding control passed on both pre- and post-move trees, and a later callback and installed-wheel recheck reported **22 passed**. The checks use authored inputs and mock readiness; no real benchmark, model or Docker service was run. Moving code changes the platform content identity, so historical admissions remain governed by existing provenance checks.

A separate real CLI comparison used the authored `demo` fixture and mock backend on the preflight parent `39a3d5fd` and extracted driver. Both `--check-only` and `--prepare-only` returned success; check names, required/result statuses, non-path-dependent details, printed preparation path and prepared metadata matched after normalizing checkout/work paths and excluding platform Git/content identities and timestamps. The first bytewise comparison exposed only path-dependent table truncation and the expected platform identity changes; the second compared check status fields for harness, writable and disk rows instead of their host/path-dependent detail. This is one synthetic configuration, not full preflight equivalence or a real benchmark run.

The first refusal comparison inadvertently passed mutually exclusive `--harness` and `--harness-path` together in its two missing-harness cases, so those two cases only tested argument-parser refusal, not preflight. After correcting the invocations and asserting that the harness failure reached the preflight table, both the parent and extracted driver exited 2 with matching normalized output/check statuses for missing harness under `--check-only`, the same failure with `--check-only --prepare-only`, and a missing custom split under `--prepare-only`; neither created run or work directories. The new combined-flag regression test passed on both trees. Checkout paths and host-dependent table detail were normalized; no real model, dataset or Docker service was used.

## Treatment identity orchestration

Early treatment capture, resume comparison and runtime-configuration projection now live in `harnessgrad.orchestration.treatment_workflow`; driver callbacks still provide metadata, configuration and budget profile access. The projection body matches the prior AST after normalizing callback bindings. Treatment identity and resume controls reported **19 passed** before the move; the corresponding expanded post-move selection reported **40 passed** with 2 syntax warnings. A focused ordering/environment-isolation control passed, and the installed-wheel, identity and import-boundary recheck reported **21 passed** with 2 warnings. These use authored inputs and mock execution, not real models or benchmarks. The platform content identity changes; historical records are not rewritten.

The retained authored treatment-resume records from the pre- and post-extraction test runs were compared directly. Selected metadata, resume source rounds and Harness SHAs, score/per-task and outcome fields, method-attempt status and usage fields, and treatment-record key sets matched for both scenarios: respectively **3 curve points / 2 attempts** and **2 curve points / 1 attempt**. The initial comparison also exposed expected differences in resume timestamps and platform content identities; those fields were excluded from the final projection. Treatment digest values and full records were not claimed equal, and this does not establish real-model or benchmark equivalence.

## Execution policy coordination

Direct Harness option validation and staged diagnostic evidence checks now live in `harnessgrad.orchestration.execution_policy`; the driver retains refusal handling. After normalizing the helper refusal return, the moved AST matches the original block. The same synthetic selection reported **63 passed** before and after the move; a broader diagnostic, Harness entry, source selection, import-boundary and installed-wheel selection reported **218 passed**, with 2 syntax warnings. Six actual CLI refusal cases matched in exit code and message and did not create run or work directories. No real dataset, model or Docker service was run; platform source identity changes and historical records remain unchanged.

## Cross-layer recheck after driver coordination extraction

The expanded post-driver selection ran in a clean detached checkout at `3579fd49804d09a11c960d56fe00762975c316eb` and reported **1,117 passed, 15 warnings**. It repeated the 876-test cross-layer selection and added readiness CLI, driver readiness closures, treatment identity and resume, diagnostic policy, Harness-path and selected-run-plan controls. The test checkout, empty data root, slot directory and pytest basetemp were isolated; the checkout was removed afterward. No real model, released benchmark data or Docker service was used. This documentation update changes the platform content identity; the checked commit remains reachable through the local validation-evidence branch. Genuine Loop SDK, other native runtimes and full-suite acceptance remain unverified.

The `tools.new_dataset` Python compatibility import is retired with a canonical import message; the existing script and `python -m` commands remain. In an isolated checkout, scaffold generation and maintenance-entrypoint controls reported **58 passed**. A separate source, installed-wheel and documentation selection reported **58 passed**. The scaffold tests used authored fixtures only; no released dataset, model or Docker service was used.

The `tools.dataset_lint` Python alias is also retired; its script and module commands remain. Internal tests use `tools.dataset_maintenance.dataset_lint`. In an isolated checkout, the offline lint, provider protocol, scaffold, registry, maintenance and installed-wheel selection reported **218 passed, 2 skipped, 1 deselected**. The skips require cached `pyarrow==23.0.0`; the deselected all-dataset lint is the baseline DS1000 empty-data failure described above. An earlier combined run reported **251 passed, 9 failed, 2 skipped, 1 deselected** because reviewed-environment-only mode disables the temporary `.env` files expected by `test_data_paths.py`; running that file separately in the isolated checkout under its intended `.env` mode reported **42 passed**. No real model, released data or Docker service was used.

## Delivery-candidate offline validation

At clean commit `9174c7d7f5c8f7e28887a1c52c6356a13c67623d`, an isolated checkout ran the post-driver cross-layer selection plus dataset lint, readiness, provider-protocol, download-index, registry-order, runtime-tool and execution-alias tests, and the two provider-environment nodes. It reported **1,329 passed, 2 skipped, 1 deselected, 15 warnings**. Both skips require an unavailable offline `pyarrow==23.0.0` cache; the deselected all-datasets lint is the pre-existing DS1000 failure with an empty data root. The data-path tests were run separately in the same isolated checkout with their required temporary `.env` mode and reported **42 passed**. No real model, released benchmark data or Docker service was used. The checkout was removed and the tested commit retained through the local validation-evidence branch. This documentation update changes the platform content identity; genuine Loop SDK, other native environments and full-suite acceptance remain unverified.

## Follow-up: locked native dependencies available

At clean commit `4380b9e93d2234686c5f9ff3bd598b25d687bb08`, bypassing the failing ambient proxy allowed explicit acquisition of the pinned Loop dependencies from PyPI into an owned external cache and runtime store. Four offline native controls passed using the locked `openai==2.21.0` artifact: preparation in an SDK-free platform environment, the files-mode SDK/shell/private-scorer control with a scripted loopback endpoint, private-state reuse, and changed-tree cache refusal. The H0/resume control was not run because it uses a shared fixed `/tmp/hg-h0-work` location; container routes were not run.

Separately, `pyarrow==23.0.0` was obtained from PyPI into another owned cache. The first two offline provider selections each produced **10 setup errors** because a cache seeded with `--no-deps` lacked resolution metadata; removing `PIP_NO_INDEX` alone did not fix it. After seeding normal package metadata in an isolated environment, the offline authored-Parquet provider, unprepared-runtime, static-tool and missing/corrupt-data controls reported **10 passed**. This includes the two provider nodes skipped in the earlier delivery selection, but does not retroactively change its reported counts. No real benchmark data, paid model or Docker service was used. The test checkout was removed; the checked commit is retained through the local validation-evidence branch. This record changes platform content identity, and other native routes and full-suite acceptance remain unverified.

## Isolated native Loop H0 and resume

At clean commit `f2cd41f325ed5b90492cb3c4d62febae07abcccd`, the shipped Loop H0 creation/reuse and snapshot-resume control reported **1 passed** with the locked SDK, mock backend and authored `demo` task. A bubblewrap mount namespace redirected the fixed `/tmp/hg-h0-work` path to an owned directory; a unique marker check verified the namespace mapping before the test. The first attempt failed before pytest collection because `/dev/null` was unavailable in that namespace; adding the existing device bind allowed the control to run. The isolated checkout and owned H0 directory were removed afterward. No real model, released benchmark data or Docker service was used. This record changes platform content identity; the checked commit remains reachable through the local validation-evidence branch. Container paths and full-suite acceptance remain unverified.

## MiniAgent and authored QA integration

At clean commit `254c3a28b97d26003722eab92760863d05d300e0`, MiniAgent pinned wheels were acquired from PyPI into an owned cache and the tests then ran with `UV_OFFLINE=1`. Two authored TriviaQA/HotpotQA provider-to-MiniAgent controls passed: each checked the measured result, bound runtime identities and that the actor could not see provider dependencies or private scoring input. Five additional native MiniAgent controls passed, including source pin, mock tool round trip, candidate skill execution and a real SDK talking to a scripted loopback endpoint. No released benchmark data, paid model or Docker service was used. The isolated checkout was removed and the checked commit retained through the local validation-evidence branch; this record changes the platform content identity. Other native routes and full-suite acceptance remain unverified.

A read-only remote check on 2026-11-17 found GitHub `main` at `8264a916bd9a6f3fbe7b0ef49437efc6be6f0fe1`, already an ancestor of the refactor branch and the LightRSI fix. The current production code and tests match the previously checked delivery/native tree `4380b9e93d2234686c5f9ff3bd598b25d687bb08`; only these validation documents changed afterward. At this check, all 58 resolvable short and full commit references in the validation documents were reachable from the documented local refactor, history and evidence branches. The remote may advance later, and those evidence branches are not published automatically.

## Local CI guard and selection check

At clean commit `e93a072ea158f2db8d275b8edb50bdad742102c3`, the eight files in the workflow’s gating checks were run separately in an isolated checkout, as the workflow does: **221 passed**, with 15 warnings from the import-direction file. The standard-library dependency assertion, driver help, entrypoint import and clean-checkout checks also passed. Running the CI selector against recorded remote main `8264a916bd9a6f3fbe7b0ef49437efc6be6f0fe1` identified 793 changed paths and selected the full suite; the current workflow defers that full selection for ordinary push/PR and runs it on schedule or manual dispatch. No remote CI was run by this check, and the full suite remains unverified. The isolated checkout was removed; the tested commit is retained through the local evidence branch. This record changes platform content identity.

The eight gating CI files were subsequently rerun at clean commit `42739c6f8b2c3d66607352cd770f60a0db387a55` in an isolated Python 3.12 virtual environment containing `pytest==9.2.0` and its dependencies, with plugin autoload disabled and an empty external data root. They reported **221 passed**, plus 462 pytest subtests and 15 syntax warnings. The project dependency list was empty; numpy, chess, PyYAML, OpenAI, HTTPX and PyArrow were absent from that environment. Driver help, entrypoint import and clean-checkout checks also passed. Pytest was acquired from PyPI into an owned cache before the offline checks; no real benchmark, model or Docker service was run. The checkout was removed and the checked commit retained through the local evidence branch. This is not a remote CI run or full-suite acceptance; this record changes platform content identity.

## GEPA platform-level differential

A separate authored one-task GEPA run was executed through the real method sandbox and platform scoring path at LightRSI fix `a0f837fd` and the refactored tree, using a scripted loopback editor and isolated work, run and slot directories. The selected projections of all four curve points, method reports and Harness content hashes matched after excluding timestamps and platform identity; the score sequence was **0 → 1 → 1 → 1** with one scripted editor request. The existing OPRO platform integration control is now parameterized to cover GEPA as well; both cases reported **2 passed** on each tree. CI method selection includes this control for OPRO and GEPA changes, and the related selection and GEPA controls reported **8 passed**. This is one synthetic task, not real-model effectiveness or full method equivalence.

The complete CI selector, OPRO/GEPA sandbox integration, GEPA compatibility and selection, proposal loading, import-boundary and documentation-link selection was rerun at clean commit `e71091603368de60b0951809324d16ca853f620a`: **152 passed, 2 syntax warnings**. It used isolated temporary data/slot paths and scripted loopback model responses; no real model or benchmark was invoked. This record changes platform content identity; the checked commit remains reachable via the local validation-evidence branch.

## Final module-history candidate

The consolidated candidate `22c31b5299eafe8f28d1fde721387eaff3f7c974` contained the exact same Git tree as the pre-consolidation branch. In an isolated checkout, GEPA, OPRO, customization, dataset entrypoints, readiness, treatment identity/resume, diagnostic policy, CI selection, documentation and installed-wheel controls reported **316 passed**. The checkout was removed; this record was added afterward and changes platform content identity. The tested commit is retained in the local validation-evidence branch. This does not establish full-suite, real-model or container acceptance.

## Module-history entrypoint check

A review of the first consolidated history found that its fourth and fifth module commits retired dataset runtime/component imports before older maintenance commands had migrated, so four tool entrypoints failed at those intermediate revisions. A replacement history keeps four same-module or narrow bridging imports only in those two intermediate commits and removes them in the tooling commit; the final source tree was byte-for-byte identical to the prior final tree. At candidate `a07d191311900a53a38af14c403a64e6a1d86869`, all eight module commits individually passed driver and HarnessGrad help, seven dataset-tool help checks and a read-only GSM8K metadata listing with an empty data root. The final-tree GEPA/OPRO, customization, readiness, treatment/resume, diagnostic, CI, documentation and installed-wheel selection reported **316 passed**. This validation does not establish that every test passes at every intermediate revision. The tested commit is retained on the local validation-evidence branch; this record changes platform content identity.

A further per-commit control ran the authored one-task `demo` with `loop_plain`, mock backend, zero improvement rounds and `--no-sandbox` in an isolated checkout at the LightRSI fix and at each of the eight module commits ending at `8767cc6238b2291f97a7851421444555224cd1d6`. All nine runs completed; selected score, per-task values, coverage/outcome fields, scorer-isolation class and Harness content hash matched the fix baseline (score **0.0**). Work, run and slot directories were owned temporary paths, and the checkout was removed afterward. Timestamps, cost and platform identities were not compared; one synthetic task does not establish general experiment equivalence. The checked history remains reachable through the local validation-evidence branch, and this record changes platform content identity.

## Source comparison of modified experiment-related files

A source-only comparison against LightRSI fix `a0f837fd` examined 62 modified Python paths selected from `driver.py`, dataset adapter/preparation/profile sources and nested scaffolds, `eval/run/`, `harnessgrad/loops/`, and top-level/port method modules. Parsing succeeded for all 62. After removing import statements and module/class/function docstrings from both ASTs, **41 matched** and **21 differed**. This does not prove that changed imports are equivalent, and excludes added files and other repository areas.

Of the 21 differing paths, seven preparation/control files match after additionally normalizing the explicitly added `errors="strict"` argument on `subprocess.run` (the previous implicit decoding policy was strict): GAIA preparation, JobBench preparation, Reasoning Gym preparation, ScienceWorld control, and native Harbor, QA and research preparation. Eight are dataset scoring-profile files changed as part of the separately requested data-source/loader/scorer provenance separation. The remaining six are the driver, measurement cache, runner, two experiment loop modules and policy-port runtime; these contain the recorded orchestration, measurement-identity wiring and shared proposal extractions. Their focused and synthetic differential controls are documented above. This classification is an audit aid, not proof of equivalence for every input or runtime.

## Legacy measurement cache key differential

The LightRSI fix and current code were compared for all **128 combinations** of seven optional legacy cache-identity axes, with no new measurement-components bundle. Every resulting key matched byte-for-byte. The baseline capture is represented by a deterministic golden digest in `test_evaluation_admission_plan.py`; the cache-admission and measurement-component selection reported **22 passed**. This checks key formatting for authored inputs, not every cache hit, runtime or historical receipt.

The legacy cache lookup was separately compared against LightRSI fix `a0f837fd` for eight authored cases without the new measurement-component field: accepted hit, forced fresh, disabled reuse, missing or tampered verification context, unexpected native receipt, missing pristine binding and absent entry. All decisions and refusal details matched. A parameterized regression now checks those cases and that the cache is not mutated; the cache-admission and measurement-component selection reported **30 passed**. This does not establish equivalence for every possible cache payload or runtime.

A further cache control changed the data-source, loader and scorer identities independently and placed each changed record under the current key. All three were refused without mutating the cache, demonstrating that admission checks the recorded components as well as the key. Cache-admission and measurement-component controls reported **33 passed** with authored inputs; this is not a real-dataset execution test.

A follow-up static split-source control found that a nonliteral module-level `SPLIT_SOURCE` could shadow `__getattr__`, so treating its fallback return as authoritative would misreport an unknown value. The source-only reader now refuses that inference when explicit module-scope bindings or potentially conflicting imports are present. Four authored shadowing cases failed before the correction; the split-inventory, maintenance-entrypoint and documentation selection reported **69 passed** afterward. No adapter, benchmark data or scorer was changed.

A follow-up review found two additional cases in the source-only split reader: a dynamically bound module-level `SPLIT_SOURCE` could shadow a literal lazy fallback, and a literal source could be rebound conditionally or dynamically. The initial authored controls for both cases failed before correction. The reader now treats explicit conflicting bindings and unknown lazy loaders as undeclared rather than asserting a fixed source; it does not execute the adapter. Split-inventory, maintenance-entrypoint, documentation and CI-selector controls reported **197 passed** with an empty data root. No dataset adapter, scorer or benchmark data was changed.

The isolated installed-wheel control also exercises the split reader with authored source: an explicit dynamic binding remains undeclared, while an unshadowed literal lazy declaration is recognized. The wheel/resource test file reported **2 passed** without package-index access. No adapter source was executed by this check.

## Final local guard recheck

At clean commit `8f433ca28dcfdc59a1004bb9de657b56e636a91e`, the eight gating CI files were rerun separately in the isolated Python 3.12 / pytest 9.2 environment: **222 passed**, with 462 pytest subtests and 15 syntax warnings. The two files directly affected by the latest split-source and cache controls added **46 passed**. Driver help, entrypoint imports and checkout cleanliness also passed. The isolated checkout was removed; the checked commit is retained on the local validation-evidence branch. These checks used an empty data root and no real benchmark, model or Docker service; they are not a remote CI or full-suite result. This record changes platform content identity.

After the split-reader fix and cache-regression additions changed the tooling and test commit hashes, those two module revisions were rerun against `a0f837fd` with the same isolated one-task `demo` control. Both retained the same selected score, per-task, outcome, scorer-isolation and Harness-hash projection as the baseline. This confirms the affected intermediate revisions still run that synthetic path; it does not establish general experiment equivalence.

The authored GEPA sandbox/platform control was also repeated at LightRSI fix `a0f837fd` and each of the eight module commits ending at `4c916c2a1b79ad1270b54fad2524eedb461fe679`. All nine runs produced matching selected four-round scores, method reports and Harness hashes, with one scripted loopback editor request per run. This is a synthetic one-task control, not general method equivalence. The isolated checkout was removed and the checked history retained via the local evidence branch; this record changes platform content identity.
