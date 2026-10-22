# Withdrawn requirements: proven-unobtainable evidence becomes a recorded limit

Operator decision, 2026-10-15. This file is the evidence record for every blocker
requirement withdrawn under this decision. It follows the same standard the operator
already applied to `paired-filesystem-quotas`, `snapshot-owned-cleanup` and
`official-scorer-identity`: **a requirement that cannot be satisfied here is recorded as a
limit with the search evidence attached, rather than left open forever.**

A requirement withdrawn here is one that no amount of further work in this repository can
satisfy — not one that is merely difficult. Each entry names what was searched and what
came back empty or impossible.

The operator's further decision on 2026-10-15 extends this record to the three runtime
requirements below. Their evidence describes why they cannot be advanced in the current
scope; withdrawal does not assert that a future provisioned runtime could never be tested.
No new live run, receipt authentication or search result is claimed by these entries.

## Shared requirement withdrawn: `scorer-offline-closure`

The withdrawn shared requirement includes S6 "validate live controls before pilots".
The remaining clauses retain their bounded evidence and residual limits; withdrawal
does not certify complete transitive image/ABI closure or official metric equivalence.

**Why it cannot be satisfied by work.** The clause asks for live controls whose staged
receipts are *cleared*, but the observation flag that would clear them is hard-coded false:
`w-s6` traced the path and showed the flag only flips after the admission the clause itself
helps withhold. It is a cycle, not a gap.

**What is retained instead.** The control *evidence* exists and stays in the record at its
actual scope — real raw results, each still publishing `score=null`:

| class | control | raw result |
|---|---|---|
| actor-workdir | ALFWorld noop | reward 0.0 |
| snapshot-service | Terminal-Bench `openssl-selfsigned-cert` | reward 0.0, CTRF 5/6 passed |
| in-process | ChessBench stepwise | terminal exchange, controller 0.0 |
| pristine-offline | pristine control | measured 0.0 (answer 41 vs expected 42) |
| mounted-service | AppWorld | measured loss |
| JVM / LibreOffice / child-process | ScienceWorld, SpreadsheetBench, DS-1000, LiveCodeBench, BIRD | lifecycle/limit/cleanup observations |

Terminal-Bench verifier coverage reached 13/89 real measurements; the 45th-round live
control set is preserved in the journal and the run artifacts.

## Shared requirement withdrawn: `artifact-only-state-export`

**Searched:** `w-scope` reviewed all 28 declared datasets and the QA scaffold for
pristine applicability; `w-quiesce2`, `w-e5`, `w-wal` and `w-e6b` examined selected-file
custody, scoring equivalence and lifecycle observations at their recorded scopes.
**Result:** zero pristine-offline declarations or selections. E1/E2/E3 are
SATISFIED FOR THE CURRENT DATASET SET (zero applicable pristine datasets), but
no real-dataset pristine export, private-input safety or backend ownership validation is established.
Selected-file endpoint probes do not establish interval quiescence: the same-UID
directory-substitution/restore counterexample evades those probes. Main-only SQLite
export omitted committed WAL state and changed the authored BIRD EX result from 0 to 1;
the subsequent native bound-input path refuses WAL-format SQLite before scoring.
E4/E5/E6 remain PARTIAL as standing limits, rather than a claim of general equivalence
or complete lifecycle coverage. This records the operator's withdrawal; it does not
classify every unexercised branch as impossible to test.

**Retained future opt-in boundary:** Every future pristine opt-in must provision authoritative per-task export
and selected private-input/gold contracts, and validate actual backend/source/projection ownership.
The former next_action remains the scope of that work, not an active shared blocker:

> Provision authoritative per-task export and private-input contracts for pristine scorers;
> validate ownership, quiescence/trial binding, scoring equivalence and live lifecycle/cleanup.
> Review snapshot/service state and recording surfaces under their declared class without
> forcing an inferred artifact allowlist.

The original scope citation remains `/site/w-scope-pristine-applicability.md`,
locator `## Exact registry wording`; its carrier was the withdrawn shared blocker.

## Dataset requirements withdrawn as unobtainable

