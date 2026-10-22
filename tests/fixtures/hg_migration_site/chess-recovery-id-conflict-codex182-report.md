# Chess recovery-ID guidance correction — Codex 182

Base: `e8dd6f6`; isolated worktree: `/tmp/hg-codex-182`.

The journal's interruption-audit sentence recommended reusing the already stopped
`chess-pilot-h0-20261013-recovery-01`. Its future-launch clause now requires a
genuinely new unique run ID for every attempt and forbids resuming or overwriting
any used ID. Existing supervisor, identity checks and current AGENTS.md gates
still apply.

Removed the corrected `recovery-id-conflict` entry from the registry's current
blockers and documented that closure in the registry guide. The schema has no
resolved status. The independent `interruption-closure`, trusted transport and
shared blockers remain unchanged.

Both historical IDs (`chess-pilot-h0-20261013` and its recovery), observations,
incomplete statuses, null effects and evidence citations remain unchanged, as do
the original pilot/recovery-result journal lines and the pinned history fixture.
The audit's findings, external path and SHA-256 citation are preserved. External
receipts were not opened or modified.

Added one focused assertion to the existing source-only registry tests covering
the new-ID rule in both guidance locations, absence of the old launch advice,
removal of the documentation blocker, retention of the interruption blocker and
exact equality of Chess history to the independently pinned baseline. Existing
test guards deny adapter imports, external reads, sockets and subprocesses.

Validation:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
TMPDIR=/tmp/hg-codex-182/.scratch/chess-recovery-id-codex182 \
python3 -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/harnessgrad-pytest-codex182-chess-recovery-id-codex182-pytest \
  tests/quality/test_dataset_blockers.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_dataset_blockers.py
git diff --check
```

Results: **46 passed**; registry valid with **28 datasets, 8 shared blockers,
11 historical records**, `production_authorized=false`; whitespace check passed.
Reviewed the complete diff and compared all historical records and the pinned
fixture against the base commit. Only documentation, the focused test and this
unique report are committed. Owned test scratch was removed after validation.
No run/archive edits, model/H0 execution or main-branch changes.
