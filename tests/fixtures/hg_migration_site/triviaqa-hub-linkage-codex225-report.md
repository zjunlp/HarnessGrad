# Task 225 — TriviaQA Hub export/scorer metadata review

**Compatibility for the exact Hub revision is not established.** The external
`hub-export-scorer-linkage` blocker remains open. A historical conversion source
is now acquired, pinned and licensed; no immutable evidence associates it with
Hub revision `0f7faf33a3908546c6fd5b73a660e0f8ff173c2f`.

Work stayed in isolated `/tmp/hg-codex-225`, detached base
`61e7f82245c76e7c833740216940d1fcaf3b76be`. Inspection covered public source,
config/schema/split/checksum manifests, Git/API metadata and existing scorer
code only. No parquet, archive, questions, answers, documents, task/sample rows
or heldout payloads were downloaded or opened. No loader/scorer/model/H0,
GPU endpoint or main-branch action was performed. No benchmark score or
diagnostic receipt is produced.

## What was acquired

GitHub path history for `huggingface/datasets` identifies
`413205c1a7247a2ff81cf709117ce93624fa6fd3` as the latest historical change to
`datasets/trivia_qa/trivia_qa.py` before removal in
`8803c182e6d03e3cb43f62da2572548f55e5b14d`. The immutable historical loader,
manifest and original Apache-2.0 license are retained unchanged in
`tests/fixtures/triviaqa_hub_codex225/historical_loader/`.

| File | SHA-256 |
| --- | --- |
| `trivia_qa.py` (13,358 bytes) | `653d4b0179d708c23d20aebf40253bc64c2b30d70627247d2a4e6d63c63213d2` |
| `dataset_infos.json` (41,283 bytes; metadata only) | `220e5edddaa870a2840d363bd0d69a08997a4c6e107795ac727e5fa935533194` |
| `LICENSE` (11,358 bytes) | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| Acquisition receipt | `36996acbab13dca74686c9ffc152df4dd0f418c875b3dfaabeeb29493dc2aef3` |

Immutable source URL:
<https://github.com/huggingface/datasets/blob/413205c1a7247a2ff81cf709117ce93624fa6fd3/datasets/trivia_qa/trivia_qa.py>.

The receipt pins successful API responses and decoded blobs, and records four
exact-revision Hub failures. Nonrecursive tree metadata plus reconstructed
signed commit bytes connect all three files to the historical commit. The
offline checker verifies SHA-256, Git blob/commit/tree identities, tree
membership and license linkage. This establishes byte consistency, without
independent PGP verification or an attestation of the Hub conversion process.
Unfetched tree entries are references only. No README examples or dummy-data
payloads were acquired.

## Supported comparison with original scorer

Reference scorer: `ca43b5820b107f3970cf4b7d67f7db7a98117b79`,
`evaluation/triviaqa_evaluation.py`, SHA-256
`51785a3bc2e02a8f1ebf9db3aa18834c6a5daa5089ee5d435c99e0babf4be682`.
Its unchanged source and helper files were inspected; the scorer was not run.

| Aspect | Historical loader code/metadata | Implication for compatibility |
| --- | --- | --- |
| Normalized aliases | Lines 234–246 copy `NormalizedAliases` to `normalized_aliases`, trimming each string; no renormalization or alias truncation | The field mapping and boundary trimming preserve this alias component under the pinned scorer's whitespace normalization, conditional on this being the actual converter |
| Human answers | Answer schema and mapping omit `HumanAnswers` | Original scorer adds normalized `HumanAnswers` to accepted aliases. Full preservation requires evidence of absence/redundancy or a different actual converter; no actual-row absence or score difference was measured |
| Config/split | `rc.nocontext`: both Web and Wikipedia, no context, builder 1.2.0; validation maps to both `dev.json` filenames | Historical declaration only; no export revision association or original Domain/VerifiedEval selection is established |
| Context and flags | Both document lists cleared; Domain/VerifiedEval/question/document verified flags omitted | Original scorer uses those fields for selection and Web question/document keys; the emitted schema alone cannot restore the original evaluation domain |
| Keys and duplicates | Generator emits basename-plus-question-ID keys, without explicit cross-source deduplication | Original helper uses question dictionaries for Wikipedia and question/document dictionaries for Web. Writer-level duplicate handling and exact export incidence remain unverified |
| Aggregate metric | Original scorer computes EM/F1 over its requested key domain with missing predictions in the denominator | No row-average/F1/full-domain equivalence is established by alias field mapping |

The historical manifest declares 17,944 validation examples. A matching adapter
count is corroboration of a plausible candidate, not evidence of immutable
conversion lineage. The manifest also contains four config names absent from
the loader's eight-config list. Matching schema/version/count cannot certify
which code produced the exact Hub export.

The production adapter's claim that this config has no human answers is not
verified by this review: omission from a conversion schema does not establish
absence from the original scorer's input. No production code or metric identity
was rewritten on the strength of this historical candidate.

## Failed access versus unresolved evidence

Four retained requests target **only** exact-revision public metadata/code:
Hub API revision metadata, nonrecursive root tree, commit history, and raw
`trivia_qa.py`. Each failed with `URLError`, `[Errno 101] Network is unreachable`,
with no HTTP response/body. This establishes an acquisition blocker in this
workspace. It does not establish 404, absence of the loader, the export's tree
contents, or incompatibility of the actual export.

Earlier exploratory requests observed the same Hub connectivity failure and
a connection reset for a historical raw GitHub URL. The durable acquisition
pass succeeded through GitHub's content/Git APIs and is what the retained hash
and lineage checks cover. No alternate loader is asserted to be the actual
Hub converter.

## Exact remaining request

Provide public **code and metadata only**, without rows:

1. Exact Hub commit/tree/config/split/file-identity metadata for
   `mandarjoshi/trivia_qa@0f7faf33a3908546c6fd5b73a660e0f8ff173c2f`,
   especially `rc.nocontext` and validation. The attempted immutable URLs are
   recorded in `tests/fixtures/triviaqa_hub_codex225/acquisition.json`.
2. Loader/converter source actually used, immutable source commit, original
   license, SHA-256/Git blob hashes, and conversion/build metadata binding that
   code and its input revision to this export. If the export revision has no
   loader, identify the predecessor loader and immutable conversion receipt.
3. Code/metadata establishing normalized-alias and human-answer preservation
   (or supported absence/redundancy), source membership, Domain/VerifiedEval
   selection, duplicate handling, original scorer-key mapping, denominator and
   aggregation. Explicitly identify equivalences the export cannot support.

The fuller code-line trace, license details and request are in the fixture
README. The active blocker remains `external`. Preserve
`triviaqa-alias-max-em-upstream-normalization-all-aliases-v2`, prior receipts,
shared admission gates and `production_authorized=false`.

## Validation

The offline historical-source checker passes, including reconstructed Git
commit/tree/blob membership and original Apache-2.0 license. With network calls
forbidden, local changed-source and missing-source controls both fail as
required; restored source passes. The original-scorer integrity checker verifies
5,710 source bytes without running the scorer.

The source-only registry checker passes: 28 datasets, eight shared blockers,
11 historical records, `production_authorized=false`. Existing focused registry
tests pass **52/52**, with no skips. Exact commands, outputs and failure controls
are retained in `triviaqa-hub-linkage-codex225-validation.txt`.
`git diff --check` passes. No provisioned dataset suite or scorer execution was
used. Temporary validation/renderer files were removed. The optional comparison
page is `triviaqa-hub-linkage-codex225.html`.
