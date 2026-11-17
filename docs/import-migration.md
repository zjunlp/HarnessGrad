# Import migration

The refactor moves implementations into focused packages and gradually removes old import paths. Update integrations to the current path before switching to this branch. These are source-code API changes; they do not migrate or rewrite historical experiment records.

| Removed import | Current import | Scope |
| --- | --- | --- |
| `eval.candidate`, `eval.metrics`, `eval.integrity`, `eval.task_contract`, `eval.runner` | matching `eval.run` module | Candidate validation, metrics and execution contracts |
| `eval.container`, `eval.container_state`, `eval.sandbox` | matching `eval.containers` module | Container execution, state and sandbox helpers |
| `eval.verifier` | `eval.verification.verifier` | Verification execution |
| `eval.harness_runtime`, `eval.scorer_bundle`, `eval.separated_stdio`, `eval.gaia_preflight` | matching `eval.containers`, `eval.admission`, `eval.service`, `eval.policy` module respectively | Runtime, scorer and preflight tools |
| `eval.pilot_admission`, `eval.artifact_admission`, `eval.runner_admission`, `eval.scorer_admission`, `eval.scorer_backend`, `eval.lifecycle_receipt` | matching `eval.admission` module | Admission and lifecycle interfaces |
| `eval.services`, `eval.modelgate`, `eval.unix_model_relay`, `eval.stepwise_transport` | matching `eval.service` module | Model and tool service interfaces |
| `eval.console`, `eval.progress`, `eval.trace`, `eval.tracing`, `eval.phases` | matching `eval.observe` module | Observation, progress and trace interfaces |
| `eval.diagnostic_policy`, `eval.runtime_policy`, `eval.appworld_reporting`, `eval.gaia_snapshot_attestation` | matching `eval.policy` module | Evaluation policy and source-attestation interfaces |
| `harnessgrad.task_python` | `harnessgrad.datasets.task_python` | Dataset-owned task Python binding |
| `harnessgrad.provisioning` | `harnessgrad.core.readiness` and `harnessgrad.datasets.provisioning` | Scoped readiness facts and dataset status normalization |
| `harnessgrad.snapshot` | `harnessgrad.core.snapshot` | Portable Harness snapshots and restoration |
| `harnessgrad.code_metrics` | `harnessgrad.core.code_metrics` | Static Harness code diagnostics |
| `harnessgrad.components` | `harnessgrad.core.components` | Component declarations and changed-path attribution |
| `harnessgrad.dataset_runtime` | `harnessgrad.datasets.runtime` | Dataset runtime preparation and admission |
| `harnessgrad.dataset_provider` | `harnessgrad.datasets.provider` | Dataset provider and private worker coordination |
| `harnessgrad.dataset_runtime_identity` | `harnessgrad.datasets.runtime_identity` | Dataset runtime admission and H0 preflight |
| `harnessgrad.scoring_identity` | `harnessgrad.provenance.scoring_identity` | Scoring profile identity |
| `harnessgrad.verification_context` | `harnessgrad.provenance.verification_context` | Private selected-input commitments |
| `harnessgrad.native_judge_identity` | `harnessgrad.provenance.native_judge_identity` | Native JobBench judge selection |
| `harnessgrad.identity` | `harnessgrad.provenance.identity` | Harness and improver measurement identity |
| `harnessgrad.h0_identity` | `harnessgrad.provenance.h0_identity` | H0 agent comparability |
| `harnessgrad.budget_dispatch` | `harnessgrad.execution.budget_dispatch` | Trusted budget dispatcher |
| `harnessgrad.budget_integration` | `harnessgrad.execution.budget_integration` | Driver-owned budget binding |
| `harnessgrad.bubblewrap` | `harnessgrad.execution.bubblewrap` | Bubblewrap selection |
| `harnessgrad.slots` | `harnessgrad.execution.slots` | Shared execution slots |
| `harnessgrad.run_namespace` | `harnessgrad.execution.run_namespace` | Run namespace coordination |
| `harnessgrad.candidate_runtime` | `harnessgrad.execution.candidate_runtime` | Candidate runtime admission |
| `harnessgrad.python_artifacts` | `harnessgrad.execution.python_runtime.python_artifacts` | Python artifact utilities |
| `harnessgrad.python_harness_runtime` | `harnessgrad.execution.python_runtime.python_harness_runtime` | Native Python Harness runtime |
| `harnessgrad.python_exec_runtime` | `harnessgrad.execution.python_runtime.python_exec_runtime` | Container Python Harness runtime |
| `harnessgrad.evidence` | `harnessgrad.evidence_reports.evidence` | Evidence manifest contract |
| `harnessgrad.evidence_export` | `harnessgrad.evidence_reports.evidence_export` | Evidence export and validation |
| `harnessgrad.evidence_stats` | `harnessgrad.evidence_reports.evidence_stats` | Evidence comparability and statistics |
| `harnessgrad.receipt_verification` | `harnessgrad.evidence_reports.receipt_verification` | Receipt loading and verification |
| `harnessgrad.policies` | `harnessgrad.experiment.policies` | Recorded experiment scoring and reference comparison |
| `harnessgrad.channel` | `harnessgrad.experiment.channel` | Experiment evidence projection to methods |
| `harnessgrad.methods` | `harnessgrad.execution.method_runner` | Method sandbox execution |
| `harnessgrad.environments` | `harnessgrad.orchestration.environments` | Run environment selection and reporting |
| `harnessgrad.readiness` | `harnessgrad.orchestration.readiness` | Run readiness checks |
| `harnessgrad.method_attempts` | `harnessgrad.experiment.method_attempts` | Experiment method-attempt accounting |
| `harnessgrad.records` | `harnessgrad.experiment.records` | Experiment record writing |
| `harnessgrad.experiments` | `harnessgrad.experiment.config` | Experiment recipe configuration |
| `harnessgrad.completion` | `harnessgrad.experiment.completion` | Experiment completion reporting |
| `harnessgrad.splits` | `harnessgrad.experiment.splits` | Dataset partition commands |
| `harnessgrad.customization.contracts` | `harnessgrad.improvement.contracts` | Shared feedback and surface contracts |

