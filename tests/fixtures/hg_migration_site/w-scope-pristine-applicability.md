# Pristine applicability determination - w-scope

**Conclusion (b): zero current datasets declare or select `pristine-offline`.
E1/E2/E3 are SATISFIED FOR THE CURRENT DATASET SET because their dataset-specific
condition has no instances; the supported mechanism is implemented and exercised
on authored controls under its documented trusted-caller contract. This is not
real-dataset pristine export, private-input or ownership verification.**

The class is **unused by current datasets**, not intrinsically unsuitable for
them. Its conditional dataset obligations are inapplicable to today's routes;
they become mandatory for any future opt-in. **E4/E5/E6 remain PARTIAL and
`artifact-only-state-export` remains OPEN/code-solvable. No blocker closes.**

Reviewed baseline: `9f78bcdd39a67e708372c44ad5c858a7e7d397fd`. Source review
date: 2026-10-15, registered `/tmp/hg-scope`, branch `wg-scope`. No adapters/task
pools were imported, and no benchmark, scorer, model, GPU, endpoint or container
was executed. Existing control packets were read, not rerun. An independent
read-only mechanism audit used registered `/tmp/hg-scope-audit` at the same HEAD;
that temporary worktree was removed after review.

## Factual declaration inventory and dispatcher

The inventory covers **all 28 literal `KIND = "dataset"` adapters**, not only the
survey's 23 shared-blocker datasets. All 29 top-level Python modules in
`data/datasets/` were parsed with `ast.parse` (the extra module is `__init__.py`).
Every adapter and its verifier/environment factory was read, including lazy
`__getattr__` factories and shared QA construction. A recursive source search was:

```sh
rg -n 'scorer_bundle|isolation_class|pristine-offline' data/datasets data/scaffolds
```

Within `data/datasets/`, there are exactly **two** declaration sites:

| Adapter | Declaration, file + current line |
| --- | --- |
| AppWorld | `data/datasets/appworld.py:270`: `"scorer_bundle": {"isolation_class": "snapshot-service"}` |
| Terminal-Bench | `data/datasets/terminal_bench.py:233`: `"scorer_bundle": {"isolation_class": "snapshot-service"}` |

There is **no other dataset `scorer_bundle`/`isolation_class` declaration and no
dataset occurrence of `pristine-offline`**. The shared QA factory does not add
one (`data/scaffolds/qa/base.py:322`, `:339`, `:478`, `:480`). The table below
accounts for every dataset; omitted declarations are not fabricated pristine
or snapshot declarations.

Source establishes selection as well as declarations:

- `data/scaffolds/scorer_isolation.py:10` reads only
  `spec["scorer_bundle"]["isolation_class"]`; line 12 returns `unknown` when absent.
- `eval/verification/verifier.py:465` reads this declaration; line 466 computes
  the actual answer/native/service route. **Only line 468's explicit
  `declared == "pristine-offline"`** dispatches to pristine at line 474.
  Lines 469-472 refuse reward-file, non-exit0 and non-artifact-command routes.
- `eval/run/runner.py:233` sends container state to its separate path.
  `eval/verification/verifier.py:244` admits only unknown/snapshot-service
  declarations on that path, rejects pristine, and line 252 records snapshot-service.
- `eval/run/runner.py:522` uses the Chess trusted-controller branch separately;
  `eval/run/chess_stepwise.py:64` explains its topology and line 67 records
  `in-process`. The survey's historical missing-class caveat is superseded here.

Thus **0/28 declared datasets, 0/23 surveyed shared-blocker datasets, and zero
current adapter factories select pristine**. The authored
`w-pristine-addition-control` is a test task, not a 29th published dataset.

## Applicability by actual route and scoring requirements

`INTERFACE.md:1178` explicitly makes isolation per-bundle rather than global;
`docs/scorer-admission-contract.md:58` preserves native and snapshot/service
routes. `INTERFACE.md:178` requires a separate verifier invocation, trusted
rematerialized inputs and read-only artifacts; lines 197-207 require private
inputs to stay out of actor SETUP. Those requirements do not prescribe pristine.
No dataset may escape those requirements by omitting an isolation declaration.

The survey's 19 native datasets are **12 QA + 7 other native commands**. Its four
remaining datasets use three actor snapshots and one mounted service. Current
source confirms the same routes; the additional five declared datasets are
accounted for separately.

