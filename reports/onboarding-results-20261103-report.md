# Result identity and coverage repair — 2026-11-03

Implemented in the existing isolated parent worktree. No git writes, dependency installations, real model calls, benchmark pilots, or secret output were performed. Changes remain for parent review and commit.

## Changes

- `tools/prepare_h0.py`: resolve the effective agent backend, model and endpoint from the child environment, honoring the driver's `.env` < process environment < explicit CLI precedence. Use the parent's shared `platform_config.read_env_file` parser without importing `driver`; opted scoring providers retain their effective `.env` judge settings and process-environment precedence. Forward `--model` through `HG_AGENT_MODEL`. Compare the requested identity with the platform-measured curve identity and the recorded effective agent configuration before publishing or reusing an H0. Require explicit platform measurement evidence. Existing task-set, split-content and trial identity controls remain. Both measured and reused H0 hints include `--runs-root`.
- `tools/export_harnesses.py`: label a checkpoint score using its recorded `side`, falling back to `score_kind`; historical records with neither say `unknown side`. An absent split no longer triggers the claim that train and eval used the same tasks.
- `tools/results_matrix.py`: retain split protocol/hash, trials, seed, source checkpoint, full baseline comparison block, scoring identity and scorer isolation/protocol metadata. Preserve per-point comparison context, including earlier-round warnings and skipped/unmeasured entries. JSON retains unknowns as `None`; Markdown shows scored/planned coverage and custom-heldout, partial and noncomparable labels. Existing score arithmetic and negative `last_delta` remain intact.
- `harnessgrad/loops/eval_side.py`: snapshot planned eval task IDs before resume filtering or scoring, and retain them in `sampling.task_ids`; scoring still receives only scored IDs and scores. Invalid outcomes remain separate.
- `tools/run_summary.py`: add planned/scored counts to JSON and the rendered table, alongside existing invalid/unmeasured counts. Explicitly state that invalid and unmeasured may overlap.
- Regression files: `tests/quality/test_prepare_h0.py`, `test_export_harnesses.py`, `test_results_matrix.py`, `test_run_summary.py`, new `test_eval_coverage.py`, and `test_scoring_identity.py`. The latter's exact-key assertion now includes backend identity; its mock-driver test quotes the method command with `shlex.join`, as requested by the parent after the space-containing Windows/WSL source path exposed the old fixture bug.

## Test environment and commands

All test roots are outside the source tree. Commands ran through `wsl -d lightrsi-ubuntu -- bash -lc '<command below>'`; the common prefix was:

```bash
cd "/mnt/c/Users/Tony Yu/.codex/worktrees/hg-latest-review/HarnessGrad"
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH=".:/mnt/c/Users/Tony Yu/AppData/Local/Programs/Python/Python310/Lib/site-packages"
```

Initial regression-first red gate:

```bash
python3 -m pytest -q tests/quality/test_prepare_h0.py tests/quality/test_export_harnesses.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py tests/quality/test_eval_coverage.py --basetemp=/tmp/hg-onboarding-results-red
```

Result: **14 failed, 49 passed**. Expected failures reproduced dropped model override, unresolved effective configuration, false H0 identity reuse, missing run-store hint, invented eval labels, discarded matrix metadata, missing planned/scored summary counts, and reduced eval selections in both fresh and resumed evaluation.

An overlapping targeted red command used the same five files with `-k "override_reaches or resolves_dotenv or recorded_configuration or reuse_hint or labels_the_recorded or keeps_comparison or unknown_matrix_identity or partial_eval_summary or sampling_keeps" --basetemp=/tmp/hg-onboarding-results-red-focused`; result: **14 failed, 2 passed, 47 deselected**.

Additional regression-first red gates:

```bash
python3 -m pytest -q tests/quality/test_results_matrix.py -k earlier_partial --basetemp=/tmp/hg-onboarding-results-red-context
python3 -m pytest -q tests/quality/test_results_matrix.py -k "earlier_partial or unmeasured_matrix_entry" --basetemp=/tmp/hg-onboarding-results-red-evidence
python3 -m pytest -q tests/quality/test_prepare_h0.py -k unverified_curve --basetemp=/tmp/hg-onboarding-results-red-measured
python3 -m pytest -q tests/quality/test_results_matrix.py -k missing_split_protocol_kind --basetemp=/tmp/hg-onboarding-results-red-unknown
```

Results, respectively: **1 failed, 9 deselected**; **2 failed, 9 deselected**; **2 failed, 31 deselected**; **1 failed, 11 deselected**. These reproduced discarded earlier/skipped evidence, reuse of a curve without explicit platform measurement, and an invented custom-heldout label when the split protocol kind was unknown.

Initial green attempt:

```bash
python3 -m pytest -q tests/quality/test_prepare_h0.py tests/quality/test_export_harnesses.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py tests/quality/test_eval_coverage.py tests/quality/test_resume_eval.py tests/quality/test_scoring_identity.py --basetemp=/tmp/hg-onboarding-results-green
```

Result: **102 passed, 5 failed**. Three run-summary fixture assertions still expected the old table columns, and a matrix fixture accidentally treated the newly documented word `unmeasured` as a run row; those assertions were updated to match the intentional output and inspect rows correctly. The existing mock-driver case `test_unopted_driver_can_still_share_h0_after_judge_changes` additionally exposed `bwrap: execvp python: No such file or directory` and an unquoted method path under `Tony Yu`. Parent owns the narrow runner interpreter fallback; the owned scorer fixture now quotes its command.

Focused green after corrections:

```bash
python3 -m pytest -q tests/quality/test_prepare_h0.py tests/quality/test_export_harnesses.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py tests/quality/test_eval_coverage.py tests/quality/test_resume_eval.py tests/quality/test_scoring_identity.py -k "not test_unopted_driver_can_still_share_h0_after_judge_changes" --basetemp=/tmp/hg-onboarding-results-green-focused
```

Result: **111 passed, 1 deselected**. A later matrix unknown-protocol regression and repair were independently verified with:

```bash
python3 -m pytest -q tests/quality/test_results_matrix.py --basetemp=/tmp/hg-onboarding-results-green-matrix
```

Result: **12 passed**.

Final focused core run before the shared-parser integration:

```bash
python3 -m pytest -q tests/quality/test_prepare_h0.py tests/quality/test_export_harnesses.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py tests/quality/test_eval_coverage.py tests/quality/test_resume_eval.py tests/quality/test_scoring_identity.py -k "not test_unopted_driver_can_still_share_h0_after_judge_changes" --basetemp=/tmp/hg-onboarding-results-final
```

Result: **112 passed, 1 deselected in 25.04s**.

After integrating the parent's shared dotenv parser, reran only H0 tests as requested, including file-vs-process judge precedence guards:

```bash
python3 -m pytest -q tests/quality/test_prepare_h0.py --basetemp=/tmp/hg-onboarding-results-h0-shared-parser
```

Result: **35 passed in 1.93s**. Source and test edits were frozen after this green gate.

Expanded resume/reference/diagnostic command:

```bash
python3 -m pytest -q tests/quality/test_repeated_trial_resume.py tests/quality/test_ordinary_resume_statistics.py tests/quality/test_eval_reference_block.py tests/quality/test_diagnostic_policy.py -k "not probe_mock_and_live and not check_only" --basetemp=/tmp/hg-onboarding-results-resume
```

Stopped this optional expanded run with SIGINT after **1 failed, 12 passed in 268.23s**. Exact failure: `test_repeated_trial_resume.py::test_partial_resume_preserves_full_manifest_and_exact_trial_identity` raised `PlatformTampered` because `tests/quality/test_results_matrix.py` changed during evaluation. This worker added the unknown-protocol regression while the snapshot-sensitive expanded run was active; that overlapping edit was the cause. No integrity guard was bypassed. The rest of the expanded suite is unverified.

Reran the exact failed test after the edit:

```bash
python3 -m pytest -q tests/quality/test_repeated_trial_resume.py::test_partial_resume_preserves_full_manifest_and_exact_trial_identity --basetemp=/tmp/hg-onboarding-results-resume-first
```

Result: **1 passed in 54.07s**. Per the parent's instruction, no further expanded testing is being launched.

Scoped whitespace verification:

```bash
git diff --check -- tools/prepare_h0.py tools/export_harnesses.py tools/results_matrix.py tools/run_summary.py harnessgrad/loops/eval_side.py tests/quality/test_prepare_h0.py tests/quality/test_export_harnesses.py tests/quality/test_results_matrix.py tests/quality/test_run_summary.py tests/quality/test_eval_coverage.py tests/quality/test_scoring_identity.py
```

Result: **exit 0**.

## Review concerns and limits

- Historical H0 records without the effective agent configuration or an explicitly platform-measured curve cannot be verified and are not reused by this tool. Their old receipts are preserved.
- Existing `sampling.task_ids` already truncated in old eval receipts cannot be repaired reliably; reporting uses the recorded selection without inventing a larger plan.
- Matrix warning metadata is descriptive evidence, not a new scorer or an authorization to run/expand a pilot. Invalid/unmeasured outcomes retain their existing definitions and may overlap.
- The task brief reserves full integration for the parent's final gate while other workers edit. No full project-suite pass is claimed by this worker.
- The full-driver scorer compatibility case has not been rerun by this worker after the parent's interpreter fallback. Its path quoting fix and the new parser are covered separately; parent final integration must cover that mock-driver path.
- Owned `/tmp/hg-onboarding-results-*` test roots were checked for cleanup with explicit names and resolved-parent validation. No production receipts or unrelated paths were removed.
