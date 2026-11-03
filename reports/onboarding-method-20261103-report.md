# External method onboarding repair

Status: implemented in the parent isolated worktree; no commits or other git writes.
No models, endpoints, benchmark pilots, dependency installs, or full driver runs.

## Changed files

- `tools/validate_method.py`: displays external paths without assuming they belong
  to the repository; reads method source and trajectories as UTF-8; rejects
  non-object stdout, missing trajectories, non-object trajectory documents,
  missing/empty/non-array steps, non-object steps, invalid harness_dir values,
  and steps reporting a method error. A valid unchanged harness step remains
  accepted with `changed: false`. Corrected the structure-only example to
  `--no-call` (the previous `--smoke` example could invoke models).
- `methods/protocol.py`: corrected evidence location to
  `base_harness/_harnessgrad`; documented the required nonempty trajectory and
  legitimate no-change submission behavior of the unified engine.
- `docs/bring-your-own-method.md`: executable Linux/WSL walkthrough that copies
  existing noop plus `_siblings.py` and `protocol.py` into one external directory,
  validates without models, explains the wire contract, and constructs a quoted
  absolute entrypoint for a check-only launch. Links echo_base as the existing
  scripted-edit/batch reference rather than adding another example abstraction.
- `tests/quality/test_validate_external_method.py`: 17 behavioral cases using
  real external Python subprocesses and temporary roots outside source.
- This report.

The parent owns README and the `.gitignore` exception for the new documentation.
I notified it that `/docs/*` initially ignored the page; the exception is now
visible in the shared worktree. I did not edit those files.

## Engine evidence

Read `harnessgrad/methods.py` and the unified engine in
`harnessgrad/loops/mode_a.py` before implementing. `_invoke_method` treats a
missing/unreadable trajectory as failure. The submission reader rejects no
steps before consulting `changed`; each step must name a readable harness
directory, and a step-level error is a failure. An unchanged complete candidate
is valid, with `changed: false` affecting the default stop decision. These rules
do not permit omitting the trajectory for a no-change result.

## Red verification

Windows PowerShell, before modifying implementation:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/quality/test_validate_external_method.py -q
```

Result: **14 failed, 3 passed**. Expected failures:

- `test_external_method_directory_validates_without_a_repository_relative_path`:
  `entry.relative_to(ROOT)` raised ValueError for the external method path.
- `test_json_response_still_requires_a_trajectory`, both changed true and false:
  validator warned and incorrectly accepted the missing trajectory.
- `test_invalid_or_empty_trajectory_fails_cleanly`: list/null documents and
  non-object steps crashed; missing/empty/wrong-shaped steps were accepted or
  crashed. Seven parameter cases failed. Invalid JSON and a step without a
  harness directory already failed as expected.
- `test_stdout_must_be_an_object`, all three cases: arrays were accepted and
  null/numeric responses crashed.
- `test_step_error_is_a_failed_submission_not_an_unchanged_candidate`: an
  explicitly failed step was accepted as a valid unchanged harness candidate.

The valid complete-harness no-change case passed before the change and remains
a compatibility guard.

## Green verification

WSL lightrsi-ubuntu, source
`/mnt/c/Users/Tony Yu/.codex/worktrees/hg-latest-review/HarnessGrad`:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH='/mnt/c/Users/Tony Yu/AppData/Local/Programs/Python/Python310/Lib/site-packages'
python3 -m pytest tests/quality/test_validate_external_method.py -q
python3 -m pytest tests/quality/test_validate_external_method.py \
  tests/quality/test_method_lint.py tests/quality/test_method_score_side.py -q
```

Results: **17 passed in 5.32s**; neighboring suite **76 passed in 107.24s**.
Pytest used its default temporary roots outside the source tree.

Final Windows rerun of the red command: **17 passed in 2.33s**. The CLI test
sets `PYTHONIOENCODING=utf-8` for its captured subprocess output, so it is
portable across the Windows GBK and Linux UTF-8 environments.

Executed the documentation's copy-and-validate workflow under WSL using
`tempfile.TemporaryDirectory(prefix="hg-external-method-doc-")` and copying
exactly `methods/noop/run.py`, `methods/_siblings.py`, and `methods/protocol.py`
into that external directory before invoking:

```bash
python3 tools/validate_method.py <external-directory> --harness base_harness/loop
```

Result: **exit 0**, changed false, one valid trajectory step, nomination zero.
The existing lexical helper detection emits an optional warning because noop
loads protocol through `_siblings.load`; this is not a contract failure.

Executed the documented preflight with the same copied files, a `shlex.join`
entrypoint, and temporary external method directory:

```bash
python3 driver.py --harness loop --dataset demo --side train --sampling all \
  --mode A --rounds 1 --trials 1 --jobs 1 --improver none \
  --method-entrypoint '<quoted absolute python and external run.py>' \
  --work-root /tmp/hg-external-method-work --check-only
```

Result: **exit 0**, every required Python/git/bwrap/dataset/harness/writable/method
check passed. No measurement or method execution occurred in this check-only
step. Temporary method directories were automatically removed.

`git diff --check -- methods/protocol.py tools/validate_method.py`: **exit 0**.

## Concerns and verification limits

- No full project suite was run: the task explicitly disallows full driver runs
  while other workers edit source. The parent should run its combined focused
  verification after reviewing all workers' changes.
- Validator invocation is a direct subprocess contract check, not a sandbox or
  dataset readiness certificate. The documentation states that limit.
- A Windows verification attempt using `PYTHONUTF8=1` failed during interpreter
  startup because an installed site `.pth` file is not UTF-8. A normal Windows
  invocation succeeds; forcing `PYTHONIOENCODING=utf-8` only for the CLI test
  avoids this unrelated local environment issue. No installs or environment
  files were modified.
- Native Windows console GBK cannot print the validator's pre-existing Unicode
  status glyphs without an output-encoding override. The documented execution
  surface is Linux/WSL; UI glyph changes are outside this scoped repair.

## Integration documentation correction

After parent integration of the main patch (`91d81b34`), the documentation link
test identified `base_harness/_harnessgrad/` as an incorrect repository-path
claim. Changed only that page's inline location to the runtime placeholder
`<base_harness>/_harnessgrad/`. Left this correction uncommitted for the parent.

Reran documentation checks only in WSL with the same bytecode/plugin-disabled
environment and pytest import path recorded above:

```bash
python3 -m pytest tests/quality/test_documentation_links.py \
  tests/quality/test_documentation_commands.py -q
git diff --check -- docs/bring-your-own-method.md
```

Results: **15 passed in 9.35s**; diff check **exit 0** (Git emitted its existing
LF-to-CRLF working-copy notice). No additional implementation changes or runs.
