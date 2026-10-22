# E1: FEVER, Physics Questions and Bird evidence review

Reviewed in `/tmp/hg-e1` against base `13fa17c` (2026-10-15 local date).
The first two dataset names were absent from the task message; this review uses
FEVER and Physics Questions, the first two journal entries among the seven
described review/acquisition candidates (six journal follow-ups plus Bird).
Bird is identified by the task's SQL coverage description. No other registry
entry, adapter, runtime, scorer or historical record is changed.

**Disposition: partial source/acquisition progress; zero complete blocker
closures.** All three records remain `measurement`. Physics content linkage,
upstream evaluator custody and FEVER Hub revision/tree metadata are now checked
in. FEVER export/group preservation
and Bird historical trial reconciliation still lack the required evidence.
Source custody is distinct from installed/runtime custody and measured coverage.

## Exact next actions read before work

The initial records, including history, are preserved in
`tests/fixtures/hg_e1_review/starting-gates.json:1`.

### FEVER / dataset-journal-gap

> NEXT gate: source-provenance and metric-comparability. Independently authenticate the pinned Hub export/conversion and original FEVER evidence-group preservation, with an exact upstream scorer release; document metric identity and establish offline/pristine runtime evidence before any diagnostic. The official-scorer-identity proof obligation is withdrawn; retain the remaining shared gates. This source review authorizes no execution.

Source: `tests/fixtures/hg_e1_review/starting-gates.json:101`.

### Physics Questions / dataset-journal-gap

> NEXT gate: source-provenance and metric identity. Authenticate the ReWOO archive/member-to-BIG-bench task/release and accepted-target linkage, and independently pin the original exact_str_match evaluator; then establish scorer-offline-closure and pristine verifier evidence. The official-scorer-identity proof obligation is withdrawn; retain the remaining shared gates and unmeasured coverage. No diagnostic is authorized.

Source: `tests/fixtures/hg_e1_review/starting-gates.json:136`.

### Bird MiniDev / sql-coverage-reconciliation

> Keep this measurement gate open until original selected-ID/phase/trial/error metadata binds the 90 phases to actual measurements, invalid/unmeasured counts, SQLite/schema/scorer identity and the score denominator. Preserve v4 incomplete and v5 partial-measurement/effect=null, including the historical complete label; do not infer 90 or 84 measured trials or compare full-coverage effects.

Source: `tests/fixtures/hg_e1_review/starting-gates.json:27`.

## FEVER: evaluator acquired; export and group preservation unresolved

**Established.** The acquired GitHub commit response binds scorer source to
`sheffieldnlp/fever-scorer@4801615100fbf6327f8e99b5dbaefe5dd890e869`
(`tests/fixtures/hg_e1_review/fever-scorer-head.body:1`). The immutable contents
response and decoded scorer are retained; decoded SHA-256 is
`fa12eaab2c88208f990948fc5d533ad4f91a6b8f48640f29317d1f00ab410ac9`
(`tests/fixtures/hg_e1_review/fever-scorer-source-api.receipt.json:40`;
`tests/fixtures/hg_e1_review/SHA256SUMS:56`).

The upstream rule uppercases labels, requires **one entire gold evidence
group**, and exempts correctly labelled NOT ENOUGH INFO
(`tests/fixtures/hg_e1_review/fever-upstream-scorer.py:22`, `:26`, `:37`, `:44`).
Its public `fever_score` defaults to the first five predicted evidence pairs
(`tests/fixtures/hg_e1_review/fever-upstream-scorer.py:106`). This supports the
adapter's declared binary per-item strict-score identity and five-pair limit
(`data/datasets/fever.py:27`). It does not establish equivalence of gold groups.
The local flat-mirror branch converts each sentence into a sufficient singleton
(`data/scaffolds/qa/evidence_scorers.py:157`); a conjunction of two sentences
would become two alternatives under that conversion. The acquired upstream
requires the conjunction. This is a structural comparability concern, not an
observed count of corrupted mirror records.

