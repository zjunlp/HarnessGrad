# Linux runtime acceptance remaining work

The [2026-11-08 final-source Linux validation](linux-release-validation.md) records
107 selected files with **1721 passed, zero failures/errors and eight explicit
optional skips**, plus source-free wheel, native and required-CI controls at
`ecb004b7`. See its [receipt](../.github/assets/linux-final-delivery-20261108.json)
for exact selections and retained failed attempts. This is bounded software
acceptance; Windows/macOS and external dataset/image inputs remain separate.

This is a source and test evidence audit of 56 published adapters, not a host
installation certificate or benchmark result. `data/runtime-support.json` is the
maintained matrix; regenerate `DATASETS.md` with
`python3 tools/datasets_doc.py --write`. The shared `native_qa` profile is a
preparation-only component and does not add a 57th dataset. Its use of the
execution-profile storage mechanism does not make it a scoring runtime.
`jobbench_renderer` and `native_harbor` are also non-adapter component profiles.
The former isolates the pinned author renderer; the latter binds Harbor verifier
dependencies. None increases the 56-adapter count.

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
external-runtime reuse and unknown-cost controls. That invocation skipped four Chess native-package
controls, two uncached-image controls and one full PersonaMem inventory check.
Later original-package recovery closes the named Chess controls, as recorded
below; the historical receipt is unchanged. The two image controls and external
PersonaMem inventory still require their own inputs. This is the stable Linux baseline for
the next architecture wave, not full-suite or full-Goal completion.

The cold stdlib controls in `test_stdlib_adapter_acceptance.py` exercise selected
loader and oracle/wrong/noop routes through the native sandbox using authored
records, including BFCL irrelevance and FEVER evidence branches. They leave
production pool/count pins intact. Other relevant controls are
`test_native_qa_preparation_runtime.py`, `test_spreadsheetbench_execution_runtime.py`,
`test_tau_execution_binding.py`, `test_reasoning_gym_managed.py`,
`test_tau_bench_prepared.py` and the Jericho managed/relay/authored-engine tests.
Local import/preparation evidence is separate from test-file presence.
An earlier Jericho focused result was 49 passed, 1 skipped, 7 deselected;
unsupported authored-native and fake-engine controls retain their limited scope.
Tau2/Tau3 shared-bwrap relay controls passed twice with dependency stubs, covering
readonly/source/neighbor privacy rather than real upstream service lifecycle.

## Historical next delivery priorities (pre-final-validation snapshot)

These priorities record the earlier planning snapshot, not a new statement
that the final-source checks above remain unrun. They follow the Linux Goal: implemented dependency boundaries,
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
   responsibilities, H0 planning, driver readiness/configuration, metadata and
   finalization have been separated. Remaining ownership/equivalence review and
   final installed delivery checks must follow their actual scope; a large root
   file alone is not evidence of a missing responsibility boundary. Refresh the actual
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

## Current dependency and identity evidence (2026-11-08)

The earlier dependency reconciliation read source `6a34b591`; that historical
source label is unchanged. This update also projects the later retained named
component receipts linked below. Documentation reconciliation runs only the
metadata generator and its consistency check, not a new test suite.
The results below are separate, sometimes overlapping controls, never a summed
final acceptance count. Historical delivery/architecture receipts stay immutable.

- **Kumo**: 210 original-pin env/common Python source files matched their hashes.
  A package-free `python -I -S` process loaded the canonical preparation recipe,
  registered 103 environments and exercised one author-created datapoint.
  No released JSONL/book, third-party import, model, network, write or child
  process was used. `common.configs`/`common.utils` and the registry's agent
  registration branch are not invoked by `prepare -> env.load_env`. The
  historical original-pin prepare log records 5,290 eligible/10 excluded and the
  unchanged manifest hash; this audit did not repeat the full preparation.
  Together with the retained 23-case prepared-route result, this closes the
  previously unknown Python dependency boundary, not all release semantics.
- **PersonaMem text-32k MCQ**: the retained dataset file result is 95 passed and
  one skipped full-release inventory. Stdlib preparation, ordered service state,
  selected private history/gold, actual sealed scorer, failures, cleanup and
  source refresh are not still generic implementation TODOs. The external full
  inventory and other context/modalities remain distinct.
