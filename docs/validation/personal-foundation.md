# Personal foundation validation

## Stage B review fixes on 2026-11-18

The Windows 11-file command and four-file Node command below were rerun on the
reviewed working tree. The WSL run used the five-file command plus
`tests/quality/test_personal_model.py`, with `-rs` and the checkout mapped into
an external test environment so its CLI subprocess could import the package.

| Check | Actual result |
| --- | --- |
| Windows CPython 3.12, 11 files | `229 passed, 2 skipped, 15 warnings, 478 subtests passed in 57.92s` |
| WSL, six files | `159 passed, 1 skipped in 41.39s` |
| Node, four files | `27 passed`, zero failures or skips |

The cancellation fixture waits just before candidate call reservation, accepts
cancellation, then resumes the worker. Only the original planning call is
recorded and transport attempts remain zero. A separate fixture checks that the
frozen question and answer reach both plan and candidate requests. A repeated
question after the one allowed reply ends the job without another open question.
An isolated Chrome check reopened that saved receipt and confirmed that no
reply, cancel, or Diff control appeared. Two scripted callbacks, zero real model
calls, and unchanged `AGENTS.md`. The Windows skips are the POSIX symlink/FIFO
case and a chmod check bypassed by the current account. WSL skips the
Windows-only filesystem controls. The prior native Rust and desktop results
below are historical for this review fix.

## Windows floating window hand-off on 2026-11-18

The latest Windows development build opened against the existing scripted
service on one monitor at 125% scaling. The user reported passing all four
manual checks: the taskbar icon and short prompt fit, the first proposal with
clarification and Diff, cancellation and history, and minimum size plus close
and draft handling. This is a manual report. The same local state independently
showed a ready, unapplied `AGENTS.md` proposal with `plan`,
`plan-clarification`, and `proposal` calls, and zero real model calls. The draft
revision survived reopening, but the stored text was empty when checked.

The locked native build passed 12 Rust tests. An isolated Chrome layout check
using a simulated native bridge at 125% verified the 440 × 560 logical pixel
ordinary client area, including input, submit, and clarification controls.
The earlier offline cancellation check verified that a cancelled receipt had
no Diff and did not change the source file. Multi-monitor, DPI switching, and
live models are outside this hand-off test.

## B5 and B6 completion on 2026-11-18

The B2–B6 working tree is based on `6996606b`. Repeat the 11-file Windows
and five-file WSL pytest commands below in a fresh external scratch directory.
Use `-rs` with the Windows selection to show the two platform-specific skips.
The four-file Node command below covers the production page controllers.

| Check | Observed result |
| --- | --- |
| Windows CPython 3.12, 11 files | `227 passed, 2 skipped, 15 warnings, 478 subtests passed in 48.89s`. POSIX symlink/FIFO and chmod permission tests skipped; the Windows junction, hardlink, stream and write-sharing controls passed. The existing warnings come from the console UAT and vendored extractor. |
| WSL, five files | `138 passed, 1 skipped in 29.08s`. The skipped check covers Windows-only filesystem controls. |
| Node, four files | `26 passed`, zero failures or skips. Includes resuming from the last event ID and aborting an obsolete stream. |
| Windows Chrome, 412 × 780, native Windows service | Feedback submitted, one SSE connection dropped, question received on reconnect, reply resumed the same task, native `AGENTS.md` Diff opened and reopened after reload. A second receipt was cancelled without a Diff. Four scripted callbacks, zero transport attempts, source bytes unchanged. |

The service-level tests also accepted identical reply replays without charging
extra calls, rejected stale snapshots before a new callback and stopped later
stages after cancelling an in-flight callback. The schema v6 migration preserved
an older ready receipt and its Diff. The browser used a synthetic project and a
scripted callback behind the production page and API. No actual project was
modified, and the Windows Tauri shell was not rerun for B6.

Run the native, read-only loading diagnostic using an explicit executable and a
new destination outside the checkout:

```powershell
python -X utf8 -B -m tools.diagnostics.probe_personal_surfaces --codex "$codex" --output "$scratch/native-load"
```

The isolated app-server reported `status=measured` on Windows Codex `0.169.0`
with two completed fresh threads, four scripted loopback requests and zero real
model calls. Each thread's request contained the expected revision of both the
selected `AGENTS.md` and standalone `SKILL.md` markers, without the other
revision. The adapter independently checked both original snapshots. This
measures explicit loading on this executable and fixture only; it does not
verify daily sessions or measure behavior after applying a proposal.

