# First four onboarding fixes implementation plan

> **For agentic workers:** Use the existing worktree, regression tests first, and review each scoped diff before integration.

**Goal:** Make the documented first run, external method contract, preflight, and exported measurement identity agree with the execution path.

**Architecture:** Extend the current standard-library helpers and record readers. Keep dataset admission, scoring, and the external method subprocess boundary unchanged. Preserve historical records with explicit unknown values rather than inventing metadata.

**Tech Stack:** Python >= 3.10, pytest, Linux/bubblewrap for execution checks.

**Spec:** The four findings accepted in this chat; the user requested fixes following repository conventions.

## Global constraints

- No third-party platform runtime dependency, paid endpoint, real benchmark pilot, or scorer changes.
- Preserve operator commits, historical receipts, unknown fields, and secret redaction.
- Tests and harness work roots must be outside the source tree; remove temporary test trees.
- Fix existing responsibilities without unrelated refactoring or a second configuration system.
- Reference/no-op methods and methods with their own editor must remain usable without HG_METHOD_*.
- Invalid outcomes and unmeasured coverage remain distinct recorded facts.

## Review focus

- A checkout path containing spaces and a machine exposing python3 but no python alias.
- An external method directory and a response missing its required trajectory.
- Missing absolute method entrypoints rejected before any baseline measurement.
- H0 reuse after effective model, endpoint, or backend changes.
- Training/custom-heldout/partial measurements retaining their identity outside a run directory.

## Task 1: Runnable quickstart and configuration template

Files: README.md, .env.example, install.sh only if its suggested demo needs alignment; existing configuration tests if needed.

- [x] Verify the template through the real load_env function and retain a regression for numeric values.
- [x] Make system prerequisites and editable installation consecutive steps; state Linux/WSL2 requirements.
- [x] Use the checked-in experiment resolver for the mock demo, with demo/all/noop or echo_base and scoped mock environment variables.
- [x] Give the real example complete local agent settings, data preparation/dependencies, and a named editor configuration when a published method is chosen.
- [x] Run documentation checks and the smallest no-model command sequence the environment supports.

## Task 2: External method contract and validator

Files: methods/protocol.py, tools/validate_method.py, focused validator tests, docs/bring-your-own-method.md, README navigation only at integration.

- [x] Add failing regressions for an external absolute directory and missing/empty trajectory.
- [x] Display external paths safely; use the real engine trajectory requirements, retaining legal no-change responses.
- [x] Correct the evidence path to base_harness/_harnessgrad and provide a small walkthrough using existing reference methods rather than a redundant framework.
- [x] Run focused tests, inspect the diff, and commit only this task's files in its isolated worktree.

## Task 3: Early method/editor readiness

Files: harnessgrad/methods.py, harnessgrad/readiness.py, driver.py, tests/quality/test_readiness_cli.py and narrowly related tests.

- [x] Add failing tests for a missing absolute method script, a valid quoted external script, and an unconfigured built-in editor method.
- [x] Reuse one entrypoint check in preflight and the real driver; run static validation before H0.
- [x] Check only the editor prerequisites the selected built-in method requires, without assuming every external method needs the shared editor.
- [x] Keep preflight free of model calls, installs, and output-directory creation.
- [x] Run focused readiness/configuration and entrypoint tests.

## Task 4: H0 and result identity

Files: tools/prepare_h0.py, tools/export_harnesses.py, tools/results_matrix.py, tools/run_summary.py, harnessgrad/loops/eval_side.py, focused tests.

- [x] Add failing regressions for --model propagation and effective model/backend/endpoint cache identity, plus a usable donor root hint.
- [x] Label exported scores from side/score_kind, including historical unknowns.
- [x] Preserve split protocol/hash, trials, seed, baseline problems, source checkpoint, and scorer identity in matrix JSON; make custom/partial/noncomparable conditions visible in Markdown.
- [x] Keep planned eval task IDs separate from scored IDs so exported coverage reflects the requested set; do not redefine invalid outcomes as ordinary failures.
- [x] Run the existing export/summary/matrix/H0 tests plus focused regressions; commit only this task's files.

## Integration and verification

- [x] Review scoped commits and integrate them onto the parent fix branch without modifying main.
- [x] Run the repository's selected test lane and documentation checks with external basetemp; record environmental limitations accurately.
- [x] Obtain an independent whole-diff review and resolve material findings.
- [x] Record commits, commands, results, and remaining limitations in reports/onboarding-first-four-20261103-journal.md.
