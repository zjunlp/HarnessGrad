# Results final-review follow-up

Scoped changes only: `tools/export_harnesses.py`, `tools/results_matrix.py`,
`tools/run_summary.py`, and their three corresponding quality test files.
Existing parent changes in those files were preserved. No git writes, installs,
model calls, pilots, full driver runs, or unrelated file changes.

## Evidence and resulting behavior

Read `data/scaffolds/registry.py:validate_split`: a declared benchmark split
normally has both pools, but the official report run intentionally allows an
empty training pool and a filled evaluation pool. Read
`harnessgrad/loops/eval_side.py`: eval measures the selected checkpoint rather
than executing a method. Therefore `split.eval` populated plus `split.train`
empty does not establish exposure of exam traces. Exported provenance now
labels training diagnostics based on explicit `side=train` or legacy
`score_kind=training` evidence, using the already-resolved score side. This
describes eligibility for method feedback, not a claim that the method actually
read traces. An eval-only split no longer claims that
the method studied the exam or that the dataset declared no split. Training
diagnostics still carry their generalisation limitation.

Read the candidate rejection path in `harnessgrad/loops/mode_a.py` and
`harnessgrad/records.py:_curve_point`. Rejection can leave an ordinary numeric
`score: 0.0` placeholder while recording `n_scored: 0`, `per_task: {}`,
`measured_by_platform: false`, and
`unmeasured: {reason, problems, harness_dir}`. Tests use this numeric rejected
shape rather than relying only on `score: null`.

The matrix now admits a numeric score only if no explicit unmeasured or
zero-coverage evidence contradicts it. It independently recognizes
`measured_by_platform: false`, `n_scored: 0`, a structured unmeasured reason,
and the flat legacy `unmeasured_reason` field. Missing legacy fields do not
establish a failure; measured zero with positive coverage remains a valid
result. Its first/last/best/deltas and scored-round count use admitted scores.
Every original round remains in `measurement_context`, including planned and
scored coverage and the original refusal object. A wholly rejected curve is
skipped as unmeasured with coverage and a refusal detail. Refusal reasons are
also shown in the Markdown comparison notes.

The single-run summary keeps rejected rows in its per-round table, with
`score: null`, `delta: null`, explicit measurement flag/refusal evidence, and
the original planned/scored/unmeasured counts. It excludes those placeholders
from best/delta arithmetic. If a later measured point follows a rejected
submission, its per-round delta compares against the previous actual measured
score. An unmeasured terminal round leaves the summary's terminal score and
total delta undefined rather than fabricating a decline.

Thus H0 0.8 followed by a rejected placeholder 0.0 yields no -80pp claim. A
genuinely measured 0.0 after H0 0.8 still yields -80pp. Partial measurements
with positive scored coverage remain eligible; the coverage limitations remain
visible.

## Red regressions

Before changing implementation, Windows PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/quality/test_export_harnesses.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py -q -k 'official_eval_only or training_provenance or rejected_numeric or rejected_round or explicitly_unmeasured or measured_zero' --tb=short
```

Result: **10 failed, 2 passed, 34 deselected**. The failures were the official
eval-only provenance warning, missing explicit training warning, three matrix
numeric-rejection markers, a rejected-only matrix curve, the complete rejected
summary shape, and three summary rejection markers. Both measured-zero/legacy
compatibility guards passed before the fix.

## Focused verification

After implementation, the same targeted Windows command: **12 passed,
34 deselected**.

WSL lightrsi-ubuntu, working directory
`/mnt/c/Users/Tony Yu/.codex/worktrees/hg-latest-review/HarnessGrad`:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH='/mnt/c/Users/Tony Yu/AppData/Local/Programs/Python/Python310/Lib/site-packages'
python3 -m pytest tests/quality/test_export_harnesses.py \
  tests/quality/test_results_matrix.py tests/quality/test_run_summary.py -q --tb=short
```

Result: **46 passed in 25.89s**. After strengthening the mixed-curve refusal
visibility and score-only legacy compatibility assertions, and adding the
matrix's arithmetic explanation, a fresh identical focused run also passed:
**46 passed in 23.21s**. All fixtures use pytest's default temporary
roots outside source. The exporter tests' git operations are confined to their
synthetic temporary fixture repositories; this agent performed no workspace
git writes.

```powershell
git diff --check -- tools/export_harnesses.py tools/results_matrix.py tools/run_summary.py tests/quality/test_export_harnesses.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py
```

Result: **exit 0**.

## Limits

Only the authorized focused tests were run. No real archive was rewritten, no
receipt was resumed, and no endpoint was contacted. The changes derive reporting
from recorded evidence; they do not turn rejected candidates into measurements
or treat missing historical metadata as proof that a score was unmeasured.

## H0 provenance review correction

Parent review identified that training H0 (`round: 0`, `side: train`,
`score_kind: training`) also exists when the run requests zero method rounds.
The prior wording still asserted actual exposure without a method-call receipt.
Replaced both the training warning and legacy numeric `train_score` row with
the neutral description: training-side diagnostic; tasks are eligible for
method feedback; score does not establish held-out performance. Neither path
now claims the method actually viewed traces.

Added a realistic H0 provenance fixture with a normal nonempty training and
evaluation split. It covers modern `train_score: null` and legacy numeric
`train_score`, without launching the driver. Updated the diagnostic limitation
assertion. Before implementation, this scoped command failed as expected:

```powershell
python -m pytest tests/quality/test_export_harnesses.py -q -k 'training_h0 or training_provenance' --tb=short
```

Result: **2 failed, 15 deselected**, because the required eligibility wording was
absent and the old assertion of actual exposure remained. Only exporter source,
exporter tests, and this report changed in this review correction. No git writes.

Final WSL focused verification, using the same environment recorded above:

```bash
python3 -m pytest tests/quality/test_export_harnesses.py -q --tb=short
git diff --check -- tools/export_harnesses.py tests/quality/test_export_harnesses.py
```

Result: **18 passed in 9.07s**; diff check **exit 0**. Exporter correction is
frozen for parent review; no matrix or summary files were modified in this step.
