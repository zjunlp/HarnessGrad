# First four onboarding fixes — 2026-11-03

Base: 0a2551b028b5d6e527199458c8ce358a74c7d608.
Parent branch: codex/fix-onboarding-first-four-20261103.

## Conflict scan

| Tasks | Shared boundary | Ruling |
| --- | --- | --- |
| 1 / 2 | README method guide link | Parent adds the link after the method commit; child does not edit README. |
| 1 / 3 | Mock/real model examples and preflight | Real settings must pass the same selected-method checks; no endpoint calls in preflight. |
| 2 / 3 | External method entrypoint/trajectory | Task 2 owns validator and protocol; parent owns harnessgrad/methods.py and driver admission. |
| 3 / 4 | driver consumes recorded H0 | Results task does not modify driver; effective identities come from existing config helpers. |
| 1 | Documentation and executable config | Reuse experiments resolver; no new dependency. |
| 2 | Validator and engine | Missing trajectory fails; legal no-change behavior must match engine. |
| 3 | Readiness and execution | Validate before H0; preserve external/self-contained methods. |
| 4 | Records and exports | Historical missing identity stays unknown; planned/scored coverage stays explicit. |

## Implementation and receipts

- `91d81b34`: external method validation, trajectory contract and walkthrough. Parent
  reviewed the scoped diff; independent method review found no remaining defect.
- The isolated parent worktree holds the quickstart/readiness and results patches;
  no changes have been pushed or merged into the operator checkout.
- The paused mock smoke completed successfully: `demo`, mock backend, `echo_base`,
  rounds 0/1 scored 0.0/1.0, both platform-measured with zero invalid tasks, exit 0
  (115.65 seconds). Receipt/log were preserved outside the source tree in the chat's
  visualization attachment directory. It authorizes no production pilot.

## Final review follow-up

The independent final review identified these cases for a focused repair wave:

1. Include sampling in H0 cache identity, verify the measured policy, and resolve
   relative donor roots before launching a child with a different cwd.
2. Bind `driver --h0-from` to the recipient's effective model/backend/endpoint;
   reject unverifiable donor identity.
3. Preserve legal official eval-only splits without claiming method exposure.
4. Exclude explicitly unmeasured numeric placeholders from report arithmetic while
   retaining their round, reason and coverage; preserve measured zero/legacy scores.
5. Resolve staged rounds before check-only, honor explicit CLI editor overrides,
   preserve the first `.env` assignment, and isolate template-test environment changes.

Parent regressions for item 5 reproduced five failures before the implementation
change. Focused validation is recorded below when complete.

All five groups above are now closed. The final re-review also found that a
historical intermediate H0 consumer's endpoint is not the original measurement's
endpoint. A regression reproduced this acceptance before the final guard. Points
with `shared_from` now require the original donor rather than inferring its endpoint
from the consumer. Unknown historical identities remain refused and preserved.

An actual WSL `bash -n install.sh` found CRLF line endings in the Windows checkout.
The existing attributes file now pins this entrypoint to LF. Its executable content
diff remains the quoted method-command output. Both direct Bash syntax and nested
argument quoting were checked, including the checkout's space-containing path.

## Parent verification

Tests used the existing WSL distro `lightrsi-ubuntu`, mock agent backend, disabled
third-party pytest plugin autoload and bytecode writes, and an external temporary
pytest root under `/tmp`. The existing native pytest installation supplied the
test runner; no dependencies or credentials were installed. Each named file below
lives under `tests/quality/`.

| Focused invocation | Result |
| --- | --- |
| `test_method_readiness.py test_config.py test_readiness_cli.py test_files_interpreter.py` | 44 passed, 97.29 s |
| `test_documentation_commands.py test_documentation_links.py test_eval_coverage.py test_validate_external_method.py` | 34 passed, 18.02 s |
| `test_results_matrix.py test_run_summary.py test_export_harnesses.py` | 46 passed, 15.21 s; final exporter wording tested again below |
| `test_export_harnesses.py test_boundaries.py test_import_direction.py -k 'not publication_preserves_the_reviewed_adapter_inventory'` | 51 passed, 38 subtests passed, 1 deselected, 76.28 s |
| `test_prepare_h0.py test_h0_reuse_identity.py test_scoring_identity.py` | 86 passed, 466.33 s; includes real driver mock runs for same-agent sharing and model/endpoint mismatch refusal |
| Final `test_prepare_h0.py test_h0_reuse_identity.py` after the intermediate-donor guard | 56 passed, 0.97 s, native Windows utility-only tests |
| Final `test_method_readiness.py test_documentation_commands.py test_documentation_links.py` after driver import cleanup | 26 passed, 16.55 s |

Counts overlap and are not a whole-suite total. The first full-driver gate completed
before the final intermediate-donor guard; its direct-donor paths are also covered
by the final helper tests. The guard regression failed before the patch and passed
after it. No protected-tree guard was disabled.

Final static checks: 24 changed Python files compiled without writing bytecode;
`bash -n install.sh` exited 0; Git reports `i/lf w/lf attr/text eol=lf` for the installer;
the staged implementation passed `git diff --cached --check`.

The independent reviewer inspected the scoped implementation, re-ran the synthetic
intermediate-donor probe and installer check, and closed all material findings in
`reports/onboarding-final-rereview-20261103.md`.

## Commits and delivery

- `91d81b34`: external method validator, protocol and walkthrough.
- `174ba19f`: reviewed quickstart, environment reader, early method/editor checks,
  Python/venv launch compatibility, H0 binding and reporting/coverage fixes, with
  regressions and the walkthrough path-placeholder correction.
- This journal and the scoped receipts are recorded in a separate documentation
  commit. Work remains on `codex/fix-onboarding-first-four-20261103`; main and the
  remote were not updated. Unused managed worktrees were archived through the app.

## Environment limits

Native Windows cannot collect driver tests because the platform imports `fcntl`.
Linux-only checks run in the existing WSL distro with temporary pytest roots outside
the repository. The local WSL dependency inventory cannot import `chessbench_puzzles`
(`chess` absent) or `livecodebench` (the available NumPy extension targets Windows
Python 3.10 rather than Linux Python 3.14). This explains the reviewed adapter
inventory test failure; these adapters were not changed by this patch.

The full repository suite and real model/benchmark runs are not claimed as verified.
The earlier expanded resume run's snapshot-sensitive failure was re-run successfully
after workers stopped editing; its original receipt remains documented in the results
report. Vendored FlashRSI escape-sequence warnings under Python 3.14 were unchanged.

Automatic approval rejected deletion of the three native H0 follow-up test roots
under `%TEMP%` (`hg-h0-followup-red`, `hg-h0-followup-green`, and
`hg-h0-followup-green-final`) with the reason `blocked by policy`. They were retained;
the rejection was not bypassed. The final WSL runner's own temporary roots were
automatically removed on normal completion.
