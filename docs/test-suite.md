# Running the tests

The quality suite covers platform contracts, dataset adapters and optional native
controls. Run the affected tests during development. A selected passing subset
does not establish whole-suite acceptance.

A plain `pytest` invocation discovers tests in `tests/` and `console/uat/`. Authored dataset exercises contain intentionally failing test files and are not part of default discovery. Explicit pytest paths remain available for targeted work.

## CI selection and shared method code

`tools/testing/ci_select.py` maps changes to quality-test files. Changes under `methods/policy_ports/` or to top-level Python modules in `methods/` select the full-suite path because those modules are shared across method implementations and customization. For an individual method directory, selection uses the method directory name and includes its method-specific tests; if no such test family is mapped, it selects the full-suite path. Test helpers without an audited mapping also select the full-suite path. Changes to `tools/registry_ops/` and its top-level compatibility entrypoints select the full-suite path because registry declarations are shared across documentation and dataset tools. Changes to `tools/testing/` and deletions of its former top-level command paths select the full-suite path, because they control selection, execution or receipts for unrelated tests. In the standard push workflow, a full-suite selection is deferred to nightly or manual dispatch; it is not an assertion that the full suite ran on that push.

## Run the tests for one dataset

```sh
python3 tools/testing/run_tests.py --dataset gsm8k
python3 tools/testing/run_tests.py --dataset appworld --dataset gaia     # repeatable
python3 tools/testing/run_tests.py --dataset bfcl_v3 --list              # plan only, runs nothing
```

This selects the files for that dataset **plus the core platform tests**. It does
not run other datasets' tests. Scorer suites can include release replay; keep
those checks selected when changing their adapter or a shared execution path.

Core is included on purpose — a platform regression still matters while you work on
an adapter.

## When you changed the dataset *layer*

Adding or removing an adapter, editing the taxonomy, or touching the shared QA
scaffold is not one dataset's business. Those changes are checked by the
**registry-wide** files, which verify the contract across all datasets:

```sh
python3 tools/testing/run_tests.py --dataset gsm8k --with-registry
python3 tools/testing/run_tests.py --registry-only
```

They are the slowest thing in the suite because "all datasets" is the thing they
check. Run them for layer work, not for every adapter edit.

## Run everything

```sh
python3 tools/testing/run_tests.py                      # whole suite, parallel
python3 tools/testing/run_tests.py --jobs 8             # eight workers
python3 tools/testing/run_tests.py --fast               # skip the measured-slow files
python3 tools/testing/run_tests.py --file test_scorer   # files whose path contains this
python3 tools/testing/run_tests.py --file test_scorer --file test_verifier   # both, one pass
```

`--file` is repeatable. Repository-relative paths containing `/` match exactly;
bare terms match path substrings. The selection includes a file matching any
argument. Each selected file runs in its own lane; the lane split is derived
once for the combined selection.

The selection helpers live in `tools/testing/scope.py` and `tools/testing/lane.py`. Their names avoid pytest collecting their `test_files` helpers as tests. The runner imports the scope helper from the same package; direct script execution is supported outside the checkout.

The runner starts one pytest process per file across two lanes:

* a **parallel lane** for the isolated majority, and
* an **exclusive lane**, at concurrency 1, for files that bind a fixed port or a
  shared service. Running two of those at once produces failures that have nothing
  to do with the code — measured here as a batch of false failures that disappeared
  when the same files ran one at a time.

Use `--list` to see the plan and the estimated wall clock before running anything.

## Why `--basetemp` must be outside the platform

The runner gives each file its own external `--basetemp`. Set its parent with
`--basetemp-root`; the default uses `HG_TEST_TMPDIR` or Python's temporary-directory
selection. Driver-based tests use `tmp_path` as a harness work root, which must
be outside the platform tree. `conftest.py` rejects an in-platform `--basetemp`
at startup. Direct pytest invocations retain pytest's normal default when the
option is omitted.

## Which tests belong to which dataset

`tools/testing/scope.py` decides, from **filenames**:

* `test_appworld_reporting.py` → `appworld`, because the name says so.
* `test_dataset_controls.py` → registry-wide, because it checks all datasets.
* everything else → core.

A dataset name appearing only in a file's *body* does **not** scope it. Several
platform tests use `terminal_bench` as an example fixture; scoping them would make
`--dataset terminal_bench` run the runner's own contract tests, and would pull them
into every other dataset's selection too.

Files that are about one dataset without being named after it are listed explicitly
in `EXTRA`; each entry is a decision, not a guess.

