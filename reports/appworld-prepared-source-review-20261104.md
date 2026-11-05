# AppWorld prepared-candidate identity follow-up — 2026-11-04

Base: `670c81f64502b9038ed17712dcee8e4d14278fea` from the clean, unchanged
selection-review worktree. Separate registered worktree:
`/tmp/hg-appworld-prepared-source-20261104`, branch
`codex/appworld-prepared-source-20261104`. No main, prior review, DSH source or
frozen review edits. Main advanced independently to `f752837` during this work;
this follow-up remains explicitly based on the requested selection review.

## Problem and resulting behavior

Portable DSH ships without machine-local runtime mounts. Preparing a separate
candidate is supported by configure_dsh_harness.py and driver --harness-path, but
the AppWorld selection/launcher hardcoded ROOT/base_harness/name. Modifying that
tracked manifest would violate the launcher's clean-platform gate.

Bindings may now include an optional mapping:

```json
{
  "harness_sources": {
    "deepseek-harness": "/absolute/canonical/path/to/prepared-candidate"
  }
}
```

Prepare that candidate independently; this follow-up never configures or modifies
it. Paths must be canonical absolute directories outside the current/main
platform checkouts (and cannot contain those checkouts), without Git metadata.
Aliases, missing paths, unsafe source links, invalid static candidates, controls
and a manifest.name differing from the requested harness refuse. Bytecode remains
excluded under the existing submission identity contract.

One resolver supplies current_identity, execution_environment and the exact
--harness-path command. The external canonical path, submission SHA256 and hash
of the parsed manifest bytes are included in runtime_sha256. The same bytes at a
different prepared path therefore change identity. Execution environment creation
rehashes source and selected runtime files immediately before subprocess launch.
No mapping keeps the existing ROOT source and legacy runtime identity shape.

## Driver boundary closes the recheck-to-start gap

A launcher-only recheck was insufficient: a different stable directory could
replace the candidate before the driver captured its own source receipt. Commands
now include --expected-harness-sha256 with the reviewed submission SHA256.
The driver accepts it only with --harness-path and a lowercase 64-digit digest.

Before describe_model, dataset loading, readiness or any model/task work, the
driver checks the initial receipt, creates an owned snapshot through the existing
safe-copy helper, checks that snapshot against the expected digest, and checks the
original source stayed unchanged. All later source manifest/readiness/staging work
uses that private reviewed copy. ExitStack removes it on normal or exceptional
exit. Late staging again compares the owned snapshot to the expected digest,
retains the existing staging-change check, and rechecks original source identity.
Run metadata preserves the original submitted path and submission identity.

Existing absolute-path/output-overlap, source-link ownership, external Git/hook
exclusion, read-only candidate mounts, sandbox staging and native permission
policies are retained. The new flag does not remove any ordinary admission gate.
Legacy direct --harness-path without the flag preserves its existing behavior.

## Verification

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
 tests/quality/test_appworld_prepared_source.py \
 tests/quality/test_appworld_selection_prep.py \
 tests/quality/test_appworld_baseline_launcher.py \
 tests/quality/test_harness_path.py tests/quality/test_harness_path_eval.py \
 --basetemp=/tmp/hg-appworld-prepared-source-final
```

**180 passed in 53.31s**. Python compile and git diff --check pass.
New tests use synthetic prepared DSH-like candidates and local runtime fixture
files, not a native DSH/model process. They verify external-source four-pool
selection with real identity validation, unchanged 9/6/17/30 caps, selected path
and digest in launcher command, content/path identity changes, pre-subprocess
rehashing, invalid source/path/name/role/mapping cases and default-source support.

Driver tests verify success on a harmless synthetic files task, preservation of
original metadata/source, invalid hash/combinations, stable source replacement
after launcher review, and a transient source replacement between receipt and
snapshot that is restored before the final source check. Both tamper tests put a
failure guard on describe_model, prove it is never reached, and create no run
receipts; the transient case also checks temporary snapshot cleanup. Existing
harness-path/eval tests retain unsafe-link, overlap, Git metadata, read-only and
staging-change coverage.

No AppWorld service, official scorer, real model, inference, network request,
benchmark or campaign ran. Tests do not attest real DSH ABI compatibility or full
runtime dependency closure. Runtime identity remains selected-file evidence, not
transitive attestation or immutable storage proof; ordinary platform/runtime
checks and separate experiment authorization still apply. New source bindings
change identity and require freshly prepared selections; no historical receipt
or selected cohort is rewritten.

## Separate bytecode follow-up after 8f34064

Independent review found a real hole in the first prepared-source commit:
submission identity intentionally excludes `__pycache__` and leaf `*.pyc`, but
owned snapshots copied them. Python -B prevents writing caches, **not reading
them**. Thus matching reviewed source bytes did not constrain cached executable
module code. The earlier 180-test result did not cover this threat and is not a
claim that the first commit alone closed it.

The expected-hash workflow now strips all ignored bytecode from its early private
snapshot before identity/model checks and again from its late staging snapshot.
All children named `__pycache__` (files, directories or owned aliases) and leaf
`*.pyc` files/aliases are removed. Ordinary directories and directory aliases
named `*.pyc` are retained because their child-file paths contribute to the
existing source hash. The original submitted tree and source_identity semantics
are unchanged. Legacy direct-source runs without the expected-hash flag retain
their previous copying behavior.

All snapshot links are checked for owned targets before stripping. Cache aliases
are unlinked rather than followed; escaping, broken/cyclic cache aliases and
reviewed aliases into removed caches fail closed before describe_model. Existing
safe-copy helper and sandbox permission/mount policy remain unchanged. The early
reviewed block now normalizes ignored-cache symlink-loop errors into a refusal.

The new end-to-end synthetic regressions create valid PEP552 unchecked-hash and
checked-hash caches (the latter with a forged matching source hash header). Their
malicious code only raises an identifying RuntimeError. Controls prove Python -B
executes these caches while the reviewed submission SHA remains unchanged. The
actual reviewed driver workflow instead executes the reviewed source: all four
synthetic files-task processes exit 0, and original caches/source remain untouched.
Additional cases cover root/nested caches, owned aliases, meaningful directories
ending in .pyc, external links, dangling/cyclic aliases and ambiguous reviewed
source links, with failure guards proving describe_model is never reached for
unsafe inputs.

Final combined command is the five-file focused suite above with
`--basetemp=/tmp/hg-reviewed-bytecode-final`: **188 passed in 57.58s**.
Python compile and git diff --check pass. No real model, network, benchmark,
AppWorld service/scorer, native DSH process or campaign ran. This closes the
source-cache execution gap for the new expected-hash workflow; it does not attest
external runtime dependency contents or full filesystem identity.
