# Task 225 — historical TriviaQA loader, not a Hub-export attestation

**Exact Hub revision linkage remains unverified.** This directory contains only
public historical conversion code, its original license, a config/schema/split
manifest and Git/API metadata. No question, answer, document, sample/task row,
parquet, archive or heldout payload was fetched or opened. The loader was never
imported or executed. No scorer/model/H0/endpoint was invoked.

`acquisition.json` retains dated successful requests and the four failed
exact-revision Hub requests. All four failed with `URLError`, `[Errno 101]
`Network is unreachable`; no HTTP status or response body was obtained. This is
an access failure, not evidence that the revision/file is missing.

## Historical code pin and license

Repository: <https://github.com/huggingface/datasets>.
Commit: `413205c1a7247a2ff81cf709117ce93624fa6fd3` (2021-10-01).

Public path history lists this as the latest change to
`datasets/trivia_qa/trivia_qa.py` before its 2022 removal from GitHub. That
establishes a historical source candidate, not its use by a later Hub export.

| Original path | SHA-256 | Git blob SHA-1 |
| --- | --- | --- |
| `datasets/trivia_qa/trivia_qa.py` | `653d4b0179d708c23d20aebf40253bc64c2b30d70627247d2a4e6d63c63213d2` | `5e1c9aab6b9e71f7d1251089ae34590794c7ae58` |
| `datasets/trivia_qa/dataset_infos.json` | `220e5edddaa870a2840d363bd0d69a08997a4c6e107795ac727e5fa935533194` | `02fa50221855cf0619e5f6a6dfcdcf977f484be0` |
| `LICENSE` | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` | `d645695673349e3947e8e5ae42332d0ac3164cd7` |

Files retain their acquired bytes. The loader's Apache-2.0 header credits the
TensorFlow Datasets Authors and HuggingFace Datasets Authors. The original
repository license is retained; the pinned root tree has no NOTICE. This license
pin covers the historical code; no license file at the exact Hub revision was
acquired. Original scorer licensing remains separately pinned in
`../triviaqa_upstream/`.

API content responses retain original base64 blobs. Nonrecursive Git tree
responses connect loader/manifest/license to the reconstructed immutable commit.
`tools/check_triviaqa_hub_provenance_codex225.py` independently checks those
identities offline. Signed commit reconstruction establishes byte consistency;
it does not independently verify PGP or establish Hub conversion custody.
Tree entries referencing other files/directories are metadata only: none of
their payloads, including the `dummy` subtree, were traversed or acquired.

## Static conversion findings and limits

Line references below refer to unmodified `historical_loader/trivia_qa.py`.

- **Config/split (77–95, 101–151, 199–226):** `rc.nocontext` selects both Web
  and Wikipedia, `unfiltered=False`, `exclude_context=True`, builder version
  1.2.0. Validation maps to both source `dev.json` filenames. Download URLs in
  source/manifest are declarations only and were not followed.
- **Answers (234–257):** the loader copies `Aliases` and `NormalizedAliases`
  to snake-case arrays and strips each string. It does not recompute normalized
  aliases. It copies/strips canonical fields. When `Answer` is absent, both
  alias arrays are empty and several fields use `<unk>`.
- **Human answers (181–190, 237–257):** neither the answer schema nor the
  conversion includes `HumanAnswers`. This proves field omission by this
  historical loader, not absence of human answers from original data or from
  the exact Hub export. The original scorer's union includes normalized human
  answers; complete accepted-answer preservation needs evidence that none are
  lost or that all are redundant for the specified evaluated source.
- **Context/selection (259–261, 301–329):** no-context empties both document
  lists. Emitted rows retain question ID and question source, but omit top-level
  `Domain`, `VerifiedEval` and question/document verified flags. This generator
  does not implement the original evaluator's verified filtering.
- **Keys/duplicates (314–329):** the generator yields source-basename plus
  question-ID keys and emits one example per parsed record. It does not
  explicitly deduplicate/group across source files. Framework writer behavior,
  actual duplicate incidence and the eventual export are unverified. This is
  different from upstream Wikipedia question dictionaries and Web
  question/document dictionaries; equal row counts would not prove equivalence.

The historical manifest declares 17,944 validation examples for `rc.nocontext`,
matching the adapter's recorded count. It also includes config entries absent
from this loader's eight-config list. Neither matching counts, schema nor version
establishes immutable Hub-export lineage; this metadata is not a current run or
an independently measured count.

Original scorer commit `ca43b5820b107f3970cf4b7d67f7db7a98117b79`,
`evaluation/triviaqa_evaluation.py:76–77`, selects `NormalizedAliases` plus
normalized optional `HumanAnswers`. Its normalizer collapses boundary whitespace,
so trimming normalized alias strings preserves this component's EM semantics.
That conditional component observation does not certify F1, full accepted-answer
sets, grouping, selection or denominator equivalence for the requested export.

## Exact remaining code/metadata request

Supply the following public evidence with **no rows or dataset payloads**:

1. Commit/tree metadata for `mandarjoshi/trivia_qa` revision
   `0f7faf33a3908546c6fd5b73a660e0f8ff173c2f` and its `rc.nocontext` config,
   validation split declarations and file identities. Safe entry points are
   the exact-revision Hub API revision/tree URLs retained in `acquisition.json`.
2. The loader/converter bytes actually used, immutable commit and SHA-256/Git
   blob identities, original license and conversion/build metadata connecting
   those bytes to this exact export. If it used the historical loader above,
   supply the immutable association rather than relying on name/count matches.
   If no loader is present at the export revision, supply the pinned predecessor
   loader and export-conversion commit/receipt that identifies its input revision.
3. A code/metadata account of `NormalizedAliases` and `HumanAnswers` preservation,
   source Domain/VerifiedEval selection, both source-file membership and
   duplicate question/document handling. Specify the mapping to scorer keys,
   missing-prediction denominator and aggregation. State unsupported equivalence
   explicitly where the export intentionally discards those fields.

Until then, keep `hub-export-scorer-linkage` external. Preserve the v2 adapter
metric identity and historical receipts. This source-only evidence authorizes
no diagnostic or production execution.
