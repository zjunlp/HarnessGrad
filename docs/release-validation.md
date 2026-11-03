# Release onboarding: verification receipt

Worktree: `/tmp/hg-release-onboarding`, branch `codex/release-onboarding`.
Base: `e5ff0c7`. The already delivered custom-split change was brought into this
branch as `5391e48`; it was not merged into the operator's main checkout.

## Scope and resulting behavior

The initial command uses the bundled demo instead of assuming GSM8K is installed.
Mock environment settings are scoped per command; the real recipe explicitly
configures solver and editor. The main research routes are BBEH × DGM,
Terminal-Bench 2, DeepSearchQA and SWE-bench Pro V2. The recipes link pinned
source/preparation requirements, custom splits, fixed-checkpoint evaluation and
result export. The recipes name their upstream sources where a claim depends on one.

Known method prerequisites and selected ordinary judge/environment declarations
are checked before H0. Explicit network probes issue GET requests only and fail
required channels safely. Preflight loads selected declarations rather than
traversing unrelated runtime entries. `--fail-on-incomplete` is supported in
experiment JSON; metadata and summaries distinguish complete, legitimate early
stop and incomplete experiments. The matrix lists explicit incomplete runs as
skipped even when they retain a valid H0 score. Legacy default exit behavior is
preserved when the strict flag is absent.

Sandbox plan 6 mounts a normal venv and its base interpreter read-only at their
own paths. Environments inside hidden `/tmp`, platform or writable work storage
are refused with an actionable message. The original privacy boundary is not
relaxed. New Python/Bash harness and external method entry guides use executable
templates and the same driver.

## Actual public-source preparation

The README's BBEH preparation was run on the real pinned release, not just a
native-shaped fixture. The direct Hugging Face host was unreachable on this
machine, so the same pinned preparation command used the explicit
`HF_ENDPOINT=https://hf-mirror.com` acquisition endpoint.

- Source: `BBEH/bbeh@08e07a803851822c04399782ece3c4a07ce419f9`.
- Complete prepared source: **4520 rows**; SHA256 of derived JSONL:
  `82001d7dc83aa0c6d58a0426cc84ddb143b1b60e776734bbe463750a0721245b`.
- Default behavior exposed a real failure: duplicate questions include conflicting
  answers. The pinned upstream already has an explicit exclude-whole-groups policy.
  The preparation now supports it as `--duplicate-questions exclude`.
- That policy excludes **9 rows in 2 groups**, retaining **4511 tasks** without
  choosing any answer arbitrarily. Source bytes remain complete; the exclusion
  receipt is recomputed and verified at load.
- Seed42 / train-ratio0.2 froze **902 study / 3609 held-out** tasks. The exact
  `rsi-bbeh` recipe passed local preflight with the frozen manifest. Its placeholder
  model URLs were intentionally not probed in this configuration-only check.

[Machine-readable source receipt](../.github/assets/bbeh-preparation.json).
This establishes source preparation and configuration, not a BBEH model score.

## Executed controls

Tests run with no paid API, learned-model inference or large benchmark pilot:

- Real fresh venv, isolated pip-installed marker dependency, namespace read-only
  and hidden-private-tree controls. A separate fresh venv completed editable
  installation, CLI invocation and a complete mock driver run. Its local receipt
  is `/tmp/hg-release-editable-venv-receipt.json`; temporary venvs were reaped.
- Exact `rsi-bbeh` recipe with authored native-format rows, actual DGM entrypoint,
  localhost scripted editor, actual loop candidate, sandbox and private native
  correctness scorer. The platform measured study0→1 and fixed round1 held-out1.
  The editor was called once; held-out evaluation called no optimizer. Those
  instrument outcomes are not public-benchmark or model-capability results.
- Missing editor before H0, broken live endpoints, secret-bearing invalid URLs,
  required judge/environment channels, optional local keys, selected-only runtime
  inspection, valid early stop, strict method failure preserving H0, and strict
  invalid/unmeasured task results.
- Custom frozen partitions, private method feedback, checkpoint pairing and
  source drift, legacy split/config behavior, summaries and matrix exclusions.

The bundled mock demo was also run through the actual driver: scripted-edit
training0→1→1, no-op training0→0 with early stop, and fixed-checkpoint demo eval1.
Both training runs scored four authored study tasks. The
[figure receipt](../.github/assets/demo-curve.json) records source curve hashes
and harness identities; [the figure](../.github/assets/demo-curve.svg) explicitly
says mock/scripted and makes no benchmark claim. Matplotlib is optional and used
only by the figure-regeneration script.

## Recheck commands

Use a fresh external pytest temporary root. Keep protected source files static
while driver tests run: the integrity guard correctly rejects concurrent edits.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/quality/test_release_entrypoints.py tests/quality/test_release_preflight.py \
  tests/quality/test_release_results.py \
  tests/quality/test_release_venv.py tests/quality/test_rsi_recipes.py \
  tests/quality/test_release_rsi_protocol.py tests/quality/test_bbeh_duplicate_policy.py \
  tests/quality/test_custom_splits.py tests/quality/test_readiness_cli.py \
  tests/quality/test_experiments.py tests/quality/test_run_summary.py \
  tests/quality/test_results_matrix.py tests/quality/test_sandbox.py \
  tests/quality/test_shared_dataset_runtime.py tests/quality/test_service_commands.py \
  tests/quality/test_import_direction.py tests/quality/test_split.py \
  tests/quality/test_method_channel.py tests/quality/test_registry_selection.py --basetemp=/tmp/hg-release-review-fresh