## Integrated B2–B5 and first B6 flow on 2026-11-18

The shared validation tree uses A+B1 commit `6996606b` plus the local B2–B4
changes and B5 source/test blobs from `c1b1d6b6`. Before adding B6, a WSL
integration probe collected a project instruction and standalone Skill, generated
a B4 logical candidate and used the real B5 adapter to render one native Skill
change. Two scripted callbacks ran, the transport count stayed zero and both
source files were unchanged. The four-file WSL suite reported
`128 passed in 19.26s`. The selected Windows suite reported
`172 passed, 36 skipped, 15 warnings, 478 subtests passed in 46.21s`.

After adding B6, run the following from the checkout with a fresh external
`$scratch` directory. The Windows selection includes the platform guard suites:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -X utf8 -B -m pytest -q -rs --tb=short -p no:cacheprovider --basetemp "$scratch/final-windows" `
  tests/quality/test_personal_feedback.py tests/quality/test_personal_planning.py `
  tests/quality/test_personal_proposals.py tests/quality/test_personal_codex_adapter.py `
  tests/quality/test_personal_proposal_flow.py tests/quality/test_personal_foundation.py `
  tests/quality/test_personal_drafts.py tests/quality/test_personal_model.py `
  tests/quality/test_import_direction.py tests/quality/test_improvement_import_isolation.py `
  tests/quality/test_test_lanes.py
```

The WSL selection runs the adapter and feedback-to-Diff suites:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -q --tb=short -p no:cacheprovider --basetemp "$scratch/test" \
  tests/quality/test_personal_codex_adapter.py tests/quality/test_personal_proposals.py \
  tests/quality/test_personal_planning.py tests/quality/test_personal_feedback.py \
  tests/quality/test_personal_proposal_flow.py
node --test console/static/personal-draft.test.js console/static/personal-window.test.js \
  console/static/personal-feedback.test.js console/static/personal-proposal.test.js
```

| Check | Observed result |
| --- | --- |
| Windows CPython 3.12, the 11 files above | `185 passed, 39 skipped, 15 warnings, 478 subtests passed in 52.10s`. All skips require B5 safe filesystem reads. The warnings are the existing console UAT and vendored extractor escapes. |
| WSL CPython 3.12, the five files in the command | `134 passed in 32.18s`, no skips. Includes request scoping, idempotent start, busy worker, abandoned-task status, question display, stale-source rejection and v4 migration. |
| Frontend controllers | `24 passed`, zero failures or skips. |
| Windows Chrome, 412 × 780 viewport with the WSL loopback service | The production 小绿 page submitted feedback, displayed the B5 native `AGENTS.md` Diff and reopened the same Diff after reload. Two scripted model calls, zero transport attempts, original file byte-for-byte unchanged. |

Each invocation used a separate external state and pytest working directory.
The browser run used an external fixture harness and an injected scripted model;
it used the normal page, API, worker and filesystem adapter. No benchmark,
real model, native Codex load, Windows native-window Diff test or write was run.

## B4 logical candidate checks on 2026-11-18

Run from the checkout with an external `$scratch` directory:

```powershell
python -X utf8 -B -m pytest -q --tb=short -p no:cacheprovider `
  --basetemp "$scratch/final" `
  tests/quality/test_personal_proposals.py tests/quality/test_personal_planning.py `
  tests/quality/test_personal_feedback.py tests/quality/test_personal_drafts.py `
  tests/quality/test_personal_model.py tests/quality/test_personal_foundation.py `
  tests/quality/test_improvement_model.py tests/quality/test_improvement_import_isolation.py `
  tests/quality/test_shared_improvement_example.py tests/quality/test_import_direction.py
```

Windows CPython 3.12.10:

```text
172 passed, 15 warnings, 478 subtests passed in 40.23s
```

No skips. The warnings are the pre-existing invalid escape sequences in the
UI smoke script and vendored FlashRSI extractor. With the same pytest options
and only `tests/quality/test_personal_proposals.py`, WSL CPython 3.12.16 reported
`17 passed in 5.83s` with no skips.

B4 coverage includes in-place rule and Skill changes, CRLF preservation on
append, duplicate/ambiguous edits, project/job/plan binding, exhausted budgets,
concurrent requests, forged Diff rejection and the publication/commit gap. A
separate WSL probe loaded the B5 adapter from `c1b1d6b6`, selected an instruction
and a standalone Skill from a temporary project, then rendered the complete B4
mapping. It produced one native Skill change, made two scripted model calls
with zero transport attempts, and preserved both source files byte for byte.
This covers read-only rendering compatibility. Desktop/worker connection,
live model quality, loading, behavioral testing and adoption are open.