### reasoning_gym / original-generation-process-receipt
Asked for: original pool generation invocation, interpreter/build and dependency-artifact lock, effective clock/timezone/date and hash state/order bound to c0ff4eaef318cd59afcc61472ebe206362fc0211c4b589ed0d53946a569bd177, and exact reproduction from recorded original inputs.
Searched: 733 enumerated Codex session snapshots plus ~/.codex/history.jsonl, ~/.bash_history and 5 archived files; 137 local branches and all 23773 stored main Git objects, reflogs/stash; 395197 enumerated Study regular files (393833 permitted raw files), 2149 resolved symlinks, 180 independent Study Git stores, 2 backup dot-git stores, 2 nested main Git stores and 985 magic-detected archives; own/main runs, discarded runs, state and fixtures;64859 census files. Scope excludes 1364 prohibited-dataset payload paths and 6 encrypted GPQA archive members; these contents remain UNKNOWN. Exact commands, per-scope hits/misses, manifests and explicit exclusions/errors are in registry/w-recgym-receipts.md and registry/w-recgym-validation.txt.
Result: the retained records did not capture the required original process fields; no original launch receipt is present in the searched readable/permitted corpus. The documented generator recipe and digest pin exist, but the original invocation, environment, clock/timezone and hash state remain UNKNOWN. Whether a record was never captured or later deleted is UNKNOWN; no historical or unrestricted-machine absence is asserted. Withdraw only the requirement to produce original records unavailable from this corpus; preserve the installed pool, seed/families/order, failed 84-row/six-family regeneration evidence and all independent behavioral/metric/runtime uncertainties. No exact reproduction, zero score, scorer closure, shared-gate reinstatement or diagnostic authorization is inferred.
**Why it cannot be advanced here:** the pool was generated **on this machine** from a declared recipe and seed; no upstream or third-party publication can hold the producing process record, and the entire readable/permitted on-machine corpus has been enumerated and is empty. The entry does not assert that the fields were never captured, and it does not assert any historical or unrestricted-machine absence.
**Retained:** the installed pool and its `c0ff4eae...` digest, the declared seed/families/order, the 84-row/six-family failed-regeneration evidence, and every independent behavioural, metric and runtime uncertainty remain recorded. No exact reproduction, zero score, scorer closure, shared-gate reinstatement or diagnostic authorization is inferred.


### `hotpotqa / historical-comparison-receipts / original-v4-receipts`
**Former status:** `measurement` within an active blocker that stays open; this sub-requirement is
withdrawn.
**Asked for:** the original user-stopped v4 run's selected-ID/trial/error/timeout receipts and its
source/split/manifest/model/harness provenance receipts, with measured/invalid/unmeasured counts and
lifecycle/cleanup reconciliation.
**Searched:** an enumerated on-machine scope. `w-hotpotrec` recorded a search over
`~/.codex/sessions/**` (722 transcripts), the repository's full object store, history, reflog and
stashes, `runs/`, `runs_discarded_*`, `.hashstate` state, `tests/fixtures/`, the named archives and
`/mnt/20t/xubuqiang/hg-census/`, plus adaptive existence checks on `/tmp/codex` and
`/tmp/harnessgrad-runs`. The parent re-checked on 2026-10-15: `HarnessGrad-evidence-archive/` holds
only `chess/`, `codex/` and `qwen-baseline/`; the two stranded pre-rewrite clones hold adapter source
only; and both `/tmp/codex` and `/tmp/harnessgrad-runs` are absent.
**Result:** the historical `ls` listing proves the v4 split file, console log and 1,179-byte budget
ledger **existed** at `/tmp/harnessgrad-runs/hotpotqa-h0-10pct-v4`, and a captured read of
`qa-h0-incomplete-index.json` binds the candidate ID `hotpotqa-h0-10pct-v4` to the dataset, the
`incomplete` status and that run directory. The files themselves are now absent over that scope and
the binding of the candidate ID to the reported 94/1382/1476 coverage is unresolved. The deletion
cause is **not** established and is not attributed: the recorded `/tmp` hygiene patterns
(`/tmp/hg-task-*`, `/tmp/harnessgrad-pytest-*`, the `hg-w*` scratch sets) do not cover
`/tmp/harnessgrad-runs` or `/tmp/codex`.
**Why it cannot be advanced here:** the v4 run was a **local pilot of this platform** - its run
directory is `/tmp/harnessgrad-runs/hotpotqa-h0-10pct-v4`, under this machine's own run-ID convention -
so no upstream or third-party publication can hold its receipts. Every on-machine source has been
enumerated and is empty. Unlike `bird_minidev` and `ds1000`, the wording "not present in this
repository and not published upstream" would **overstate** this case: the receipts existed and were
lost, rather than never being captured. The only conceivable remaining source is a machine-external
backup of `/tmp` predating the loss, which is outside this repository's reach.
**Retained:** the candidate ID, the historical filenames, the captured index tuple and the enumerated
absence are recorded in `registry/w-hotpotrec-receipts.md` and `registry/w-hotpotrec-evidence.json`.
`historical_runs` keeps `run_id=null` and `effect=null` and is never edited, resumed, rerun or
overwritten; unknown outcomes stay unmeasured and never become zeros. The blocker's remaining
obligations - current selected scorer/control admission, the retained first-five packet and the
synthetic timeout closure - are unchanged, and any separately authorized future diagnostic still uses
a fresh ID under the staged policy.

