# Result identity repair brief

Work in the existing isolated parent worktree on codex/fix-onboarding-first-four-20261103. Other workers edit disjoint files; do not run git write commands or modify their files.

Own only tools/prepare_h0.py, tools/export_harnesses.py, tools/results_matrix.py, tools/run_summary.py, harnessgrad/loops/eval_side.py, and their focused test files. Do not change driver.py, readiness.py, README, method validator, or the shared configuration declarations.

Use regression-first behavioral tests, then minimal fixes:

1. H0: --model must reach HG_AGENT_MODEL in the actual child environment. Reuse identity must resolve effective backend/model/endpoint including existing .env precedence, rather than caching None when environment settings change. Verify against measured identity before publishing/reusing an H0. Keep existing task/split/trial controls. Make the printed --h0-from hint include the correct --runs-root; avoid changing unrelated defaults.
2. Export provenance: use side/score_kind to label training versus eval; unknown historical sides must not be invented as eval.
3. Matrix: preserve split_protocol/split_sha, trials, seed, source checkpoint, baseline comparison problems, and scorer identity in JSON. Markdown must visibly identify custom-heldout/partial/noncomparable rows. Keep existing scores and last_delta behavior; do not silently coerce invalid outcomes to zero or discard evidence. Use existing record fields; unknown metadata remains None/unknown.
4. Eval coverage: sampling.task_ids must retain the planned eval selection, while task_ids/scored data passed for scoring remain the scored set. Preserve invalid separately and show planned/scored coverage in reports. Do not claim invalid and unmeasured are disjoint if existing record definitions overlap.

No third-party platform runtime dependency, scoring changes, real model calls, benchmark pilots, dependency installation, or secret output. Test roots outside source. WSL lightrsi-ubuntu has python3/bwrap; pytest is readable at /mnt/c/Users/Tony Yu/AppData/Local/Programs/Python/Python310/Lib/site-packages with autoload/bytecode disabled. Source: /mnt/c/Users/Tony Yu/.codex/worktrees/hg-latest-review/HarnessGrad. Prefer unit/tool tests while other workers edit; full integration is the parent's final gate.

Write full red/green commands, outcomes, changed files and concerns to reports/onboarding-results-20261103-report.md. Return short status. Do not spawn subagents. Parent reviews and commits your scoped changes.
