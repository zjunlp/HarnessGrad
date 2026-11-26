# Shared-foundation refactor review

> **Historical validation (November 2026, before `f1d5f062`).** The refactor
> was integrated into main by `f1d5f062`. The branch state, local refs and
> results below refer to the named review checkouts. See the
> [architecture map](../architecture.md) and [test guide](../test-suite.md)
> for the current source and checks.

This is the pre-integration review map for the shared-foundation refactor. Earlier controls cited below refer to the specific commits named alongside them, including `10bbe985cbf882c1984c25a912260cfb4d333011`; they have not all been rerun on the final branch. The branch is not a claim of full-suite, real-model or full-benchmark acceptance. Detailed dated controls and their limitations are in [shared-foundation-offline.md](shared-foundation-offline.md).

## Subsequent module updates

The consolidated module changes include: a shared GEPA instruction proposal role, an optional failure-focused customization refinement, organized dataset-maintenance tools, driver preflight orchestration, treatment identity coordination, execution-policy validation and retirement of two dataset-maintenance import aliases. GEPA parent selection, experiment measurements and candidate adoption remain owned by their workflows. The customization option is off by default and shares the existing model-call limit. Dataset maintenance retains the original operator commands, and historical-root taxonomy reading is supported. The driver keeps its public entrypoints while delegating preflight and treatment coordination to the orchestration layer. See [the shared foundation](../shared-foundation.md), [customization guide](../customization.md) and [import migration](../import-migration.md).

The newer controls are recorded separately in [the detailed validation log](shared-foundation-offline.md): scripted GEPA behavior was compared with the LightRSI fix, customization refinement was tested through review and patch export, and maintenance entrypoints were checked with isolated inputs and an installed wheel. A later cross-layer run on the delivery candidate reported 1,329 passes, two skips and one deselection; it and the focused differential controls are described in the log. None of these checks proves real-model effectiveness or establishes full-suite acceptance. A known DS1000 lint failure under an empty data root remains present on the pre-move baseline; five native Loop SDK controls, including H0 reuse and resume, and ten authored QA-provider controls were subsequently run as described in the log; container and other native routes remain unverified.

The 1,329-test selection ran at `9174c7d7f5c8f7e28887a1c52c6356a13c67623d`. Compared with the delivery tree, the subsequent tracked changes are limited to documentation, tests, `tools/dataset_maintenance/split_inventory.py` and `tools/testing/ci_select.py`. The selected suite was not rerun on the delivery tree; later focused checks of the two tools are recorded in the detailed log.

