# Strict launch/recovery contract follow-up

Registered independent worktree `/tmp/hg-strict-launch-contract-followup-20261104`,
branch `codex/strict-launch-contract-followup-20261104`, exact base
`f0cc714d998d4383752b4c5bbd8621575c4e7967`. Earlier delivered trees and main are
unchanged. This addresses actual independent synthetic failures, not benchmark
ability: sandboxfalse, different sampling, mismatched source path, duplicate
harness-path and no-sandbox could previously retain matched recovery status.
Argparse abbreviations could override a reviewed digest that recovery ignored.

## Changes

For opt-in expected-source invocations, the actual driver parser now disables
abbreviations and refuses duplicate parsed option destinations and no-sandbox.
Detection, duplicate checking and parsing use the same expanded argv, including
config/set expansion; readiness receives that same parsed args object. Without
expected-source flags, legacy CLI abbreviation/last-value behavior is unchanged.
These flags remain requests and evidence checks, not an isolation attestation.

Formal-v2 recovery uses an exact command grammar: all dataset, harness-path,
source/filesystem hashes, side, sampling, tasks, rounds/trials/seed/jobs, improver,
run-ID/output roots and fail-on-incomplete options must occur once and match the
reviewed identity, launch selection and receipt. Abbreviated, duplicate, missing,
unknown or extra switches (including no-sandbox/config overrides) fail closed.
Negative reviewed integer seeds remain valid. The Python/driver prefix must
exactly match the committed canonical paths, not merely end in driver.py.

Public runtime commitment schema is now
`harnessgrad-runtime-source-filesystem-v2`. Alongside the private base-runtime
digest and filesystem schema/hash/count, it explicitly includes:

- source canonical original path, submission SHA and manifest SHA;
- resolved execution interpreter path/SHA and exact driver path/SHA.

The runtime hash binds this complete public commitment. Raw runtime environment,
including credential-bearing proxy URLs, is never serialized by this change.
Interpreter identity uses the existing runtime_binding hash where available;
command construction uses resolved sys.executable. Driver source hashing is
bounded local platform code. Historical recovery compares recorded commitments;
it does not reread historical binaries or claim live executable attestation.

Recovery requires committed source path == unique command harness-path == original
run_meta.harness_source.path. The private temporary snapshot never replaces this
original public path. It also requires actual run configuration sandbox isTrue,
samplingall, correct dataset/harness/runID, trials/jobs, roots and seed, and
improverNone; malformed/missing values remain unverified. Existing model/side/
round/source/filesystem checks remain. Existing task evidence must be inside the
selected subset and carry the expected source_split pool; command tasks must
exactly match launch.selected order. This does not claim a separate run_meta task
manifest comparison or independently prove the dataset's pool materialization.

This intentionally changes reviewed runtime identities again. Older commitment
schemas, ambiguous commands, incomplete attempts or mismatched metadata remain
audit-only/unverified rather than becoming comparable official coverage.
Source path+content and complementary filesystem SHA contracts are unchanged.
No owner/xattr/ACL or external runtime/dependency closure claim is added. The
separate dotenv reintroduction and preexisting config redaction boundaries remain
outside this scoped fix.

## Verification

The actual driver parser is exercised with synthetic sources and failure guards
before describe_model/task loading: duplicate full and equals flags, abbreviated
source/fs/harness/jobs flags and no-sandbox all refuse. A real legacy parser case
retains its previous abbreviations. Synthetic receipt regressions include all
five independently reported cases plus abbreviated aliases, duplicate seed,
wrong tasks/side/roots/improver, extra config, source-path coordinated tampering,
wrong executable/driver, interpreter-hash tampering and negative-seed success.
The existing two-interruption continuation fixture now records the full public
source/entry commitment, exact formal command and actual execution metadata.
Credential-bearing synthetic proxy URLs remain absent from public launch plans.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
 -p no:cacheprovider --tb=short \
 tests/quality/test_reviewed_filesystem_identity.py \
 tests/quality/test_appworld_prepared_source.py \
 tests/quality/test_appworld_baseline_launcher.py \
 tests/quality/test_harness_path.py tests/quality/test_harness_path_eval.py \
 tests/quality/test_appworld_selection_prep.py \
 --basetemp=/tmp/hg-strict-launch-contract-final2-20261105
```

**273 passed in69.05s**. A subsequent equivalent small optimization reuses the
runtime_binding interpreter hash rather than rereading it; focused launcher
validation is recorded separately if rerun. `git diff --check` passes. All controls
use harmless synthetic/mock/demo inputs: no real model, benchmark, external
assessment data/answers, official scorer, service or campaign was executed.

Final interpreter-hash reuse focused validation: **113 passed in4.64s** using
`--basetemp=/tmp/hg-strict-launch-reuse-hash-final-20261105` on the prepared-source
and baseline-launcher files.
