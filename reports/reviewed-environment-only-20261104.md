# Reviewed v2 launch environment cannot be refilled from ignored dotenv files

Independent increment based on `670c81f`, registered worktree
`/tmp/hg-reviewed-environment-only-20261104`, branch
`codex/reviewed-environment-only-20261104`. Main, frozen branches, prepared-source
worktrees and existing receipts were not changed.

The v2 launcher already builds a small explicit child environment, but the driver
later called load_env and reintroduced unreviewed local overrides. Separately,
harnessgrad.paths._settings discovered main-checkout and linked-worktree .env
files even when no driver load occurred. Either could defeat reviewed wrapper,
step, dataset-root or overlay selection.

The launcher now unconditionally sets HG_REVIEWED_ENVIRONMENT_ONLY=1 **after**
all permitted runtime/native settings have been applied and **before** spawning
the driver interpreter. This fixed tracked policy is neither inherited from the
operator nor an optional reviewed runtime override. Under exactly that value:

- driver.load_env skips file parsing/mutation at every centralized call site and
  resets platform_config before further reads;
- paths._settings reads only explicit process environment, preserving trusted
  scorer_paths behavior and suppressing both main/worktree dotenv discovery;
- platform_config's cache signature includes the marker, so a mode transition
  cannot reuse a previously file-derived dataset-root snapshot.

The ordinary CLI without the marker retains existing dotenv parsing, file
precedence, explicit-process-env precedence and path semantics. This is a scoped
opt-in consumer policy, not a global dotenv parser change. Declared reviewed
process dataset and overlay roots remain available. Relative explicit paths
retain existing main-checkout-relative semantics; no new root discovery or
filesystem writes were added.

Policy identity binding: the marker name/value and all reader behavior are in
tracked platform source. The v2 measurement identity includes platform_commit,
current_identity recomputes that commit, and measurement_identity_sha256 hashes
the identity containing it. Therefore adopting this patch requires a fresh
selection/measurement identity at its integrated platform commit; historical
identities/receipts are not upgraded or rewritten. No identity-schema change or
user-controlled policy variable can substitute for the fixed launcher setting.

Validation: **140 passed**,4.55s across reviewed-environment-only, existing
AppWorld launcher/selection, data-path and configuration suites. Tests use fake
main+linked .git layouts and synthetic dotenv wrapper/step/data/overlay values.
They assert neither reader even opens dotenv in reviewed mode, verify explicit
process roots and wrappers remain intact, verify legacy dotenv behavior, verify
marker-only cache invalidation, and start a fresh subprocess with the launcher's
actual sanitized environment before importing real driver code. That subprocess
calls real driver.load_env for both files and real paths/config resolvers; ignored
Codex/DSH wrappers and step limits remain absent. Runtime/provenance probes are
mocked; no endpoint, model, dataset execution, network or benchmark is used.
git diff --check passed.

```bash
python3 -m pytest -q tests/quality/test_reviewed_environment_only.py \
 tests/quality/test_appworld_baseline_launcher.py tests/quality/test_appworld_selection_prep.py \
 tests/quality/test_data_paths.py tests/quality/test_config.py \
 --basetemp=/tmp/hg-reviewed-environment-only-final-tests-NEW
```

Limits: the marker prevents later file injection; it does not sanitize arbitrary
already-populated process variables or remove values imported by a previous
legacy load in that same process. The reviewed launcher supplies the clean
process environment before interpreter startup, which is the intended boundary.
It does not attest arbitrary third-party code, its own independent configuration
readers, or live model state. Launcher-side data selection/preparation remains
subject to the existing source/runtime identity checks. A source checkout with
uncommitted edits is not made trustworthy by setting a marker. No launch,
experiment recovery, campaign authorization or main merge was performed.