**Release limit.** GitHub tags and releases both returned empty arrays
(`tests/fixtures/hg_e1_review/fever-scorer-tags.body:1`;
`tests/fixtures/hg_e1_review/fever-scorer-releases.body:1`). Acquired setup source
declares version `0.0.0`
(`tests/fixtures/hg_e1_review/fever-upstream-setup.py:13`). Thus an exact upstream
**source revision** is now pinned. No named release, installed distribution, or
historical scorer-to-run binding is invented from that version placeholder.

**Established Hub metadata; direct failures preserved.** Both the canonical Hub revision request and the `www`
host request failed with `Network is unreachable`
(`tests/fixtures/hg_e1_review/fever-hub-revision.receipt.json:7`;
`tests/fixtures/hg_e1_review/fever-hub-www-revision.receipt.json:7`). A prior review
documents an existing public egress proxy (`reports/b6-dataset-blocker-review.md:291`).
Using it, canonical HTTPS requests authenticated the exact declared Hub revision,
its five-file tree and README
(`tests/fixtures/hg_e1_review/fever-hub-proxy-revision.body:1`;
`tests/fixtures/hg_e1_review/fever-hub-proxy-tree.body:1`). The tree identifies
`valid.jsonl` as Git blob `ae61c5b6cb11eafcc7d6c33069c68dd5c848f91f`,
6,471,611 bytes. This is export identity **metadata**, not a downloaded export's
SHA-256 or installed-file authentication. The README explicitly describes
classification-only fact checking, gold evidence for SUPPORT/REFUTE and system
evidence for NEI (`tests/fixtures/hg_e1_review/fever-hub-proxy-readme.body:59`).
The exact tree has no conversion script; the guessed loader path returned 404,
preserved in `tests/fixtures/hg_e1_review/fever-hub-proxy-loader.receipt.json:7`.

Following the README's source repository, a separate commit/tree and two source
files are acquired at `aaa126ec4f3cf7163f69b82a0a0976d2579a04a6`
(`tests/fixtures/hg_e1_review/fever-conversion-head.body:1`;
`tests/fixtures/hg_e1_review/fever-conversion-tree.body:1`). Its converter emits a
separate flat list of sentence triples **for each original evidence group**,
drops NEI, and can omit missing sentences
(`tests/fixtures/hg_e1_review/fever-create-nli-dataset.py:54`, `:56`, `:62`, `:70`).
Its reader joins those sentences together for classification
(`tests/fixtures/hg_e1_review/fever-conversion-datareader.py:101`). This source
supports the concern that a flat row can require a conjunction, rather than
independently sufficient singletons. It does **not** establish which converter
revision produced the Hub export or account for its NEI rows and row-ID mapping.

**Still open and why.** No authenticated export-to-converter/original-row-ID map
or evidence-group-preservation check is acquired. The adapter declares the
revision and `valid.jsonl`, but its reader only checks file presence and reads
JSONL (`data/datasets/fever.py:16`, `:46`, `:67`). The failed response's empty-body
SHA-256 is **not** a remote dataset checksum. A successful acquisition must bind
that exact Hub revision and export to original FEVER IDs and complete evidence
groups; installed bytes and a selected manifest still need custody evidence.

Offline/pristine runtime evidence is also absent: source wiring only describes
a files/command verifier with rematerialized gold and adapter inputs
(`data/scaffolds/qa/base.py:339`, `:350`, `:380`, `:444`). No runtime was launched.
The withdrawn official-scorer-identity proof gate is not reinstated
(`docs/enforcement-proof-decision.md:10`, `:41`).

**Status left:** `measurement`, original blocker ID/family retained. The scorer
acquisition substep is complete; source provenance, metric comparability and the
four currently referenced shared gates remain open. No diagnostic is authorized.

## Physics Questions: content linkage established; metric differs; runtime open