For example, replace `from harnessgrad import components` with `from harnessgrad.core import components`. The component vocabulary and attribution rules remain unchanged. A small refusal guard at the old path prevents Python from silently loading an older editable installation from another checkout; it raises an import error with the replacement path and is not a compatibility alias. The source tree and therefore the recorded platform content identity do change; existing baseline or cache admission rules still apply.

Some historical descriptions embedded in preserved Harness artifacts may name the older source path. They describe the version in which they were recorded and are not runtime imports. Other compatibility aliases remain temporarily; their removal needs separate review of their callers and, where relevant, scoring-source and subprocess contracts.

For the split command, replace `python -m harnessgrad.splits ...` with `python -m harnessgrad.experiment.splits ...` or `harnessgrad split ...`. The partition operations and their output are unchanged.

The dataset runtime identity preflight is now `python -m harnessgrad.datasets.runtime_identity`. Its identity envelope and comparison rules are unchanged; the old module path refuses with migration guidance.

New code should import dataset runtime preparation from `harnessgrad.datasets.runtime`. LoCoMo and both SpreadsheetBench adapters use the canonical runtime import. The older `harnessgrad.dataset_runtime` now refuses with migration guidance. The three migrated adapters have a different loader fingerprint; their declared data sources and scorer fingerprints remain unchanged. Existing identity admission continues to treat the changed loader conservatively.

Test tooling moved from `tools/test_scope.py` to `tools/testing/scope.py`, and from `tools/test_lane.py` to `tools/testing/lane.py`. Use `python tools/testing/lane.py ...` for the change-based lane. These old files were removed so pytest does not collect their helper functions as tests.

The test runner, selected-plan runner, CI selector and quality sweep are now invoked as `python tools/testing/run_tests.py ...`, `python tools/testing/run_selected.py ...`, `python tools/testing/ci_select.py ...`, and `python tools/testing/quality_sweep.py ...`. Their previous top-level scripts have been removed. The cost inventory is packaged at `tools/testing/test_costs.json`.

Result analysis commands now live under `tools/analysis/`: `plot_curve.py`, `results_matrix.py`, `run_summary.py`, `evidence_export.py`, `component_attribution.py`, `report_appworld.py`, `report_livemathbench.py` and `tau3_report.py`. For example, replace `python tools/run_summary.py ...` with `python tools/analysis/run_summary.py ...`; Python imports move from `tools.run_summary` to `tools.analysis.run_summary`. The former top-level scripts have been removed.