A later user-authorized real-model smoke comparison ran Loop + DGM on five GSM8K tasks against a local Qwen model. The LightRSI-fix and refactor runs both completed, with matching task-level scores and initial and candidate Harness hashes. The [run details and limits](shared-foundation-offline.md#follow-up-real-qwen-gsm8k-smoke-comparison) apply only to that configuration; they do not establish complete experiment equivalence.

## Delivery audit against the requested refactor

| Requirement | Evidence at review | Limit at review |
| --- | --- | --- |
| Keep the experiment checkout separate | The work is on `refactor/shared-foundation` in `HarnessGrad-refactor`; the original local `main` README matches its HEAD and its untracked files were retained. | The branch has not been merged or published. |
| Retain the LightRSI fixes | Fix commit `a0f837fd` and original local-main commit `1eedb4c5` are ancestors. | The remote can advance; check it again at integration. |
| Organize code and repository | Shared core, improvement, customization, experiment, execution, orchestration, provenance and dataset-maintenance owners are mapped in [architecture](../architecture.md). The unpublished history is grouped by module. | Some root entrypoints and historical evidence paths intentionally remain; see [migration](../import-migration.md). |
| Preserve experiment behavior | Selected scorer sources are unchanged. Scripted ACE, SkillOpt, EvoHarness, GEPA and OPRO controls, mock driver/resume comparisons and native Loop/MiniAgent controls are recorded in the [validation log](shared-foundation-offline.md). | These controls cover selected inputs and runtimes, not all methods, datasets or deployment conditions. Source identity intentionally changes; old records are not silently reused. |
| Share useful RSI mechanisms | Explicit-feedback proposal stages are reusable by customization; experiment selection and scoring remain separate. | These are selected proposal roles, not automatic execution of every complete RSI algorithm. Personal Harness evolution is deferred. |
| Produce reviewable commits and evidence | The branch has a merge base plus module-focused commits; cited development commits are retained on the two local history/evidence branches described below. | Those branches have not been published and must be retained if the cited commit objects are to remain independently accessible. |
| Verify delivery | The documented offline selection reported **1,329 passed, 2 skipped, 1 deselected**; targeted locked native dependencies and clean CI guards were tested separately. | The full suite, real benchmark/model runs and container routes have not been established. Ordinary push/PR CI defers a full-suite selection. |

## Scope and ownership

- [Shared core](../../harnessgrad/core/) holds Harness descriptions, components, snapshots, readiness facts and method transport. [Improvement](../../harnessgrad/improvement/) provides caller-supplied feedback contracts and reusable proposal stages; it does not choose or evaluate a candidate.
- [Customization](../../harnessgrad/customization/) collects explicitly selected inputs, composes proposal stages, reviews candidates and exports a patch. Its proposal command does not execute tasks or adopt changes. Personal Harness integration remains outside this refactor.
- [Experiment](../../harnessgrad/experiment/), [orchestration](../../harnessgrad/orchestration/), [execution](../../harnessgrad/execution/), [datasets](../../harnessgrad/datasets/) and [provenance](../../harnessgrad/provenance/) separate workflow decisions from shared contracts. Existing Console inspection and editing remain in the same workbench.
- The existing ACE, SkillOpt and EvoHarness method ports keep their experiment selection and measurement ownership. Selected proposal mechanisms are shared with customization; this is not a claim that the full methods have been deployed in another workflow.
- Tools are grouped under purpose-specific directories. Removed imports and retained command entrypoints are described in the [migration guide](../import-migration.md). Some refusal guards and operational compatibility entrypoints remain deliberately present.

## Experiment and provenance boundaries

The LightRSI fix commit `a0f837fdf1abd85664be0cb99ea2ffe431e406d8` is an ancestor. Against that commit, the tracked `data/**/verify/**`, native QA scoring modules and `eval/run/metrics.py` have no file changes. Selected ACE/SkillOpt/EvoHarness scripted prompts, candidates and reports match both the fix branch and pre-refactor fixtures. A separate four-round, one-task OPRO mock control matches the fix-branch projection. These checks do not establish equivalence for every method, environment or input.

Source declarations, loader, scorer, actual runtime and selected-input commitments are distinct. Several datasets now opt into separate metadata; consequently older records missing the new evidence are not silently treated as equivalent. The declared source digest is not proof of downloaded bytes. Shared source files can belong to multiple implementation roles. Platform content identity changes with this refactor; historical receipts are preserved rather than rewritten. AppWorld's verifier-image boundary is documented separately because its private evaluator cannot be identified from the public bundle declaration alone.

## Validation and remaining review

- At clean commit `a507933e6ca215fb0a902810b708b33b08922666`, the selected offline cross-layer suite reported **760 passed**. Later source-profile and decoding changes have their own focused controls in the detailed record; they are not covered by that earlier run.
- The reviewed subprocess-decoding audit now passes at `10bbe985`; its [inventory](decoding-inventory.md) lists the source-scan scope and the individually reviewed fail-closed cases. This is not full runtime validation.
- Five native Loop controls now pass with the locked `openai==2.21.0` SDK, including H0 reuse and resume; container routes were not run. Ten authored QA-provider controls pass with `pyarrow==23.0.0`; two authored QA-to-MiniAgent controls and five MiniAgent native controls also pass with its locked SDK. Other external dataset/Harness runtimes remain unverified.
- The results-matrix display test showed a mismatch on the reviewed checkouts: JSON retains graceful-stop metadata, but the Markdown view does not display the expected status. The separate [test guide](../test-suite.md#existing-failures-checked-during-shared-layer-refactoring) describes the baseline evidence. Full test-suite acceptance has not been established.
- The local equivalent of the eight gating CI guard files passed **222 tests** (plus 462 pytest subtests) at `8f433ca28dcfdc59a1004bb9de657b56e636a91e` in an isolated Python environment. Against remote main `8264a916`, the CI selector chose the full suite, which this workflow defers on ordinary push/PR to a scheduled or manually dispatched run. The local guard and 1,329-test selection are not a completed full CI run.
- The separate original `main` checkout was inspected without modifying it: its README matched its local HEAD, and its untracked files were retained. The branch comparison and validation do not authorize rewriting existing experiment records or silently resuming across a changed platform identity.

## History before integration

Before integration the work was consolidated into eight module-oriented commits on a merge base retaining both the LightRSI fix and original local-main ancestry. The original development history is preserved locally in `refactor/shared-foundation-history-20261117` and a backup ref. Additional cited commits outside that history are retained by `refactor/shared-foundation-validation-evidence-20261117`, whose archive-only merges retain those exact objects. These local refs are not published automatically; both history branches must be preserved when publishing if readers need to inspect all cited commits. Published history was not rewritten.
