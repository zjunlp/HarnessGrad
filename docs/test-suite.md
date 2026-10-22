# Running the tests

The quality suite is ~3,500 tests in ~140 files. Run **all** of it rarely; run the
part you changed, always.

## Run the tests for one dataset

```sh
python3 tools/run_tests.py --dataset gsm8k
python3 tools/run_tests.py --dataset appworld --dataset gaia     # repeatable
python3 tools/run_tests.py --dataset bfcl_v3 --list              # plan only, runs nothing
```

This selects the files for that dataset **plus the core platform tests**. It does
not run other datasets' tests. That matters: `test_appworld_official_scorer.py`
spends about twenty minutes in release replay, and someone editing the GSM8K
scorer has no reason to pay for it.

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
python3 tools/run_tests.py --jobs 8             # fewer workers
python3 tools/run_tests.py --fast               # skip the measured-slow files
python3 tools/run_tests.py --file test_scorer   # files whose path contains this
python3 tools/run_tests.py --file test_scorer --file test_verifier   # both, one pass
```

`--file` is repeatable and a file matching **any** of the substrings is included. Passing
several in one invocation is not just brevity: each file still lands in its own lane, and
the lane split is derived once for the whole set instead of once per file.

The runner starts one pytest process per file across two lanes:

* a **parallel lane** for the isolated majority, and
* an **exclusive lane**, at concurrency 1, for files that bind a fixed port or a
  shared service. Running two of those at once produces failures that have nothing
  to do with the code — measured here as a batch of false failures that disappeared
  when the same files ran one at a time.

Use `--list` to see the plan and the estimated wall clock before running anything.

## Why `--basetemp` must be outside the platform

Every file gets its own `--basetemp` under `/tmp`. This is not cosmetic. Driver-based
tests use `tmp_path` as a harness work root, and the driver **correctly refuses** a
work root inside the platform tree. A basetemp of `.scratch/...` therefore turns
ordinary tests into confusing failures. `conftest.py` rejects an in-platform
`--basetemp` at startup with an actionable message rather than letting you debug the
symptom. Omitting the option keeps pytest's normal default.

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

`tests/quality/test_test_scope.py` guards the rule: it fails if a dataset is added
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
4. Run `python3 tools/run_tests.py --file test_test_scope` to confirm the scoping
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
