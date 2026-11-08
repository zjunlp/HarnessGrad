# Linux runtime acceptance remaining work

This is a source and test evidence audit of 56 published adapters, not a host
installation certificate or benchmark result. `data/runtime-support.json` is the
maintained matrix; regenerate `DATASETS.md` with
`python3 tools/datasets_doc.py --write`. The shared `native_qa` profile is a
preparation-only component and does not add a 57th dataset. Its use of the
execution-profile storage mechanism does not make it a scoring runtime.
`jobbench_renderer` is another non-adapter component profile, supplying the
pinned author's data preparation dependencies without adding them to the platform.

Evidence levels are deliberately separate: **declared** means a recipe or binding
exists; **prepared** means an identified artifact was locally built/imported;
**verified** covers only the named authored control; **remaining** describes the
unverified acceptance boundary. A skip is missing evidence, never acceptance.
Synthetic engine/judge/HTTP responses and authored task bytes are not benchmark
measurements. No models, benchmarks or system installation were invoked for this
audit. Concurrent adapter changes may extend the evidence after this snapshot.

The [2026-11-07 delivery receipt](../.github/assets/linux-delivery-validation-20261107.json)
records fixed source `d41db67`: 46 selected files, **950 passed, no failures or
skips**. A 13-step wheel check built and installed the package, removed its build
source, then verified use including managed contributor smoke and a network-disabled
56-adapter readiness catalog. The cold catalog keeps 55 adapters unready and
reports the bundled authored example ready; it neither prepares those adapters
nor establishes their workload acceptance. This receipt does not close the
remaining boundaries below or establish full-suite coverage.

The [extended integrated receipt](../.github/assets/linux-delivery-validation-20261107-extended.json)
records source `2a9b657`: **1494 passed, no failures, 7 skipped** across 71 selected
files, plus 14 successful wheel build/install/use steps. It includes actual
scientific actor/scorer, original-package service, official spreadsheet,
external-runtime reuse and unknown-cost controls. Four Chess native-package
controls, two uncached-image controls and one full PersonaMem inventory check
skipped; those remain missing evidence. This is the stable Linux baseline for
the next architecture wave, not full-suite or full-Goal completion.

The cold stdlib controls in `test_stdlib_adapter_acceptance.py` exercise selected
loader and oracle/wrong/noop routes through the native sandbox using authored
records, including BFCL irrelevance and FEVER evidence branches. They leave
production pool/count pins intact. Other relevant controls are
`test_native_qa_preparation_runtime.py`, `test_spreadsheetbench_execution_runtime.py`,
`test_tau_execution_binding.py`, `test_reasoning_gym_managed.py`,
`test_tau_bench_prepared.py` and the Jericho managed/relay/authored-engine tests.
Local import/preparation evidence is separate from test-file presence.
Jericho's final focused result was 49 passed, 1 skipped, 7 deselected;
unsupported authored-native and fake-engine controls retain their limited scope.
Tau2/Tau3 shared-bwrap relay controls passed twice with dependency stubs, covering
readonly/source/neighbor privacy rather than real upstream service lifecycle.

## Next delivery priorities

These priorities follow the Linux Goal: implemented dependency boundaries,
preparation/check/identity interfaces, integration controls, packaging and
architecture. Complete released benchmarks, every workbook function and public
redistribution of private task pools are separate, authorization-dependent work;
their absence must not be presented as an unimplemented software boundary.
External inputs still need explicit provenance and missing-input diagnostics.

1. Continue the architecture migration from the fixed integrated baseline.
   Parser, outcome and record-reader ownership, selected-dataset planning,
   evaluator admission/cache, source leases/staging, trial journals/scheduling,
   result reduction, deferred resume, actor launch payloads, and Console
   query/settings/launch/candidate/workbench/explanation/submission/HTTP
   responsibilities and H0 planning have been separated. Task service lifetime and driver
   readiness/metadata/finalization still require migration. Refresh the actual
   ownership/call graph before each move and preserve cleanup, private inputs,
   source selection, identity capture, admission and publication ordering.
