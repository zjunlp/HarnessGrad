# Upstream result/H0 audit — 2026-11-03

The initial audit below was a read-only comparison of committed objects, independent of the worktree. The subsequent parent-authorized reporting integration is recorded in the addendum. No git writes, model calls, benchmark runs, or driver integration tests were performed by this worker.

Compared commits:

- Original base: `0a2551b028b5d6e527199458c8ce358a74c7d608`.
- Audited upstream `origin/main`: `1365a826e7be76f4a54fb4b57f3d29b6ef5c1b24`.
- Complete local fixes: `20b701b9775b8db023355af91d88d360eea550e3` on `codex/fix-onboarding-first-four-20261103`.

## Per-item verdict

| Item | Upstream status | Committed evidence | Local patch still needed |
| --- | --- | --- | --- |
| H0 effective agent identity and model forwarding | Not fixed | `tools/prepare_h0.py` is unchanged from the original base. `configuration()` still records `args.model`/`args.endpoint`, omits backend, and the child environment still does not forward `--model` as `HG_AGENT_MODEL`. | Resolve effective dotenv/process/CLI model/backend/endpoint; forward the model; validate recorded/measured identity before publication/reuse. |
| H0 sampling identity and measured sampling | Not fixed | Upstream config omits sampling even though the CLI accepts it. `verify()` has no policy cross-check. | Include sampling in config and compare curve `sampling.policy`, metadata `sampling_policy`, and `config.run.sampling`; refuse missing or conflicting evidence. |
| Relative H0 runs root and reuse hint | Not fixed | Upstream passes a relative `--runs-root` to a child launched with `cwd=ROOT`, while parent filesystem operations use its own cwd. Hints still contain only `--h0-from`. | Resolve root at parse time and include that root in both reuse/publication hints. |
| Driver H0 donor-to-recipient agent binding | Not fixed | Upstream `driver.py` H0 branch still checks harness SHA and scorer identity, then copies donor data. No effective recipient agent versus donor metadata/measured curve check exists; upstream has no `harnessgrad/h0_identity.py`. | Bring the shared helper and driver call. Bind model/backend/endpoint to donor `run_meta.config.agent` plus measured curve model/backend. Refuse unknown legacy identity and reused donor points whose original endpoint evidence is unavailable; require the original donor. |
| Eval planned/scored coverage | Not fixed | `harnessgrad/loops/eval_side.py` is unchanged from the original base; `sampling.task_ids` remains `list(scored)`. | Capture planned IDs before resume filtering/evaluation and retain them in sampling, while `task_ids`/scores remain the scored set. Keep invalid outcomes separate. |
| Export score provenance | Not fixed | `tools/export_harnesses.py` is unchanged; provenance still labels every aggregate `score (eval)` and infers shared train/eval tasks from absent training IDs. | Use side/score_kind; missing side stays unknown. Preserve the local training-diagnostic wording without claiming which traces a method actually read or inventing held-out status. |
| Results matrix | Partially fixed | Upstream adds `completion`, `partition`, `split_sha`; a `completion.status=incomplete` skip; partition/status columns; and explanation of custom-heldout/nonofficial results. | Add full split protocol, trials, seed, source checkpoint, baseline/scorer identity and per-point context; planned/scored coverage; partial/noncomparable labels; preserve earlier/skipped refusal evidence; exclude explicit unmeasured/zero-coverage placeholders from score arithmetic. Keep upstream completion behavior too. |
| Run summary | Partially fixed | Upstream adds JSON `completion` and a prominent completion/round-budget/reason banner. It does not add planned/scored columns or exclude explicit unmeasured placeholders from arithmetic. | Add the local per-round coverage/reason fields, planned/scored table, overlap caveat and measured-score arithmetic. Retain upstream completion JSON/banner and valid surviving H0 score. |

No audited item is fully superseded by upstream. Matrix and summary overlap with upstream reporting improvements, so those files need semantic combination rather than replacement.

## Upstream behaviors the merge must retain

1. `driver.py` now runs `readiness.configuration_checks` before H0, optional endpoint checks under `--check-net`, and `selected_model_checks` after selected per-task declarations are loaded. These are new upstream admission gates; the local branch was based on older code and does not contain them.
2. `--fail-on-incomplete`, completion metadata, completion console observations, eval-side end summaries and the optional incomplete exit code must remain. Preserve the distinction between a valid H0 score and a finished requested experiment, and between method-requested `early_stop` and failure.
3. Matrix JSON must retain upstream `completion`, `partition`, `split_sha`; Markdown must keep partition/status visible. Explicitly incomplete runs remain skipped even if they have a numeric H0. Graceful early-stop runs remain eligible. Local `measurement_context` should enrich these rows/skips, not remove the completion gate.
4. Run summary must retain upstream `completion` including `None` for legacy absence, and the prominent status/round-budget/reason banner. The local coverage table and unmeasured arithmetic are additive.
5. Existing donor harness-tree/scorer checks, original payload/curve reconstruction, trace staging, and recipient method/improver identity merging must remain alongside the new agent binding.