| Family / every current member | Current source and required scoring surface | Is pristine owed today? |
| --- | --- | --- |
| QA: `bfcl_v3`, `chessbench_puzzles` offline, `fever`, `gsm8k`, `hotpotqa`, `musique`, `physicsquestions`, `reasoning_gym`, `sportsunderstanding`, `strategyqa`, `triviaqa`, `twowikimultihopqa` | QASpec bindings respectively `data/datasets/bfcl_v3.py:529`, `chessbench_puzzles.py:370`, `fever.py:53`, `gsm8k.py:238`, `hotpotqa.py:65`, `musique.py:232`, `physicsquestions.py:191`, `reasoning_gym.py:153`, `sportsunderstanding.py:166`, `strategyqa.py:176`, `triviaqa.py:339`, `twowikimultihopqa.py:654`. `data/scaffolds/qa/base.py:322` stages selected inline gold and checker/adapter dependencies; `:340` declares command, `:347` reward file, `:445` files environment. `data/scaffolds/qa/verify/check.py:238` and `:242` invoke the scorer in the verifier child. BFCL and MuSiQue overrides at `bfcl_v3.py:583` and `musique.py:227` retain that command contract. | **No.** Answer/prediction data plus private gold make a possible future projection, not an existing pristine obligation. Current reward/evidence and possibly fractional/multi-component metrics are not pristine's Boolean exit0 protocol. Conversion needs a new sealed command/contract and metric validation; a class relabel would be false. |
| Native replay: `alfworld`, `jericho`, `scienceworld` | Command/reward specifications at `data/datasets/alfworld.py:343`, `:358`; `jericho.py:253`, `:271`; `scienceworld.py:307`, `:326`. Checks reconstruct and replay trusted games/worlds using submitted logs: `alfworld/verify/check.py:147`, `:159`, `:178`; `jericho/verify/check.py:98`, `:109`, `:120`, `:130`; `scienceworld/verify/check.py:157`, `:170`, `:194`. Native/JVM dependencies and partial-reward conventions remain. | **No.** Fresh native replay is a supported actor-workdir verifier route. A log could be exported in a future design, but executable/game/JVM closure and reward semantics cannot be inferred from that fact. Current native custody/cleanup gaps remain real under their existing obligations. |
| Native execution: `bird_minidev`, `ds1000`, `livecodebench` | Command/reward specs: `data/datasets/bird_minidev.py:254`, `:258`; `ds1000.py:194`, `:204`; `livecodebench.py:264`, `:277`. BIRD executes gold/predicted SQL against the trusted database (`bird_minidev/verify/check.py:108`, `:119`); DS-1000 reads source as data then composes/executes official context (`ds1000/verify/check.py:26`, `:29`, `:55`); LiveCodeBench uses a separate executor (`livecodebench/verify/check.py:169`). | **No.** Submitted code can be data without execute bits, so future pristine is not inherently impossible. Today's runner/database/test/runtime/reward contracts use native workdir verification. Merely exporting a candidate would neither provision these closures nor validate their transformations. |
| Native workbook: `spreadsheetbench` | `data/datasets/spreadsheetbench.py:230`, `:251` declare command/reward; `spreadsheetbench/verify/check.py:165` reads result/golden workbooks, `:348` selects formula handling and `:235` starts recalculation. | **No.** Workbooks could be projected later, but formula/recalculation dependencies and scoring profiles need preservation. Native reward/evidence verification is currently declared and supported. |
| Actor snapshot: `gaia` | `data/datasets/gaia.py:103` selects container state. `:201`-`:212` supply command, separately staged official scorer/gold and reward/evidence files. `gaia/verify/check.py:90` and `:99` ultimately read answer.txt and invoke official answer normalization. | **No, with an explicit qualification:** the answer-only predicate is a plausible future pristine candidate; snapshot is not inherently necessary to that predicate. Current container/reward route is nevertheless valid under the per-bundle contract; neither the adapter nor interface mandates migration. A future migration requires the authoritative answer/private-gold contract and matching official metric, not an inferred pristine label now. |
| Actor snapshot: `tau_bench` | `data/datasets/tau_bench.py:186` selects container state; `:340`-`:359` supply trusted replay inputs and reward/evidence. `tau_bench/verify/check.py:266` replays clean ground truth, `:285` reads call logs/done/output gates, and `:294` reads the actor's final database before `:309` computes reward. | **No.** The graded DB/log/output state is intentionally supplied by the snapshot. A complete future task-defined projection is conceivable but not currently prescribed or safe to infer. |
| Actor snapshot: `terminal_bench` | `data/datasets/terminal_bench.py:200` explains full actor filesystem state; `:202` selects it, `:233` explicitly declares snapshot-service, `:234` runs trusted task test.sh and `:239` explains why exit status is not reward. `eval/verification/verifier.py:285` preserves task dependency-install networking and `:298` attaches declared bridge. | **No.** Arbitrary filesystem effects, task tests, dependency installation and reward files are the released contract. No authoritative narrow allowlist follows from the platform. Forcing pristine changes the task/scoring surface. |
| Mounted service: `appworld` | `data/datasets/appworld.py:270` explicitly declares snapshot-service, `:279` selects service API-call recording, `:298` uses mount state and `:301` keeps the service. `appworld/verify/check.py:45`, `:52`, `:56` reconstruct/replay/evaluate; `eval/verification/verifier.py:575` supplies recording mounts and `:587` preserves its live network. | **No.** Service recordings, initial/end DBs and evaluator/service connectivity are the reviewed class-specific contract, not an inferred pristine data export. |
| Extra controller route: `chessbench_puzzles` stepwise | `eval/run/chess_stepwise.py:28` reads trusted inline gold before actor execution; `:49` grades the relay receipt and `:67` records in-process. Runner bypass at `eval/run/runner.py:522`. | **No.** Trusted live-controller scoring has no post-actor pristine artifact projection. Its own gold/controller integrity obligations remain. |
| Additional published native dataset outside the 23: `agentdojo` | `data/datasets/agentdojo.py:84` files environment; `:91`-`:101` lazily produce command, selected private case/trusted runtime, reward/evidence. `agentdojo/verify/check.py:21`-`:25` replay action.json in that trusted runtime. | **No.** Same native replay reasoning. Its absence from the older shared-blocker survey is not evidence of pristine selection. This audit adds no blocker and executes no task. |
| Four declared refusals outside the 23: `api_bank`, `nestful`, `sotuqa`, `tau2_bench` | `data/datasets/api_bank.py:169`, `nestful.py:51`, `sotuqa.py:157`, `tau2_bench.py:150` define load refusals. SOTUQA's latent QA spec at `:121` also has no bundle. | **No current scoring instance.** Existing refusals are preserved; no hypothetical scorer/contract is fabricated and none of these datasets is executed. |