2. Retain the independent native-scorer input requirements. Both original Chess
   and Jericho packages have now been recovered without changing their pins and
   passed actual authored native controls; see the
   [recovery receipt](../.github/assets/native-original-recovery-20261107.json).
   Complete archives and provenance/license sidecars exist as local handoff
   artifacts. A fresh installation still needs those reviewed packages: no public
   download URL for the complete archives is registered, and the retained Frotz
   and system-library bytes are not established as a fully public rebuild recipe.
3. Keep final acceptance tied to the final source revision. After the architecture
   migrations, repeat the affected controls and installed first-use, recovery,
   viewing and export paths, then update the maintained documentation/website
   source and validation receipts. A declared or prepared component alone is not
   a completed end-to-end result.
4. Complete final difference review, release notes and PR review, then consolidate
   local development commits into a few reviewable themes before pushing. A
   failed PR-status refresh is missing evidence, not an empty PR list.

Selected stronger component controls now exist: MemoryAgentBench's exact
resources and real tokenizer/chunk differential; original-package CostBench
service/scorer lifecycles; actual Tau2/Tau3 package tools and ALL scoring with
scripted LLM responses; original-Frotz authored-game state and replay; and
21 official-source spreadsheet differentials. The matrix and component guides
state what each control establishes. They do not certify every released task,
original container deployment, historical runtime or a complete hermetic OS.

## Per-adapter remaining acceptance

Every current published adapter appears once below. The more detailed role and
verification scope remain in the generated matrix. A common requirement for all
external benchmarks is released-source custody and metric-preserving execution;
no item here authorizes downloading protected data or contacting a model.

