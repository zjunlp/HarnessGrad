# TriviaQA Hub export/scorer linkage review - w-trivia

The blocker remains external: authoritative Hub identities are corroborated, but immutable conversion execution provenance is missing.

Reviewed in registered worktree `/tmp/hg-trivia`, branch `wg-trivia`, base
`6b41bd6`. The operator's withdrawal of signature gates in
`docs/independent-review-deferred.md:3` applies. No human/custodian approval or
separate reviewer sign-off is required by this finding.

This builds on tasks 196/205/215/225 and B5. Their acquired source, synthetic
scorer comparisons, historical failures and mirror evidence remain unchanged.
The new work supplies successful **authoritative-origin transport**, source-byte
corroboration, and an explicit boundary on what the public export record proves.
It does not repeat historical source acquisition as if it had been missing.

## Corroboration

Below, `F` means `tests/fixtures/hg_trivia/`. JSON `views` are derived line-numbered
views of the corresponding exact raw `responses/*.body`. The offline checker
verifies their equality. Each raw JSON body is compact and located at line 1;
its receipt records the actual fetched URL and recomputed SHA-256. All requests
use verified HTTPS through the canonical proxy. There were 13 HTTP 200 responses,
no redirected or failed requests. Ten new origin responses match the corresponding
B5 mirror bodies byte for byte, including the complete revision/card metadata.