- **JobBench rendering**: the pinned original renderer now runs in an explicit
  managed preparation artifact with a real Hub library and 15-package lock.
  Source/templates/helpers and worker/controller bytes are admitted before and
  after execution. An eleven-control retained group includes real-Hub/original
  renderer loopback execution plus missing/drift/error checks; no released
  generation, container or model is
  implied. Read-only check and missing-runtime refusal do not install packages.
- **Native Harbor**: original Harbor 0.23.0 and its canonical 90-distribution
  binding now ran an actual Trial/replay/script verifier on a tiny authored
  DeepSWE-shaped task through real relay/export: correct/wrong **1/0**, with
  owned containers, networks and images cleaned and the base image preserved.
  Earlier import/identity probes remain separate. This authored verifier is not
  JobBench's official weighted-rubric judge or released Pro grading.
- **JobBench judge**: the actual task-owned HTTP gateway now binds the selected
  bridge, preserves API paths and closes failed/normal starts correctly. A
  prepared model/normalized-endpoint identity is checked before cache reuse,
  before/inside/after the SDK child and in its return marker. Key rotation does
  not change semantic identity; keys and deployment details are withheld from
  method/public projections. The retained independent groups have 62 identity/
  gateway/real-SDK-child passes and 19 lifecycle/healthy-gateway passes; they
  overlap and are not added. Neither group runs a Trial, Docker task or model.
- **Judge-only routes**: 50 retained cold-stdlib authored local HTTP controls
  cover BrowseComp, DeepSearchQA, HLE, LiveMathBench and StrongREJECT, including
  failures, usage and malformed/configuration cases. Their executed scorer
  paths are stdlib prompts/parsers plus trusted HTTP; unused upstream SDK
  functions do not require platform packages. These are not judge-quality or
  released-task measurements. OfficeQA's vendored reward is also stdlib `re`.
  The later [named component receipt](../.github/assets/component-runtime-controls-20261108.json)
  records ten authored loader/reward/private-verifier tests and a separate tiny
  text import/load control; real corpus custody and citation quality remain
  outside that evidence.
- **SWE**: a new fixed-source `9d59de99` run completed six cases with zero
  skips in 200.69 seconds. Three actual Docker actor/relay/export/fresh official
  grader controls score repair=1, wrong=0 and empty=0, with verified private
  inputs, official-execution markers and complete actor/verifier/relay cleanup.
  Post-checks found all six owned containers and two fixture images absent; the
  original cached base image remained. The original 602-file upstream source
  and real 75-package canonical artifact are retained. The other cases are an
  actual official missing-execution response-shape control, an injected bridge
  unit using that shape, and an authenticated source/cache snapshot unit; they
  are not three more Docker/grader executions. This is new authored evidence,
  not retrospective repair of the old README six-pass claim, SWE Verified
  accuracy, a real model result or a pristine-offline certification.
- **Original native packages**: the public [recovery receipt](../.github/assets/native-original-recovery-20261107.json)
  records exact unchanged Chess/Jericho packages and actual authored scorer
  controls (Chess 12 passed, Jericho 1 passed). Import-only probes and the older
  skipped native controls are not substituted for those results. Fresh installs
  still need reviewed archives; no complete public archive URL or fully public
  reconstruction of all retained native/system bytes is claimed.

For an explicitly authorized repeat of the new SWE authored interface control,
use the current entrypoint and already prepared inputs (the old `HG_SWE_PYTHON`
README command is not this receipt):

```sh
HG_SWE_SOURCE=/absolute/verified-original-source \
HG_DATASET_RUNTIME_ROOT=/absolute/admitted-component-store \
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/quality/test_native_swe_native.py --basetemp=/absolute/owned-control-directory
```

The test needs the existing cached helper image and its authored offline wheels;
this documentation check did not run it, pull images or install dependencies.

The relevant implementation and scope guides are [JobBench renderer](../data/datasets/jobbench/README.md),
[native Harbor](../data/scaffolds/native_harbor_runtime/README.md),
[Kumo](../data/datasets/kumo/README.md) and [PersonaMem](../data/datasets/personamem_v2/README.md).
Where older component prose still says Kumo preparation was not measured, the
cold-source evidence above concerns dependency imports; it does not silently
turn an old TLS failure into a newly repeated full-pool preparation.

