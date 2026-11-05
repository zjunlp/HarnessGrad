# Adapter integration and evidence

For current adapter requirements and validation steps, see [dataset onboarding](dataset-onboarding.md), [adapter specification](adapter-spec.md), and the dataset-specific documentation under `data/datasets/`. Current known blockers and their evidence are maintained in [`registry/dataset-blockers.json`](../registry/dataset-blockers.json).

The old full journal and lifecycle-review snapshots have been retired from this checkout. Historical line-number quotations in retained records describe those retired sources, not this page. Unavailable experiment receipts remain explicitly unverified; a historical quotation does not establish a new measurement.

A new adapter should document its source version and licensing, split, scorer identity, trusted/private data boundaries, runtime requirements, resource and network assumptions, and validation coverage. Synthetic or mock controls are useful interface tests; they do not establish benchmark scores or authorize production runs. Any reported measured result must retain a verifiable run identifier, tested harness tree identity, and its underlying receipt.

### Dataset loading gets an independent provider environment (2026-11-05)

Commit `e79c621f` adds the opt-in runtime/provider path; `759904a5` separately
documents a seeded platform venv and activated `python` commands. GSM8K is the
first migrated adapter. Its static declaration pins Python 3.12 and
`pyarrow==23.0.0` without importing its loader in the platform. Explicit
`python tools/prepare_dataset.py --dataset gsm8k` builds a dedicated external
venv at its permanent physical path, verifies its installed package set and file
inventory, then atomically publishes its identity. Ordinary checks never install,
repair or fall back to the platform's packages. A changed runtime fails admission.

The [runtime manager](../harnessgrad/dataset_runtime.py) and
[provider client](../harnessgrad/dataset_provider.py) preserve the registry's
existing APIs. The trusted worker returns public tasks separately from private
answer/verifier data. QA SETUP, VERIFY and ENV mappings construct values only for
selected tasks, including an empty selection. Fetch, lint and generated metadata
discovery avoid importing the opted-in adapter in the platform. The README now
includes preparation between fetching and running. Download checks inspect pip
entries in the selected runtime; JSON checks return a nonzero exit status when
anything is missing, including for legacy adapters.

Validation is indexed by the retained
[receipt](/home/wangyenan/.cache/hg-provider-acceptance-20261105-nvi2zkkh/validation-summary.json).
A fresh seeded platform install had no pyarrow before or after preparation. The
fetch command verified already available pinned GSM8K files; this was not a fresh
network transfer. The provider loaded all 8,792 tasks (7,473 train, 1,319 eval),
and catalog, split, private fallback answers and one selected declaration matched
the local adapter path by canonical hashes. A separate no-pip/no-pyarrow platform
fixture exercised authored Parquet rows, selected/empty resolution, private-input
separation and poisoned import-path rejection. Its actual sandboxed driver control
ran one authored task and one trial with a fixed answer: score 1, zero invalid or
harness-failed tasks and zero model calls. This is an instrument check, not a
measurement on released GSM8K tasks.

The final focused regression passed 338 tests and 38 subtests; 120 data-dependent
cases skipped, and 16 cases were deselected (12 requiring the absent chess package
and four failures reproduced on clean `ea05e260`). Installed-wheel tests separately
passed all three checks, including exact provider resources and offline synthetic
scoring. Seven additional dataset-control failures also reproduced on that clean
baseline because unrelated datasets or runtimes were unavailable. Full-suite
success is not claimed. The final regression command and JUnit report are retained
at `/tmp/hg-env-regression-complete-20261105-tzs_to47/command.json` and
`/tmp/hg-env-regression-complete-20261105-tzs_to47/junit.xml`. The live integration
can be repeated after explicit preparation with
`HG_TEST_DATASET_RUNTIME_ROOT=/absolute/prepared/store python -m pytest -q tests/quality/test_dataset_provider_integration.py`
and an external `--basetemp` directory; the tests themselves do not install or fetch.

This increment isolates trusted loader dependencies. It does not seal host system
libraries or the entire base standard library, migrate the other adapters or
service/scorer/Harness environments, or bind runtime identities into H0, caches
and resume comparisons. Direct-adapter diagnostic scripts still require their
own dependency environment. Retained manifests, logs, JUnit reports and the
authored control curve describe those scopes. Temporary fixture directories,
validation venvs and the baseline worktree were removed; cleanup is recorded in
the receipt. No model endpoint or benchmark pilot was invoked.

Follow-up review found that unexpected loader exceptions were reported as
unavailable, allowing lint to pass a broken adapter. Provider loading now reports
those exceptions as invalid; explicit missing data/configuration and missing
runtime handling keep their existing behavior. Ten authored QA rendering cases
cover code errors and missing prerequisites with and without
`--allow-unprovisioned`: six failed before the fix and all ten pass afterwards.
The focused provider, protocol, QA, registry and lint-readiness suite passed 133
tests. Commands, logs and JUnit reports are retained under
`/tmp/hg-provider-readiness-fix-20261105-uh0hn7m9`; fixture directories were cleaned.
No dependency installation, benchmark run or model call was needed.

A second review found the same downgrade in the provider client. Identity,
protocol and unexpected worker errors now remain invalid. Timeout and local
startup failures retain unavailable status and their error type, and lint fails
these exchanges instead of treating them as missing preparation. Runtime failures
preserve their original status and scope, including changes after readiness or
during catalog/resolve. Explicit missing prerequisites remain skippable.
Real subprocess controls cover wrong identity, malformed JSON, startup crashes,
unsuccessful exits, worker exceptions, timeouts and missing interpreters, both
with and without `--allow-unprovisioned`. The focused suite passed 172 tests;
18 of the new regression cases failed before the fix. Commands, logs and JUnit
reports are retained at `/tmp/hg-provider-client-fix-20261105-zh3md5wy`; temporary
fixtures were cleaned. These controls installed no dependencies and used no model.