| Claim | Result and retained file+line |
| --- | --- |
| Exact origin revision | `0f7faf33a3908546c6fd5b73a660e0f8ff173c2f`; `F/views/hub-revision.json:5`; raw `F/responses/hub-revision.body:1`. Mirror equality includes all eight config/schema/split declarations. |
| Export root | Eight config directories, `.gitattributes`, `README.md`; no loader, CI directory, runtime lock or build receipt; `F/views/hub-root-tree.json:1` and raw `F/responses/hub-root-tree.body:1`. |
| Complete tree coverage | Recursive API returned 150 unique entries (8 directories, 142 files), no pagination Link. Every config subtree reconstructs to its origin-declared OID. All files are the two root metadata files and Parquet paths. Raw `F/responses/hub-recursive-tree.body:1`, headers `F/responses/hub-recursive-tree.headers:1`, view `F/views/hub-recursive-tree.json:1`. Root reconstructed from declarations: `b5e163e9705f1c0cd80445f994848fac96dba89e`; this is a derived tree hash, not a reconstructed/fetched commit object. |
| Selected tree | `rc.nocontext` OID `699cfa660faac5431585c72d42c7c5ecac3468a8`; `F/views/hub-root-tree.json:4`. Recomputed from its three pointer blob IDs; selected raw response `F/responses/hub-selected-tree.body:1`. |
| Config and splits | `rc.nocontext` maps train/validation/test to their respective `rc.nocontext/<split>-*` patterns; `F/views/hub-revision.json:1496`. Declared counts are 138,384 / 17,944 / 17,210; `F/views/hub-revision.json:290`. These are metadata counts, not measured row coverage. |
| Validation file declaration | `rc.nocontext/validation-00000-of-00001.parquet`; size 7,335,321; LFS SHA-256 `48a5005c0eb4f8a4ae5b9868644297fd5bf1e694aeb3dc9c8ab958cac0d5b201`; pointer blob `fbe4369287ccf5e4fbd8d148d817b0c1ca7bf71e`; `F/views/hub-selected-tree.json:26`. Canonical pointer reconstruction matches the declared blob. No payload bytes were fetched or rehashed. |
| Export and PR ancestry | Origin commit title is “Convert dataset to Parquet (#8)”; `F/views/hub-commits.json:3`. PR #8 names predecessor `d63a76f01c8226449f76b437dd512cf8b17adf5d` as first conversion commit's parent (`F/views/hub-discussion-8.json:138`), records the rc.nocontext addition (`:171`), loader deletion (`:460`), and exact merge revision (`:542`). Raw bodies `F/responses/hub-commits.body:1`, `F/responses/hub-discussion-8.body:1`. |
| Added versus final selected files | Origin directory at addition `279d93a6e999c93dc992e1a705389e350ba7f706` is byte-identical to the final selected-tree response; `F/views/hub-added-selected-tree.json:1`; raw `F/responses/hub-added-selected-tree.body:1`. This is file lineage, not execution attestation. |
| Predecessor source | Origin-acquired `trivia_qa.py` SHA-256 `b80c2307a37cfb261b66b7c73157de5495ae18add3f91f5f7d62f86620edc137`, Git blob `a9a6932c23d378b9e5628ecec1f0509496806d77`; tree binding `F/views/hub-predecessor-tree.json:22`, source `F/responses/hub-predecessor-loader.body:1`. Matches B5; differs from task 225's `653d4b0179d708c23d20aebf40253bc64c2b30d70627247d2a4e6d63c63213d2` historical source. Neither is asserted to be the executed converter. |
| Potential input declaration | Predecessor declares `data/triviaqa-rc.zip`, LFS SHA-256 `f5c28a667a3cace893b0dafef8293891dd26b03144a53fc045d6db90aa0ea734`; `F/views/hub-input-tree.json:2`; raw `F/responses/hub-input-tree.body:1`. Source references relative ZIP inputs at `F/responses/hub-predecessor-loader.body:43`. Not proof of the input actually consumed. |

## Immutable build/export provenance: still missing

The inspected authoritative public record supplies repository ancestry and
output declarations, **not an execution record**. The complete recursive tree
has no build manifest, job log, lockfile or attestation. PR #8's only comment is
“Convert dataset to Parquet.” (`F/views/hub-discussion-8.json:66`); its remaining
events describe commits and merge status. The merge message and first conversion
commit message (`F/views/hub-commits.json:5`,
`F/views/hub-initial-commits.json:5`) identify changes, without a job URL,
command, converter source revision, source/license digest, runtime/writer
versions or measured input digests. These responses provide no execution receipt
to follow. The predecessor imports unpinned `datasets` (`F/responses/hub-predecessor-loader.body:24`).
Its `datasets.Version("1.2.0")` at line 129 is a **builder version label**,
not the installed library, Python, PyArrow or Parquet writer version.

Consequently, actual loader/converter execution identity and its binding to
this exact export are **not obtainable from the inspected public record**.
This is a bounded negative finding, not a claim that no receipt exists anywhere.
Recovering it requires an original build/job artifact or other immutable
execution evidence binding the export to executed code, source/license hashes,
runtime/writer versions, and consumed input revision/hashes. A predecessor blob,
PR author, matching schema/count or similarly named conversion tool cannot
supply those missing facts. No converter revision has been inferred.

The predecessor's Apache-2.0 header is at source line 4. Task 225's original
Apache text recomputes to `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`
(`tests/fixtures/triviaqa_hub_codex225/historical_loader/LICENSE:1`). There is
no standalone license in the export/predecessor root listings. The origin card
labels the dataset license `unknown` (`F/views/hub-revision.json:46`). Those
source/license identities cannot certify the license of an unidentified
executed converter or replace dataset licensing evidence.

## Scorer source and compatibility

The local scorer is implemented in `data/datasets/triviaqa.py:100`, with reference
repository/commit/source/digest at line 92 and preserved metric identity
`triviaqa-alias-max-em-upstream-normalization-all-aliases-v2` at line 91.
`_SPEC` binds `score=score` at line 346. `score()` uses the complete accepted
list at lines 162–163; `_gold()` constructs it through `get_ground_truths` at
line 260, with no former 200-alias truncation. The current adapter recomputes to
`c4c9e7121672f482abd5ec5527daa07d893f27bfbdcb7255841782332597413c`.

The official reference is `mandarjoshi90/triviaqa`, commit
`ca43b5820b107f3970cf4b7d67f7db7a98117b79`,
`evaluation/triviaqa_evaluation.py`. A fresh immutable raw GitHub request is
preserved at `F/responses/upstream-scorer.body:1`. Its 5,710 bytes exactly match
the pre-existing official fixture; recomputed SHA-256
`51785a3bc2e02a8f1ebf9db3aa18834c6a5daa5089ee5d435c99e0babf4be682`,
Git blob `b67ffcda2fc1af0616a427dc3280aa4189aef7db`. The fresh license response
also matches the fixture: SHA-256
`5cc548335f22310a53877044624711ac3e20e5ed1117617c1fd3fc462c54330e`,
Git blob `290cc8618d5da6d0027365c00e9374ae0c6120ff`, Apache-2.0
(`F/responses/upstream-license.body:1`). Existing task-196 Git commit/tree/blob
custody remains the linkage to the pinned original commit; new work verifies
origin-byte equality rather than inventing a new official implementation.

The adapter matches official normalization and alias-max EM components within
the existing fabricated-input differential tests. It is a local implementation,
with Hub spellings/string coercion/float reward/empty-max behavior, not the full
official evaluator. The source review supports these limits:

| Aspect | Grounded observation | Still unresolved |
| --- | --- | --- |
| Aliases and human answers | Predecessor copies/strips normalized aliases at `F/responses/hub-predecessor-loader.body:234`; emitted answer mapping at line 239 and origin schema `F/views/hub-revision.json:257` omit human answers. Official scorer adds `HumanAnswers` at `F/responses/upstream-scorer.body:76`. | Original HumanAnswers absence/redundancy and preservation by the actual converter. Adapter prose at `data/datasets/triviaqa.py:132` claiming “no human answers at all” is not established by schema omission. |
| Source and split membership | Candidate config combines Web/Wikipedia (`F/responses/hub-predecessor-loader.body:132`, `:145`); validation uses both dev filenames (`:84`, `:91`); split construction at `:199`. | Actual export's source membership and verified selection. Config filenames and metadata counts cannot establish row membership. |
| Domain and verified flags | Candidate return fields at `F/responses/hub-predecessor-loader.body:305` omit Domain/VerifiedEval flags; no-context clears documents at `:259`. Original filtering uses VerifiedEval/question/document flags at `tests/fixtures/triviaqa_upstream/utils/dataset_utils.py:26`, `:37`. | Whether the actual exporter selected the original evaluation domain; flags/documents cannot be reconstructed from the declared no-context schema alone. |
| Duplicates and keys | Candidate generator emits filename-plus-question-ID keys at `F/responses/hub-predecessor-loader.body:314` without explicit cross-source deduplication. Original helper uses Wikipedia question IDs and Web `qid--filename`, collapsing dictionary keys (`tests/fixtures/triviaqa_upstream/utils/dataset_utils.py:7`, `:14`, `:17`). | Writer/framework duplicate behavior and actual export incidence; mapping exported rows to official scorer keys. |
| Denominator and aggregation | Official `evaluate_triviaqa` retains missing predictions in requested-key denominator and emits 0–100 EM plus F1 (`F/responses/upstream-scorer.body:102`, `:129`). Local `score()` is per-row alias-max EM, 0/1 (`data/datasets/triviaqa.py:160`). | Full answer-set, grouping, missing-prediction denominator and aggregation equivalence. Component agreement cannot establish a leaderboard-equivalent score. |

## Decision and validation

Do not close `triviaqa / hub-export-scorer-linkage`.
The unmet part is immutable execution provenance plus the export-to-official
answer-set/selection/grouping/key/denominator linkage described above. Origin
corroboration and scorer-source custody have progressed; human sign-off is not
an outstanding acquisition requirement. The registry update preserves `external`,
`source-provenance`, v2, historical identities, shared gates and
`production_authorized=false`.

`reports/w-trivia-validation.txt` records exact acquisition/verification/test
commands and their output. Verification includes SHA-256 manifest checks,
response receipt and ten mirror comparisons, eight reconstructed subtrees,
canonical LFS pointer consistency, original scorer/source custody checks, focused
existing fabricated-input scorer tests and the registry checker. No acquired
loader was executed and no benchmark task, dataset row, archive/Parquet body,
model, GPU, endpoint, runs directory or frozen receipt was accessed.

The legacy `check_triviaqa_provenance.py` success message still mentions
“independent human review”; that historic tool wording is not re-imposed as a
gate and does not override the operator's documented withdrawal.

Bundle manifest SHA-256: `b75fd5a9a9c2e2fd24b038d7416a07abb8f06f5e46c911efae30ca386d89d9df`.

Final results: **98 passed in 4.58s**; all integrity/provenance/registry checks passed.
Registry: 28 datasets, 2 shared blockers, 11 historical records, production
authorization false. Structural review confirms only this blocker's observed,
next_action and evidence changed, with an append-only journal and owned additions.
Authored-file whitespace checks pass. The raw-inclusive diff check reports only
retained response-header trailing spaces/final blank lines; original evidence
bytes are deliberately preserved.