Upstream `harnessgrad/completion.py` observes explicit invalid/unmeasured/harness-failed/timeout evidence and valid measured points. It is a separate completion contract; the new coverage reporting must not silently replace or erase that status model.

## Local behavior the merge must retain

- Bring `harnessgrad/h0_identity.py`, all H0 preparation/driver wiring and H0 regressions, including the refusal of chained `shared_from` donors lacking original endpoint evidence.
- The H0 tool depends on the local shared `platform_config.read_env_file` function/export; that helper is absent in audited upstream. Bring the config support too, preserving dotenv precedence. The parent owns configuration integration.
- Retain `tests/quality/test_eval_coverage.py` for both fresh and resumed partial evals, with planned selection larger than the scored set and invalid separate.
- Retain side-aware provenance tests, full comparison-context tests, unknown-metadata behavior, earlier/skipped warnings, genuine measured-zero behavior, negative `last_delta`, and explicit unmeasured placeholder exclusion/reason preservation.
- Invalid and unmeasured may overlap under existing record definitions; do not add the two counts as if disjoint or fabricate zeros for unknown fields.

## Merge hazards and focused validation contracts

`git diff origin/main..20b701b9` includes apparent deletions of upstream completion/readiness behavior because the local branch started from the older base. Those are not intentional local removals. Do not apply whole local `driver.py`, `tools/results_matrix.py`, or `tools/run_summary.py` files over upstream.

The existing upstream `tests/quality/test_release_results.py` provides the reporting preservation contract:

- `test_matrix_excludes_failed_h0_but_preserves_graceful_stop`: incomplete experiment skipped; `early_stop` included; custom partition/hash retained; status and failure reason visible.
- `test_summary_keeps_valid_score_and_prominent_incomplete_status`: surviving valid H0 stays a valid score, while the page explicitly marks the experiment incomplete.
- `test_legacy_absence_is_not_a_fabricated_complete_status`: missing completion remains unknown.

Parent integration should combine those upstream contracts with local H0 identity/sampling/root tests, donor agent binding tests, eval coverage tests, and export/matrix/summary regressions. The initial read-only audit did not execute tests; subsequent reporting-only verification is recorded below.

## Inspection commands

Representative commands used, all read-only:

```text
git rev-parse origin/main 20b701b9 0a2551b0
git diff --stat 0a2551b0..origin/main -- tools/prepare_h0.py driver.py harnessgrad/h0_identity.py harnessgrad/loops/eval_side.py tools/export_harnesses.py tools/results_matrix.py tools/run_summary.py
git diff 0a2551b0..origin/main -- driver.py tools/prepare_h0.py harnessgrad/loops/eval_side.py tools/export_harnesses.py tools/results_matrix.py tools/run_summary.py
git diff origin/main..20b701b9 -- driver.py tools/prepare_h0.py harnessgrad/loops/eval_side.py tools/export_harnesses.py tools/results_matrix.py tools/run_summary.py
git show 20b701b9:harnessgrad/h0_identity.py
git show origin/main:harnessgrad/completion.py
git show origin/main:tests/quality/test_release_results.py
git show origin/main:tests/quality/test_release_preflight.py
git ls-tree origin/main harnessgrad/h0_identity.py
```

## Reporting integration addendum

Parent created `codex/integrate-onboarding-latest-20261103` from audited upstream and started the merge of the old fix branch. This worker was then authorized to resolve only the reporting conflicts/review and their focused tests.

Actual changes:

- Resolved both `tools/results_matrix.py` conflict regions semantically. Rows keep upstream `completion` and `partition`, retain the `split_sha` field through the local point-first/metadata-fallback context, and add local planned coverage and complete per-point `measurement_context`. The upstream incomplete-completion skip remains before measured-score selection, which uses the local `_measured_score` guard rather than treating every numeric placeholder as a measurement.
- Preserved upstream partition/status Markdown columns and explanatory text, alongside local coverage/comparison columns and refusal/partial/noncomparable evidence. Existing `_skip` enrichment retains context even when upstream completion rules skip the row.
- Reviewed `tools/run_summary.py` after its automatic merge: it already retained upstream completion JSON/banner and the valid H0 score, together with local planned/scored counts, refusal evidence, placeholder exclusion and overlap wording. No additional summary source edit was needed.
- Added only two combination regressions in `tests/quality/test_release_results.py`. One verifies an explicitly incomplete skip still carries coverage, baseline problems and split identity. The other preserves the recorded early-stop status while excluding the actual rejected-candidate point shape (`score: 0.0`, no scored tasks, false measurement flag, structured `unmeasured` reason/problems/harness path) from score arithmetic, retaining its refusal evidence in both matrix and summary.
- `test_results_matrix.py` and `test_run_summary.py` required no further changes by this worker. Existing upstream release-result contracts remain unchanged.

