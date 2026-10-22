# AgentDojo selected metadata privacy review — Codex 180

## Metadata review closure and production boundary

The code-solvable metadata-only privacy/detection action is closed for selected
`ENV`, `SETUP` and `VERIFY` lookup. The separate production route remains blocked.
This review establishes no production `run_one` or H0 readiness, case
materialization, runtime enforcement, verifier trust, cleanup or benchmark score.

The registry has no closed status. The completed `selected-metadata-review`
action is therefore retired from the active blocker list, with its closure
evidence attached to the remaining `production-route-admission` runtime blocker.
The Codex 164 reassessment remains evidence for the unresolved route; its
historical controls have not been authenticated or rerun.

## Synthetic privacy and detection evidence

`tests/quality/test_agentdojo_metadata_privacy.py` reads only adapter Python
source before installing the request guards. Every control executes a fresh
isolated module body under those guards, with an inert QA dependency stub,
five synthetic IDs and source paths beneath the current worktree. No synthetic
data file is created. This is a module-body control with a dependency seam, not
an integration test of real imports or the production runner.

Valid selected lookups at IDs 0, 2 and 4 match fixed metadata fields and the
selected setup/input path identities. `VERIFY.argv` remains opaque and is never
inspected or executed. Metadata map keys use only the synthetic fixed count.
Negative, out-of-range, noncanonical and foreign IDs raise `KeyError` with no
read fallback. All successful and rejected lookups record zero guarded reads.

Guards cover `builtins.open`, `io.open`, `os.open`, `Path.open`, `Path.read_text`,
`Path.read_bytes`, `json.load` and `json.loads`. The catalog/task/reference seams
`records`, `load`, `control_task`, `oracle_files` and `_task` are denied and
recorded. For each metadata map, deliberate synthetic leak controls exercise
all 13 seams and require the exact detection exception and a single recorded
attempt before any underlying hook or I/O can run. Non-metadata imports,
split classification, sockets and subprocesses are also denied.

These controls guard the tested interfaces; they are not an OS sandbox or a
proof against every possible Python I/O mechanism. Source dependencies and
production route evidence remain outside this review.

## Validation

Run from `/tmp/hg-codex-180`:

```sh
mkdir -p .scratch/metadata-codex180
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  -p no:cacheprovider --basetemp=/tmp/harnessgrad-pytest-metadata-codex180-pytest \
  tests/quality/test_agentdojo_metadata_privacy.py \
  tests/quality/test_dataset_blockers.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_dataset_blockers.py
git diff --check
```

The combined suites passed 109 tests: 63 metadata controls and 46 source-only
registry regressions. The checker reported valid coverage of 28 datasets,
8 shared blockers and 11 historical records, with `production_authorized: false`.
The registry regression explicitly preserves the production runtime blocker.

An initial guard setup failed because a global import guard also intercepted
pytest's own monkeypatch machinery. The guard was restricted to the isolated
adapter's builtins namespace, then the metadata suite passed. That setup failure
was not a dataset measurement or a production receipt.
The first combined-suite attempt also lacked the parent scratch directory for
pytest's temporary root; creating that directory resolved the setup error.

## Scope

Changes are limited to focused tests, registry documentation, this unique report
and a journal entry. No adapter/data file was changed. No catalog, `cases.json`,
task prompt or gold/action payload was read. No runtime, verifier command,
network, attack, model/H0 or production runner was invoked; main was not edited.