python3 tools/datasets_doc.py --check
python3 tools/check_dataset_blockers.py
git diff --check
```

The final combined run completed **267 passed / 1 skipped** in196.13s, including
the selected judge/environment checks, unsafe-URL regression, selected-only
preflight and both external entry routes. Earlier and focused test groups overlap
this run and must not be added as independent coverage. The skip is the
optional external RRSI checkout. Original vendored regex escape warnings remain
unchanged for source-byte fidelity.

The whole `tests/quality` tree was run once more on release commit `c15479b`,
split into four disjoint shards so the wall clock stays inside one working
session: **1515 / 1984 / 1524 / 1696 passed**, **249 / 65 / 79 / 50 skipped**,
and no failures — 6719 passed and 443 skipped in 7162 collected tests. The split
is a timing device, not four samples: every file appears in exactly one shard, so
these numbers are the single-suite result written as a sum. Two rules make a
recheck comparable: one file in one shard, and a fresh temporary root per shard
(`--basetemp=/tmp/pytest-z0` style, outside the checkout). A shard that reports a
failure after the source has been edited with the suite still running is a
measurement of the edit, not of the release: the integrity guard is doing its job.

## What the suite costs, and what CI runs instead

The gate used to run the whole `tests/quality` tree on every push. Measured on the CI runner,
the gating `checks` job takes **14 s** and the advisory full `--fast` suite about **23 minutes
at two workers**; on this host the same suite is about **750 s** over two workers, so the
runner is the slower machine by a factor of three. The shape of both numbers explains the
redesign: one file, `test_appworld_official_scorer.py`, is **1274 s** of the measured total
on its own and is already excluded by `--fast`; three dataset sweeps account for most of what
remains (`test_dataset_classes.py` ≈ 170 s for 33 tests, `test_dataset_controls.py` ≈ 180 s
for 7, `test_qa_dataset_contract.py` ≈ 140 s), and they are expensive for a real reason —
each re-runs classification or an oracle/no-op control *per dataset*, under patched inputs.

They are kept, not deleted: deleting a sweep because it is slow is how a "total and closed"
classification stops being either. What changed is *when* they run. `tools/ci_select.py` maps
a changed path to the tests that can reach it —

| change | selected |
| --- | --- |
| `data/datasets/<x>.py` | `test_<x>_*`, `test_dataset_*`, `test_datasets_doc` |
| `base_harness/<x>/**`, `methods/<x>/**` | `test_<x>_*`, `test_harness_*` / `test_method_*` |
| `tools/<x>.py` | `test_<x>.py` and `test_<x>_*` |
| `tests/quality/<name>` | that file, plus the guards |
| `docs/**`, `*.md`, `.github/**` | the guards only |
| `driver.py`, `eval/**`, `harnessgrad/**`, anything unmapped, >25 files | everything |

— always includes the guard set, and falls back to the full suite whenever it cannot reason
about a change. The `report` job compares against the pull request's base and drops the guards
because the `checks` job already ran them on that commit; a manual run has no base and still
runs everything. So a documentation edit is seconds, an adapter edit is that adapter's tests
plus the guards, and a core edit is the full suite — which is the only case that needs it.

Two of the slow files were also made cheaper without losing an assertion: `test_dataset_classes`
now caches `classify_all()`, `registry.published()` and the CLI's JSON for the tests that ask
the same read-only question more than once. Tests that monkeypatch the vocabulary still call
the tool directly, because a cache is exactly what they must not see.

## Continuous integration

`.github/workflows/quality.yml` runs two jobs on every push to `main` and every
pull request. The `checks` job is deterministic and installs nothing beyond the
interpreter: documentation links and fenced commands, the lanes, the registry and
its declared refusals, this workflow's own references, and the checkout's
cleanliness afterwards (`git status --porcelain` must be empty, so a generator
that writes into the tree cannot turn a green run into a diff nobody reviewed).
It prints how many adapters it could publish instead of asserting the host's 59 —
that count is a function of which optional extras are installed, and a fresh
runner has none; only a *broken* adapter fails the job. The `suite` job runs the
entry point a contributor runs, `tools/run_tests.py --fast --jobs 3`, which keeps
CI from drifting into a second selection of tests.

What CI does not do is the reason to keep reading it carefully: no benchmark
images, external checkouts, model endpoints or judge services are provisioned, so
the files that need them skip, and `--fast` drops the seven measured-slow files.
Those are covered by the four-shard run above, which remains a local gate. A green
badge is evidence about the deterministic surface on a clean machine — not a claim
that the platform was measured end to end.

## Practical limits

The five-task recipes are bounded integration smokes. They are not full official
evaluation, statistical evidence for an improvement, or numerical paper
reproduction. DGM's port deviations remain declared. Native TB2/ProV2 images and
DeepSearchQA search/judge services must be explicitly provisioned; none is
silently downloaded or called by a model-free check. No production benchmark
authorization was granted, no old run receipt was overwritten, and no changes
were merged or deployed from this worktree.
