# AppWorld recovery increment on current main — 2026-11-04

Base: main `44200fe8a0d7d976b3d65ed816db7a8532ccd706`. This independent
worktree carries only the recovery state machine, opt-in per-task verifier
journal, and their focused tests. It does not include the latest-harness,
resource-monitor, selection-preparation, model-provenance or ALFWorld audit
patches. The original review worktree and main remain untouched.

Attempts and task coverage are separate. Recovery identifies partial receipts
without `exit.json`, unions disjoint tasks, refuses conflicting attempts and
recomputes pools from task evidence. It compares source, scorer, platform,
harness, runtime, model and budget identities. Timeouts occupy their own state;
process outcome remains separate from a stored official artifact verdict.

The historical runner wrote end-of-round verdicts only after `evaluate()`
returned. The v2 launcher opts into a start marker before `run_one` and saves
the actual returned verifier verdict before atomic scalar progress publication.
Recovery verifies the task/run/round binding and recorded digest; scalar
progress alone cannot become an official score. Started tasks without terminal
evidence require review. This journal does not regrade or change the scorer.
The opt-in's privacy continues to depend on existing actor mount boundaries; it
does not establish universal backend or power-loss guarantees.

Focused no-model validation on this exact source snapshot:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/quality/test_appworld_baseline_launcher.py \
  tests/quality/test_progress_wall_clock.py tests/quality/test_task_id_paths.py \
  tests/quality/test_signal_termination.py tests/quality/test_gaia_level_reporting.py \
  tests/quality/test_bun_worker_identity.py \
  --basetemp=/tmp/hg-recovery-current-main-tests
```

Result: **97 passed in 10.21s**. The two-interruption test exercises the actual
campaign, launcher, evaluate, marker, verifier-journal and progress paths with
only `run_one` replaced by synthetic results. Other cases cover identity drift,
live locks, unknown official verdicts, duplicated attempts, partial publication,
conflicting evidence and nonzero exit with a valid official artifact.

This increment alone does not include the separate selection/ABI follow-up:
that follow-up binds the actor's actual Python overlay and prevents dry-run
image pulls/data preparation. Both require review before use together. No real
model, benchmark, historical receipt modification, main merge or campaign
execution was performed. New platform/model/harness/budget identities must be
generated after any eventual integration; old selections are not reusable.