### What is still required, and what is not a software TODO

The diagnosed renderer, Harbor path/version-only identity, standard-dispatch
and JobBench judge gateway/identity implementation gaps are closed at this
snapshot; they are not retained as generic pending software work. This is not
proof of every adapter branch. Missing original data, ROMs, OCI images, native
archives, source licenses and
operator judge configuration are external input boundaries with explicit
preparation/admission, not grounds to invent a second implementation. Supplied
inputs must still pass those checks. A source recipe or an SDK import never
closes a named native-engine or task-lifecycle acceptance gap. The
[named component receipt](../.github/assets/component-runtime-controls-20261108.json)
now records genuine Harbor Trial, ALFWorld authored PDDL, AgentDojo SDK/bridge,
BIRD original-pin scoring and OfficeQA loader/reward controls. These close only
those named software checks. ScienceWorld reaches the original no-task JVM and
AppWorld reaches original SDK APIs; neither has a valid task episode or score.
CooperBench now has the bounded authored shared-lane controls recorded below;
a selected original resource group remains separate. Terminal's truthful
container-state observer/admission software fix now has finite authored
positive/negative and stale-reward rejection evidence; released task/image
inputs and final delivery gates remain separate. SWE's fresh six-case receipt remains
its own Docker/official-grader evidence. A separate SWE Pro audit used the pinned capture
command, real bubblewrap/Git and a local runtime double: normal/nonzero early
exits capture when budget remains, but a native tool-command timeout aborts as
unmeasured (`score: null`), not a fabricated zero. HG capture calls remain
bounded by the actor deadline and cleanup's three-second allowance. Full
upstream timeout partial-patch capture is an explicit unsupported transport
boundary, not certified by import/completed-path controls or added as a new
all-upstream-parity Goal; no Docker or official regrade was run by that audit.

Final-source integration and installed resource/generator checks still need the
final delivery receipt. No full benchmark, all-domain or 56×7×26 combination,
live/paid model, private-resource redistribution or complete Excel/OS equivalence
is added as a new Goal requirement. This document does not declare the Goal done.

## Named component controls and original BIRD handoff

The [sanitized component receipt](../.github/assets/component-runtime-controls-20261108.json)
records source/evidence digests and bounded outcomes without raw host paths,
private credentials or task hashes. Original raw evidence remains retained by
its producers; historical delivery receipts are unchanged. ALFWorld's authored
PDDL success does not combine its engine with the platform relay/native scorer.
AgentDojo's real SDK/bridge 1/0/0 is not its full actor/relay lifecycle. OfficeQA's
vendored reward is stdlib `re`, not an Office SDK or a citation metric.

CooperBench has two separate layers in the named receipt: **16 authored tests,
zero skipped**, including original shell/Git scorer and bwrap peer controls,
used host-relocated verifier placement and do not prove a prepared image. A
separate normal public `run_one` control used two actual observed-v1 workers and
an independent fresh verifier with original `check.py` and pinned Harbor
filter/merge/both-feature algorithm: correct/one-wrong **1/0**, both measured.
All six owned containers and one authored fixture image were inspected absent;
the cached base remained. Missing runtime manifest/capacity, freeze drift and
missing selected image refused without task/model execution. Source lock
`34487d5c` records 75 named files byte-identical to the earlier `d05380ce`
source; neither label is a new final-source validation. Authored repository,
features and shared-lane success do not certify a real selected resource group
or all 652 published pairs. Exact group runtime/freeze/image/resources and any
mirror assets remain separately supplied inputs.

Terminal's earlier `d05380ce` high-level red and intermediate unmeasured
attempts remain unchanged historical evidence. The observer/admission software
fix now passed original-loader → public `evaluate`/`run_one` → snapshot-verifier
controls at `f671d05e`; five named source files match integration `63539297`.
Authored correct/wrong file-comparison reward/CTRF controls measured **1/0**.
A third control preplanted reward 1 and a passing report in actor state, while
the fresh checker emitted nothing: `per_task={}`, invalid stage `check`, no
cached task, and no measured score. It is neither zero nor one. Each case's
three owned containers, two networks and one snapshot image were independently
inspected absent. Positive/negative receipts retain no-trace harness diagnostics;
these controls establish finite admission/scoring behavior, not a completed
research experiment. Real selected tasks/images/toolchains remain externally
supplied scope; final-source release gates remain pending, without a full
benchmark or Goal-completion claim.