| Adapter | Concrete remaining acceptance |
| --- | --- |
| `agentdojo` | Real upstream episode/lifecycle beyond managed CPython 3.12, fixed 112-file source, 949-pair read-only check and ten fake-evaluate/relay controls. |
| `aime` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `alfworld` | Real game/native scorer execution beyond prepared managed service, limited matching original source/version evidence, five real bubblewrap fake-engine controls and five control-wiring controls. Original scorer pins remain unchanged. |
| `appworld` | Pinned image/service/scorer live lifecycle and encrypted released bundle custody. |
| `authored_example_fix_bug` | Retain authored control scope; this fixture cannot establish external benchmark capability. |
| `bbeh` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `bfcl_v3` | Released function-call corpus/AST metric acceptance beyond authored call and irrelevance controls. |
| `bird_minidev` | Per-route loader/actor/service closure and portable provisioning remain under review; native pin presence is not execution proof. |
| `browsecomp` | Judge configuration, scorer dependency closure and failure evidence require per-dataset acceptance. |
| `chessbench_puzzles` | Original 718-blob package recovered and verified; four authored trajectories pass through its sealed scorer with rewards 1/0/0/0. Provider/interactive controls remain distinct. A fresh installation needs the reviewed native archive; no released pool is certified. |
| `cooperbench` | Source, package and image identity plus installed workflow acceptance remain pending. |
| `costbench` | Actual original-package service, socket, trusted seal, native scorer and cleanup are verified on authored oracle/noop/invalid-tool cases; broader domains are outside this scope. |
| `costbench_dynamic` | Actual authored cost_change lifecycle/scoring passes 1/0/0; the other dynamic modes and broader domains remain outside that control. |
| `deepsearchqa` | Judge configuration, scorer dependency closure and failure evidence require per-dataset acceptance. |
| `deepswe` | Source, package and image identity plus installed workflow acceptance remain pending. |
| `ds1000` | A complete CPU scientific artifact, actor shell and independent bound official verifier pass authored native controls. The preserved historical cohort/sweep is not re-certified under the new CPU variant. |
| `fever` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `gaia` | Managed preparation closure, private attachment custody and task/scorer route acceptance. |
| `gsm8k` | Authored provider and QA controls; released datasets and every task are not certified by these controls. |
| `harvey_lab` | Real legal-task/judge/document-conversion lifecycle and native resources beyond prepared managed recipe, exact admission and two actual worker probes; no model/judge run. |
| `hle` | Judge configuration, scorer dependency closure and failure evidence require per-dataset acceptance. |
| `hotpotqa` | Authored provider and QA controls; released datasets and every task are not certified by these controls. |
| `jericho` | Original 1039-blob sealed package recovered and verified. The CC0 authored TextWorld-ABI story passes actual engine/relay/client, shipped replay and sealed scoring with 2/2 reward and cleanup. Fresh installations need the reviewed archive; no released ROM is certified. |
| `jobbench` | Source, package and image identity plus installed workflow acceptance remain pending. |
| `kumo` | Prepared engine/protocol transitive dependencies, private state and lifecycle remain under review. |
| `livecodebench` | Released executor/aggregator artifact custody and representative native sandbox behavior beyond authored controls. |
| `livemathbench` | Judge configuration, scorer dependency closure and failure evidence require per-dataset acceptance. |
| `locomo` | Released fixtures/corpus and selected Harbor scorer parity beyond authored pinned-version controls. |
| `math` | Real Math-Verify 0.9.0 authored native-sandbox equivalence plus prediction/reference/deadline failure classification; released source/pool custody and full benchmark coverage remain unverified. |
| `math500` | Same shared real-artifact failure controls as Math; released source/pool custody, full benchmark and cross-version equivalence remain unverified. |
| `memoryagentbench` | Exact locked archive/tables/BPE verified; real sentence, token-ID and upstream chunk parity passed on six authored texts in isolated pinned preparation Python. Remaining: released corpus preparation, broader corpus parity and separate judge acceptance. |
| `musique` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `nq` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `officeqa_pro` | Corpus provenance/privacy and office scoring dependency closure. |
| `personamem_v2` | Prepared engine/protocol transitive dependencies, private state and lifecycle remain under review. |
| `physicsquestions` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `reasoning_gym` | Historical exact pinned snapshot found, read-only hash/byte check matched, task-owned atomic import loaded 1,780 records, and actual native official scorer controls gave 1/0/0.65. Remaining: public acquisition/distribution licensing and historical generation reproducibility; no full benchmark claim. |
| `scienceworld` | Real world/native scorer execution beyond prepared 1.2.3 artifact, original JAR SHA, inspected JRE17 and launcher/CLI/operator fake-engine controls (producer scoped 27 passed, 1 skipped). Noop does not generate gold; original scorer unchanged. |
| `searchqa` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `sportsunderstanding` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `spreadsheetbench` | Released workbook semantics and complete corpus acceptance beyond implemented managed Python/private LibreOffice manifest/trusted helper/isolated CLI controls; native closure remains nonhermetic. |
| `spreadsheetbench2` | Released workbook and Excel parity beyond implemented managed Python/private native manifest/trusted helpers/isolated CLI and UNO controls; native closure remains nonhermetic. |
| `strategyqa` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `strongreject` | Bound trusted judge configuration, failure/unmeasured behavior and explicit model-authorized acceptance. |
| `supergpqa` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `swe_bench` | Official evaluator/image custody and real Docker task lifecycle beyond prepared managed recipe, exact admission and two actual official-worker checks; no Docker/model/benchmark run. |
| `swe_bench_pro` | Source, package and image identity plus installed workflow acceptance remain pending. |
| `tau2_bench` | Actual source-bound package, tools, user simulator, recording and ALL scorer are verified on authored arithmetic with scripted user/judge HTTP; other domains and released episode custody remain separate. |
| `tau3_bench` | Actual source-bound package, tools, user simulator, recording and ALL scorer are verified on authored arithmetic with scripted user/judge HTTP; other domains and released episode custody remain separate. |
| `tau_bench` | Five authored cases exercise actual vendored tools, HTTP service and DB/output/replay scoring with source hashes checked. Its original container deployment remains an independent boundary. |
| `terminal_bench` | Per-task image/toolchain identity, isolation, verifier and cleanup acceptance. |
| `travelplanner` | Released full-pool semantics beyond actual artifact correct=1/wrong=0 controls; no full benchmark claim. |
| `triviaqa` | Authored provider and QA controls; released datasets and every task are not certified by these controls. |
| `twowikimultihopqa` | Released source/pool custody and complete metric-preserving coverage beyond selected cold stdlib authored controls; remote/Parquet preparation checked separately where used. |
| `widesearch` | Real judge/released full-pool semantics beyond three local fake-judge artifact modes; preserve tested permanent preparation reservations. |
| `workbench` | Released-world lifecycle beyond actual artifact bubblewrap relay/post-exit positive/negative controls. |

