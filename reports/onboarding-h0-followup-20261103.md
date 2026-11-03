# H0 final-review follow-up — 2026-11-03

Implemented in the current isolated worktree, without git writes. Source and test edits were frozen after the utility green gate. Parent owns final Linux full-driver verification; this worker did not run a broad suite or a model/benchmark pilot.

## Changes

- `tools/prepare_h0.py`: include `sampling` in H0 configuration identity, so `random_1` and `all` cannot share a cached baseline when no explicit study IDs are supplied. Verify requested sampling against all three measured declarations: curve `sampling.policy`, metadata `sampling_policy`, and `config.run.sampling`. Missing or conflicting declarations refuse publication/reuse. Resolve `--runs-root` immediately after parsing, before parent-side filesystem work or child launch under the repository cwd.
- New `harnessgrad/h0_identity.py`: a small shared `agent_problems` helper compares requested model/backend/endpoint with donor `run_meta.json` → `config.agent`, and model/backend with the explicitly platform-measured curve identity. Missing legacy metadata or fields remain unknown and refuse reuse. Explicit mock facts (`model: null`, empty endpoint) remain valid when actually recorded. Error messages name mismatching fields without printing values or credentials.
- `driver.py`: import the shared helper and call it inside the `--h0-from` branch before accepting the donor payload or copying its traces/identity. The recipient's effective `platform_config.current().agent` supplies the requested binding. Existing harness-tree and scorer checks remain.
- `tests/quality/test_prepare_h0.py`: add sampling collision/mismatch/missing-evidence and relative-root regressions; make synthetic reusable receipts include measured sampling declarations. The hint fixture now refuses any unexpected measurement launch.
- New `tests/quality/test_h0_reuse_identity.py`: real synthetic donor metadata/curve files exercise matching identity, recipient model/backend/endpoint mismatches, missing legacy facts, inconsistent/unverified curve identity, and explicitly recorded mock absence values. No AST branch extraction or duplicate production logic is used.
- `tests/quality/test_scoring_identity.py`: update the exact configuration expectation/fixture for sampling and measured agent evidence. Add two full-driver mock regressions for recipient model/endpoint changes. Both explicitly pass a `shlex.join`-quoted absolute noop method entrypoint and `--improver none`; neither relies on the shared fixture's unquoted default path.

No readiness, configuration declaration/parser, export, matrix or summary files were changed by this worker in this follow-up. The parent concurrently owns its separate `driver.py` readiness adjustment.

## Regression-first evidence

Windows PowerShell prefix:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
```

RED command:

```powershell
python -m pytest -q tests/quality/test_prepare_h0.py tests/quality/test_h0_reuse_identity.py -k 'sampling_changes or measured_sampling or relative_runs_root or recipient_agent or matching_recorded or unknown_legacy or curve_must or explicit_mock' --basetemp="$env:TEMP/hg-h0-followup-red"
```

Result: **20 failed, 35 deselected in 2.41s**. Six failures reproduced existing behavior: sampling configurations compared equal; four mismatched/missing sampling declarations were accepted; relative `runs` was forwarded unchanged to a child with another cwd. Fourteen helper tests failed because the new shared helper did not exist yet.

Initial GREEN command:

```powershell
python -m pytest -q tests/quality/test_prepare_h0.py tests/quality/test_h0_reuse_identity.py --basetemp="$env:TEMP/hg-h0-followup-green"
```

Result: **54 passed, 1 failed in 4.21s**. The old reuse-hint fixture overwrote its complete `config` with an agent-only dictionary, removing the newly required `config.run.sampling`. Correct refusal then caused that fixture to attempt a driver subprocess. Native Windows stopped immediately at missing `fcntl` during import; the runtime/model/dataset execution was never reached. The fixture was repaired to use the complete synthetic receipt and now explicitly fails if any new measurement is launched.

Final GREEN command:

```powershell
python -m pytest -q tests/quality/test_prepare_h0.py tests/quality/test_h0_reuse_identity.py --basetemp="$env:TEMP/hg-h0-followup-green-final"
```

Result: **55 passed in 3.70s**.

Static checks:

```powershell
git diff --check -- tools/prepare_h0.py driver.py tests/quality/test_prepare_h0.py tests/quality/test_scoring_identity.py
python -c 'from pathlib import Path; paths=["tools/prepare_h0.py","driver.py","harnessgrad/h0_identity.py","tests/quality/test_prepare_h0.py","tests/quality/test_scoring_identity.py","tests/quality/test_h0_reuse_identity.py"]; [compile(Path(p).read_text(encoding="utf-8"),p,"exec") for p in paths]; print("Syntax checked",len(paths),"H0-scoped files")'
```

Result: **both exit 0**; six source/test files compiled without bytecode writes.

## Remaining verification and compatibility

- Parent will run all of `tests/quality/test_scoring_identity.py` on Linux after every worker freezes source, covering existing same-agent H0 sharing plus the two new recipient mismatch cases. This worker makes no full-driver green claim.
- Unverifiable legacy donor identity is deliberately refused. Existing legacy payload reconstruction remains available only when donor effective agent metadata and measured curve identity establish the requested binding; no receipts were rewritten.
- Existing task/split/trial controls and scorer checks remain. This follow-up does not change scoring or authorize any pilot.
- Cleanup of the three owned Windows `%TEMP%/hg-h0-followup-{red,green,green-final}` pytest roots was attempted with native PowerShell after validating resolved containment under `%TEMP%` and refusing reparse-point roots. Automatic approval review rejected the recursive removal command with `blocked by policy`; no deletion ran. These disposable test roots remain for parent cleanup. No attempt was made to bypass that rejection.
