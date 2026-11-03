# Onboarding fixes integrated with the refreshed upstream

## Base and history

The original reviewed fixes are preserved on
`codex/fix-onboarding-first-four-20261103` at `20b701b9`, based on `0a2551b0`.
The user authorized fetching upstream, checking which fixes already existed,
and merging the remaining fixes.

This isolated worktree uses `codex/integrate-onboarding-latest-20261103`.
The first fetch selected `1365a826`; merge `a44ee93a` integrated the reviewed
fixes with that release. A delivery-time fetch found two additional upstream
commits, `df9e5108` and `ec7552f0`. The latter is the final fetched upstream
base (upstream timestamp 2026-11-03 14:48:08 +0800). Their complete changes
were then merged, preserving the operator's history.

The main checkout and remote have not been modified. Earlier reports remain
historical receipts for their recorded bases; this document records integration.

## Which fixes upstream already contained

| Area | Upstream state and integration decision |
| --- | --- |
| Quickstart | Upstream already has demo-first onboarding, BBEH/research recipes, independent method templates and strict completion status. Retained those; corrected the displayed demo dataset row and retained the numeric `.env` and installer quoting/LF fixes. |
| External methods | Upstream's new template/tutorial were retained. The validator and protocol still had the old external-directory and missing/empty-trajectory defects, so their reviewed fixes were merged. The tutorial was strengthened around quoting, external validation, nonempty trajectories and stop semantics. |
| Preflight and venv | Kept upstream configuration/selected-model/endpoint checks and the complete PLAN6 sandbox implementation. Removed the obsolete parallel method-check implementation and its broad endpoint-only metadata flags. Kept missing-entrypoint validation and CLI override support. A/B explicitly declare a generic editor requirement; endpoint paths require their SDK, while an ordinary CLI editor remains independent of that SDK. |
| H0 and results | Upstream had not fixed effective H0 identity, sampling, relative roots, donor binding, planned eval coverage or score-side provenance. Merged those repairs. Combined local coverage/refusal handling with upstream completion, incomplete-run exclusion, partition and status columns. Unknown protocol kinds remain unknown. |

The six initial merge conflicts were reviewed and resolved semantically.
README, sandbox and method-template teaching follow upstream; no whole-file
replacement discarded the new completion or multi-channel admission behavior.
The second upstream merge applied without conflicts.

## Final ordering regression

`df9e5108` correctly prioritizes workspace isolation over configuration errors.
Its moved configuration gate nevertheless left the CLI capability probe earlier
in the driver; that probe can make two generation requests. A fake CLI and a
sentinel probe reproduced this before either a missing script or invalid
workspace was refused: two regression cases failed without network access.

The entire existing probe block was moved after both local admission checks,
retaining its skip behavior, unprobed state and metadata. A direct comparison
confirmed its content is otherwise byte-for-byte identical. Final order is
workspace isolation, configuration, optional CLI capability probe, workspace
creation, then H0. Both rejected configurations now have zero probe calls.
The old regression's stronger "before dataset loading" assumption was changed
to the required "before baseline or model calls" contract, preserving the new
upstream refusal-metadata and isolation ordering.

## Verification

Linux-only tests used the existing WSL distro, mock backend, disabled third-party
pytest plugin autoload and bytecode writes, with temporary roots outside source.
No real model endpoint, paid service or benchmark pilot was launched.

| Gate | Result |
| --- | --- |
| H0, validator, eval coverage, reporting, config and documentation files on the first merged base | 195 passed, 3 driver cases deselected, 44.41 s |
| Readiness, release preflight, interpreter and venv refusal subset | 43 passed, 11 deselected, 92.59 s |
| Final combined reporting cases, including unknown metadata protocol | 37 passed, 12.47 s |
| Reporting, experiments, recipes, source boundaries and import direction | 89 passed, 1 environment failure, 1 deselected, 38 subtests passed, 93.61 s |
| Final upstream registry reporting, decoding, readiness and driver workspace-isolation cases | 24 passed, 66.69 s |
| Final CLI-probe ordering, readiness, local release preflight and driver isolation | 33 passed, 9 deselected, 49.25 s |

Counts overlap and are not a full-suite total. The two capability-probe cases
were red before the relocation and green afterwards. Independent review also
replayed the real driver with a fake CLI and observed refusal exit 2, zero probe
calls and no created workspace. See
`reports/onboarding-latest-integration-review-20261103.md`.

Actual driver controls preserved outside source:

- `latest-edit-17be69a6e4`: one bundled demo task, mock backend, one echo edit;
  exit 0, score 0.0 to 1.0, complete coverage and `completion=complete` (150.25 s).
- `latest-shared-17be69a6e4`: same-agent H0 reuse; exit 0, recorded donor and
  `completion=complete` (60.72 s).
- `latest-refused-17be69a6e4`: changed mock model; exit 2, no measured curve
  (55.12 s). Matrix reading of the two valid receipts retained completion and
  scored/planned coverage 1/1.
- `latest-head-h0-17faf484cc`: after the `ec7552f0` merge, a one-task mock H0;
  exit 0, one platform-measured task, zero invalid tasks and complete status
  (88.11 s). The subsequent probe relocation affects the CLI branch only;
  its final driver refusal paths are covered by the final 33-test gate.

Static checks: changed Python sources compiled in memory; Bash installer syntax
passed; `git diff --check` passed; Git has no unresolved index entries. The
upstream sandbox and completion modules remain unchanged by this integration.

## Environment limitations and preserved evidence

- Two upstream fresh-venv tests could not enter their test bodies: this WSL
  Python lacks ensurepip, so fixture environment creation failed. Other venv
  path/refusal controls passed. The tests were not changed to hide this.
- The positive BBEH recipe preflight cannot pass here without the optional
  OpenAI client package. It correctly reports that missing dependency before
  generation. Dedicated absence/CLI controls passed; no fake SDK was installed.
- Upstream's host catalogue guard reports missing `chess` and a NumPy binary
  incompatible with Linux Python 3.14. These are the previously identified
  chessbench/livecodebench environment gaps, now explicitly reported upstream.
- An optional expanded isolation invocation was stopped with SIGINT after
  2 failures / 5 passes (46.55 s) to keep verification scoped. Its failures were
  the obsolete dataset-loading assumption (subsequently updated and verified)
  and the host catalogue dependency guard above. The selected isolation and
  decoding checks were rerun successfully; no integrity guard was bypassed.
- Vendored FlashRSI escape warnings were unchanged. Full-suite, fresh-venv
  dependency installation and real-model performance are not claimed verified.

Run IDs and receipts were fresh and preserved. Temporary WSL work roots were
removed on completion. This merge authorizes no pilot expansion.