## Audit verification receipt

`python3 tools/datasets_doc.py --check` passed and reports agreement with the
adapters; `--write` reported 56 datasets. The focused offline command was:

```
python3 -m pytest -q -rs tests/quality/test_datasets_doc.py tests/quality/test_native_qa_preparation_runtime.py tests/quality/test_spreadsheetbench_execution_runtime.py tests/quality/test_tau_execution_binding.py --basetemp=<external-temporary-directory>
```

Result: **39 passed, 3 skipped**. SpreadsheetBench's external execution artifact
control skipped once because no explicit artifact was supplied; Tau2/Tau3's
prepared-store controls skipped twice because `HG_TAU_EXECUTION_TEST_STORE` was
unset. These historical skips describe only that audit invocation; later explicit
artifact controls below add evidence for their named paths.
`git diff --check` passed for the maintained inventory and generated document.

## Additional integrated evidence

AgentDojo's managed CPython 3.12 artifact and 112-file source snapshot were
prepared; the read-only check covered 949 pairs. Ten native fake-evaluate/relay
controls passed without a real episode. The integrated SpreadsheetBench v1/v2
and AgentDojo focused suite passed 39 controls. Spreadsheet native manifests,
trusted scorer helpers and CLI/UNO isolation are implemented, while the native
OS/Python standard library compatibility boundary remains nonhermetic.

Actual managed artifact controls returned TravelPlanner correct=1 and wrong=0
and checked WorkBench bubblewrap relay and post-exit positive/negative behavior.
WideSearch's focused suite passed 18 controls, including scorer/precision/fetch
checks in the selected artifact and three local fake-judge modes. These establish the named paths,
not full released benchmark or live judge acceptance. Permanent preparation
reservations and deterministic barrier controls now prevent late-claim overwrite
of all directory bytes. This concurrent publication control does not establish
real released-source preparation or full semantic readiness.

Harvey LAB and SWE Bench special-worker CPython 3.12 recipes and exact dependency
locks are integrated and actual managed artifacts were prepared. Each actual
worker probe/check passed twice consecutively with exact artifact admission.
These were readiness probes only: no Docker operation, model/judge request,
legal-task/document-conversion workload or benchmark run. Public evidence retains
runtime/module identities without private case hashes or host paths.

## Task-tool isolation and readiness checks

SpreadsheetBench v1/v2 now bind `ENV.task_python` with an actor role to the
prepared managed dependency artifact. Actual unchanged default Loop controls
used local fake HTTP actions, bash and task openpyxl, then the native verifiers;
the four selected outcomes were 1/0/1/0. The producer's focused suite passed 42
tests including the four cases, UNO, sentinel and read-only checks; integration
rerun results are separate. These counts overlap earlier controls and are not
summed. Global site-package exposure is closed while retaining the task venv and
public SDK. Native UNO compatibility restores only original-pin files embedded
in the private manifest, checked by SHA and rebound individually read-only;
the entire global directory is not exposed. This establishes the named offline
editing/scoring path, not a hermetic native installation or released corpus
semantic acceptance.

