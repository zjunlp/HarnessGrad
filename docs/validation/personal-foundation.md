# Personal foundation validation

Scope: the personal A+B1 foundation based on `refactor/shared-foundation` commit
`17fa6ae0`, checked on Windows on 2026-11-17/18. The implementation has an independent
local service, desktop controls, synthetic imports, persistent drafts and bounded
model sessions. It does not yet provide the feedback-to-adoption workflow.

## Recorded checks

| Check | Result and scope |
| --- | --- |
| Focused Python after test consolidation | 69 passed, 0 failed, 11.46 s; model, draft, foundation and transport/deadline tests |
| Current frontend controllers | 16 passed, 0 failed, 0 skipped/cancelled, 148.7933 ms; scripted native bridge and DOM fixtures |
| Documentation publication checks | 29 passed, 1 Windows-incompatible path test deselected, 7.47 s; documentation links/commands, test lanes and packaging declaration |
| Broader offline selection before test consolidation | 361 passed, 34 deselected, 15 warnings, 478 subtests passed, 74.07 s; included customization, configuration, endpoint and CI guards |
| Historical native-shell unit controls | 12 passed, 0 failed/ignored/filtered, 0.90 s; placement, diagnostics and generated command permissions |
| Historical Windows development build | Passed; subsequent branding build took 13.40 s; not a packaged release |
| Historical controlled Codex probe | 52 passed, 462 subtests passed, 0 skipped, 40.25 s; included an explicitly selected native Codex 0.154.0 and synthetic model endpoint |

These are different selections and source revisions; their counts must not be
added. The broader run preceded test consolidation. The current focused run
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
    str(repo / 'tests/quality/test_personal_model.py'),
    str(repo / 'tests/quality/test_personal_drafts.py'),
    str(repo / 'tests/quality/test_personal_foundation.py'),
    str(repo / 'tests/quality/test_improvement_model.py'),
]))
```

From an external working directory, the corresponding frontend and native-unit
commands are:

```text
node --test <repo>/console/static/personal-draft.test.js <repo>/console/static/personal-window.test.js
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
frozen v1/v2 SQL fixtures and preserve evidence, cursors and model ledgers; failed
migrations roll back and unknown schemas retain their original bytes.

SQLite is now v3. Exit an old service before reopening its state with this version;
old programs cannot read v3. Only synthetic databases were migrated during these
checks. Unacknowledged input can still be lost on forced exit; inaccessible drafts
from an old browser origin are not automatically recovered.

Full project tests, Linux CI, clean wheel installation, live model quality/cost,
benchmarks and the feedback/proposal/adoption workflow remain outside this result.
Development receipts and raw local evidence are retained outside the published
documentation; a fixture success is not evidence of personal harness improvement.

For current usage and implementation ownership, see the
[desktop guide](../../desktop/personal/README.md) and
[architecture](../personal-evolution.md).