### Regression evidence

The two new combination tests were first run against the exact unmodified `1365a826` modules loaded in memory. This avoided using the unresolved worktree module and did not extract or duplicate production branches. PowerShell set `PYTHONDONTWRITEBYTECODE=1` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, then passed this script to `python -c`:

```python
import os, subprocess, sys, types
from pathlib import Path
import tools
for name in ('results_matrix', 'run_summary'):
    fullname = 'tools.' + name
    source = subprocess.check_output(
        ['git', 'show', '1365a826:tools/' + name + '.py'],
        text=True, encoding='utf-8')
    module = types.ModuleType(fullname)
    module.__file__ = str(Path('tools', name + '.py').resolve())
    sys.modules[fullname] = module
    setattr(tools, name, module)
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
import pytest
raise SystemExit(pytest.main([
    '-q', '-p', 'no:cacheprovider', 'tests/quality/test_release_results.py',
    '-k', 'incomplete_skip_retains or recorded_early_stop',
    '--basetemp=' + str(Path(os.environ['TEMP']) / 'hg-results-upstream-combined-red')]))
```

RED: **2 failed, 3 deselected in 0.48s**. Upstream lacked skipped `measurement_context` and counted the rejected zero as a second scored point.

After conflict resolution, native Windows verification:

```powershell
python -m pytest -q -p no:cacheprovider tests/quality/test_release_results.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py --basetemp="$env:TEMP/hg-results-merged-focused"
```

Result: **33 passed, 2 failed in 2.74s**. The two failures were pre-existing tests reading UTF-8 summary JSON with native Windows GBK defaults (`test_a_replacement_is_reported_not_silent`, `test_a_long_curve_keeps_the_page_short`). An attempted same-command rerun with `PYTHONUTF8=1` was blocked before pytest by an existing non-UTF-8 installed `.pth` file. No dependencies, global configuration or environment files were changed.

Final reporting-only Linux verification used the existing parent-provided runner:

```powershell
python 'C:/Users/Tony Yu/.codex/visualizations/2026/11/03/01a19fd0-8bbc-7c32-b448-c5dd4f351391/run-focused-tests.py' tests/quality/test_release_results.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py
```

GREEN: **35 passed in 9.46s**. This runner uses WSL, disabled bytecode/plugin autoload, mock configuration, and an automatically removed pytest root outside the source tree. Only the three reporting files ran; no driver tests or pilots ran.

Additional checks:

```text
git diff --check -- tools/results_matrix.py tools/run_summary.py tests/quality/test_release_results.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py
```

Result: **exit 0**. Compiling the merged matrix, summary and release-result test sources in memory also passed; there are no conflict markers in these files. No `git add` or commit was performed; the parent owns index resolution, other merge conflicts and final validation. Reporting source and tests were frozen after the 35-test green gate.

The native Windows red/first-green temporary directories remain outside source. The final Linux runner removed its own temporary tree; no production receipts or unrelated paths were deleted.

### Final protocol-kind correction

Parent review found that the preserved upstream `partition` expression and summary banner still treated any truthy metadata `split_protocol` as custom-heldout. This contradicted the local unknown-protocol rule when metadata contained only `{'official_score_comparable': False}`.

Narrow correction:

- Matrix `partition` now uses the actual metadata `split_protocol.kind`, falling back to `split_source`; absent kind/source stays `None` rather than becoming custom-heldout.
- Summary retains the exact upstream custom-heldout banner only for explicit `kind='custom-heldout'`. Other kinds show their recorded value, and absent kind says `Split protocol: unknown`. An explicit `official_score_comparable=False` still emits the noncomparability warning. Existing matrix comparison warnings remain.
- Added one metadata-based regression parameterized across matrix and summary in `test_release_results.py`. Existing custom-heldout release tests remain unchanged.

RED, with bytecode/plugin autoload disabled:

```powershell
python -m pytest -q -p no:cacheprovider tests/quality/test_release_results.py -k unknown_metadata_protocol --basetemp="$env:TEMP/hg-results-protocol-red"
```

Result: **2 failed, 5 deselected in 0.54s**, reproducing invented custom-heldout labels in both surfaces.

GREEN:

```powershell
python 'C:/Users/Tony Yu/.codex/visualizations/2026/11/03/01a19fd0-8bbc-7c32-b448-c5dd4f351391/run-focused-tests.py' tests/quality/test_release_results.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py
```

Result: **37 passed in 12.47s**. Scoped diff checks and in-memory syntax compilation of the three changed source/test files passed. Source/tests were immediately frozen; the parent owns the final actual-mock smoke. No driver tests, broader audit or git writes were performed by this worker for this correction.