The optional figure renderers now use `python tools/visualization/render_trends.py ...` and `python tools/visualization/render_demo_figure.py ...`; their former top-level scripts have been removed. Existing figure metadata is retained as historical provenance.

Older ChessBench `scoring_identity` values combined data-loading and scoring code provenance. New measurements record the declared dataset source, loader and scorer independently in `measurement_components`; `scoring_identity` describes the metric and scorer only. Shared files are conservatively fingerprinted in both roles. A provider path move alone no longer changes the loader's component digest when its bytes and semantic role stay identical, although the separate platform identity still changes. A declaration of the CSV digest does not prove which bytes were read; selected inputs retain their separate verification commitments.

MATH, MATH-500 and LoCoMo now use the same separation: upstream dataset versions and digests are declarations, not proof about local source bytes; loader and command-scorer code have separate identities. LoCoMo uses deterministic NLTK-based scoring rather than an LLM judge, and retains its distinct per-conversation and per-question aggregation descriptions. Historical identities and receipts are preserved and remain digest-verifiable. They lack the separate evidence and are not automatically treated as comparable to new measurements. The scoring algorithms and provider protocols are unchanged. A refusal guard at the old provider path prevents fallback to a stale editable installation. See the [provenance design](provenance-separation-design.md) for the boundaries and rollout.

SpreadsheetBench v1 and v2 now separately identify the declared workbook origin, loader and local scorer; the native Python and LibreOffice/UNO runtime remains identified separately. The v2 upstream evaluator fingerprint is a declaration, not verification of an upstream download. Historical combined profiles remain readable but do not automatically match the new identities.

Reasoning Gym now records its historical pool digest as a declaration independent of acquisition; its adapter and QA scaffold participate in both loading and scoring and therefore appear in both fingerprints. An adapter-only edit can still change both roles. The actual installed scoring package remains covered by execution runtime identity.

Task studies now use `python tools/studies/task_difficulty.py ...` and `python tools/studies/task_sensitivity.py ...`; Python imports move to `tools.studies`. The difficulty command reads recorded results; sensitivity can launch measurements and remains an explicit operator command.

Shared improvement contracts now expose `index_feedback`, `is_relative_path`, `selected_surfaces` and `text_diff` through `harnessgrad.improvement`. Customization and CLI code use these named interfaces instead of underscored contract helpers; the JSON parser hooks are available as `unique_json_object` and `reject_json_constant` from `harnessgrad.improvement.contracts`. Validation, ordering and diff behavior are unchanged.

Snapshot artifacts retain their existing v1 format and content IDs. Console and exporter loaders prefer the current core path but can load the prior path when reading an older platform tree; an existing invalid current implementation is not replaced silently by a legacy file.

StrongREJECT now distinguishes declared task inventory, loader and scorer roles. Its shared adapter remains in both implementation fingerprints. The resolved judge and endpoint hash remain in scoring identity; the source inventory hash represents declarations, not a downloaded-artifact verification.

Run-record and Harness-trace checks now use `python tools/validation/validate_run.py ...` and `python tools/validation/check_trace_contract.py ...`. Python trace-checker imports move to `tools.validation.check_trace_contract`. These commands remain read-only.

First-use and tool-call diagnostics now use `python tools/diagnostics/check_first_use.py ...` and `python tools/diagnostics/probe_tool_calls.py ...` (or their `tools.diagnostics` module names). The endpoint probe contacts only the endpoint explicitly configured by its caller.

The tool-call diagnostic command and experiment preflight now share `harnessgrad.execution.tool_call_probe`. Its `probe_chat` and `probe_responses` functions contact the caller-supplied endpoint when called; importing them performs no probe.

Harness validation, source identity and smoke helpers are owned by `harnessgrad.execution.harness_validation`. The existing `tools/validate_harness.py` CLI and import remain available and delegate to that same implementation; candidate admission and Console callers use the execution-layer owner.

The Web gateway implementation is owned by `eval.service.web_gateway`. `tools/web_gateway.py` remains a command and same-module compatibility import. The native research relay now uses the canonical gateway; its changed source is reflected in verification context and cannot silently reuse an older context.

Improver configuration and resolver functions are owned by `harnessgrad.improvers.resolver`. The existing `tools/improver.py` CLI and ordinary Python import remain a same-module compatibility entrypoint; method sandbox visibility remains unchanged.

