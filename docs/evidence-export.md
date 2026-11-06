# Auditable experiment evidence

Export a concern, its baseline and follow-up measurements, the attempts behind them,
the result-selection rule, and recorded costs in one portable JSON file. Concerns and
selection rules are supplied by the author; the platform does not decide which experiment
answers a research question or review.

The exporter is offline and read-only. It never launches a model, imports a dataset,
reruns a scorer, repairs a score, or changes a run. The Console editor is not part of this
command.

## Export and verify

Create an author manifest, then run from the checkout:

```bash
python3 tools/evidence_export.py --manifest concerns.json --runs-root /path/to/study-runs --out evidence.json
python3 tools/evidence_export.py --verify evidence.json
```

The output must be outside the run root, and its parent directory must already exist.
Publication is atomic and refuses an existing file. The file is readable only by its owner
until they choose to share it. `--max-receipt-bytes` sets the per-file input limit (default
32 MiB); it also controls the input limit for `--verify`. Use a larger explicit limit for
large exports. There are no additional runtime dependencies.

Use a dedicated, quiescent study directory. Every immediate directory or non-regular entry
is inventoried, including recording-only, malformed and unreadable attempts. Regular files
at the root are not runs; nested campaign directories are not recursively searched. Source
files and the entry list are checked again before returning the pack. This detects observed
changes but does not provide a transactional snapshot of a running experiment.

This is an example manifest, with run IDs and rounds to replace with your own. Omit a
result's `value` to read it from the receipt. If supplied, it must match exactly.

```json
{
  "schema": "harnessgrad.evidence.v1",
  "submission": {"run_id": "followup", "round": 3},
  "requests": [
    {
      "id": "R1.2",
      "concern": "Compare the selected candidate with the frozen baseline.",
      "runs": ["baseline", "followup"],
      "result": {"run_id": "followup", "round": 3, "metric": "score"},
      "vs_baseline": {"baseline_run": "baseline", "baseline_round": 0}
    },
    {
      "id": "R2.1",
      "concern": "Transfer to a second environment.",
      "status": "unmeasured",
      "runs": [],
      "reason": "This experiment has not been run."
    }
  ],
  "attempts": [
    {"run_id": "baseline", "purpose": "frozen reference"},
    {"run_id": "followup", "purpose": "follow-up experiment"}
  ],
  "selection": {
    "reported": {"run_id": "followup", "round": 3},
    "rule": "The final round specified before the experiment.",
    "rejected": []
  },
  "budget": {"rounds": 3, "trials": 1, "source": "author's experiment protocol"}
}
```

`submission` and `selection.reported` must agree; the reported run cannot also be rejected.
Every baseline must occur in its request's `runs` and needs an explicit round;
there is no default to the latest or best result. Only the recorded `score` metric is
supported. An unmeasured request needs a reason and cannot carry a result.

The input is an author draft: `created_utc`, observed statuses and round lists are filled
by the exporter. The resulting `manifest` satisfies `harnessgrad.evidence.check_manifest`.
The draft itself need not satisfy that completed-manifest contract.

## What the pack establishes

The outer schema is `harnessgrad.evidence-export.v1`. It contains:

| Field | Contents |
| --- | --- |
| `manifest` | Author concerns, verified result references, complete local attempt inventory, selection and declared limits |
| `runs` | Filtered metadata and curve projections, source-byte hashes, patch descriptors, missing-record codes and observed costs |
| `comparisons` | Receipt-derived configuration changes, before/after harness hashes, score differences and comparability limits |
| `variation_groups` | Explicit between-run comparisons, when the records support them |
| `discovery` | Inventoried run IDs, unexplained attempts and the exact scope of discovery |
| `content_sha256` | Checksum of the canonical export content, excluding this field |

Referenced scores must exist in valid metadata/curve records at the named rounds. The
exporter checks run identity, measurement fields, finite task scores, scored counts and
platform measurement flags. Duplicate JSON keys or rounds, unreadable receipts, missing
metadata, startup failure and platform-tamper flags cannot substantiate a reported result.
Legacy non-finite confidence bounds are accepted on input and omitted from the export;
non-finite scores are rejected.

The aggregate score is checked against the receipt, not recomputed from task rewards.
Historical receipts do not pin an aggregation contract and its denominator; assuming the
current arithmetic-mean policy for every old run would silently change the verification
claim. This limitation is recorded in `verification.aggregate_score_validation`.

A partially scored point remains a measurement of its recorded tasks. Its missing/invalid
coverage stays visible; it is not converted to a full-set score or imputed with zero.
Incomplete task coverage prevents a comparable full-set comparison or variation estimate.

A successful export means **the score claims match the supplied receipts**. It does not
prove that the receipts are authentic, that the original scorer was correct, or that no
attempts exist elsewhere. Source SHA-256 hashes identify the exact original bytes and are
separate from hashes of the filtered projections. `--verify` checks the exported checksum,
projection checksums, result/attempt references, baseline deltas/configuration diffs and
the arithmetic of available variation estimates; it does not independently authenticate the
original experiment. A party able to rewrite a pack can also recompute its hashes.

## Attempts, failed work and selection

Observed valid rounds populate the ledger even when the author did not list the run.
A directory without a usable measurement remains `unmeasured` or `invalid`, with a reason.
A directory disappearing from the draft cannot hide it from discovery.