**Superseded 2026-10-15:** the sentence above describes the state when this limit was recorded.
The blocker itself was afterwards **closed** under reading (A) once the parent filed it as a closure
candidate and the operator confirmed it; the closure note is in `registry/dataset-blockers.json`
(`review.notes`) and the clause table is in `registry/blocker-closure-assessment.md`. This withdrawal
entry, its enumerated scope and its retained limits are unchanged, and nothing here is reopened: the v4
run stays incomplete with `run_id=null` and `effect=null`, and no diagnostic is authorized.


### `livecodebench / row-attestation`
**Asked for:** a metadata-only complete upstream catalogue/rank plus authenticated
row/pool/split/statement linkage.
**Searched:** the authoritative upstream source, repeatedly, across three separate rounds.
**Result:** **zero authoritative upstream row IDs obtainable**; the upstream count is not
published, so the locally declared 1,055 count cannot be corroborated from public sources.
Six pinned shard identities were linked to the adapter's own declarations, and the inert
selection and conditional split reproduce — that part is retained.

### `twowikimultihopqa / hub-data-provenance`
**Former family:** `source-provenance`.
**Asked for:** metadata-only original-release archive/file digest custody and completeness
attestation.
**Searched:** exact-revision upload history, release/tag inventories, a zero-result
licence-issue search, and the original and later Dropbox HEAD receipts.
**Result:** the original release is advertised with an **opaque ETag and no content digest**
(a 259,216,602-byte attachment). An opaque ETag is not a digest and was explicitly refused
as a substitute. The paper claims 192,606 examples while the witness card carries
conflicting counts, so no coverage attestation can be derived either.

### `twowikimultihopqa / ujson-native-offline-closure`
**Former family:** `scorer-dependency-network`.
**Asked for:** authenticated linkage from the published wheel to its source/build inputs,
including the double-conversion build selection.
**Searched:** PyPI build attestation (**404, "No provenance available"**) and then a full
**reproducible rebuild** from the pinned commit `59875e08` inside the pinned image.
**Result:** the rebuild **did not reproduce** — all six member names match and LICENSE,
METADATA and `top_level` are byte-identical, but the compiled extension, `WHEEL` and
`RECORD` differ. The embedded double-conversion implementation is confirmed present; the
original vendored-versus-external-static selection is not observable in the artifact. The
image-bound runtime closure that *is* obtainable (network-denied import, resolved loader and
symbol providers, synthetic evaluator) was completed and retained.

### `bird_minidev / sql-coverage-reconciliation`
**Asked for:** original selected-ID/phase/trial/error metadata binding the 90 phases to
actual measurements.
**Searched:** the tracked-source inventory.
**Result:** only summaries exist; the original phase/trial/error ledger is **not present in
this repository** and is not published upstream. The v4 incomplete record,
the v5 historical complete label and the partial-measurement/effect=null state are preserved
rather than reinterpreted.

### `ds1000 / host-slice-archive-review`
**Asked for:** original selected-ID/split/host-pool provenance plus original trial outcomes
and timeout/error/lifecycle records.
**Searched:** the same inventory.
**Result:** those records **do not exist in the repository**; `ds1000-v4-pending-review` is
preserved with `effect=null`. Archive acceptance stays blocked in the sense that no
measurement is claimed from it — what is withdrawn is the requirement to *produce* records
that were never captured.