All native routes still require input provenance, private-input safety and runtime
integrity. Their actual dispatcher rematerializes inputs at
`eval/verification/verifier.py:528`, then uses a read-only task sandbox at `:607`
or the separately admitted host branch at `:613`. This finding does not certify
those boundaries for every dataset. It establishes that **no current requirement
entails the alternative pristine contract**. Thus “unused” is accurate for the
class; “inapplicable” describes the conditional clauses today, not permanent
semantic impossibility or universal dataset readiness.

## Mechanism completeness at the E1/E2/E3 boundary

Conclusion (b) requires positive mechanism evidence as well as an empty dataset
instance set. The following code paths are connected for an opt-in caller; they
are not merely unconnected schema helpers:

| Obligation | Implemented path and bounded control evidence |
| --- | --- |
| E1 authoritative task-defined export | `eval/admission/artifact_admission.py:43` authenticates exact pinned JSON and rejects duplicate keys; `:144` checks task/source/image/scorer/export/quota identity. `eval/admission/scorer_bundle.py:77` binds independently pinned bundle and task contract. `eval/verification/pristine.py:53`-`:85` loads them, checks selected task, probes and calls export. `artifact_admission.py:182` uses no-follow regular-file traversal, forbids hardlinks/executable data and detects during-read changes; `:308` copies only the explicit file list with per-file/aggregate byte and inode bounds and exclusive creation. No dataset allowlist is inferred. |
| E2 selected private input/authority/exclusion/scorer binding | The sealed scorer graph at `artifact_admission.py:97` supports exact dependency identities; `:225` rejects extra/missing/changed closure members. Separate trusted pins plus task/source/image/scorer agreement at `scorer_bundle.py:85`-`:92` connect the private dependencies to the selected export. `tests/fixtures/hg_wpristine/task-source.json:8` declares authored authority, `:9` identifies private expected.txt; `task-contract.json:1` seals it separately from answer.txt. `closure/check.sh:4` and `:5` read private42 from `/control-scorer` and submitted41 from `/control-input`. `unselected.txt` is absent from the exported inventory. Authority originates in the trusted caller's selected contract, not a claim manufactured by hashes. |
| E3 ownership/access under the caller contract | `pristine.py:27` excludes actor-tree declaration/closure paths; `:51` creates private exclusive platform scratch. `artifact_admission.py:308` documents caller source custody and platform-owned destination preconditions, `:330`-`:353` creates the export with explicit GID/modes. `:455` validates distinct scorer UID and DAC access, and `:479`-`:518` checks actual image/command/user, network-none, read-only rootfs, exact two RO binds and bounded tmpfs. `pristine.py:111` rechecks both projections after stopped-instance inspection, before `:116` starts the scorer. Runtime and cleanup evidence accompany its completion verdict. |

