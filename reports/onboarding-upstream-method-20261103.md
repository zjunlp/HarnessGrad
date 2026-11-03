# Upstream external-method reconciliation

Compared old base `0a2551b0`, upstream `origin/main` at `1365a826`, and the
complete local repair branch `codex/fix-onboarding-first-four-20261103` at
`20b701b9`. Used read-only `git show`, `git diff`, `git rev-parse`, `git ls-tree`,
and `git log`; no git write operations were performed by this worker.

## Findings sent before integration

The upstream core fixes are still missing. The validator blob is exactly
`1f3c70449bee6b0972e67f891e51814c73d1de03` in both old base and upstream; protocol
is exactly `a1812ffa8cadcb5c344f783a5f80ef29e4d9c40f` in both. The diff from old
base to upstream has no hunks in either core file.

- `tools/validate_method.py:check_structure` still calls `entry.relative_to(ROOT)`
  unconditionally, which cannot display a real method outside the repository.
- Missing trajectory still receives WARN and the obsolete "mode A can omit it"
  explanation; an empty `steps` array is still accepted. Non-object stdout and
  malformed trajectory shapes can also be accepted or crash.
- `methods/protocol.py` still places evidence at `<workspace>/_harnessgrad/`,
  contradicting the engine and the new upstream example.
- The validator's structure-only example still says `--smoke` rather than
  `--no-call`.

The newest engine still requires a trajectory and nonempty steps before reading
the no-change/stop fields. Its only changes since this base are an H0-only guard,
the unreadable-trajectory tuple return fix, and the `method_stopped` receipt
field. None supersedes the local validator/protocol correction.

Upstream added the new `docs/bring-your-own-method.md` and standalone 43-line
`examples/method-template/run.py` in `69097029`. The template already correctly
uses `Path(request['base_harness']) / '_harnessgrad'`, emits a complete nonempty
trajectory for its no-change control, and avoids helper packaging dependencies.
Its documentation adds the current mock smoke and `--fail-on-incomplete` flow.
It should remain the primary tutorial rather than being replaced with the older
local noop-plus-helpers copying walkthrough.

## Authorized integration

The parent merged the local branch onto upstream and selected the upstream
version of `docs/bring-your-own-method.md` as the resolution starting point.
This worker then changed only that document and this report. Core validator,
protocol, template, and tests were not modified in this integration step.

The document retains the upstream RSI/template/tutorial structure and research
recipe links, adding only the needed contract guidance:

- Copy the upstream template to a fresh external directory first, then validate
  that actual external copy.
- Construct the absolute interpreter/script command with `shlex.join`, quoting
  all variable paths so spaces in either path remain valid. Generate a fresh run
  ID and name an external work root for the documented smoke.
- Describe `--no-call` as structure only, `--smoke` as platform/model execution,
  and the validator as a process/trajectory/manifest check rather than a sandbox,
  candidate correctness, or dataset readiness certificate.
- Name the runtime evidence path as `<base_harness>/_harnessgrad/`.
- Require a JSON trajectory object with a nonempty steps array and complete
  absolute harness directory even for `changed: false`.
- Explain single-step no-change and batch stop defaults and explicit `stop`
  overrides, bounded by remaining rounds; errors remain errors.
- Preserve diagnostic log and temporary directory cleanup guidance without
  introducing another method abstraction or replacing the upstream template.

## Verification

Documentation-only checks on the integrated worktree:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/quality/test_documentation_links.py tests/quality/test_documentation_commands.py -q --tb=short
git diff --check -- docs/bring-your-own-method.md
```

Results: **15 passed in 1.31s**; diff check **exit 0**. These tests validate
documented paths, anchors, and command/flag references without executing driver.

Also verified the template-copy/validator portion under WSL lightrsi-ubuntu:
copied `examples/method-template` with `shutil.copytree` into a
`TemporaryDirectory(prefix="my rsi method.")` outside the repository, then ran
the equivalent documented validator command with argument-array quoting:

```bash
python3 tools/validate_method.py "/tmp/my rsi method.<temporary>" --harness base_harness/loop
```

The displayed shell path above is illustrative; execution passed the actual
temporary path as one subprocess argument. Result: **exit 0**, `changed=False`,
one valid harness step, nomination zero. The existing optional warning about
not importing protocol is expected: this standalone template implements the
wire protocol directly using the standard library. The temporary tree was
automatically removed.

No driver invocation, model call, benchmark run, install, or source/test edit was
performed. The tutorial's full mock smoke was retained from upstream but was
not executed, per the parent's explicit scope. Documentation source is frozen
for the parent's combined review and merge resolution.