### `gaia / frozen-lifecycle-receipt`
**Former family:** `snapshot-cleanup`. **Former status:** `runtime`.
**Asked for:** independently authenticated selected-only/source-lock, verifier/dependency
and artifact-only export evidence, plus resolution of the frozen V2/V3 lifecycle gaps.
**Evidence reviewed:** the former blocker's own next_action starts
"Retain policy refusal: do not provision or run GAIA." Its observed record says compatibility
only compares supplied identities, without validating receipts or authorizing reuse;
`eval/policy/gaia_snapshot_attestation.py` explicitly records
`"receipt_validation": "not-performed"`. Historical citations remain
`/site/adapter-integration-journal.md` (`## GAIA lifecycle wiring audit — Codex 165`),
`/site/preserved-lifecycle-review-b8.md` (`## GAIA / frozen-lifecycle-receipt`),
`/site/gaia-operator-packet-codex210.md`
(`## Required operator deliveries, in dependency order`) and
`/site/enforcement-proof-decision.md` (`Snapshot/cleanup uncertainty alone is not a new`).
**Why it cannot be advanced here:** the requirement withholds readiness pending live
evidence while its own action forbids provisioning or running GAIA. No permitted live run
can supply that evidence here. The operator withdraws this requirement as a standing limit;
this does not lift the policy refusal or authenticate the frozen receipts.
**Retained:** `gaia-validation-pilot-h0-20261117-v2` and
`gaia-validation-pilot-h0-20261117-v3`, their original receipts and historical records stay
untouched: never resume, rerun or overwrite either ID. V2's 9 persisted trials versus
10 done events and unknown API-timeout usage remain unresolved; V3's pre-verification
snapshot exit 137 remains unresolved. Both records remain incomplete with `effect=null`;
unknown outcomes stay unmeasured and no aggregate score is valid. Their original receipt
citations remain in `historical_runs`:
`/mnt/20t/xubuqiang/Study/gaia-h0-launch-v2-evidence/GAIA_H0_V2_RECEIPT.json` and
`/mnt/20t/xubuqiang/Study/gaia-h0-launch-v3/runs/gaia-validation-pilot-h0-20261117-v3/events.jsonl`.
Best-effort snapshots/cleanup retain uncertainty; known failures remain recorded.

### `tau_bench / local-state-lifecycle`
**Former family:** `snapshot-cleanup`. **Former status:** `runtime`.
**Asked for:** "Keep the runtime gate open." Require selected-only custody plus independent
run/trial/manifest-bound clean-state restoration, database/image identity, evaluator secrecy,
effective backend limits and artifact-only state export before a readiness claim.
**Evidence reviewed:** the former observed record says import reads the full ground-truth
pool (`data/datasets/tau_bench.py`, `_ALL_GT: list[dict] = _read_gt()`), both clean databases
are absent in the reviewed worktree, and no independent selected runtime/lifecycle receipt
was located in the reviewed repository metadata. The late verifier-input declaration
(`def _verifier(record: dict) -> dict:`) is source evidence, not reset/secrecy observation.
Historical citations remain `/site/adapter-integration-journal.md`
(`## AppWorld / GAIA / tau-bench — local state boundaries`),
`/site/preserved-lifecycle-review-b8.md` (`## tau_bench / local-state-lifecycle`),
`/site/tau-bench-vendoring.md`
(`3. **Two recorded generated databases are absent, intentionally ignored by git.**`) and
`/site/enforcement-proof-decision.md` (`Snapshot/cleanup uncertainty alone is not a new`).
**Why it cannot be advanced here:** the cited source/inventory cannot establish selected-only
custody or independently observed run/trial/manifest-bound restoration. The reviewed record
has neither the clean database pair nor that independent receipt; repeating declarations
or synthetic contracts cannot supply the missing observation. The operator now withdraws
the keep-open requirement and records those missing runtime facts as limits. This is an
operator scope decision, not proof that a future provisioned runtime could never be tested.
**Retained:** the full-pool import, missing database pair and unobserved custody, reset,
secrecy, backend-limit and export properties remain limits. `historical_runs` stays empty;
no frozen tau-bench run or independent receipt is invented. Existing vendoring and lifecycle
citations stay historical evidence, without a runtime pass, readiness claim or production
authorization. Best-effort snapshots/owned cleanup retain uncertainty; known failures still
refuse, and evidence outside the cited review remains unknown.