**2026-10-27:** the *test* of the scoping helper (`test_test_scope.py`) was
deleted with the audit-round tests it selected; the helper, now `tools/testing/scope.py`, is
still what `tools/testing/run_tests.py` uses to build its lanes.

`test_test_scope.py` guarded the rule: it failed if a dataset was added
with no test file, if an `EXTRA` entry points at a file that no longer exists, or if
a registry-wide file starts being scoped to individual datasets. It also pins a real
coverage gap it found: **8 of 29 datasets have no dedicated adapter test file** and
are reached only through the cross-dataset tests. The list can shrink, not grow.
(Both numbers here were stale until `aime` was registered: the tuple had already
shrunk to 8 when the 2026-10-16 fidelity phase gave `fever` and `tau_bench` their
own test files, and the total went 28 → 29 with the port.)

## If you add a dataset

1. Add the adapter under `data/` and classify it in `tools/dataset_maintenance/dataset_classes.py`.
2. Add `tests/quality/test_<dataset>_*.py`.
3. Run `python3 tools/testing/run_tests.py --dataset <dataset> --with-registry`.
4. Run `python3 -m pytest tests/quality` to confirm the suite
   guard accepts the new name.

## Where the temporary directories go

Per-file pytest basetemps are placed on the **roomiest writable scratch filesystem**,
not blindly in `/tmp`, and each file's tree is removed when that file finishes.

This is not tidiness. On a shared machine `/tmp` is often a small root filesystem, and
when it fills, tests fail with `No usable temporary directory found` — which looks like a
broken test rather than a full disk. It also makes real integration tests time out, so a
nearly-full disk quietly converts working code into a red suite. A test run must neither
depend on that filesystem having room nor be the thing that fills it.

Override the location with `HG_TEST_TMPDIR=/some/roomy/path`. The runner prints the chosen
directory and its free space before it starts. `--keep-basetemp` retains the trees for
debugging; without it, 140 files would leave 140 trees behind.

## Reading a failure

A failure under load and a failure in the code look identical in a summary, so the runner
re-runs every failed file **on its own** and separates them:

* **REAL failure** — still fails alone. Investigate.
* **load-sensitive** — passes alone. The test is not isolated (usually a fixed port or a
  shared service). Lower `--jobs`, or fix the test; do not ignore it forever.

Both exit non-zero by default. `--allow-load-sensitive` exits 0 when only the second kind
remain, for a fast local loop.

## Working on the composition roots

