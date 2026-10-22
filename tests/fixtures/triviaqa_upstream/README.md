# TriviaQA original evaluator fixture — Task 196

Status: **source acquired and pinned; current adapter EM components pass synthetic
differentials; full export/evaluator equivalence remains unverified**. Only public
code, license, dependency declarations, and GitHub/Git metadata were acquired.
No TriviaQA questions, answers, documents, dataset archives, parquet, or heldout
payloads were fetched or opened. No model or H0 run was performed.

## Original source and immutable identity

The original author repository is
<https://github.com/mandarjoshi90/triviaqa>. Its description identifies the
TriviaQA reading-comprehension dataset and its homepage points to the University
of Washington TriviaQA project. The previously recorded
`https://github.com/mandarjoshi/TriviaQA` returns HTTP 404; it was an incorrect
source address, not evidence that the original evaluator is unavailable.

Pinned commit: `ca43b5820b107f3970cf4b7d67f7db7a98117b79` (2023-11-13,
“Add license note.”).

Original evaluator:
<https://github.com/mandarjoshi90/triviaqa/blob/ca43b5820b107f3970cf4b7d67f7db7a98117b79/evaluation/triviaqa_evaluation.py>

Source SHA-256:
`51785a3bc2e02a8f1ebf9db3aa18834c6a5daa5089ee5d435c99e0babf4be682`.
Source Git blob: `b67ffcda2fc1af0616a427dc3280aa4189aef7db`.

The file identifies itself as the official TriviaQA v1.0 evaluator, extended
from SQuAD v1.1. GitHub's path history reports its last change at
`51b53595131828a00a1fed09cca9c87f512090e2` (2017-05-05, “refactor”);
that history response is corroborating metadata, not the fixture's commit pin.
This is the original published evaluator. It does not prove that an unrelated
later benchmark, export, or adapter used identical scoring semantics.

## Acquisition, license, and dependencies

`provenance.json` records URLs, UTC acquisition time, reviewer scope, SHA-256 and
Git blob SHA-1 for every acquired source/license/requirements file. Acquisition
method `github_git_objects` means GitHub Git API responses and base64-decoded
original blobs, not a checkout or an archive. Files retain their original bytes;
no formatting, Python conversion, function reconstruction, or implementation
substitution was applied.

`evidence/commit.git` reconstructs the signed commit's exact raw bytes from the
GitHub signature and payload. Its Git commit SHA-1 equals the pin. The root,
evaluation, and utils tree bytes likewise reproduce their Git identities and
connect that commit to all five acquired files. The offline source provenance
test validates this chain independently of the receipt's self-declared file
hashes. Full GitHub commit/repository/history responses are retained with
SHA-256 pins. GitHub reports a valid commit signature; this work does not
independently verify PGP or establish a separate human reviewer.

The original `LICENSE` is **Apache-2.0**, with its existing Kenton Lee copyright
notice preserved. The pinned commit's README change explicitly states: “The
Apache 2.0 License applies to both the code and the data.” This license-only
patch is retained in `evidence/commit_response.json`; the README and dataset
were not fetched. The root Git tree contains no NOTICE file.

The evaluator imports only standard-library `__future__`, `collections.Counter`,
`string`, `re`, `sys`, `argparse`, and the acquired `utils.dataset_utils` and
`utils.utils` helpers. The helpers add standard-library `json`. No third-party
package is required for this evaluator closure. The original repository-wide
`requirements.txt` declares `tensorflow>=0.11`, `nltk`, `tqdm`, and `jinja2`;
these are preserved as declarations, not installed or asserted necessary for
scoring. The source-only test executes the unchanged files with Python 3 using
namespace packages. Tree metadata identifies both original package initializers
as empty Git blobs; they were not downloaded or fabricated.

## Observed evaluator behavior

- **EM and F1:** normalize prediction and each accepted alias, take each metric's
  maximum independently over `NormalizedAliases` plus normalized optional
  `HumanAnswers`, then average over the requested ID list and multiply by 100.
  F1 uses token multiplicities via `Counter`. No common tokens gives F1 zero,
  including two empty strings. An empty alias list raises `ValueError` in `max`;
  an empty evaluation domain produces division by zero.
- **Normalization:** lowercase; replace underscores with spaces; replace ASCII
  punctuation and additional quote characters with spaces; remove English
  articles; collapse whitespace and strip. This differs from deleting punctuation.
- **Grouping:** Wikipedia uses `QuestionId`; every other domain takes the
  question/document path. That path uses the literal string
  `QuestionId--Filename` for every EntityPages/SearchResults document. Repeated
  keys collapse by dictionary assignment, with later values replacing earlier
  ones; there is no invented row-average or question-level max aggregation.
- **Verified subset:** the file reader filters questions on
  `QuestionPartOfVerifiedEval` when top-level `VerifiedEval` is true. For Web it
  also retains only `DocPartOfVerifiedEval` documents and asserts at least one
  remains. Wikipedia keeps question filtering without document filtering.
- **Denominator:** defaults to the constructed gold dictionary's keys. Missing
  predictions remain in the denominator and contribute zero. Extra prediction
  keys are not traversed, but count in `pred_len`. The returned receipt includes
  `common`, `denominator`, `pred_len`, and `gold_len`. An explicit `qid_list`
  changes the denominator to its length, including missing IDs or repeats.