The **trusted caller boundary is material**.
`docs/scorer-admission-contract.md:32`-`:37` assigns independent pins, platform
ownership of both trees, source quiescence, fresh authentic runtime observations
and preservation through use to the caller. `artifact_admission.py:228` and
`:312` state the same preconditions. The API does not automatically authenticate
an arbitrary dataset's official authority, exclude same-UID writers, or discover
private inputs. Ownership/access primitives are implemented and observed on the
authored control; **dataset/backend ownership validation has zero applicable
dataset instances, and has not been performed on real datasets**. Treating
caller preconditions as satisfied for all deployments would be false. Treating
their absence in nonexistent dataset opt-ins as missing export/private-input API
support would also be false. Continuous custody remains separately open in E4.

Existing raw bytes, rather than only report conclusions, were inspected:

- `reports/w-prifix-evidence/final-control/control-receipt.json` records
  selected=1, expected_trials=1, measured=1, invalid=0, unmeasured=0, score=0.0,
  benchmark=false, staged_pilot=false, production_authorized=false. Its
  `verdict.json` and `docker-02.json` retain actual41/expected42,
  completed_check=fail, attach/State exit1 and removal. `projection-rechecks.json`
  inventories sole answer.txt (3 bytes, mode0640, UID1026), separate three-file
  closure including private expected.txt, and both successful pre-start rechecks.
  `inspect-01.json` records distinct user65534:1026 and the two read-only binds.
  `post-removal-inspect.json` and `post-return-projection.json` retain bounded
  absence observations. This supplies a completed real production-dispatch
  mechanism control, not a dataset instance. The invalid original w-pristine
  attempt is preserved and is not promoted.
- Current-source follow-up
  `reports/w-quiesce2-evidence/after/unchanged-attempt-02/verdict.json` records
  measured score1.0 and no_change_observed, always quiescence_established=false.
  `after/source-aba-attempt-01/verdict.json` records the write/restore as
  difference_observed, unmeasured, score=null. Both retain selected task binding
  and **outer_run_id=null, trial_id=null, outer_run_trial_binding=not_provided**.
  `reports/w-quiesce2-receipts.md` explains the directory-swap counterexample
  that endpoint probes miss. These real controls exercise the mechanism and
  honestly bound it; no scorer/runtime command was rerun for this determination.

This establishes completeness of the **E1/E2/E3 mechanism for a conforming opt-in
caller under those explicit trust inputs**, not completeness of every clause of
the blocker or a plug-and-play production authorization. The completion-marker
protocol and fail-closed initialization/cleanup behavior in
`pristine.py:124`-`:156` and `docs/pristine-scorer-protocol.md` are also required;
an exit-only scorer is not a supported measured caller.

## Verdicts and residual obligations

