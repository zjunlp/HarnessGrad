# Physics Questions source-only journal review

Review scope: repository source at base `9066072`; no adapter import, dataset
payload access, preparation, scorer/control execution, model or network use.
“Established” below means inspected source behavior, not a measured runtime.

## Metric identity and upstream boundary

- The claimed metric is `exact_str_match over BIG-bench's accepted spellings`,
  implemented locally by `exact_str_match()` and `score()`. Prediction whitespace
  is stripped; the reference is stripped and then has trailing periods removed.
  Matching is case/unit sensitive, accepts any parsed target spelling and returns
  a binary reward; there is no numeric tolerance
  (`data/datasets/physicsquestions.py:73–114`). In particular, the prompt's claim
  about “a trailing period” must not be read as permitting a period on a prediction:
  only the **reference** gets `rstrip(".")`
  (`data/datasets/physicsquestions.py:93–100,155–161`).
- The upstream citation is
  [`google/BIG-bench`, `bigbench/benchmark_tasks/physics_questions/task.json`](https://github.com/google/BIG-bench/blob/main/bigbench/benchmark_tasks/physics_questions/task.json),
  as recorded by the adapter (`data/datasets/physicsquestions.py:21–43,214–217`).
  It claims that task lists `exact_str_match` and `bleu`; only the former is
  reported. The repository implementation is an asserted local transcription of
  the rule/examples, **not** a call to BIG-bench's installed evaluator or an
  independently pinned original metric source (`data/datasets/physicsquestions.py:93–114`).
- The existing focused tests describe and check the same parsing/string rule,
  including rejection of numeric tolerance; their fixture imports the adapter and
  depends on provisioned records (`tests/quality/test_bigbench_official_scorers.py:75–80,152–180`).
  They are source evidence of intended checks, not independent upstream custody
  or a passing result from this review. The common verifier additionally unwraps
  outer quotes on legacy text, so end-to-end text normalization is broader than
  the bare scorer (`data/scaffolds/qa/verify/check.py:129–134,233–242`).

## Task and split provenance

- Tasks are declared to come from
  [`billxbf/ReWOO@9cd0283043ff4be0c9d614fda2789d143ca6ffd1`](https://github.com/billxbf/ReWOO/tree/9cd0283043ff4be0c9d614fda2789d143ca6ffd1),
  member `data/bigbench/physics_question.csv`. The adapter pins that codeload
  tarball and SHA-256
  `7fe653d18faad40bf10ddcc7d85415782af975d80792bb26401393203b9ce136`
  (`data/datasets/physicsquestions.py:61–65,172–188`). These are declarations,
  not authenticated local acquisition receipts.
- `_rows()` reads only the extracted CSV under the declared external root,
  filters empty `input` values and requires exactly 53 remaining rows. Questions
  use `input`; gold parses the target's quoted list, taking its first spelling as
  the reference answer while retaining all spellings
  (`data/datasets/physicsquestions.py:120–137,191–198`). The comments' assertion
  that upstream had 54 examples with one blank is inherited source commentary,
  not a payload fact verified here (`data/datasets/physicsquestions.py:126–129`).
- The loaded pool is named `test`, with no `study` split. File-order IDs are
  `physicsquestions/test/<index>`; the platform applies a hash-third division.
  `OFFICIAL_EVAL=None` requests evaluation over the loaded pool and does not
  authenticate the adapter's “whole benchmark” coverage claim
  (`data/datasets/physicsquestions.py:191–218,226–238`;
  `data/scaffolds/qa/base.py:160–181,235–258,290–312`;
  `data/scaffolds/registry.py:218–248`).
- No CSV-to-BIG-bench release/target-list linkage, upstream metric commit,
  extracted-file digest, real 53-row count, eligibility/selection manifest,
  leakage/privacy assessment or score comparability has been established. The
  loader's checks are existence, parsing and count, not source authentication
  (`data/datasets/physicsquestions.py:120–137`); independent hashes/coverage and
  comparability are required for measurement (`AGENTS.md:75–81`). The adapter's
  small-sample/ranking caveat remains an inherited rationale, not a new statistical
  result (`data/datasets/physicsquestions.py:16–19,203–208`).

## Runtime requirements: source verified versus declared

- **Source verified contract:** inherited `DEFAULT_ENV` and per-task `ENV` use
  `{"kind":"files"}`; no dataset image or services are specified
  (`data/datasets/physicsquestions.py:220–238`;
  `data/scaffolds/qa/base.py:438–456`). The registry forbids files environments
  from specifying exec-only image fields and accepts only omitted/`none` network
  (`data/scaffolds/registry.py:352–361`). No installed host/image is verified.
- **Dependencies:** CSV loading/scoring uses standard-library `csv`/`re` and
  local QA code (`data/datasets/physicsquestions.py:47–50,73–137`). The download
  manifest nevertheless declares `pyarrow==23.0.0` while explicitly saying this
  CSV check does not use it; no installation or removal is warranted by this
  review (`data/datasets/physicsquestions.py:209–212`). Fetching the pinned
  archive and any declared package is provisioning, not a scorer network need
  (`data/datasets/physicsquestions.py:172–188,199–213`).
- **Source verified verifier wiring:** a `command` verifier runs `python3
  qa/lib/verify/check.py`, with inline gold, adapter/scaffold code and answer/
  prediction/reward/evidence paths (`data/scaffolds/qa/base.py:339–385,478–482`).
  The runner rematerializes inputs; missing gold, import/scorer errors and invalid
  predictions take unmeasured paths
  (`eval/verification/verifier.py:486–494`;
  `data/scaffolds/qa/verify/check.py:157–173,185–201,204–247`).
- **Not verified offline:** the host verifier's sandbox call omits `network`
  and also has an unsandboxed alternative
  (`eval/verification/verifier.py:549–573`). The sandbox defaults to `None` and
  creates a private network namespace only for explicit `none`
  (`eval/containers/sandbox.py:180,194–199,403–419`). Source declarations do not
  attest actual no-network enforcement, pristine Python/runtime identity,
  transitive dependency closure, quotas, artifact custody or cleanup.

## Gate disposition

**Closed at source-review level:** `dataset-journal-gap` documentation coverage
(`journal-coverage`): claimed string metric/call site, declared archive/task/split
provenance and runtime contract are now documented. The same `measurement`
record is retained for next-gate evidence; status classifies work, not readiness
(`docs/dataset-blockers.md:10–21`).

**Next gate:** `source-provenance` and `official-scorer-identity`: authenticate
the ReWOO archive/member-to-BIG-bench task/release linkage and independently pin
the original `exact_str_match` evaluator before asserting metric equivalence
(`data/datasets/physicsquestions.py:93–114,172–188,214–217`).

**Still open, each by name:**

| Gate | Evidence this review does not supply |
| --- | --- |
| `source-provenance`, `selected-only-privacy`, `metric-comparability` | Authentic selected bytes/target sets, independent upstream metric correspondence, private gold custody and full-pool versus hash-side coverage; see sections above. |
| `runtime-observations` | Effective backend enforcement (`docs/dataset-blockers.json:20–31`). |
| `paired-filesystem-quotas` | Hard byte/inode enforcement (`docs/dataset-blockers.json:34–55`). |
| `scorer-offline-closure` | Integrated transitive dependency/network closure (`docs/dataset-blockers.json:58–69`). |
| `pristine-verifier` | Independent fresh trusted scoring-runtime identity (`docs/dataset-blockers.json:72–83`). |
| `artifact-only-state-export` | Allowlisted data-only transfer (`docs/dataset-blockers.json:86–97`). |
| `official-scorer-identity` | Authenticated upstream evaluator and fresh execution identity (`docs/dataset-blockers.json:100–111`). |
| `snapshot-owned-cleanup` | Applicable lifecycle/owned-absence evidence; files kind is not a discharge (`docs/dataset-blockers.json:12,114–125`). |
| `pilot-guidance-conflict` | Current planning reconciliation (`docs/dataset-blockers.json:128–139`). |
| `measurement-coverage` (oracle, do-nothing, diagnostic/pilot receipts) | No control/measurement ran; all dataset gates precede the one-task diagnostic (`AGENTS.md:59–65,75–81`). |

No production/H0 authorization follows (`docs/dataset-blockers.json:5–6`).
