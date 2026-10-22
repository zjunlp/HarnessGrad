# b7: two local native runtime blockers closed

Only Jericho and ScienceWorld registry entries change. Runtime, verifier and
admission source is unchanged. All eight shared gates remain open for both:
`runtime-observations`, `paired-filesystem-quotas`, `scorer-offline-closure`,
`pristine-verifier`, `artifact-only-state-export`, `official-scorer-identity`,
`snapshot-owned-cleanup`, `pilot-guidance-conflict`. Production authorization stays
false. ScienceWorld V4 remains incomplete with `effect=null` and diagnostic-only
mean 0.1380508475.

## Trust boundary and reproducible command

The following is the exact launch command (fresh `mktemp` output is recorded in
each receipt). The outer bubblewrap bind makes the platform read-only to the
trusted pytest/control/observer processes, too. Runtime writes, package staging,
receipts and pytest scratch go only to this external work root. Source edits and
receipt publication into this worktree happen outside these runs.

```bash
taskwork=$(mktemp -d /tmp/hg-b7-runtime-XXXXXX)
mkdir "$taskwork/tmp"
printf '%s\n' "$taskwork"
bwrap --die-with-parent --ro-bind / / --ro-bind /tmp/hg-b7 /tmp/hg-b7 --bind "$taskwork" "$taskwork" --proc /proc --dev /dev --chdir "$taskwork" --setenv TMPDIR "$taskwork/tmp" --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PYTEST_DISABLE_PLUGIN_AUTOLOAD 1 -- python3 -m pytest -q -s -p no:cacheprovider --basetemp="$taskwork/pytest" /tmp/hg-b7/tests/quality/test_runtime_blocker_evidence_b7.py
```

The external `observe-bwrap` launcher directly executes `/usr/bin/bwrap` 0.6.1,
adding only an info FD and a trusted inline handshake before the original subject
exec. Bubblewrap supplies the child host PID. The parent reads that live PID's
mountinfo, namespace links and selected source hashes through `/proc/<pid>/root`
after final setup. It checks receipt storage is absent from subjects' namespaces.
Every receipt contains both the original exact argv and actual instrumented argv,
cwd, raw mount table, independent source views, exit status and observed process
cleanup. The final namespace observation is **not** supplied by the actor or
verifier. The test-only launcher is not a production admission integration, signed
attestation, pristine image or runtime provenance service.

Platform source has a read-only bind observed by the parent; nontruncating
`os.open(source, os.O_WRONLY)` reports `EROFS`. Full platform content manifests
before/after are identical; file hashes and the baseline Git commit are recorded.
Method source and Jericho native-library hashes are also read through the actual
subject namespace. This establishes local immutable source views for the measured
execution window. External operator changes, supported production image identity,
transitive dependencies and future executions still require shared admission.

Early attempts remain under separate `/tmp/hg-b7-runtime-*` directories: `w5lqyI`
was rejected because `--block-fd` pauses before effective mount setup on 0.6.1;
`Zyv30e` exposed a cleanup timing race; `1UyVfS` exposed an incorrect test assumption
that noop check exit must be zero (the real check exits 1 for a measured zero).
None is passing boundary evidence. Later `L6kxg9` and `arXOPF` runs passed before
additional parent source-identity/receipt-visibility assertions were added. Old
receipts are preserved; fresh roots are used for every rerun.
`7jelmJ` failed before launch because of an extra parenthesis in the generated
observer script; `1mAydb` passed after that was corrected. The final rerun also
checks a writable fake method source at its original absolute platform path.

## Jericho — local runtime closure

Closed: `independent-runtime-proof`, limited to the selected native standalone
control route. f27be1f repairs writable private temp storage; that repair alone is
not independent evidence. The fresh parent-collected effective-mount and source
observations make this receipt independent of the control's own reward assertion.

Original `observed`, verbatim: "Public runtime projection and phase bounds were
repaired; independently exercised production work-root evidence remains missing."
Original `next_action`, verbatim: "Review selected metadata and an independent
runtime-boundary receipt from a work root outside the platform checkout."