| Clause | Current scope verdict and remaining obligation |
| --- | --- |
| E1 | **SATISFIED FOR CURRENT DATASET SET (zero applicable pristine datasets)**; task-authoritative export API/control exists. Every future opt-in must supply and validate its own task projections. No real dataset export contract is certified. |
| E2 | **SATISFIED FOR CURRENT DATASET SET (zero applicable pristine datasets)**; selected private dependency binding/exclusion API/control exists. Future opt-ins owe authoritative selected private-input/gold generation, exclusion and scorer binding, including actor-private-input validation. |
| E3 | **SATISFIED FOR CURRENT DATASET SET (zero applicable pristine datasets)**; ownership/access mechanism is control-verified under caller custody preconditions. Future dataset/backend opt-ins owe actual source/projection ownership validation; same-UID source/writer custody through use is not established. |
| E4 | **PARTIAL**, unchanged. Endpoint file probes miss transient directory substitution; trusted source/writer custody through use and execution-enforced outer-run/trial binding are missing (`pristine.py:67`, `:72`, `:159`). No atomically infallible lease proof is newly demanded. |
| E5 | **PARTIAL**, unchanged. W-quiesce's authored paired-state equivalence and bounded official/local comparisons do not validate applicable real-dataset state/private-input transformations and official metric/profile equivalence. A future pristine opt-in owes its own paired evidence; current routes retain their actual scoring-validation obligations. |
| E6 | **PARTIAL**, unchanged. Authored pristine object/projection cleanup and one-task dataset observations do not validate all applicable current backend/runtime lifecycle surfaces. Corrected AppWorld service caps still lack a post-change live observation; GAIA/Tau and other uncovered paths remain outside the control packet. No exhaustive absence or infallibility certificate is required. |
| E7/E8 | **SATISFIED**, unchanged: class-specific snapshot/service review and no inferred artifact allowlist. This review is not broad live validation. |

The reassessment's E1/E2/E3 PARTIAL labels mixed two facts: no provisioned
standard-dataset pristine instance and limited authored-control evidence. This
scope determination supersedes those labels **only at current dataset scope**.
It does not alter their historical evidence or claim the remaining E4/E5/E6
requirements vanish. (a) is unsupported because there is no current pristine
dataset instance, explicit or required. (c) is unsupported at the E1/E2/E3
mechanism boundary: supported callers have a connected, exercised implementation;
the identified quiescence/binding/equivalence/lifecycle gaps remain E4/E5/E6.

## Exact registry wording

Append the following **verbatim** to `artifact-only-state-export.observed`.
Preserve all prior text as history; this dated paragraph explicitly supersedes
its current E1/E2/E3 scope labels. Add this report as repository evidence with
locator `## Exact registry wording`. Leave status, family, ID and the complete
`next_action` unchanged. This is an applicability correction, not deletion,
downgrading, waiver, or a rewritten closure criterion.

> Pristine applicability scope determination 2026-10-15 - w-scope: source review of all 28 declared datasets finds zero pristine-offline declarations or selections; the only explicit dataset isolation declarations are appworld and terminal_bench, both snapshot-service. The 23 surveyed shared-blocker datasets retain 19 actor-workdir and 4 snapshot-service routes; the additional AgentDojo native route and four load-refused datasets add no pristine instance. No current adapter requirement mandates pristine migration. E1/E2/E3 are SATISFIED FOR THE CURRENT DATASET SET (zero applicable pristine datasets), superseding the earlier PARTIAL labels only at that conditional dataset scope. The opt-in export/private-input/ownership mechanism is implemented and verified on authored controls under its documented trusted-caller pin, ownership and custody preconditions; no real-dataset pristine export, private-input safety or backend ownership validation is established. The class is unused by current datasets, not inherently inapplicable to future migrations. Every future pristine opt-in must provision authoritative per-task export and selected private-input/gold contracts, authority, exclusion and scorer binding, and validate actual backend/source/projection ownership; it must also discharge applicable quiescence/trial binding, scoring equivalence and live lifecycle/cleanup requirements before any dataset readiness claim. This finding grants no future opt-in a contract or automatic satisfaction. E4/E5/E6 remain PARTIAL, E7/E8 remain SATISFIED, and artifact-only-state-export remains OPEN/code-solvable with its full next_action unchanged. scorer-offline-closure and all dataset blockers remain unchanged; no blocker closes and production_authorized remains false.

All 23 dataset references to each shared blocker (46 total), dataset records,
historical receipts and `scorer-offline-closure` remain unchanged. The same commit
adds source-only pins in `tests/quality/test_dataset_blockers.py` for the precise
declaration inventory and conditional/residual wording; no existing shared-gate
pin is weakened. A future declaration must trigger re-review rather than inherit
this finding. Checker and focused test output: `reports/w-scope-validation.txt`.

Validation: checker **valid=true**, 28 datasets, two shared blockers, 11 historical
records, production_authorized=false; **56 focused source-only tests passed**.
Structural comparison against baseline additionally confirms all preserved fields
and exact equality between the proposed paragraph and the appended registry text.