**Established archive/member custody.** The declared ReWOO commit resolves via
GitHub's commit API (`tests/fixtures/hg_e1_review/rewoo-commit.body:1`). Its
downloaded archive is **6,225,904 bytes**, SHA-256
`7fe653d18faad40bf10ddcc7d85415782af975d80792bb26401393203b9ce136`, exactly matching
the adapter declaration (`data/datasets/physicsquestions.py:172`;
`tests/fixtures/hg_e1_review/rewoo-archive.receipt.json:29`). The retained archive
contains the declared `data/bigbench/physics_question.csv`; its extracted bytes
have SHA-256 `7747cf185111aaa58493b5d804ba7b496cb152a6ae89a69a66a0c443adfd34aa`
(`tests/fixtures/hg_e1_review/rewoo-physics.csv:1`;
`tests/fixtures/hg_e1_review/verify.py:82`).

Independently resolved BIG-bench revision
`092b196c1f8f14a54bbc62f24759d43bde46dd3b` supplies both the task and evaluator
(`tests/fixtures/hg_e1_review/bigbench-head.body:1`). Its task JSON SHA-256 is
`ea55d3ee0d688b7f6f023f9d607313d8b7d3a4bd9151451141c239cd10d82eb5`.
The offline verifier checks **all 53 CSV input/accepted-target-list pairs in
order** against the 53 nonblank examples of that pinned task. The sole extra
BIG-bench example has blank input and target
(`tests/fixtures/hg_e1_review/bigbench-physics-task.json:17`;
`tests/fixtures/hg_e1_review/verify.py:94`). This comparison preserves list order
and repeated inputs; no input-keyed deduplication masks the two target variants.
Content linkage closes at these exact source revisions. It does not identify
the original CSV converter or date of export, or authenticate installed data.

**Metric identity established, equivalence not established.** The task prefers
`exact_str_match`, also declares BLEU and an `output_regex`
(`tests/fixtures/hg_e1_review/bigbench-physics-task.json:11`). Acquired
`task_metrics.py` SHA-256 is
`0d210ed501882ee38742f689b5e4bbc363a7481ef39dc8ee9959bfbc39903e73`.
The authoritative checksum is in `tests/fixtures/hg_e1_review/SHA256SUMS:15`.
The evaluator counts `response in target` and divides by the number of targets,
without stripping whitespace or periods
(`tests/fixtures/hg_e1_review/bigbench-task-metrics.py:267`). The task runner
passes the task's regex to `model.generate_text`
(`tests/fixtures/hg_e1_review/bigbench-json-task.py:551`, `:634`). This review
does not assert model-specific regex behavior or execute upstream dependencies.

The local adapter instead strips prediction whitespace and reference whitespace
and trailing periods (`data/datasets/physicsquestions.py:93`); the generic text
verifier also unwraps quotes (`data/scaffolds/qa/verify/check.py:132`, `:233`).
Its task prose says a trailing period is allowed, although the adapter strips
periods only from references (`data/datasets/physicsquestions.py:155`). Therefore
document the actual metric as **local accepted-spelling EM with asymmetric
normalization**, distinct from a verified full BIG-bench evaluation protocol.
BLEU is not measured. No numerical or unit tolerance is added.

**Still open and why.** No installed dataset checksums, split/eligibility/selected
manifest or current trusted verifier receipt exists in this packet. Source
authentication cannot establish actual offline execution, interpreter/dependency
identity, or pristine verifier state. The declared files/command wiring stages
the scaffold and adapter (`data/scaffolds/qa/base.py:339`, `:350`, `:380`, `:444`);
it is not an execution receipt. Full upstream-score comparison also needs a
matched response-processing and aggregation protocol. The withdrawn official
execution proof gate remains withdrawn (`docs/enforcement-proof-decision.md:41`).

**Status left:** `measurement`, original ID/family retained. Archive checksum,
member/accepted-target content linkage and exact evaluator-source acquisition
are complete. Installed/selected custody, scorer-offline-closure, pristine
verifier and other currently referenced shared gates remain open. Coverage stays
unmeasured; no diagnostic is authorized.

## Bird: historical labels preserved; original measurement metadata missing

