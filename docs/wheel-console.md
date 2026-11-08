# Console from an installed wheel

After installing the wheel into a Python 3.12 or newer environment, set an
existing results directory and start the local observation interface:

```bash
mkdir -p "$HOME/harnessgrad-runs"
export HG_CONSOLE_RUNS="$HOME/harnessgrad-runs"
python -m console.server --port 4460
```

Open `http://127.0.0.1:4460/` directly. Point `HG_CONSOLE_RUNS` at the root
containing the results to inspect; an empty root displays an empty list. This
command starts a local service. Its Host and Origin checks are not authentication;
other processes on the same host may access it.

The wheel includes Console code and its static interface. Wheel installation and
observation do not establish that every dataset, runtime, scorer, or launch path
is provisioned. Component prerequisites are described in the
[getting started guide](getting-started.md); the
[wheel adapter resource inventory](wheel-adapter-resource-inventory.md) describes
known packaging limits. Set `HG_CONSOLE_PLATFORM` when the console should use a
separately prepared checkout for launch operations, and configure the shared
budget and endpoints as described in the [Console guide](../console/README.md).

Installed Console state defaults to `$XDG_STATE_HOME/harnessgrad/console`, or
`~/.local/state/harnessgrad/console` when `XDG_STATE_HOME` is unset. Settings,
launch logs, candidates, authored bundles, assistant threads, explain cache and
launch workspaces are kept there, outside the installed package. Set
`HG_CONSOLE_STATE_ROOT` to another writable directory when needed. Existing
`HG_CONSOLE_CANDIDATES`, `HG_CONSOLE_AUTHORED` and
`HG_CONSOLE_ASSISTANT_THREADS` overrides still take precedence. Source checkouts
retain their existing default locations. Importing the module does not create
these directories; actions that save state create them as needed.

The installed CLI can locate bundled configuration with
`harnessgrad preset path quickstart` and select it with
`harnessgrad run --preset quickstart`. Selecting a preset does not prepare its
Harness or data, configure endpoints, or establish that an experiment can run;
follow the prerequisites and preflight in the first-use guide. Some recipes
reference external split files that must be provided by the operator.

## Run the installed mock control

Use Linux, CPython 3.12 and working Bubblewrap namespaces, with the venv and base
interpreter outside `/tmp` and task/work directories. Locate the installed Loop
template and explicitly prepare its dependencies in an external store:

```bash
HG_LOOP_TEMPLATE="$(python -c 'from importlib.resources import files; print(files("base_harness").joinpath("loop"))')"
python -m tools.configure_harness --harness "$HG_LOOP_TEMPLATE" --fetch
python -m tools.check_first_use --output-root "$HOME/.local/share/harnessgrad/install-checks"
```

Preparation may download locked wheels; the checker performs no installation or
model calls. It creates separate synthetic runs and a temporary Console, verifies
duplicate protection, prepared-run consumption and evidence export, and retains
logs plus `report.json`. For a custom prepared store, set
`HG_HARNESS_RUNTIME_ROOT` consistently or pass the tools' `--runtime-root` option.

To inspect the scripted two-round quickstart yourself:

```bash
HG_INSTALL_RUN="install-$(python -c 'import uuid; print(uuid.uuid4().hex[:12])')"
HG_AGENT_BACKEND=mock harnessgrad run --preset quickstart --improver none \
  --run-id "$HG_INSTALL_RUN" --runs-root "$HOME/harnessgrad-runs" \
  --work-root "$HOME/harnessgrad-work" --fail-on-incomplete
python -m tools.run_summary "$HOME/harnessgrad-runs/$HG_INSTALL_RUN"
```

The observation command above uses this same result root. These are synthetic
installation controls; their scores are not model or benchmark evidence.