BIRD's original manifest remains
`0ecaf2a6e99b64ee035e210192fdb45789fb290e9586dc3197fc26ec06df41e0`.
The reviewed software-only archive is 19,738,111 bytes, SHA-256
`215c8e25986b74d37d2085f4cc51ed80f1e6c09e1c16ccaaddfcb2ec13cd4681`.
It contains no task DB/gold. Obtain the archive plus original manifest,
SHA256SUMS and source/license sidecars from the maintainer; no public archive URL
or fully public binary reconstruction is registered. Python/OS/scorer bytes
were individually matched, not recaptured or upgraded. Chess/Jericho packages
and their original pins were not changed. With a matching checkout or installed
wheel and a fresh external store, use the existing interface:

```sh
python -B -m tools.prepare_native_runtime import --dataset bird_minidev \
  --root /your/external/native-store --archive /your/reviewed/bird-native-0ecaf2a6.tar.gz
python -B -m tools.prepare_native_runtime verify --dataset bird_minidev \
  --root /your/external/native-store
python -B -m tools.prepare_native_runtime verify --dataset bird_minidev \
  --root /your/external/native-store --probe
```

The fresh import, verification and sealed host probe passed. `/opt/hg-python` is
a sandbox target, not a required host installation. This prepares scorer
software only; released DB/source custody and dataset acceptance remain separate.

## Per-adapter remaining acceptance

Every current published adapter appears once below. The more detailed role and
verification scope remain in the generated matrix. A common requirement for all
external benchmarks is released-source custody and metric-preserving execution;
no item here authorizes downloading protected data or contacting a model.

