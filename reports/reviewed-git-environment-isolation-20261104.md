# Reviewed launcher Git environment isolation

Independent followup to1225031, same registered reviewed-environment worktree;
separate from metadata/staging identity patches. A sanitized Python environment
still retained HOME, and framework stage_workspace calls git init/add/commit
through _git. Synthetic user configuration proved this can execute unreviewed
hooks or clean filters before any native harness/model.

The reviewed launcher sets only its preexisting reviewed-environment marker.
Git isolation is constructed only inside eval.ckpt.git_state._git for that
framework subprocess when the marker is active. It removes inherited GIT_*
variables from that child's copy, disables system/global config and attributes,
selects empty templates and uses environment-level core.hooksPath=/dev/null,
core.attributesFile=/dev/null and init.templateDir=''. Parent os.environ and
runner.harness_env are unchanged; the native task's Git behavior is preserved.
Legacy _git passes env=None exactly as before. No /dev/null Git configuration
setting is injected into a task sandbox or native command environment.

The earlier intermediate commit e15f1ae injected these settings in the launcher
parent environment; review identified its native files-task propagation risk.
The scoped followup removes that injection. Adopt the final pair, not e15f1ae
alone. The fixed scope policy is tracked platform code and platform_commit bound.

Author no-model verification:146 passed in5.12s across new Git tests, reviewed
environment, AppWorld launcher/selection, data paths and config regressions.
New tests invoke actual stage_workspace in fresh subprocesses using only
synthetic HOME/global/system configuration. Four paired controls demonstrate
legacy execution and reviewed suppression of user hooksPath, user template
hook, synthetic system template hook and user clean filter: legacy writes a
sentinel; reviewed commits successfully without sentinel or installed executable
pre-commit hook. Another paired control proves default HOME attributes otherwise
normalize committed CRLF bytes and reviewed attributes isolation preserves bytes.
No real user configuration or system Git config was read by these controls.
git diff --check passes.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
 -p no:cacheprovider tests/quality/test_reviewed_git_environment.py \
 tests/quality/test_reviewed_environment_only.py tests/quality/test_appworld_baseline_launcher.py \
 tests/quality/test_appworld_selection_prep.py tests/quality/test_data_paths.py \
 tests/quality/test_config.py --basetemp=/tmp/hg-reviewed-git-environment-final-tests-NEW
```

An additional regression invokes actual runner.harness_env and native Git commit:
framework stage does not write its synthetic HOME hook sentinel, while the native
commit still executes that same hook. Parent environment is byte-for-byte unchanged.

Limits: isolation applies only to framework _git children in reviewed mode;
legacy CLI is intentionally unchanged. It does not attest the git executable,
OS libraries, malicious same-UID mutation or arbitrary Git commands that pass
explicit overriding -c/--template or replace their environment. Source-provided
.gitattributes remain source inputs; unknown filters without a reviewed defining
config cannot execute, but built-in attributes can still affect staging. Existing
repository-local Git configuration is not globally disabled; the reviewed source
snapshot path must continue to exclude external .git metadata before staging.
This patch blocks automatic hooks for those children and external/default
configuration/templates, not all potential filesystem or execution channels.
No main/other worktree/old receipt edits, network, dependency download, actual
model, benchmark, campaign launch/resume or merge was performed.
