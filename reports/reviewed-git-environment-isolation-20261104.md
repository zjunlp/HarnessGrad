# Reviewed launcher Git environment isolation

Independent followup to1225031, same registered reviewed-environment worktree;
separate from metadata/staging identity patches. A sanitized Python environment
still retained HOME, and framework stage_workspace calls git init/add/commit
through _git. Synthetic user configuration proved this can execute unreviewed
hooks or clean filters before any native harness/model.

Only v2 launcher's final child environment changes. Fixed settings disable
system/global configuration (GIT_CONFIG_NOSYSTEM=1,
GIT_CONFIG_SYSTEM=GIT_CONFIG_GLOBAL=/dev/null), system attributes
(GIT_ATTR_NOSYSTEM=1), and default/user/system templates (GIT_TEMPLATE_DIR='').
Environment-level config overrides core.hooksPath=/dev/null,
core.attributesFile=/dev/null and init.templateDir=''. These keys are written
after reviewed runtime/native settings; inherited GIT_CONFIG_COUNT/KEY/VALUE,
GIT_DIR and similar variables are already absent from the launcher allowlist.
No generic Git helper or legacy staging behavior is changed. The fixed policy
is tracked platform code and thus bound by the reviewed platform_commit.

Author no-model verification:145 passed in4.04s across new Git tests, reviewed
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

Limits: isolation applies to Git children receiving this launcher environment;
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