- **No-context/validation:** the original CLI accepts only a dataset file and a
  prediction file. It has no Hub config, split, validation, or no-context branch.
  The supplied file's Domain and VerifiedEval fields drive selection. Removing
  all documents still permits Wikipedia question keys; it produces no Web keys.
  Therefore a Hub `rc.nocontext` row mean cannot be identified with the original
  Web evaluator merely by sharing EM components. Version mismatches print a
  warning and do not themselves stop scoring.

These rules are observations of pinned source and fabricated source-only tests.
They are not observations of released TriviaQA payloads. The original scorer
has no standalone mapping from Hub `rc.nocontext`/`validation` rows to its domain
or question/document keys. The adapter's historical validation counts and test
blinding claims were not remeasured here; validation is not a test leaderboard
score.

## Adapter correction and remaining gates

At acquisition in task 196, the unchanged adapter differential **failed** on
`A-Team`: upstream normalized it to `team`; the adapter normalized it to `ateam`.
Commit `fcbed17368e27d1f065ae334dee53f6b3973d91d` subsequently corrected punctuation,
underscore, and quote normalization. Task 205 reviewed that correction and removed
a separate 200-alias scoring cap: a fabricated match against alias 201 scored zero
locally and one upstream. All aliases now reach the scoring maximum.

New verifier evidence identifies the adapter as
`triviaqa-alias-max-em-upstream-normalization-all-aliases-v2`, with the pinned
upstream repository, commit, source path and SHA-256 in `official.reference`.
That reference hash identifies upstream source, not the deployed adapter bytes;
new run receipts must also pin the actual adapter/scorer and harness bytes.
The adapter still computes per-row EM only, and retains local string coercion,
Hub key support, float rewards and empty-reduction behavior. Full Hub export,
grouping and denominator equivalence remains open. Historical receipts/results
and the task-196 acquisition receipt are preserved with their original scope.
Lost/corrupt fixtures fail rather than skip.

`test_triviaqa_source_provenance_codex196.py` verifies the source identity chain,
license linkage, dependency closure, and unchanged full evaluator/CLI on
fabricated inputs, including F1, EM, missing/extra predictions, grouping,
duplicate document keys, verified filtering, and no-document behavior. It
imports no production adapter. A separate interpreter disables site/environment
imports and bytecode writes and rejects network connections. The existing
adapter differential suite suppresses pool construction and forbids payload
reads before importing its private adapter instance.

The task-205 suite adds separator/Unicode differential cases, matches beyond
alias 200 (normalized aliases, human answers and their combination), and actual
shared-verifier evidence checks on synthetic hit/miss inputs. See
[`reports/triviaqa-scorer-review-codex205-report.md`](../../../reports/triviaqa-scorer-review-codex205-report.md).

Run the current review from the repository root. `--basetemp` must be **OUTSIDE**
the platform because driver-based tests use `tmp_path` as a harness work root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/quality/test_triviaqa_source_provenance_codex196.py tests/quality/test_triviaqa_official_scorer.py tests/quality/test_triviaqa_scorer_review_codex205.py --basetemp=/tmp/harnessgrad-pytest-triviaqa-codex205-review
PYTHONDONTWRITEBYTECODE=1 python tools/check_triviaqa_provenance.py
```

The source-only checks, integrity tool and current adapter component differentials
pass. The acquisition-time combined failure remains recorded in the task-196
report. No H0/model admission follows from any of these checks.

## Exact remaining acquisition request for Hub mapping

Task 225 subsequently acquired and pinned a **historical candidate** loader,
manifest and original license from HuggingFace's GitHub repository; see
[`../triviaqa_hub_codex225/README.md`](../triviaqa_hub_codex225/README.md).
Its immutable association with the exact Hub export remains unverified. That
review records answer-field omission and selection/grouping limits without
changing scorer metrics or the historical acquisition attempts below.

If full Hub export equivalence is required, supply **code and metadata only**:

1. The original loader/conversion source that produced
   `mandarjoshi/trivia_qa` revision
   `0f7faf33a3908546c6fd5b73a660e0f8ff173c2f`, including the `rc.nocontext` builder
   and validation split declaration, at an immutable source commit with source
   URL, original license, and file SHA-256. Identify the source revision actually
   used by the export rather than assuming a similarly named current loader.
2. Public config/split/conversion provenance metadata binding that export
   revision to the loader, identifying Domain/VerifiedEval selection and
   duplicate question/document handling, with no question, answer, document,
   sample-row, or heldout bytes.

The exact candidate code URL attempted was
`https://huggingface.co/datasets/mandarjoshi/trivia_qa/raw/0f7faf33a3908546c6fd5b73a660e0f8ff173c2f/trivia_qa.py`;
network access failed, so file existence and relevance at that pin are unverified.
An attempted GitHub historical loader lookup hit the anonymous API rate limit
before source or commit metadata was acquired. Neither attempt grants authority
to reconstruct a loader or infer export semantics. This remaining request does
not block the original evaluator fixture's acquisition.