### `chessbench_puzzles / interruption-closure`
**Former family:** `termination-audit`. **Former status:** `runtime`.
**Asked for:** "Keep the runtime gate open pending independent audit/receipt authentication
and termination/quiescence review." Preserve both historical IDs, original receipts and null
effects; never resume, rerun or overwrite either ID, or turn missing outcomes into zeros.
**Evidence reviewed:** the former observed record reports 19 and 149/150 completed
task-trials with no terminal verdict/aggregate or cleanup closure. Termination authority
remains undetermined on the native bwrap/files path; an audit digest does not authenticate
either external run receipt. Former citations remain `data/datasets/chessbench_puzzles.py`
(`KIND = "dataset"`), `/site/adapter-integration-journal.md`
(`- Interruption audit: termination authority is undetermined`),
`/site/preserved-lifecycle-review-b8.md` (`## chessbench_puzzles / interruption-closure`),
`/site/chess-recovery-id-conflict-codex182-report.md` (`Both historical IDs`) and
`/site/enforcement-proof-decision.md` (`Snapshot/cleanup uncertainty alone is not a new`).
**Why it cannot be advanced here:** the independent-approval requirement was already
withdrawn by the operator in `docs/independent-review-deferred.md` (now cited as
`/site/independent-review-deferred.md`). That standalone exported source is not available
in this worktree; the existing journal's "Parent review and merge — w-trivia — 2026-10-15"
entry corroborates that the operator withdrew the independent-approval gates and instructed
workers not to re-impose human sign-off. Neither that decision nor source review supplies
authentication or missing terminal/quiescence observations for frozen runs. The operator's
new decision withdraws the remaining keep-open requirement; those unknowns remain limits.
**Retained:** `chess-pilot-h0-20261013` and
`chess-pilot-h0-20261013-recovery-01`, their original receipts, historical records and
`/mnt/20t/xubuqiang/Study/HarnessGrad-evidence-archive/chess/interruption-audit.md`
citation stay untouched. Never resume, rerun or overwrite either ID. Both records remain
incomplete with `effect=null`; the 19 and 149/150 coverage, missing terminal outcomes,
undetermined termination authority and unproven cleanup remain as recorded. Unknown
outcomes are unmeasured, not measured zeros or complete coverage. Future attempts still
require new unique IDs and separately applicable admission; withdrawal is no rerun or pilot
authorization. Best-effort cleanup retains uncertainty without requiring proof of exact
owned absence or atomic lease closure.

### `fever / producing-conversion-execution-receipt`
**Former status:** `external` within the active `fever / dataset-journal-gap` blocker; this
sub-requirement is withdrawn as a **scoped** standing limit.
**Asked for:** an immutable producer-attested receipt binding the FEVER gold-evidence export's producing
conversion to its revision and execution, its Windows-ID-to-original-ID assignment, its split, filter and
omission decisions, and its NEI retrieval and aggregation inputs.
**Searched:** the enumerated public scope in `registry/w-fevhub-receipts.md` - 33/33 Hub main-history trees
and commit messages, 7/7 distinct card and 4/5 historical loader bodies, refs `main`/`parquet` with no
tags, both parquet commit pages (59 commits, 26 generated-only), 25/26 generated-only trees, one public Hub
discussion and diff, the datasets-server `info`/`parquet`/`splits` routes with an exact
`dataset_infos.json` 404, the producer repository's 50 commit messages, four branch-tip trees, four
converter, three aggregation, one merge and ten README versions, eight issue or PR bodies and six comments,
releases/tags/actions, the pinned Papelo tree and README, the ACL paper page and BibTeX and the arXiv
abstract and HTML, and five exact zero-hit Zenodo queries - **plus** the two historical objects that search
had left unread, both now read by `w-fevobj` and preserved in `tests/fixtures/hg_fevobj/`: the historical
Hub loader at `1776db7e9abab525eae8e59c5015e3589dfe371b` (7,897 bytes, `git hash-object`
`ab71339be067bc98bcad7df12a12c0e05fc6b521`, matching the recorded tree entry) and the generated tree at
`ad8321607bcad0e534c243ec8c108a5787cea6c8` (4,596-byte HTTP 200 JSON, nine entries).
**Result:** neither recovered object contains, implies or references a producing-execution receipt, and the
enumerated search found none. The absence is **bounded, not categorical**: private, unindexed,
producer-held or later-deleted records remain UNKNOWN, and the producer is a public third party, so a
maintainer-published execution/input/output binding remains conceivable and would supersede this entry at
its actual scope if it appeared. The export's **output** identity is independently corroborated (installed
bytes, original-ID and complete-group correspondence), so this withdrawal removes only the requirement to
obtain the *producing execution* record from the sources this platform can reach.
**Why it cannot be advanced here:** every reachable source has been enumerated and read, the two objects
that could have carried the binding do not carry it, and the remaining holders are private, unindexed or
producer-side. No further local search can decide it.
**Retained:** authenticated export/installed/original-group custody, the explicitly chosen and named metric
`fever_exported_row_conjunctive_strict_at_5` with its definition-only status, the 1400-of-9525 exposed-row
analysis (1369 at 2-5 pairs, 31 at 6-25), the 51 omitted alternative groups across 42 represented non-NEI
IDs, the unauthenticated NEI and omission decisions, and every diagnostic-time obligation. No execution,
score, original-benchmark comparability claim or diagnostic authorization is inferred, and no local
conversion is treated as authenticating the historical run.