Run-summary implementation is owned by `harnessgrad.evidence_reports.run_summary`; the existing `tools/analysis/run_summary.py` command and Python import delegate to the same module used by the driver.

Native research data conversion is owned by `data.scaffolds.native_research.conversion`; the existing `tools/prepare_native_research.py` command and ordinary Python import delegate to the same implementation. The isolated preparation worker imports the dataset-layer owner directly.

Registry, command-verifier, public native verifier and tool-relay platform code now use `harnessgrad.datasets.runtime` directly. The former `harnessgrad.dataset_runtime` import now refuses with migration guidance. This import-only change does not change the recorded data or scorer identities, although the platform content identity changes.

The private dataset provider worker now imports the canonical runtime. Its source participates in ChessBench loading, so ChessBench’s loader fingerprint changes; its declared data source and scorer fingerprints do not. Existing reuse checks treat the changed loader conservatively.

Native QA loading and preparation now use the canonical dataset runtime. Their source contributes to the MATH and MATH-500 loader fingerprints, so those fingerprints change; their declared data and scorer fingerprints remain separate and unchanged.

Spreadsheet actor binding and native benchmark declaration validation use the canonical dataset runtime. Their import-only migrations leave the currently opted-in dataset source and scorer identities unchanged; the platform content identity still changes.

Native Harbor dependency preparation and exact runtime admission now use the canonical dataset runtime import. Artifact bindings and admission semantics remain unchanged; the platform content identity still changes.

Native research adapter and runtime helpers now use the canonical dataset runtime. Their source is part of some verifier-directory commitments, and `special_runtime.py` is included in SWE/Harvey execution fingerprints; those identities change and old evidence is not silently reused. This does not itself change the declared dataset source or the installed dependency artifact.

The shared command-control helper now uses the canonical dataset runtime. Its source is included in GAIA source-tree compatibility checks, so a historical source identity will reflect the changed code; this is not a change to GAIA task data or scoring.

GAIA preparation and its isolated decoder now use the canonical dataset runtime. New preparation receipts record the updated worker source digest; older receipts are retained. Full GAIA source-tree compatibility checks distinguish the changed platform code from prior source trees.

CostBench shared execution binding and preparation controller now use the canonical dataset runtime. Their code is included in preparation-source and verifier-directory commitments, so those platform-code identities change; this does not claim the upstream dataset or scoring algorithm changed. The batch scoring report retains its existing import pending separate review.

Reasoning Gym preparation now uses the canonical dataset runtime; its loader fingerprint changes with the preparation source, while its declared historical pool and shared scorer identities remain unchanged. The scorer-bearing adapter retains its current import pending separate review.

Experiment record writing now lives in `harnessgrad.experiment.records`. The former `harnessgrad.records` import now fails with migration guidance; patch the new module directly. The implementation and record format are unchanged; platform code identity changes with the move.

ChessBench's stepwise controller, the CostBench reporting command, and JobBench's renderer preparation and worker now import `harnessgrad.datasets.runtime` directly. The imports still resolve to the same runtime module. ChessBench's declared data source and loader identities remain unchanged; because the controller is included in its scoring-code fingerprint, the scorer and combined scoring identities change conservatively even though the algorithm is unchanged. JobBench's renderer source attestation also changes for its two edited files. Historical identities and receipts are not rewritten or silently treated as equivalent.

Tau2 and Tau3 preparation entrypoints and the ScienceWorld operator control now use the canonical dataset runtime import. The Tau preparation supervisor binds both preparation source files by content, so their source identity changes and stale captured identities are refused. This does not change the declared dataset source or scorer algorithm. The old runtime import now refuses with migration guidance.

Reasoning Gym's adapter now imports the canonical dataset runtime. Its declared pool source remains the same. The adapter participates in both loading and scoring, so its import-only edit changes both implementation fingerprints and the scoring identity; previous identities remain valid historical records and are not automatically equivalent to the new captures.

AgentDojo, ALFWorld, AppWorld, Jericho and ScienceWorld adapters now use the canonical dataset runtime import. AppWorld does not yet opt into independent data-source or scorer identities; the import migration does not create such evidence or establish cross-version comparability. AgentDojo, ALFWorld, Jericho and ScienceWorld now opt into separately described source, loader and scorer identities. The platform content identity changes, while the adapter's normalized syntax is otherwise unchanged.