ALFWorld's managed service was actually prepared. NumPy 1.26.4 and selected
original source/version evidence match limited original native files, rather
than reconstructing the full historical environment. Five actual bubblewrap
fake-engine and five control-wiring checks passed. ScienceWorld's prepared
1.2.3 artifact preserves the original JAR SHA and inspected JRE17 identity;
its producer scoped suite passed 27 tests with one skip for launcher, old/new
CLI byte parity, operator fake-engine and study controls. Its isolated oracle
worker is separate from the ordinary service, and noop does not generate gold.
Neither component ran a real game/world or its unchanged native scorer.

For managed components, the readiness tool's `--check` probes admitted packages
instead of platform packages. Other adapters retain an explicitly labeled legacy
metadata scope; this does not inspect their container/native environments. Read-only
provider and execution status are reported separately, alongside `data_present`
and `loader_ready: null` where loading was not attempted, under a minimal
environment. The producer reported 101 focused tests passed; actual Math,
Math500 and AgentDojo checks correctly reported admitted artifacts ready while
data was missing. Artifact readiness does not imply dataset readiness or a
full benchmark run, and these test counts are not combined with other suites.

Earlier MemoryAgentBench CDN/TLS failures remain historical evidence. A later
bounded request to the original locked URL returned the exact 4,319,076-byte
archive (SHA-256 `e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106`).
Offline archive restoration plus retained locked BPE verified all five files;
the real tokenizer microepisode now reports `full_tokenizer_ready: true`. Six
author-owned texts matched actual sentence boundaries, BPE IDs and the original
pinned upstream chunk function (only its downloader statement omitted). The
controls used an isolated pinned preparation venv, not platform packages or an
admitted execution artifact. No released corpus or live judge acceptance is
claimed; see `data/datasets/memoryagentbench/TOKENIZER_ACCEPTANCE.md`.

Remaining work distinguishes offline dependency closure from real workload,
licensing and resource acceptance. Broader architecture restructuring, PR
integration and publication are not completed by these scoped controls.


## Reproducible real Math-Verify failure boundaries

Component reports can establish artifact readiness while reporting
`data_present: false` and `loader_ready: null`. Artifact readiness does not
establish released-data or complete loader acceptance.

`tests/fixtures/math_native_failure_control.py` now makes the missing failure
classification reproducible through the ordinary private verifier and actual
Math-Verify 0.9.0, not a mock score function. It uses fresh, explicitly local
native-format authored records and a cold stdlib-only platform venv. The platform
venv lives outside `/tmp`, as required by the existing sandbox prefix policy;
working data and private reports remain in the test's temporary directory. No
package is installed and no artifact file is modified.

For each of Math and Math500, empty predictions, malformed final boxes and a
later wrong box are measured zero. A deliberately injected unparseable private
reference returns unmeasured, not zero. An actual outer native-verifier deadline
exhaustion after a successful score also returns unmeasured instead of reusing
the old reward. This is the outer verification budget, not a test of the
Math-Verify worker's internal 15-second limit. Private gold remains absent from
public tasks/setups and from the compact report. The unchanged result contract
normalizes deadline failures to `outcome: unmeasured`.

```
HG_TEST_MATH_RUNTIME_ROOT=/absolute/already-prepared-math-store \
python -B -m pytest -q tests/quality/test_math_native_failure_boundaries.py \
  --basetemp=/absolute/task-owned-temporary-directory
```

Observed result: **1 passed**, covering 16 actual verifier invocations. Six are
repeated positive baselines; the added scope is ten prediction/reference/budget
cases across two adapters. The test skips without an explicitly selected store
or when that store has no prepared artifact; an existing invalid artifact fails.
It never prepares missing dependencies. This does not certify released MATH data,
a benchmark run, every equivalence class, or cross-version agreement. Scorer,
source and dependency pins are unchanged. The matrix's existing authored-control
classification remains correct, so no matrix status was promoted.

Chess provider/import and authored stepwise receipts are also not evidence that
its independently pinned native scorer package is available; that boundary is
left open.