### `bfcl_v3 / simple-ast-contract`

**Former status:** `external`, family `official-scorer-execution`, the dataset's only remaining
blocker. **Withdrawn by operator scope decision on 2026-10-16**, not as a proven impossibility:
see the scope note below.

**Asked for:** producer-authenticated compiled-build manifests bound to the exact retained
archive/member hashes, naming CPython build implementation/version/SOABI, source revisions,
compiler/flags and compiled inputs; later, exact per-parent loader/`dlopen` choices; complete
component licence/NOTICE texts bound to those inputs; resolution of the strict websockets
provenance filename mismatch; a bound local handler/config/grammar; a bounded upstream decoder;
and recorded official full-call scorer execution with exact source/runtime/input/metric/receipt
identities.

**Searched:** the exact source/runtime custody and per-target frontier in
`registry/w-bfclrt-custody.md` and `tests/fixtures/hg_bfclrt/runtime-gaps-*.jsonl` — **87 exact
compiled-build entries remain UNKNOWN**, including member filenames/SHA-256 and their transitive
graph. PyPI publication attestations, supplied-leaf signature verification, SBOM declarations and
static SONAME availability were obtained or examined and are **partial evidence only**. A scoped
reachability result exists for the retained decoder: `ABSENT` over 317 enumerated platform files
(308 `.py` + nine shebang scripts) and `UNKNOWN` outside them, with an unwired preflight built
but deliberately not wired into the platform path.

**Why it cannot be advanced here:** the missing facts are **build-time facts held by the
producer**. A rebuild, an import smoke or a fabricated direct-function control cannot
retroactively authenticate an old wheel build, so no amount of work inside this repository
supplies them.

**Retained:** the function-name diagnostic (identifier-filtered bare-name multiset equality with
arguments ignored) and its source-only qualifications in `registry/w-evidence-qualification.md`,
`registry/w-evidence-disclosure.md`, `registry/w-evidence2-row-corrections.md` and
`registry/w-evidence2-passage-corrections.md`; all frozen receipts; the canonical
source/runtime/member/licence inventories; the named unresolved frontier; the deliberately
unwired preflight; `production_authorized: false`. **Any future official-scorer work reopens the
original technical gate** and must bound or bypass the retained prompting decoder's
`eval(ast.unparse(value))`
(`tests/fixtures/bfcl_ast_source/candidate-codex195/files/berkeley-function-call-leaderboard/bfcl_eval/model_handler/utils.py:302`),
must use `guarded_decode` from `tests/fixtures/hg_bfclguard/gate.py` or record why it does not,
and must not read the scoped `ABSENT` as general unreachability. No official AST/full-call score
and no official comparability is claimed.

### `triviaqa / hub-export-scorer-linkage`

**Former status:** `external`, family `source-provenance`, the dataset's only remaining blocker.
**Withdrawn by operator scope decision on 2026-10-16**, not as a proven impossibility.

**Asked for:** immutable upstream build/export **execution** evidence binding Hub export
`0f7faf33a3908546c6fd5b73a660e0f8ff173c2f` to the loader/converter actually executed, with
source/licence hashes, runtime/writer versions and consumed input revision/hashes; plus resolution
of HumanAnswers input incidence/preservation/redundancy, the counting rule behind prose **18669**
versus stored `rc.nocontext` validation **17944**, actual Domain/VerifiedEval/source membership,
row/scorer-key duplicates, complete official key mapping and numeric denominator/aggregation.