### B4 review

Recovery now requires the exact digest recorded before publication and the
corresponding completed proposal call. The review exercised a digest-valid
file left after an invalid reply, interrupted writes before and after
publication, legacy committed references and reads during an unrelated writer
transaction. Replacement tests also cover mixed reply line endings on CRLF
files and rejection of anchors that split a CRLF pair.

The ten-file Windows command above, with `--basetemp "$scratch/windows"`, reported
`178 passed, 15 warnings, 478 subtests passed in 45.75s`. The warnings are the
same existing warnings listed above. The WSL B4-only command with the same
pytest options and its own external `--basetemp` reported `23 passed in 4.78s`.
Both runs had zero skips. The B5 read-only probe again rendered one native
Skill change from two selected files, with two scripted calls, zero transport
attempts and unchanged source bytes.

## B3 planning checks on 2026-11-18

B3 review fixed input rebinding after failed calls, replay after job completion or
budget exhaustion, and the publication/database interruption gap. Cached records
now revalidate their full input, target set, fixed checks and event metadata.
Credential checks inspect decoded strings, including escaped quotes/backslashes.
A request with no failure observation can ask for context without claiming a
reproduction. Record tests cover Windows long paths, links/junctions, ambiguous
outputs and process exit immediately after publication.

The current Windows CPython 3.12.10 regression result is

```text
145 passed, 15 warnings, 478 subtests passed in 45.41s
```

The reviewed CLI fixture completed with one scripted call and zero real model
calls. Reopening its completed job returned the same record with zero further
calls, and the project sentinel remained unchanged. Raw commands, source digests
and results are retained in the external `b3-review` receipt directory.

The first extra Linux pass had 142 passes and three environment failures. Its
interpreter was a symlink refused by two host-probe fixtures, and the CLI package
was not installed for a subprocess running outside the checkout. B3's 38 tests
passed in that run. A physical-interpreter environment with an editable package
installation is a separate validation setup; the Windows result above is the
completed review acceptance.

### Initial B3 run

B3 is developed on `codex/personal-planning-b3` in its own registered worktree.
It inherits an exact snapshot of the uncommitted B2 files at `6996606b`.
The original B2 checkout's 19 changed/new files were hash-checked after testing
and remained unchanged. The independent B5 adapter branch was not integrated.

Windows CPython 3.12.10 checks ran in an isolated copy of the existing test
interpreter, with its import path bound to the B3 checkout. Test data, model
budgets and immutable plan records were created outside the checkout.

```text
128 passed, 15 warnings, 478 subtests passed in 27.43s
```

The executed suite covered personal planning, feedback, drafts, bounded model
sessions, the A foundation, improvement import isolation and import direction.
The warnings are invalid Python escape sequences in two unchanged source files.

```bash
python -X utf8 -B -m pytest -q --tb=short -p no:cacheprovider \
  --basetemp=<external-test-root>/pytest-acceptance \
  tests/quality/test_personal_planning.py tests/quality/test_personal_feedback.py \
  tests/quality/test_personal_drafts.py tests/quality/test_personal_model.py \
  tests/quality/test_personal_foundation.py \
  tests/quality/test_improvement_import_isolation.py \
  tests/quality/test_import_direction.py
```

The explicit `personal plan-demo` CLI completed a real local B2 feedback
submission and B3 plan publication using one scripted model reply. Its result
was `plan_ready`, `fixture=true`, `verified=false`, `applied=false`,
`scripted_calls=1`, `real_model_calls=0`. The selected project's sentinel file
remained unchanged. Tests also covered questions/no_change, safe target selection,
cross-project refusal before dispatch, original evidence labels and truncation,
immutable-record corruption, retries after database reopen and shared budgets.

This completes the B3 backend's offline scope. The plan describes a future
comparison and fixed checks. B4 candidate generation, B6 worker/API/UI reply
handling and C's actual reproduction, retest and adoption still need integration.

## Historical A+B1+B2 scope

Scope: the personal A+B1+B2 implementation, checked on Windows on 2026-11-17/18.
A+B1 was committed as `6996606b` on the shared-foundation base `17fa6ae0`;
B2 is developed separately on `codex/personal-feedback-b2`. The implementation
provides a local service, desktop controls, synthetic imports, persistent drafts,
bounded model sessions and explicit feedback with selected evidence. It does not
yet provide planning, candidate generation or the feedback-to-adoption workflow.