Only `acorncourt`, already documented as a train/study game, is played. Its
metadata is selected directly from the official installed record; no heldout game
is played and no walkthrough contents are printed. The receipt records ROM,
seed, setup/verifier paths, public runtime allowlist, selected manifest hash, ROM
hash and installed package hashes. Metric: game score / game get_max_score(),
not normalization by the reference solution. Coverage is 1 selected of the
historical 56 eligible games, one trial per noop/oracle control, jobs=1. Eligibility
56 is inherited from the adapter's documented inventory, not freshly enumerated;
source archive provenance is declared, not independently reverified.

Exact control commands are in `reports/jericho-runtime-b7.json` at
`controls[*].command`, with complete stdout/stderr. They invoke:

```text
python3 /tmp/hg-b7/data/datasets/jericho/control.py --noop --task-id acorncourt --workdir <external-test-root>/noop
python3 /tmp/hg-b7/data/datasets/jericho/control.py --oracle --task-id acorncourt --workdir <external-test-root>/oracle
```

Observed output: noop `reward 0`, `score=0`, `max_score=30`, `measured=true`;
oracle `reward 1`, `score=30`, `max_score=30`, `moves=17`, `measured=true`.
There are 18 agent launches and 2 verifier launches. Agent network namespace
differs from the parent; package projection is read-only, `game_info.py` is absent
in parent source views, and the native library matches the installed hash.
Verifiers retain the original `game_info.py` hash, frozen artifacts, a writable
verdict directory and a private writable `/tmp`. The actual verifier argv binds
the dedicated `.control` root instead of `/`.

Counterfactual probes and exact commands/stdout are recorded under
`projection_controls` and `old_root_negative`:

```text
projection=True:  {"bytecode": false, "reference_bindings": false, "reference_module": false, "walkthrough_api": false}
projection=False: reference_bindings=true, reference_module=true, walkthrough_api=true
old verifier work_root=/: {"tempfile": "ENOENT"}; effective /tmp is ro
```

The identical installed-code presence probe is run with and without the overlay;
it emits no reference values. The old-root probe reproduces tempfile's failure
instead of calling it a zero score. Removing each boundary changes an actual
observation. Task directories are removed and absence is observed. Kernel/PID
receipts are retained outside actor trees. No model/endpoint/GPU is used, no
additional hard byte/inode quotas are claimed, agent/check timeout is 300s, and
the instrumented observer has a stricter 30s per-launch timeout. The unmodified `run_one` call
still returns `backend_observation_unavailable`, no score/verdict, and
`production_authorized=false`.

Open: every shared gate listed above, including production observer integration,
trusted verifier/dependency/release identity, official scorer attestation and
full lifecycle cleanup. This control is no H0 authorization or dataset effect.

## ScienceWorld — local runtime closure

Closed: `prevent-platform-writes` for the existing native harness/verifier/method
mount plans. Tests call `sandbox.command` and the real `_method_sandbox_plan`,
then execute their bubblewrap argv directly. They import no ScienceWorld task
payload, run no JVM episode and generate no benchmark score.

Original `observed`, verbatim: "Code-solvable prevention review is closed: existing
native sandbox call sites hide protected platform paths and restore required
source read-only before execution; synthetic mount/refusal contracts pass. Commit
34a472a preserves fatal mismatch detection with unknown writer attribution.
Trusted live enforcement remains unverified; V4 remains incomplete with effect=null."
Original `next_action`, verbatim: "Independently establish trusted effective mount
enforcement and immutable platform provenance for the intended runtime; retain all
shared admission gates. Synthetic closure does not authorize production/H0 or an
unsandboxed route. Preserve V4 and its diagnostic-only mean; any future authorized
diagnostic requires a fresh run ID and the staged AGENTS.md policy."

Probe: `os.open(path, os.O_WRONLY)` without truncate, reported as errno or
`OPENED_WRITABLE`; actual scratch and verdict writes also prove that the entire
process has not simply lost write permission. Parent kernel observations and
source identities accompany these probe outputs. Exact commands/output appear
in `reports/scienceworld-runtime-b7.json` under `positive[*]` and `negative`.

