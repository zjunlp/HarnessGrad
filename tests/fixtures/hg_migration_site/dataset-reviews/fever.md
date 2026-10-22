# FEVER source-only journal review

Review scope: repository source at base `9066072`; no adapter import, dataset
payload access, preparation, scorer/control execution, model or network use.
“Established” below means inspected source behavior, not a measured runtime.

## Metric identity and upstream boundary

- The adapter claims `FEVER strict_score`, a per-item binary reward with
  `max_evidence=5`; `score_prediction()` calls the local
  `fever_is_strictly_correct()` transcription. Missing required evidence becomes
  an incorrect prediction; legacy `answer.txt` passes no evidence
  (`data/datasets/fever.py:27–43`).
- The upstream citation recorded by the transcription is
  [`sheffieldnlp/fever-scorer`, `src/fever/scorer.py`](https://github.com/sheffieldnlp/fever-scorer/blob/master/src/fever/scorer.py),
  specifically `is_correct_label` and `is_strictly_correct`
  (`data/scaffolds/qa/evidence_scorers.py:124–149`). This is an unpinned upstream
  reference, not acquired upstream-source custody or proof of official execution.
- The implemented rule uppercases labels without punctuation normalization,
  requires a whole gold evidence group within the first five predictions, and
  exempts correctly labelled NOT ENOUGH INFO items from evidence
  (`data/scaffolds/qa/evidence_scorers.py:128–170`). Crucially, the mirror-specific
  branch turns a flat evidence list into independently sufficient singleton
  groups (`data/scaffolds/qa/evidence_scorers.py:157–165`). Whether that preserves
  the original benchmark's sufficient-evidence groups is **not established** by
  the adapter. Strict-score comparability therefore remains open.
- The materialized verifier uses structured scoring only when a valid prediction,
  `EVIDENCE_AWARE` and `score_prediction` are present; otherwise it unwraps matching
  outer quotes and strips the text answer before `score()`
  (`data/scaffolds/qa/verify/check.py:129–134,233–242`). Malformed predictions and
  scorer exceptions are unmeasured, rather than ordinary zero rewards
  (`data/scaffolds/qa/verify/check.py:185–201,243–247`).

## Task and split provenance

- The declared source is
  [`copenlu/fever_gold_evidence`](https://huggingface.co/datasets/copenlu/fever_gold_evidence/tree/a6b8d891d393e97a4efac791afffb2d7de5e57c6)
  at `a6b8d891d393e97a4efac791afffb2d7de5e57c6`. Only external-root `valid.jsonl`
  is loaded; questions use `claim`, gold uses `label` and `evidence`, and the pool
  is named `validation` (`data/datasets/fever.py:16–20,46–58,67–93`). Neither test
  nor train is loaded merely because their constants exist there.
- The download declaration selects only `valid.jsonl` and explicitly leaves its
  checksum null; it also retains a stale “adapter refuses to load” note
  (`data/datasets/fever.py:78–89`). The current `load()` delegates to QASpec and
  structured evidence is supported (`data/datasets/fever.py:23,27–38,108–109`);
  that note cannot establish a current refusal or a passing gate.
- With no `study` split supplied, QASpec assigns file-order IDs
  `fever/validation/<index>` and a hash-based platform study/eval division;
  `OFFICIAL_EVAL=None` means the whole loaded pool is evaluation material when
  requesting the official side, **not** authenticated full official-test coverage
  (`data/datasets/fever.py:53–94`;
  `data/scaffolds/qa/base.py:160–181,235–258,290–312`;
  `data/scaffolds/registry.py:218–248`).
- Loading checks existence and parses JSONL, not revision/checksum authenticity
  (`data/datasets/fever.py:46–50`; `data/scaffolds/qa/read.py:149–160`). This review
  establishes no local bytes, row count, eligibility/selection manifest, mirror
  conversion lineage, evidence-group preservation, heldout-test gold access or
  leakage/privacy assurance. The source explicitly limits its claim to validation
  (`data/datasets/fever.py:3–7,93`); measurement requires manifest/hash/coverage
  evidence (`AGENTS.md:75–81`).

## Runtime requirements: source verified versus declared

- **Source verified contract:** inherited `DEFAULT_ENV` and per-task `ENV` are
  `{"kind":"files"}`; there is no dataset image or service declaration
  (`data/datasets/fever.py:96–101`; `data/scaffolds/qa/base.py:438–456`). The registry
  forbids exec-only fields such as images for files environments and accepts only
  omitted/`none` network policy (`data/scaffolds/registry.py:352–361`). No container
  image identity or installed host interpreter is verified by that contract.
- **Source verified verifier wiring:** a `command` verifier runs `python3
  qa/lib/verify/check.py` with answer/prediction/gold paths and reward/evidence
  files; inline gold and the adapter/scaffold sources, including evidence scoring
  and prediction validation, are staged as verifier inputs
  (`data/scaffolds/qa/base.py:339–385,478–482`). Inputs are rematerialized before
  execution (`eval/verification/verifier.py:486–494`). The FEVER scoring path and
  JSONL reader use local/standard-library code
  (`data/datasets/fever.py:11–12,27–38`;
  `data/scaffolds/qa/evidence_scorers.py:10–17`;
  `data/scaffolds/qa/read.py:149–160`), but transitive installed-runtime closure
  has not been exercised here.
- **Network distinction:** fetching is declared as Hugging Face provisioning
  (`data/datasets/fever.py:67–89`); scoring has no dataset-declared network need.
  An omitted network field does not prove offline verification: the host sandbox
  call omits `network`, and a separate unsandboxed branch exists
  (`eval/verification/verifier.py:549–573`). The sandbox defaults it to `None`
  and adds `--unshare-net` only for explicit `none`
  (`eval/containers/sandbox.py:180,194–199,403–419`). Fresh isolation, no-network
  enforcement, interpreter/source identity, quota and cleanup are unverified.

## Gate disposition

**Closed at source-review level:** `dataset-journal-gap` documentation coverage
(`journal-coverage`), including the claimed metric/call site, declared task/split
source and runtime contract documented above. The registry retains the same
`measurement` record to carry the unresolved evidence; its status is a work
classification, not a pass (`docs/dataset-blockers.md:10–21`).

**Next gate:** `source-provenance` and `metric-comparability`: independently bind
the pinned Hub export/conversion to original FEVER evidence groups and the exact
upstream scorer release before claiming a comparable strict score (the unresolved
conversion is visible at `data/scaffolds/qa/evidence_scorers.py:157–165`).

**Still open, each by name:**

| Gate | Evidence this review does not supply |
| --- | --- |
| `source-provenance`, `selected-only-privacy`, `metric-comparability` | Authenticated bytes/selection, private verifier input custody and original evidence-group linkage; see provenance and metric sections above. |
| `runtime-observations` | Effective backend enforcement (`docs/dataset-blockers.json:20–31`). |
| `paired-filesystem-quotas` | Hard byte/inode enforcement (`docs/dataset-blockers.json:34–55`). |
| `scorer-offline-closure` | Integrated transitive dependency/network closure (`docs/dataset-blockers.json:58–69`). |
| `pristine-verifier` | Independent fresh trusted scoring-runtime identity (`docs/dataset-blockers.json:72–83`). |
| `artifact-only-state-export` | Allowlisted data-only transfer into verification (`docs/dataset-blockers.json:86–97`). |
| `official-scorer-identity` | Acquired/pinned upstream linkage and fresh official evaluator execution (`docs/dataset-blockers.json:100–111`). |
| `snapshot-owned-cleanup` | Applicable lifecycle/owned-absence proof; files kind alone does not discharge a shared requirement (`docs/dataset-blockers.json:12,114–125`). |
| `pilot-guidance-conflict` | Current planning reconciliation (`docs/dataset-blockers.json:128–139`). |
| `measurement-coverage` (oracle, do-nothing, diagnostic/pilot receipts) | No control or measurement ran in this review; all dataset gates must precede the one-task diagnostic (`AGENTS.md:59–65,75–81`). |

No production/H0 authorization follows; the registry explicitly disallows it
(`docs/dataset-blockers.json:5–6`).
