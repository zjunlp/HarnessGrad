# Running the tests

The quality suite covers platform contracts, dataset adapters and optional native
controls. Run the affected tests during development. A selected passing subset
does not establish whole-suite acceptance.

## Run the tests for one dataset

```sh
python3 tools/run_tests.py --dataset gsm8k
python3 tools/run_tests.py --dataset appworld --dataset gaia     # repeatable
python3 tools/run_tests.py --dataset bfcl_v3 --list              # plan only, runs nothing
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
python3 tools/run_tests.py --dataset gsm8k --with-registry
python3 tools/run_tests.py --registry-only
```

They are the slowest thing in the suite because "all datasets" is the thing they
check. Run them for layer work, not for every adapter edit.

## Run everything

```sh
python3 tools/run_tests.py                      # whole suite, parallel
python3 tools/run_tests.py --jobs 8             # eight workers
python3 tools/run_tests.py --fast               # skip the measured-slow files
python3 tools/run_tests.py --file test_scorer   # files whose path contains this
python3 tools/run_tests.py --file test_scorer --file test_verifier   # both, one pass
```

`--file` is repeatable. Repository-relative paths containing `/` match exactly;
bare terms match path substrings. The selection includes a file matching any
argument. Each selected file runs in its own lane; the lane split is derived
once for the combined selection.

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

`tools/test_scope.py` decides, from **filenames**:

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
deleted with the audit-round tests it selected; the helper itself (`tools/test_scope.py`) is
still what `tools/run_tests.py` uses to build its lanes.

`test_test_scope.py` guarded the rule: it failed if a dataset was added
with no test file, if an `EXTRA` entry points at a file that no longer exists, or if
a registry-wide file starts being scoped to individual datasets. It also pins a real
coverage gap it found: **8 of 29 datasets have no dedicated adapter test file** and
are reached only through the cross-dataset tests. The list can shrink, not grow.
(Both numbers here were stale until `aime` was registered: the tuple had already
shrunk to 8 when the 2026-10-16 fidelity phase gave `fever` and `tau_bench` their
own test files, and the total went 28 → 29 with the port.)

## If you add a dataset

1. Add the adapter under `data/` and classify it in `tools/dataset_classes.py`.
2. Add `tests/quality/test_<dataset>_*.py`.
3. Run `python3 tools/run_tests.py --dataset <dataset> --with-registry`.
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
`console/server.py` or `eval/run/runner.py`. The current stage extracts argument
configuration, record projection and outcome interpretation only. Linux acceptance
remains tracked separately in [remaining work](linux-runtime-remaining.md).

Start with the canonical unit's interface, callers and tests. Keep dependencies
explicit, give each mutable cache/lock/resource one owner, and patch the canonical
implementation in internal tests. Existing public CLI/function delegates and the
identity-preserving `eval.runner` alias remain supported. Function aliases do not
promise that rebinding an old private global changes the new owner's dependency.
Never add reflected global synchronization to make an old test patch work.

For these extracted units, a focused offline check is:

```sh
python -m pytest -q --basetemp=/tmp/hg-extracted-units \
  tests/quality/test_import_direction.py tests/quality/test_boundaries.py \
  tests/quality/test_eval_package_compat.py tests/quality/test_outcomes.py \
  tests/quality/test_console_record_reader.py tests/quality/test_run_parser.py \
  tests/quality/test_cli_entry.py tests/quality/test_experiments.py
```

Use a unique external basetemp for concurrent runs. Also select affected consumer
checks: provider/signal interpretation for outcomes, console record APIs for the
reader, and source-review/scenario/experiment refusal tests for parsing changes.
Report exact commands, pass/skip counts and scope. These checks establish local
syntax, dependency and fixture contracts; they do not establish real model,
Docker, benchmark or Linux isolation/lifecycle acceptance.

Before any later migration, name the complete responsibility being moved and
its dependency ports, inspect callers and monkeypatch targets, and retain the
selection -> identity capture -> admission -> workspace/commit -> measurement
ordering documented in architecture. Preserve historical record shapes, measured
versus invalid outcomes, unknown costs, actor/verifier privacy and cleanup on
partial startup. Require the applicable Linux gate receipts before splitting
those execution closures. Do not combine a large move with a scoring/schema or
identity change.