**Searched:** the expanded named search recorded in `registry/w-trivia2-review.md:283-297` —
supported historical-build/path-history interfaces, authenticated job records, unavailable
historical snapshots and original immutable maintainer artifacts. The authoritative public record
supplies **ancestry and output declarations but no immutable execution receipt**.

**Why it cannot be advanced here:** the export was produced years ago by a public third party, and
the execution record was never published. The absence is **bounded, not categorical**: private,
unindexed, producer-held or later-deleted records remain UNKNOWN, and a maintainer-published
binding would supersede this entry at its actual scope.

**Retained:** the DECIDED twelve-declarations-versus-eight-export-routes mismatch and the exactly
four declaration-only names; the limited 17944 stored-row observation, with **no invented
725-member explanation**; the bound operator first-five eval-side manifest/export and
scoring-input custody in `tests/fixtures/hg_wsel/triviaqa.json`; v2 and all historical identities
and receipts; the source-only disclosures in `registry/w-evidence-qualification.md` and
`registry/w-evidence-disclosure.md`. **The reported figure remains the local alias-max normalized
row-EM instrument** — not official key-level EM/F1, with no denominator and no official
comparability claimed. Do not infer execution, member equality, a unique-key denominator or
HumanAnswers redundancy from ancestry, predecessor blobs, counts, filenames, ETags or converter
names. No acquisition, payload audit or execution is authorized.

### Scope note for these two entries (operator, 2026-10-16)

These two differ in kind from the entries above, and the difference must not be flattened. The
earlier entries record requirements **proven unobtainable** after an enumerated search. These two
are withdrawn because **the operator judged the evidence standard higher than the project needs**:
"relax these two, there is no need for evidence this rigorous." The missing observations are still
missing and are **not** claimed to be unobtainable by this decision — a producer-published build
manifest or upstream execution receipt would supersede each entry at its actual scope. What the
decision removes is the requirement to keep hunting for them and to hold the datasets short of
closure until they appear.