Read [architecture](architecture.md#small-unit-extraction-stage-and-remaining-responsibilities)
and [import direction](import-direction.md) before moving code from `driver.py`,
`console/server.py` or `eval/run/runner.py`. Current extracted responsibilities include
argument configuration, selected-dataset planning, evaluation admission/cache,
source leases and committed-candidate preparation, trial journals/scheduling,
record projection and Console query/settings/launch services. Linux acceptance
remains tracked separately in [remaining work](linux-runtime-remaining.md).

Start with the canonical unit's interface, callers and tests. Keep dependencies
explicit, give each mutable cache/lock/resource one owner, and patch the canonical
implementation in internal tests. Existing public CLI/function delegates remain supported; `eval.runner` now directs callers to `eval.run.runner`. Function aliases do not
promise that rebinding an old private global changes the new owner's dependency.
Never add reflected global synchronization to make an old test patch work.

For these extracted units, a focused offline check is:

```sh
python -m pytest -q --basetemp=/tmp/hg-extracted-units \
  tests/quality/test_import_direction.py tests/quality/test_boundaries.py \
  tests/quality/test_eval_package_compat.py tests/quality/test_outcomes.py \
  tests/quality/test_console_record_reader.py tests/quality/test_run_parser.py \
  tests/quality/test_selected_run_plan.py tests/quality/test_evaluation_admission_plan.py \
  tests/quality/test_console_query_service.py tests/quality/test_console_range_diff_boundary.py \
  tests/quality/test_console_launch_service.py tests/quality/test_trial_worker_contract.py \
  tests/quality/test_source_candidate_stage.py tests/quality/test_post_execution_usage_retention.py \
  tests/quality/test_cli_entry.py tests/quality/test_experiments.py
```

Use a unique external basetemp for concurrent runs. Also select affected consumer
checks: provider/signal interpretation for outcomes, console record APIs for the
reader, and source-review/scenario/experiment refusal tests for parsing changes.
Report exact commands, pass/skip counts and scope. These checks establish local
syntax, dependency and fixture contracts; they do not establish real model,
Docker, benchmark or Linux isolation/lifecycle acceptance.

The [Loop SDK fixture guide](../tests/quality/loop_runtime_fixtures.md) explains
explicit offline cache or wheelhouse preparation for the genuine locked SDK.
Native controls run the platform in a separate pip-free venv and check that its
private packages stay hidden from the actor. Container controls additionally
require their exact local images; a native pass does not cover those placements.

Before any later migration, name the complete responsibility being moved and
its dependency ports, inspect callers and monkeypatch targets, and retain the
partial ordering documented in architecture: selected dataset identity before
durable claim; actor-runtime binding after fixed-source staging/commit; score
publication after measurement. Preserve historical record shapes, measured
versus invalid outcomes, unknown costs, actor/verifier privacy and cleanup on
partial startup. Require the applicable Linux gate receipts before splitting
those execution closures. Do not combine a large move with a scoring/schema or
identity change.

## Subprocess decoding guard: current audit

The `test_harness_decoding.py` audit scans 138 text-mode subprocess call sites in the current tree, including its embedded actor example. The original main and LightRSI fix trees contain the same total; four calls moved from `tools/` to `harnessgrad/` during refactoring. Each call has a reviewed policy or a specific documented exception; the [inventory review](validation/decoding-inventory.md) records the comparison and limits. Strict decoding remains intentional for source-bound scoring and identity paths where malformed output must refuse measurement rather than change it. The test scans source syntax; it does not prove every runtime output is valid or every decoder handles failures correctly.

## Existing failures checked during shared-layer refactoring

On 2026-11-16, the following focused controls were run without a model or benchmark on both the original `main` checkout at `1eedb4c5be7104025c8eb89c3782323b7cbdf0b6` and the refactor checkout at `b3b87b4631ad4d5b6587157513e9fc63c47c2b56`. Both controls failed in both checkouts:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/hg-baseline-control \
  tests/quality/test_release_results.py::test_matrix_excludes_failed_h0_but_preserves_graceful_stop \
  tests/quality/test_dataset_runtime_identity.py::test_provider_session_freezes_actual_requests_and_resets_afterward
```

- The results matrix JSON retains the graceful-stop record and its completion metadata, but this test expects `early_stop` in the Markdown table and does not find it. This is a display/contract discrepancy; the failed study was still excluded in this control.
- At that revision, the provider-session test expected a call to `admit_provider` when requesting a cached catalog; the mock observed no call. `DatasetProvider.catalog()` returns its cached result before issuing another request. The test was subsequently corrected to distinguish cache hits from a new request: it verifies that a changed storage snapshot re-admits the same artifact and that failed admission refuses the request. This does not claim that a cache hit rechecks artifact bytes or establish admission behavior elsewhere in a complete run.

These are existing baseline failures, not passing acceptance evidence for the refactor. Neither runtime behavior nor the test assertions were changed by this diagnostic. Separately, image Python runtime controls requiring the locked Loop SDK could not complete offline because `openai==2.21.0` was unavailable in the inspected local wheel cache.

The setup-tool move was also checked against the original main checkout: both parameterizations of `test_responses_overlay.py::test_formal_failure_keeps_capture_after_task_cleanup` initially failed because the fixture supplied `prompt` without the `goal` or `instruction` required by the task contract. The synthetic fixture now supplies `goal`, and both cases pass without changing production execution. `test_openclaw_harness.py::test_native_cli_mock_writes_answer_and_trace` errored on both checkouts because the locally discovered OpenClaw launcher did not resolve to a recognized installed package. These controls do not establish successful OpenClaw runtime execution in this environment.

## Historical migration-source check

The [migration ledger](../tests/fixtures/hg_migration_site/PROVENANCE.json) lists 45 entries; seven have no current checkout carrier and are identified as history-only. The remaining entries retain their recorded carriers. The ledger itself is pinned by SHA-256 in `test_migration_provenance_integrity.py`.

On 2026-11-16, the source commit `4d30de827b4b1073a65cb62643a970b917eecd2b` was fetched into the isolated refactor clone and retained there under `refs/harnessgrad/verification/migration-4d30de82`. With that object available, the historical-source test verified all eight applicable entries against their recorded lengths and SHA-256 values. Together with the platform integrity controls, **15 tests passed**. No run receipt was modified.

Another clone can reproduce the historical check without checking out the commit:

```sh
git fetch --no-tags origin 4d30de827b4b1073a65cb62643a970b917eecd2b
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/hg-migration-source-check tests/quality/test_migration_provenance_integrity.py
```

Use a separate temporary directory for each concurrent test invocation. The custom verification ref is local to the refactor clone; it is not automatically published with a branch. When the source commit is absent, the historical-source test skips rather than claiming verification. Matching historical bytes checks this ledger's sources only: it does not validate all experiment results or make platform identities interchangeable.