DS-1000, LiveCodeBench, Tau2 and Tau3 adapters now import the canonical dataset runtime. These adapters do not yet opt into independent data-source and scorer identities; the import migration does not create such evidence. Tau's preparation supervisor also fingerprints its adapter files, so captured source identities change and older preparation identities are rejected. All production Python imports have moved to the canonical runtime; the old import now refuses with migration guidance.

Dataset blocker registry validation, proposal application and semantic merge implementations now live under `tools.registry_ops`. Their existing `tools/*.py` commands and Python module imports remain available. The validator reads registry evidence; the applier writes only when explicitly invoked with `--apply`.

Method sandbox execution and entrypoint checks now live in `harnessgrad.execution.method_runner`. The old `harnessgrad.methods` import now refuses with migration guidance; update imports and patch targets to the execution-layer module. Experiment scheduling and source-protection behavior are unchanged; platform content identity changes.

Run readiness checks now live in `harnessgrad.orchestration.readiness`. `harnessgrad.readiness` now refuses with migration guidance; the shared readiness fact type remains in `harnessgrad.core.readiness`. The duplicate, identical split-check definition and repeated import were removed without changing the active check.

Experiment environment selection, reporting and preflight helpers now live in `harnessgrad.orchestration.environments`. The old `harnessgrad.environments` import now refuses with migration guidance. The selected environments and reporting behavior remain unchanged.

The former `eval.container_state` and `eval.sandbox` aliases now refuse imports with their canonical `eval.containers` paths. The former file remains at its historical path because GAIA source-identity v1 requires that path. Its changed bytes alter current source identity; historical identities are not rewritten or silently treated as equal.

The former `eval.verifier` import now refuses with its canonical `eval.verification.verifier` replacement. Its historical path remains present because GAIA source-identity v1 requires it; old source evidence is not rewritten.

The former `eval.container` import now refuses with its canonical `eval.containers.container` replacement. Its old file remains present for GAIA source-identity v1, and its changed bytes remain visible to source comparison.

The former `eval.runner` import now refuses with its canonical `eval.run.runner` replacement. The historical path remains present for GAIA source-identity v1; source comparison continues to detect changed bytes. Some preserved harness and dataset descriptions retain their original historical paths.

The former `eval.harness_runtime`, `eval.scorer_bundle`, `eval.separated_stdio` and `eval.gaia_preflight` imports now refuse with their canonical paths. Their `python -m` command entrypoints and the standalone `eval/separated_stdio.py` script still delegate to the same implementations.

Dataset declaration lint and inventory documentation implementations now live in `tools.dataset_maintenance.dataset_lint` and `tools.dataset_maintenance.datasets_doc`. The existing `tools/dataset_lint.py` and `tools/datasets_doc.py` commands remain available. The `tools.dataset_lint` Python import now directs callers to the canonical module; `tools.datasets_doc` remains a same-module import during migration. The move changes platform source identity but not dataset bytes or scoring implementations.

The dataset taxonomy implementation now lives in `tools.dataset_maintenance.dataset_classes`; `tools/dataset_classes.py` remains a command and same-module import. The blocker registry reads the canonical taxonomy declarations when present and supports older checkouts using the historical path; a present invalid canonical file is not bypassed. Its current repository evidence points to the moved source. Historical records are not rewritten.

Dataset scaffold generation is owned by `tools.dataset_maintenance.new_dataset`. The existing `tools/new_dataset.py` command remains available. Python imports from the old path now direct callers to the canonical module; generated template text and source declarations remain unchanged.

Dataset download management and runtime preparation now live in `tools.dataset_maintenance.datasets` and `tools.dataset_maintenance.prepare_dataset`. Their former top-level scripts and Python imports still delegate to the same implementations; transfers and runtime installation remain explicit operations. The move changes platform source identity, not dataset origin declarations or scoring code.

Dataset lint, inventory documentation and split inventory now import the canonical `tools.dataset_maintenance.datasets` implementation directly. The former `tools.datasets` import remains available for external callers during migration.

The split inventory implementation now lives in `tools.dataset_maintenance.split_inventory`; `tools/split_inventory.py` and its existing Python import remain supported entrypoints.