## B2 checks on 2026-11-18

| Check | Result and scope |
| --- | --- |
| Focused Python and import direction | 109 passed, 0 failed, 15 existing warnings, 478 subtests passed, 32.52 s |
| Frontend controllers and page integration | 21 passed, 0 failed/skipped/cancelled, 182.3387 ms |
| Documentation and packaging declaration | 29 passed, 1 Windows-incompatible path test deselected, 2.56 s; isolated publication snapshot |
| Browser interaction | Selected a source substring and tool observation, removed an extra excerpt, searched the session, submitted and reopened the actual persisted receipt |
| Browser service restart | Normal stop and recreation at a new port restored the same feedback ID, two excerpts and Chinese draft; only the owned fixture service was stopped |
| Small-window geometry | Browser measurement at width 382 required about 280.3 px including native padding, within the existing 288 px input height; not native focus/IME validation |
| Native feedback submission | Manual Windows operation at 125% scaling recorded an explicit request without evidence and a correction with one whole synthetic user event; receipts and Chinese drafts were verified in SQLite |
| Native restart data | Normal stop and recreation changed port 7373 to 9404; project-scoped API responses and all persisted record tables matched before/after, including three feedback IDs, one evidence attachment, two drafts and five source events; the reopened shell reported visible |
| Native recovery, layout and IME | The user confirmed that the reopened feedback/evidence and Chinese draft restored, feedback type/save/submit controls displayed normally, and Chinese selection did not trigger application Enter/Escape actions |

The B2 native acceptance covers these Windows development-sample flows. Native
substring selection/removal and sequence-bound interaction have not been
independently confirmed in this pass; their browser/offline coverage above is
separate. Native command diagnostics reported no failed actions during the manual
feedback test. Cross-project receipt access was refused with HTTP 400. The earlier
two zero-evidence submissions were retained as historical test records.

Python checks cover frozen v1/v2/v3 migration preservation and rollback, scoped
FTS5/session fallback, late corrections in a 200-event session, literal search,
Unicode excerpts, invalid/forged selections, request idempotency and API guards.
Page integration also covers UTF-16-to-Unicode conversion and preserving a known
submission acknowledgement when refreshing history fails. All model responses
remain scripted; B2 itself made zero model calls and changed zero harness files.
B5's shared contracts, Codex adapter, model configuration/calls and native shell
source were unchanged. Feedback imports remain explicitly synthetic.

## Historical A+B1 checks

| Check | Result and scope |
| --- | --- |
| Focused Python after test consolidation | 69 passed, 0 failed, 11.46 s; model, draft, foundation and transport/deadline tests |
| A+B1 frontend controllers | 16 passed, 0 failed, 0 skipped/cancelled, 148.7933 ms; scripted native bridge and DOM fixtures |
| Documentation publication checks | 29 passed, 1 Windows-incompatible path test deselected, 7.47 s; documentation links/commands, test lanes and packaging declaration |
| Broader offline selection before test consolidation | 361 passed, 34 deselected, 15 warnings, 478 subtests passed, 74.07 s; included customization, configuration, endpoint and CI guards |
| Historical native-shell unit controls | 12 passed, 0 failed/ignored/filtered, 0.90 s; placement, diagnostics and generated command permissions |
| Historical Windows development build | Passed; subsequent branding build took 13.40 s; not a packaged release |
| Historical controlled Codex probe | 52 passed, 462 subtests passed, 0 skipped, 40.25 s; included an explicitly selected native Codex 0.154.0 and synthetic model endpoint |

These are different selections and source revisions; their counts must not be
added. The broader run preceded test consolidation. The A+B1 focused run
verified the consolidated tests, including v1/v2 migration preservation/rollback,
quota recovery and draft conflict handling. Runtime code was unchanged by that
consolidation. Native results are historical observations, not a new native test
of each later Python or frontend edit.

The broader Windows selection excluded POSIX no-follow customization operations,
POSIX venv/Linux method sandboxes, experiment-help paths importing `fcntl`, and
Windows-incompatible Bash/path controls. They remain unverified here. The 15
warnings came from existing invalid escapes in two unmodified files.

## Running the focused checks

Use Python 3.12+, pytest and Node. CLI subprocess tests clear `PYTHONPATH`, so the
interpreter must already resolve this checkout (an editable install is sufficient).
The recorded private CPython 3.12.10 explicitly configured the checkout path and
used pytest 9.2.0. The working directory, basetemp and all personal
state were outside the checkout; bytecode, plugin autoload and pytest cache were
disabled. Set `repo` to the absolute checkout and `work` to a new external directory:

```python
import os
from pathlib import Path
import sys

repo = Path(r"<absolute checkout>")
work = Path(r"<new external temporary directory>")
work.mkdir()
os.chdir(work)
sys.path.insert(0, str(repo))
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
import pytest

raise SystemExit(pytest.main([
    '-q', '-p', 'no:cacheprovider', '--basetemp', str(work / 'pytest-outside'),
    str(repo / 'tests/quality/test_personal_feedback.py'),
    str(repo / 'tests/quality/test_personal_model.py'),
    str(repo / 'tests/quality/test_personal_drafts.py'),
    str(repo / 'tests/quality/test_personal_foundation.py'),
    str(repo / 'tests/quality/test_improvement_model.py'),
    str(repo / 'tests/quality/test_import_direction.py'),
]))
```

From an external working directory, the corresponding frontend and native-unit
commands are:

```text
node --test <repo>/console/static/personal-draft.test.js <repo>/console/static/personal-window.test.js <repo>/console/static/personal-feedback.test.js
cargo test --offline --locked --manifest-path <repo>/desktop/personal/Cargo.toml --target-dir <external-temp>/cargo-target --jobs 2
```

The publication check used the same Python settings and this selection against an
isolated snapshot containing only the proposed public files:

```text
pytest -q -p no:cacheprovider --basetemp <external-temp>/pytest-docs
  <repo>/tests/quality/test_documentation_links.py
  <repo>/tests/quality/test_documentation_commands.py
  <repo>/tests/quality/test_test_lanes.py
  <repo>/tests/quality/test_cli_entry.py::test_pyproject_is_the_installable_contract
  -k "not test_no_git_fallback_does_not_walk_runtime_trees"
```

The Cargo command requires prepared dependencies. Historical native checks used
Rust/Cargo 1.94.0 and the locked Tauri build. They used monitor and authority
fixtures, not physical display/input automation.

## Browser and native observations

An independent browser test saved a Chinese synthetic draft, stopped the owned
service normally and reopened the same state at a different port. Pairing with
the new connection restored the complete text in an enabled editor, with revision
1. Model calls and harness changes were both zero. Temporary tabs and that service
were closed afterward; no user service or Codex process was stopped.

Native user operation on Windows x86-64, one monitor at 100% scaling, confirmed:

- ordinary and floating startup, expansion and project-mark dragging;
- hover preserving another application's focus, and click-to-input focus;
- Chinese composition suppressing application Enter/Escape actions, followed by
  Enter save, Shift+Enter newline and Escape collapse;
- minimize/restore, close and restart placement recovery.

The shell is opaque. Physical multi-monitor/DPI changes, transparent composition,
automatic fallback, Ubuntu X11/Wayland and macOS remain untested. Browser rendering
and geometry fixtures do not establish those native behaviors.

The separate stage-0 diagnostic used a private Codex home and controlled stdio
app-server. It observed selected memory/Skill content and fixed workflow versions
across v1/v2/v1 controlled threads. That does not establish observation of existing
daily sessions, native hook installation, arbitrary generated-code isolation or
atomic harness adoption.

To repeat that separate diagnostic, explicitly select a native executable and a
new external output directory; it creates its own app-server and scripted endpoint:

```text
python -B <repo>/tools/diagnostics/probe_personal_codex.py --codex <selected-codex.exe> --output <new-external-directory>
```

## Compatibility and remaining validation

Shared-callback checks retain the original customization import/constructor,
default prompt and usage/error behavior. Draft tests cover a stopped/recreated
service with a guaranteed changed port, project isolation, stale-save HTTP 409,
empty-draft preservation and acknowledgement before success. Migration tests use
frozen v1/v2/v3 SQL fixtures and preserve evidence, cursors, drafts and model ledgers; failed
migrations roll back and unknown schemas retain their original bytes.

SQLite is now v4. Exit an old service before reopening its state with this version;
old programs cannot read v4. Only synthetic databases were migrated during these
checks. Unacknowledged input can still be lost on forced exit; inaccessible drafts
from an old browser origin are not automatically recovered.

Full project tests, Linux CI, clean wheel installation, live model quality/cost,
benchmarks and the planning/proposal/adoption workflow remain outside this result.
Development receipts and raw local evidence are retained outside the published
documentation; a fixture success is not evidence of personal harness improvement.

For current usage and implementation ownership, see the
[desktop guide](../../desktop/personal/README.md) and
[architecture](../personal-evolution.md).
