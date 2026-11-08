# Installation validation

The [first-use guide](getting-started.md) is the user-facing Linux source workflow.
An installation control verifies execution and records using synthetic tasks and
mock responses. It is not a benchmark or evidence of model quality.

## Source installation acceptance

Use a clean checkout and a fresh external venv. Prepare the declared Loop runtime
explicitly, as shown in the first-use guide. Preparation may download locked wheels;
the subsequent control does not install packages, download data or contact models.

```bash
python tools/check_first_use.py --output-root "$HOME/.local/share/harnessgrad/install-checks"
```

Use `--runtime-root /path/to/prepared-store` if preparation used a custom store.
The checker refuses an absent or invalid artifact before launching a task. It
keeps logs and a JSON report in a new directory; existing attempts are not reused.

The check covers native sandbox execution, explicit mock identity, preserved
records on duplicate IDs, consumption of a matching prepared reservation, and
result inspection and evidence export through its own temporary Console. It does
not start or stop an existing Console, and the temporary handler exposes only
its required read/export routes.

The venv and base Python must stay outside the checkout and `/tmp`, and work
roots must stay outside the platform. Working Bubblewrap user namespaces are
required. A resource-envelope warning is a separate limitation: namespace
isolation alone does not establish resource containment.

## Packaging regression

The focused packaging tests build and install a wheel from reviewed inputs, then
check its CLI and declared resources from an unrelated working directory. They
need Python 3.12+, pip, setuptools and pytest in the test environment. They do not
prove every external adapter is provisioned or executable.

```bash
python3 -m pytest tests/quality/test_clean_install_contract.py \
  tests/quality/test_cli_entry.py tests/quality/test_install_sh.py \
  tests/quality/test_check_first_use.py tests/quality/test_wheel_console.py \
  tests/quality/test_wheel_recipe_assets.py \
  --basetemp=/tmp/hg-clean-install-pytest -q
```

See the [adapter resource inventory](wheel-adapter-resource-inventory.md) for
current packaging coverage and [wheel Console guide](wheel-console.md) for
observation without a source checkout. Build wheels from a clean reviewed source tree: an
explicit package-data allowlist does not exclude untracked Python modules.
External datasets, models and large runtime artifacts are prepared separately.

## Installed-wheel execution acceptance

Install a wheel into a fresh external CPython 3.12 venv, then run from an unrelated
directory with the build source removed. With the matching Loop runtime already
prepared in an external store, the same control is available as a module:

```bash
python -m tools.check_first_use --runtime-root /path/to/prepared-store \
  --output-root "$HOME/.local/share/harnessgrad/wheel-checks"
```

This exercises sandboxed mock execution, namespace preservation, Console
inspection and verified evidence export from installed code. The bundled
`quickstart` recipe is also available through `harnessgrad run --preset quickstart`;
use `--improver none`, a new run ID, and external result/work roots for its
scripted control. See [installed usage](wheel-console.md) for the commands.

Keep build/install logs, the wheel digest, source commit and control reports with
the validation record. An installed mock run does not establish support for all
adapters, all host platforms, or system resource containment.

## Historical validation

The earlier 2026-11-04 audit is preserved at published commit
`b87be212e901d26262a1d3cc1b6356847899d19e`, path
`docs/clean-install-validation.md`. Its reported results apply to that environment
and fixture. Repeat the current workflow to validate a later version; historical
passes and skipped tests do not establish current installation support.