Recorded completion states `failed` and `stopped` remain distinct; an `incomplete` completion
with failed rounds is recorded as failed. A run with valid points but no final completion
receipt can have status `measured` and `completion_status: null`: measured does not mean
finished. A recording-only directory is not guessed to have failed or been stopped.

Authors can explicitly annotate `failed`, `stopped`, `invalid` or `unmeasured` attempts with
a reason. These exclusions are preserved and marked `status_source: author`; they cannot be
silently promoted into reported results. Reached measurement rounds remain listed as
historical observations. All four non-measured attempt statuses are excluded from result
and baseline references.

Selection rules and rejection reasons remain author statements, not automatically verified
claims about their conduct. Attempts not mentioned in the concerns, submission or rejection
list appear in `discovery.unexplained_attempts`. The pack does not claim completeness beyond
the selected root or prove an author did not choose that root after seeing results.

## Baseline changes and privacy

Comparisons are built from the actual receipts, not an author's supplied `diff`. They expose
allowlisted configuration changes with before/after values and presence indicators, candidate
harness hashes, changed or missing comparison conditions, and a descriptive score delta.
A delta is not a significance or causal claim. Missing scorer identity, different tasks,
models, runtime settings or platform versions prevent a comparable label. Metadata spanning
a platform-version seam cannot identify every round's measurement platform and is reported
as unknown for this comparison.
Per-task evaluation protocols and observed scorer isolation must also agree and cover the
task set. An explicit empty protocol map means no additional protocol; an absent map is
unknown. Existing writers may omit empty maps, so those receipts conservatively report
comparability as unavailable rather than silently filling in their provenance.

The default bundle contains no raw task IDs/text, answers, trajectories, stdout/stderr,
workspace paths, endpoint URLs, arbitrary metadata or source trees. Task scores/trials are
exported in sorted source-task order without the task names; a task-set digest binds the
ordering. Equal task-set digests allow comparing those anonymous positions across runs.
Hashes are identifiers, not a guarantee against guessing known public inputs.

A patch descriptor records `diffs/round-N.patch`, its original hash and byte count. Patch
text is withheld because code can contain benchmark answers or credentials. Missing and
unreadable patches are explicitly distinguished from withheld patches. The original patch
remains available to its custodian; the bundle alone cannot reconstruct or review source
edits. Review source material separately before sharing it.

Author text is intended for publication. Common credential patterns and URLs are filtered,
but automatic filtering cannot identify every private sentence or secret. Review concerns,
purposes and reasons before sharing. Unrecognized receipt fields are excluded rather than
copied through a general recursive redactor. Symlinks, hardlinks and non-regular receipt
files are not followed.
Allowed model/dataset identifiers must be short identifiers; numeric and boolean fields
must have their corresponding types. An arbitrary secret disguised as an otherwise valid
identifier cannot be recognized automatically, so review these metadata fields as well.

## Budgets and variation

`manifest.budget` is the author's declared limit and source. Each run separately reports
`config.run` limits and costs recorded on every curve point, with coverage per cost field.
Missing cost is `null`, never zero. Sums cover recorded curve points only; work that failed
before a point, provider charges and missing usage may add spend. Method totals are withheld
when multi-candidate submissions or missing submission identity make deduplication ambiguous.
Reused measurements with nonzero copied costs are not summed as new spending. Raw per-round
cost projections remain available for inspection.

Variation uses sample variance (`ddof=1`) and distinguishes two units:

* **Within a run:** complete, equal-width `per_task_trials` with at least two recorded trials
  permit descriptive variance of arithmetic mean task reward at each trial index, plus
  per-task sample variances. The trial means must agree with the recorded per-task scores.
  This is explicitly `within_run_trial_index_means`, not independent optimization-run
  variance or necessarily the benchmark's aggregate metric. Trial indices do not establish
  statistical independence.
* **Between runs:** authors can add `variation_groups`, shown below. At least two distinct
  run IDs must measure the same candidate tree with complete, matching task sets, models,
  scorer identity, environment, platform and recorded execution settings. Reused/shared H0
  and cached observations are excluded. The result is descriptive variation of the same
  Harness across recorded runs, not variance of an optimization method that produced
  different trees. Different run IDs alone do not prove independence or exclude copied data.

```json
{
  "variation_groups": [
    {
      "id": "repeat-candidate",
      "states": [
        {"run_id": "repeat-a", "round": 0},
        {"run_id": "repeat-b", "round": 0}
      ]
    }
  ]
}
```

This field is optional and belongs in the author manifest. Single observations, missing or
ragged trial arrays, reused data, incompatible conditions and incomplete coverage produce
`status: unavailable`, `sample_variance: null` and a reason. The exporter never substitutes
`score_std` or a task-level confidence interval for missing repeated observations.

## Offline validation

The synthetic tests exercise exports, source verification, malformed and missing receipts,
partial coverage, discovery of failed/stopped attempts, configuration diffs, trial statistics,
private-data exclusion, file boundaries, mutation detection and CLI publication:

```bash
python3 -m pytest tests/quality/test_evidence_manifest.py tests/quality/test_evidence_export.py tests/quality/test_evidence_export_privacy.py tests/quality/test_evidence_selection.py tests/quality/test_evidence_stats_conditions.py -q --basetemp=/tmp/harnessgrad-evidence-tests
```

These tests do not validate historical benchmark outcomes or authorize new measurements.