| Adapter | Concrete remaining acceptance |
| --- | --- |
| `agentdojo` | Actual original SDK predicates/bridge authored correct/wrong/empty 1/0/0 and error refusals passed; retain the fixed source/runtime checks. Genuine full actor/relay lifecycle and released predicates remain unverified. |
| `aime` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `alfworld` | Original engine/wrappers completed and closed an authored CC0 PDDL world. Genuine engine plus relay/native scoring and released worlds remain outside that control; earlier fake-engine checks are distinct. |
| `appworld` | Original service/scorer SDK API handshakes and cleanup passed, but zero valid task worlds/evaluations. Compatible authored task DB/schema/metadata/scorer fixtures or separately supplied released inputs remain external; no valid lifecycle/score is claimed. |
| `authored_example_fix_bug` | Retain authored control scope; this fixture cannot establish external benchmark capability. |
| `bbeh` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `bfcl_v3` | Released function-call corpus/AST metric acceptance beyond authored call and irrelevance controls. |
| `bird_minidev` | Original 701-blob package and unchanged pin passed fresh archive import/verify/probe plus authored sealed SQLite scoring 1/0/0 and readonly/WAL controls. Reviewed archive supply, released DB/source custody, 500-row release parser and broader actor lifecycle remain separate; no public URL/full-public rebuild promise. |
| `browsecomp` | Pinned stdlib decrypt/prompt/scoring and trusted judge HTTP have scoped authored failure/usage/config controls. Gated source custody and real configured judge acceptance remain separate. |
| `chessbench_puzzles` | Original 718-blob package recovered and verified; four authored trajectories pass through its sealed scorer with rewards 1/0/0/0. Provider/interactive controls remain distinct. A fresh installation needs the reviewed native archive; no released pool is certified. |
| `cooperbench` | Sixteen authored tests passed (host-relocated verifier, not a prepared image); a separate real observed-v1 dual-peer/fresh-verifier run_one shared lane measured correct/one-wrong 1/0 with original scorer and owned cleanup. Missing manifest/capacity/freeze/image refusal passed without tasks. Exact selected original group source/runtime/freeze/image/resources/mirrors remain external; no 652-pair or real selected-group acceptance. |
| `costbench` | Actual original-package service, socket, trusted seal, native scorer and cleanup are verified on authored oracle/noop/invalid-tool cases; broader domains are outside this scope. |
| `costbench_dynamic` | Actual authored cost_change lifecycle/scoring passes 1/0/0; the other dynamic modes and broader domains remain outside that control. |
| `deepsearchqa` | Pinned stdlib answer/count/F1 parser and trusted judge HTTP have authored failure/usage/config controls. Preserve the configured-judge versus original Gemini metric boundary; no released/live judge claim. |
| `deepswe` | Canonical Harbor binding plus actual Trial/relay/export/replay/script-verifier authored correct/wrong 1/0 and owned cleanup passed. Released task/image/source custody and benchmark grading remain separate; authored verifier success is not official weighted judging. |
| `ds1000` | A complete CPU scientific artifact, actor shell and independent bound official verifier pass authored native controls. The preserved historical cohort/sweep is not re-certified under the new CPU variant. |
| `fever` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `gaia` | Stdlib materialized metadata/selected attachments and admitted native_qa Parquet preparation are implemented. Gated source/attachment custody and bounded task/scorer-route receipt remain distinct; no full-release claim. |
| `gsm8k` | Authored provider and QA controls; released datasets and every task are not certified by these controls. |
| `harvey_lab` | Real legal-task/judge/document-conversion lifecycle and native resources beyond prepared managed recipe, exact admission and two actual worker probes; no model/judge run. |
| `hle` | Pinned stdlib prompt/schema/parser and independent judge transport have authored local-HTTP success/failure/usage/configuration evidence. Frozen judge config and external source custody remain; no live judge/model acceptance claim. |
| `hotpotqa` | Authored provider and QA controls; released datasets and every task are not certified by these controls. |
| `jericho` | Original 1039-blob sealed package recovered and verified. The CC0 authored TextWorld-ABI story passes actual engine/relay/client, shipped replay and sealed scoring with 2/2 reward and cleanup. Fresh installations need the reviewed archive; no released ROM is certified. |
| `jobbench` | Managed original-renderer preparation and canonical Harbor/judge identity are implemented with scoped real-source/Hub/local-HTTP controls. Shared Harbor authored actual-Trial 1/0 is now recorded separately; official weighted-rubric Trial, explicit public-network approval and selected images/private tests remain separate inputs/acceptance. |
| `kumo` | Prepared execution and original env.load_env preparation imports are proven stdlib on the unchanged pin (210 hash-checked source modules, 103 registered environments, authored cold invocation). Retain source custody and existing 23-case actor/relay/scorer/failure scope; no new venv or full-release/model requirement. |
| `livecodebench` | Released executor/aggregator artifact custody and representative native sandbox behavior beyond authored controls. |
| `livemathbench` | Pinned stdlib extraction/label and configured judge transport have authored local-HTTP controls. Preserve single-sample versus G-Pass report distinction; released pool/live judge and all-trial aggregates remain separate. |
| `locomo` | Released fixtures/corpus and selected Harbor scorer parity beyond authored pinned-version controls. |
| `math` | Real Math-Verify 0.9.0 authored native-sandbox equivalence plus prediction/reference/deadline failure classification; released source/pool custody and full benchmark coverage remain unverified. |
| `math500` | Same shared real-artifact failure controls as Math; released source/pool custody, full benchmark and cross-version equivalence remain unverified. |
| `memoryagentbench` | Exact locked archive/tables/BPE verified; real sentence, token-ID and upstream chunk parity passed on six authored texts in isolated pinned preparation Python. Remaining: released corpus preparation, broader corpus parity and separate judge acceptance. |
| `musique` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `nq` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `officeqa_pro` | Named original vendored stdlib re reward/private-verifier tests: 10 passed, no skips; separate authored text local import/load 1/0, missing-corpus refusal and private source_docs boundary passed. Actual corpus custody remains external; citation quality and Office application capability are not scored. |
| `personamem_v2` | Supported text-32k MCQ prep/storage/ordered relay/sealed scoring and failure/privacy/refresh controls are real stdlib paths (retained 95 passed, one full inventory skip). External prepared corpus and unsupported lengths/modalities remain separate. |
| `physicsquestions` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `reasoning_gym` | Historical exact pinned snapshot found, read-only hash/byte check matched, task-owned atomic import loaded 1,780 records, and actual native official scorer controls gave 1/0/0.65. Remaining: public acquisition/distribution licensing and historical generation reproducibility; no full benchmark claim. |
| `scienceworld` | Original 1.2.3 JAR/Py4J/JRE17 no-task JVM handshake/clean exit passed; 30 task names are metadata. No valid task load/reset/step or gold generation; valid episode/native scoring remains unverified. |
| `searchqa` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `sportsunderstanding` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `spreadsheetbench` | Managed actor/verifier and private verified-host CLI/UNO are implemented, with actual Loop editing and 21 official-source authored differences. Released workbooks are external; full Excel/corpus and hermetic OS equivalence are not added software TODOs. |
| `spreadsheetbench2` | Same scoped actor/native/21-differential evidence as v1 with distinct pinned v2 evaluator and modes. Preserve error/regression/modification semantics; complete Excel/released-corpus equivalence is not certified or automatically required. |
| `strategyqa` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `strongreject` | Trusted configured judge routing has real local-HTTP authored success/failure/unmeasured/usage controls. This does not authorize a real model or establish refusal-benchmark/judge quality. |
| `supergpqa` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
| `swe_bench` | New fixed-source six-case receipt includes three real Docker/relay/export/official-grader cases (1/0/0) and three separately scoped shape/bridge/source-cache controls. Verified 75-package artifact, 602 source files and owned cleanup; preserve unknown/pristine isolation distinction and separate original images/released tasks/models. |
| `swe_bench_pro` | Canonical Harbor binding and a shared authored actual-Trial 1/0 control are recorded; neither SDK/metadata checks nor that DeepSWE-shaped control certify official Pro grading. Local pinned-command/bwrap/Git controls preserve normal/nonzero early capture with budget; tool timeout remains explicitly unsupported/unmeasured (not zero), not a new full-parity Goal. Selected images/tests and actual grading acceptance stay separate. |
| `tau2_bench` | Actual source-bound package, tools, user simulator, recording and ALL scorer are verified on authored arithmetic with scripted user/judge HTTP; other domains and released episode custody remain separate. |
| `tau3_bench` | Actual source-bound package, tools, user simulator, recording and ALL scorer are verified on authored arithmetic with scripted user/judge HTTP; other domains and released episode custody remain separate. |
| `tau_bench` | Five authored cases exercise actual vendored tools, HTTP service and DB/output/replay scoring with source hashes checked. Its original container deployment remains an independent boundary. |
| `terminal_bench` | Observer/admission software fixed; authored public evaluate/run_one/snapshot scoring measured correct/wrong 1/0. Preplanted reward/pass report with no fresh checker output stayed unmeasured: empty per_task, invalid check, no cache. Per case three containers/two networks/one snapshot were absent. Historical red/unmeasured attempts retained; real selected task/image/toolchain inputs and final delivery gates remain separate. |
| `travelplanner` | Released full-pool semantics beyond actual artifact correct=1/wrong=0 controls; no full benchmark claim. |
| `triviaqa` | Authored provider and QA controls; released datasets and every task are not certified by these controls. |
| `twowikimultihopqa` | Named stdlib authored loader/metric controls remain scoped; retain original source/pool custody and declared metric limitations when supplying external inputs. Remote/Parquet preparation is separate; full-pool/model coverage is not a new implementation requirement. |
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

Earlier Harvey LAB and SWE Bench special-worker CPython 3.12 recipes and exact
dependency locks were prepared; each worker probe/check passed twice with exact
artifact admission. Those particular checks were readiness probes only, without
Docker, model/judge, legal-task/document-conversion or benchmark execution.
The later six-case SWE evidence above is separate; it does not turn an import
probe into a Trial. Public summaries retain runtime/module identities without
private case hashes or host paths.

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
Those earlier controls ran neither a real game/world nor the unchanged native
scorers. The later named component receipt separately records ALFWorld's real
engine completing authored PDDL and ScienceWorld's original no-task JVM
handshake. It does not establish combined relay/native scoring, a valid
ScienceWorld task episode or released-world acceptance.

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