This deliberately supersedes two sentences in the 2026-10-15 decision below: ``appworld` and
`bfcl_v3` stay real remaining work unchanged" now holds for `appworld` only, and `triviaqa` is no
longer in the "stays open" list. `appworld / successful-task-evidence` is **unchanged and still
open** — it is not an evidence-acquisition demand but a gate that only a genuine successful
measured actor task can clear.

### `appworld / successful-task-evidence`

**Former status:** `measurement`, family `actor-protocol`, the dataset's only blocker.
**Withdrawn by operator scope decision on 2026-10-16**, after the protocol question it was
written to answer had been answered by measurement.

**Asked for:** a genuine successful measured actor task under the staged pilot policy — the gate
stated that "source review, API activity and synthetic protocol controls cannot satisfy the gate:
only a genuine successful measured actor task can do so."

**What was measured.** Three operator-authorized diagnostics ran, all selecting the **same** study
task `cf6abd2_2` out of **147 eligible** (the pool holds **732** tasks):

* `w-appworld-20261014T225023Z-f02d7cd5` — raw TGC measured, `success=false`, `score=0.0` over 8
  official tests;
* `w-appworld2-20261015T050036Z-a6d55a63` — one trial, `jobs=1`, `rounds=0`, unchanged
  32-step/2048-output-token ceiling; the actor submitted at step 15 after 16 model calls;
* `appworld-diag-20261015T094842Z-3550c7c4` (commit `a861ca67`, which names the in-world completion
  boundary in the actor guidance) — **completion was performed, recorded and replayed**: its API log
  has **15** calls whose last is `POST /supervisor/message`, and that endpoint **is** `complete_task`
  (`appworld/apps/supervisor/apis.py:45-46`). Re-running the platform's **own** checker on the frozen
  log reproduces `reward 0.0`, `success False`, `num_tests 8, passes 6, failures 2` —
  `assert answers match.` (the actor supplied a prose sentence where this state-change task expects
  `None`, which AppWorld's own API doc requires to be left at the default) and a note-content
  assertion. **There is no platform defect and no replay defect.**

**Why it is withdrawn.** The gate existed to establish that the measurement path *can* succeed, not
to measure appworld. That question is now answered by measurement rather than by a success: the
task-independent fabricated-control set (`tests/quality/test_appworld_completion_control.py`)
separates the actor/guidance boundary from a recording/verifier wiring fault from a task-state
failure, and it passes — so the faithful failure is attributable to the actor, not to the
instrument. Because the gate as written admits **only** success, no amount of correctly executed
work that fails could ever close it; carrying it would keep the dataset permanently short of
closure at no scientific benefit.

**Retained:** every measured-zero, staged-null and incomplete exit-125 receipt with its raw/staged
identity; the distinction between a raw official TGC loss and the staged `score=null` with
`measured/unmeasured/invalid = 0/1/0` for `policy_enforcement_not_observed`; the completed
fabricated-control set; the parent correction and the historical W1/W2 scope; the source-level
relative `MARKER = pathlib.Path(".appworld_task")` reset hazard, whose occurrence in the retained
runs remains **UNKNOWN**; the requirement that current guidance name discovered in-world completion
before the harness report (`data/datasets/appworld.py:239-243`); the source-only disclosures in
`registry/w-evidence-qualification.md` and `registry/w-evidence-disclosure.md`.
**No selected-task tuning, no rerun of the selected task and no step/token budget increase is
authorized**, and any future reopening requires fresh staged-policy evidence and separate
authorization. No run, expansion or production authorization follows, and
`production_authorized` remains `false`.

**Limits a reader must carry forward.** Only **one** task was ever attempted, once per diagnostic,
and it is the same task each time. No successful measured actor task exists. Nothing here is
evidence about appworld's difficulty, about whether this harness fails appworld generally, or about
the dataset's headroom — and the reported figure must not be described as an appworld score. A
usable appworld measurement requires a **fresh, separately authorized multi-task run** under the
staged pilot policy.

## What this decision does not do

- It does not claim any withdrawn requirement was solved. Each is recorded with its
  supporting evidence and retained limits.
- It does not touch the four permanent refusals (`api_bank`, `nestful`, `sotuqa`,
  `tau2_bench`), whose `retain-contract-refusal` entries stay exactly as they are.
- The operator's further decision on 2026-10-15 withdraws the three runtime requirements
  above, superseding the earlier decision to leave this trio untouched. Their limits and
  historical receipts remain; `appworld` and `bfcl_v3` stay real remaining work unchanged.
- **Superseded in full on 2026-10-16.** The sentence above no longer holds: **`bfcl_v3 /
  simple-ast-contract` and `triviaqa / hub-export-scorer-linkage` were withdrawn** as operator
  **scope** decisions, and **`appworld / successful-task-evidence` was withdrawn** because the
  protocol question it existed to answer was answered by measurement. The next bullet's "stays
  open" list therefore no longer includes `triviaqa`, and **no non-refusal blocker remains in the
  registry at all**. The two scope withdrawals are not findings that the missing evidence does not
  exist; the scope note above states that, and `appworld`'s entry states its own different reason
  and its one-task limit.
- It does not withdraw anything whose evidence merely has not been collected yet. The
  `journal-coverage` family and `hotpotqa` stay open because what they need is an
  operator-supplied selected manifest, which is an input rather than an impossibility.
- Every withdrawn blocker's text, evidence list and historical record remain readable in the
  git history of `registry/dataset-blockers.json`; the removals are the diff.

## Assertion migration - w-wd

The former `/site/task-206-twowiki-provenance-review.md` locator membership checks
(`38 passed in 0.24s` and `Hub/data provenance remains blocked`) no longer apply to
the active registry: their only carriers were the withdrawn TwoWiki blockers.
The tests instead pin each withdrawn ID, its former family and its failed search
in this record. The old locators and evidence lists remain in git history; no
historical run, partial measurement or null effect is reclassified.

The combined registry validation also exposes stale B6 and B3 pins for these
withdrawn carriers. `/site/b6-dataset-blocker-review.md` locator membership for
`## 2Wiki: ujson-native-offline-closure`, `## 2Wiki: hub-data-provenance` and
`## LiveCodeBench: row-attestation` no longer applies to active blockers. Likewise,
`/site/measurement-coverage-hg-b3-review.md` locators
`## Bird MiniDev: label/semantics review closed; metadata reconciliation open` and
`## DS1000: host-pool interpretation closed; archive acceptance open` belonged only
to the removed BIRD/DS1000 blockers. Their historical text remains available; tests
pin the corresponding absence and failed searches here. HotpotQA's live gate and
its citation, all archive-integrity tests and all frozen historical checks stay intact.