**Established history, with limits.** The journal retains v4 `incomplete`, v5's
historical `complete`, **34 selected train-side tasks x3**, `jobs=1`, reported
**28/34 measured tasks**, **90 verify phases**, and score `0.3214285714`
(`docs/adapter-integration-journal.md:305`). That is **102 planned task-trials**,
not 102 certified measurements. Six selected tasks lack a reported task-level
measurement; their cause is unknown. The immutable historical records still
classify v5 `partial-measurement`, `effect=null`, v4 `incomplete`, `effect=null`
and unknown v4 run ID null
(`tests/fixtures/hg_e1_review/starting-gates.json:30`). The historical archive
index is an external citation (`docs/adapter-integration-journal.md:313`), which
was not opened under the worktree-only scope.

The raw tracked-source search and command/commit binding are retained in
`tests/fixtures/hg_e1_review/bird-repository-matches.txt:1` and
`tests/fixtures/hg_e1_review/bird-search.json:1`. Matches are journal, registry,
readiness/review text and the history baseline, not original phase/trial/error
rows. This is a bounded repository inventory; it makes no claim about ignored
files or the contents of the external archive.

**SQL engine/schema classification.** Current source uses SQLite, gold-first
execution, and EX result-set equality; duplicate/order distinctions are ignored
(`data/datasets/bird_minidev/verify/check.py:39`, `:105`, `:108`, `:119`). The
introductory prediction-first prose is stale (`:8`); the executable order governs
the reviewed source contract. Missing database/empty gold and caught gold-query
errors, including schema errors and query deadline expiry, yield no reward and
`measured=false`, exit 3 (`:82`, `:99`, `:109`). With runnable gold, a missing
prediction or caught prediction-query failure yields measured EX zero (`:114`,
`:120`). This describes the current checker, not the missing historical error
allocation. Connection/open/read errors outside these catches cannot be assumed
to carry the same structured result. Preserve original stderr and phase status.

The 30-second query timer is a configured per-query limit (`:34`, `:55`), not an
observed timeout count or full-trial wall deadline. Soft-F1 and R-VES are separate
metrics (`:20`). Current adapter release, source/database declarations and hash
split (`data/datasets/bird_minidev.py:68`, `:74`, `:310`, `:336`) cannot establish
the historical SQLite binary/version, database/schema bytes, scorer hash or
selected manifest.

**Still open and exactly what is missing.** Obtain the original 34 selected IDs
with split/manifest binding; every phase ID mapped to task ID and trial ordinal;
raw verdict/evidence/reward/stderr and exit/timeout statuses; SQLite/runtime,
database/schema and scorer identities; and the original score aggregation
denominator. Separate retried/duplicate/non-verification phases from unique
trials. Then reconcile measured/invalid/unmeasured tasks and trials, error
counts and actual lifecycle outcomes. Ninety phases do not imply 90 measured
trials, 30 fully measured tasks, or 84 measured trials. Arithmetic similarity of
the reported score to a fraction is not denominator evidence. The six task gaps
cannot be converted to measured zeros or all attributed to schema failures.

**Status left:** `measurement`. Original blocker, both historical records and
four shared references are retained unchanged. Historical reconciliation and
full-coverage comparison remain open; no rerun, archive append or score change.

## Evidence verification and scope

Every attempted HTTP request has a raw `.body` and `.receipt.json` containing URL,
status/error, byte count and SHA-256. `SHA256SUMS` also covers decoded sources,
archive members and repository-search output. Failed requests retain empty bytes
and their explicit transport errors. Successful GitHub contents responses are
base64-decoded and checked against their Git blob IDs before comparison.

Run the offline evidence verifier and registry validator:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/hg_e1_review/verify.py
python3 tools/check_dataset_blockers.py
```

Their raw outputs are retained under `reports/e1-*.json`. No adapter import,
upstream-code execution, model/endpoint call, benchmark run or whole-suite test
was performed. Acquiring upstream code does not establish a pristine/offline
production scorer. All four current shared gates remain as found; withdrawn
proof requirements are not revived.

Validation results enumerate packet-file checksums, successful responses,
preserved transport/HTTP failures, decoded Git blobs, matching archive/member
bytes and all 53 ordered input/target-list pairs. Registry validation returns
`valid:true` for 28 datasets, four shared blockers and 11 historical records
(`reports/e1-evidence-verification.json:1`; `reports/e1-registry-validation.json:1`).