```text
harness: source=EROFS, artifact=OPENED_WRITABLE, platform_source=ENOENT, platform_driver_visible=false
verifier: source=EROFS, artifact=EROFS, platform_source=ENOENT, platform_driver_visible=false
method: source=EROFS, method_source=EROFS, artifact=OPENED_WRITABLE, platform_source=ENOENT, platform_driver_visible=false
all three: private_tmp="temporary", verdict="verdict writable"
negative: source=OPENED_WRITABLE, artifact=OPENED_WRITABLE, platform_source=OPENED_WRITABLE, method_source=OPENED_WRITABLE, platform_driver_visible=true
negative provenance: {"added": [], "removed": [], "modified": ["eval/containers/sandbox.py"]}
```

The negative starts with the same verifier plan and appends writable overmounts
for external disposable workspace/task/platform copies. The fake platform is
mounted at `/tmp/hg-b7`, so the **same absolute platform paths** become visible
and writable; no actual platform source is exposed writable. A deliberate
external canary edit proves the provenance detector can fail. Positive parent
mount tables show a tmpfs hiding the platform, `ro` source/artifact binds and
`rw` scratch/verdict binds; the negative tables show effective `rw` mounts and
the fake source hash. Actual platform manifests remain identical.

Open: all eight shared gates, unsupported runtime modes, production observer
integration and immutable supported image/dependency custody. Tests establish
the native mount mechanism and local source provenance only. No refusal hook,
admission override or policy is weakened. V4 and all historical receipts remain
unchanged; any future diagnostic requires separate authorization, a fresh ID and
the staged policy.

## Validation

Final live invocation and focused regression/checker output are recorded below
after receipt publication. Only the checker and named focused tests are run;
never the full suite. The registry's active-only schema requires a remaining gap:
both proven local IDs are retired and replaced with `shared-runtime-admission`,
without changing any shared reference or historical record.

Final external root: `/tmp/hg-b7-runtime-Of2MgL`; exact command is the live
invocation above. Output:

```text
/tmp/hg-b7-runtime-Of2MgL
B7_RECEIPT /tmp/hg-b7-runtime-Of2MgL/pytest/test_parent_observer_detects_e0/scienceworld-b7.json
.B7_RECEIPT /tmp/hg-b7-runtime-Of2MgL/pytest/test_jericho_selected_runtime_0/jericho-b7.json
.
2 passed in 9.06s
```

Focused regression invocation (same outer read-only guard, external root
`/tmp/hg-b7-focused-0UlQN4`):

```bash
taskwork=$(mktemp -d /tmp/hg-b7-focused-XXXXXX)
mkdir "$taskwork/tmp"
printf '%s\n' "$taskwork"
bwrap --die-with-parent --ro-bind / / --ro-bind /tmp/hg-b7 /tmp/hg-b7 --bind "$taskwork" "$taskwork" --proc /proc --dev /dev --chdir "$taskwork" --setenv TMPDIR "$taskwork/tmp" --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PYTEST_DISABLE_PLUGIN_AUTOLOAD 1 -- python3 -m pytest -q -p no:cacheprovider --basetemp="$taskwork/pytest" /tmp/hg-b7/tests/quality/test_jericho_control_tempdir.py /tmp/hg-b7/tests/quality/test_jericho_runtime_admission.py /tmp/hg-b7/tests/quality/test_scienceworld_platform_writes_codex186.py /tmp/hg-b7/tests/quality/test_scienceworld_integrity_codex181.py /tmp/hg-b7/tests/quality/test_dataset_blockers.py
bwrap --die-with-parent --ro-bind / / --ro-bind /tmp/hg-b7 /tmp/hg-b7 --bind "$taskwork" "$taskwork" --proc /proc --dev /dev --chdir "$taskwork" --setenv PYTHONDONTWRITEBYTECODE 1 -- python3 /tmp/hg-b7/tools/check_dataset_blockers.py
```

```text
86 passed in 3.59s
{"valid": true, "datasets": 28, "shared_blockers": 8, "historical_records": 11, "production_authorized": false}
```

The focused registry test now pins retirement of both local IDs, all eight
unchanged shared references, the remaining runtime gap and V4's immutable
historical record. No existing admission assertion is removed. The final live
rerun above adds the method-source counterfactual and parent source-identity
checks; focused production/refusal source and registry behavior did not change
after the 86-test validation. Receipts bind test source hashes to the exact
measured version. Subsequent report/journal publication changes documentation
only and is not presented as part of the measured immutable execution window.
