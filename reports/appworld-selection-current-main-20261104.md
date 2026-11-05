# AppWorld selection and ABI increment on current main — 2026-11-04

This worktree starts at the separate recovery increment `5f2bd12`, whose parent
is main `44200fe8a0d7d976b3d65ed816db7a8532ccd706`. Selection therefore has an
explicit dependency on recovery; it is not represented as a standalone patch
against code that does not yet contain that state machine. Main and the older
review worktrees were not modified.

`tools/prepare_appworld_selection.py` creates a fresh v2 selection for review
from the four pinned release rosters, explicitly supplied model/runtime
bindings and complete per-harness budgets. It uses the existing deterministic
pilot caps, validates the same identity contract as recovery, requires a clean
platform commit, checks for identity drift during preparation and refuses an
existing output or an output in either platform checkout. It never starts an
experiment. Declared source hashes do not attest GPU-resident model bytes.

Before the AppWorld adapter is imported, the launcher, campaign validator and
preparation tool require the existing bundle and pool. This avoids triggering
the adapter's implicit data preparation during a dry run. For harnesses with
Python dependencies, the launcher now binds the actual image Python ABI and
requirements SHA to the exact reviewed overlay `PROVENANCE.json`, requiring an
explicit absolute `HG_OVERLAY_ROOT`. A random other-version provenance is not
accepted. The actor image must be an immutable digest before any probe; the
network-isolated Docker Python probe also uses `--pull=never`. File inventories
remain selected-file evidence, not transitive dependency attestation.

Focused no-model validation on this source snapshot:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/quality/test_appworld_selection_prep.py \
  tests/quality/test_appworld_baseline_launcher.py \
  --basetemp=/tmp/hg-selection-current-main-tests
```

Result: **68 passed in 1.85s**. Tests cover four separate pools, explicit
budget/source identity, changed and missing inputs, clean source, no overwrite,
no automatic data generation, missing or mismatched ABI overlay, immutable
image validation and refusal to pull. The preceding isolated branch had an
independent 68-test recheck and a real actor ABI control: 3.12 inventory was
refused and the provisioned 3.11 inventory accepted.

The old `selection-ae0fa07.json` is retained as a historical draft, not reused.
It binds a different platform commit and illustrative, unapproved budgets.
After a final integrated platform snapshot and explicitly reviewed bindings are
available, generate new selection and identity files with this tool. The
campaign's `execution_allowed` indicates recovery eligibility only; it is not
operator authorization. No model, benchmark, data generation, old receipt
modification, main merge or campaign execution was performed in this increment.
