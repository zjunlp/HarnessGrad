# Task 215 — current TriviaQA source and scorer identity review

Reviewed isolated worktree `/tmp/hg-codex-215` at main snapshot
`82459cfa6d16c323ca40f21db2daf597921aedb3`, including normalization correction
`fcbed17368e27d1f065ae334dee53f6b3973d91d` and all-alias/identity correction
`4fa8481db07027c737cf3156df72a93bfe672afb`. Only code, metadata and fabricated
inputs were read. No dataset payload/heldout, network, model, H0, GPU or pilot
was used, and no external receipt was opened.

Only confirmed repository-solvable mismatch: the active registry still named upstream evaluator acquisition as open.
Its evidence anchor also described the corrected identity as pending. Both
scoring corrections and the v2 identity were already present in main. The
review reproduced no further adapter EM mismatch within the tested scope;
there is no justification for changing production scoring or assigning v3.

## Source review and preserved identities

The five original source/helper/license/requirements pins, retained Git evidence,
commit reconstruction and tree/blob membership pass the existing offline tests.
The unchanged full evaluator and CLI execute on fabricated inputs with standard
library dependencies and acquired helpers. Repository-wide TensorFlow/NLTK
requirements were neither installed nor substituted for evaluator dependencies.
Retained signature metadata establishes byte consistency, not independent PGP
verification or new acquisition custody.

| Artifact | SHA-256 / identity |
| --- | --- |
| Upstream reference commit | `ca43b5820b107f3970cf4b7d67f7db7a98117b79` |
| Original evaluator | `51785a3bc2e02a8f1ebf9db3aa18834c6a5daa5089ee5d435c99e0babf4be682` |
| Original Apache-2.0 license | `5cc548335f22310a53877044624711ac3e20e5ed1117617c1fd3fc462c54330e` |
| Acquisition receipt | `e58f0619976171e862530835287c0f33173c5097cfe41c6de197dd0c50221041` |
| Reviewed adapter source | `1c18737824cbda3bb3e90bbdea3805f5b83c2ddc53f239de29c8a447335f6dcd` |
| Current adapter metric identity | `triviaqa-alias-max-em-upstream-normalization-all-aliases-v2` |

The adapter hash remains exactly the one recorded by task 205. Original source,
receipt, prior reports/results, historical runs and their identities are unchanged.
The upstream reference hash identifies original upstream code; it cannot stand
in for the actual adapter or harness hash in a future run receipt.

## Differential and evidence trace

The original three suites pass **38 tests**: source/license/Git linkage,
dependency closure, full original evaluator/CLI on fabricated data, normalization
including separator/Unicode cases, and normalized/human/mixed alias matches
beyond position 200. Hit/miss checks already verify that
`data.qa.verify.check` writes v2 and the pinned reference into `official` evidence.

Six new regressions exercise a separate route: `QASpec.verifier` produces its
actual input manifest for fabricated gold; those exact source files are copied
to a temporary workdir; a fresh `-I -S -B` interpreter runs the copied check and
adapter. Before adapter import, the copied scaffold's pool construction is
suppressed and parquet/network calls are forbidden. No replacement scorer or
cached private adapter is supplied to the child. The child checks its scaffold
origin, and the parent checks that the copied adapter bytes match the checkout.

Text and structured predictions cover both hit and miss, plus empty and absent
text answers. Structured predictions take precedence over a conflicting legacy
text answer. Each case checks the actual reward file, `measured=true`, reward,
`scorer_module`, `official.scorer_identity` and `official.reference` in the
written evidence. All six pass; missing/empty answers are measured zero for
these fabricated, nonempty aliases.

Source inspection also confirms that the host reward/evidence branch of
`eval.verifier._verify_inner` forwards the parsed evidence dictionary into
`verdict["evidence"]`. That forwarding path was inspected, not executed by the
new tests; no runtime isolation or production backend attestation is claimed.
Unmeasured failure paths are not certified as measured v2 results by this review.

## Focused repair and remaining gate

`docs/dataset-blockers.json` now retires the acquired-source blocker and records
the actual `external` gate as `hub-export-scorer-linkage`, with current review
evidence. The registry guide and a new journal entry explain the correction.
The taxonomy, eight shared TriviaQA blockers, production authorization flag,
historical baseline and historical run records are preserved.

Needed external evidence remains the original loader/conversion source and
public configuration/split provenance binding Hub revision
`0f7faf33a3908546c6fd5b73a660e0f8ff173c2f` to its loader/scorer, original
license, immutable commit/file hashes, Domain/VerifiedEval selection and
duplicate/question/document handling. No missing mapping is invented from
synthetic inputs. Full grouping, missing-prediction denominators, aggregation
comparability, actual scorer/harness hashes and shared runtime/admission gates
remain open. The adapter reports per-row EM only; upstream F1 tests do not turn
that metric into a full evaluator or leaderboard score.

## Validation

Baseline: **38 passed**, no skips, retained in
`triviaqa-review-codex215-baseline-test-result.txt`. New materialized cases:
**6 passed**, no skips.

Final combined command:

```sh
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/quality/test_triviaqa_official_scorer.py tests/quality/test_triviaqa_source_provenance_codex196.py tests/quality/test_triviaqa_scorer_review_codex205.py tests/quality/test_triviaqa_identity_codex215.py tests/quality/test_dataset_blockers.py --basetemp=/tmp/harnessgrad-pytest-triviaqa-codex215-final > triviaqa-review-codex215-test-result.txt 2>&1
env PYTHONDONTWRITEBYTECODE=1 python3 tools/check_triviaqa_provenance.py
env PYTHONDONTWRITEBYTECODE=1 python3 tools/check_dataset_blockers.py
git diff --check
```

Final results: **93 passed in 4.77s**, no failures/skips, retained in
`triviaqa-review-codex215-test-result.txt`. The code-only fixture integrity tool
exits 0 (5,710 source bytes). The registry checker exits 0 (28 datasets,
8 shared blockers, 11 historical records), with `production_authorized=false`.
`git diff --check` passes. The provisioned dataset
suite is deliberately excluded. No diagnostic or production admission follows.
