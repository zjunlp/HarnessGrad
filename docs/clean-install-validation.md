# Clean installation: no-model reproducibility validation

## Scope and result

Audited from `348953cdf61512a7731e160d5b086059992bf23e` in registered worktree
`/tmp/hg-clean-install-20261104`, branch `codex/clean-install-20261104`.
No README, driver, H0 or base-harness source was changed. No operator experiments
were imported or run. This is a synthetic repository fixture check, **not a
benchmark, production H0 authorization, or evidence of model quality**.

A noneditable offline installation initially printed help but failed the mock demo
with exit 2: `no such harness: loop`. Namespace discovery excluded `base_harness`.
After including it and its manifests, a sandboxed run reached all four tasks but
failed with `FileNotFoundError: .../data/aggregation_policies.json`. Packaging now
includes JSON contracts, Markdown skills/prompts, YAML configuration and harness
requirements files through a curated explicit path allowlist (not wildcard data
collection). The installer also now reports its actual Python floor (3.12,
not the stale 3.10 error). No runtime dependencies were added.

Final installed `demo-smoke` run (`clean-verified`) exited **0**, with Bubblewrap
active, 4/4 train tasks, one trial each, zero improvement rounds, and `COMPLETE`.
The mock emits no correct answers: score **0.000** is expected; task execution and
measurement completion, not score improvement, is the installation assertion.

## Follow-up wheel review

Independent review found two omitted declared components: DeepSeek Harness's
`dsh_farm.tsv` and MiniAgent's `LICENSE.upstream`. Both now ship. The extensionless
`methods/dsh-improver` launcher also ships with executable permissions; it was
inspected but **not executed** (its external DSH/model dependencies remain untested).

Broad package-data globs were replaced by explicit reviewed resource paths with
`include-package-data = false`. This avoids collecting ignored/untracked JSON,
Markdown or YAML such as local credentials/receipts. The wheel regression injects
12 synthetic local-secret files into a disposable source copy and verifies none
ship, verifies every tracked local component/entrypoint/install file declared by
each shipped harness manifest, and checks the launcher executable bit. Absolute
runtime paths and generated/untracked outputs are not shipping obligations. The
build itself requires no Git; only the test uses Git to construct its fixture.
**The supported/validated installed execution scope here is only the demo.**
This curated list does not ship adapter assets or `data/runtimes/*-native.json`:
the wheel cannot run affected external adapters as a complete platform deployment.
For those, use a reviewed checkout and their own provisioning/readiness gates;
catalog visibility is not install support or authorization. No native manifest
hashes were changed or repinned. In particular, inspection found these local
resource consumers (not executed during this audit):

- `data.runtimes.NATIVE_PINS` refers to scienceworld/chessbench/alfworld/jericho
  native manifests; these are not included.
- `data/scaffolds/native_research/swe_official.py` reads
  `SWE_EVALUATOR_SOURCE.json`; native Harbor reads its runtime `PROVENANCE.json`.
- Kumo/CostBench read `source-files.json`; MemoryAgentBench reads
  `specs/source.json` and `specs/resources.lock.json`; JobBench/DeepSWE/SWE-bench
  Pro preparation reads `SOURCE.json`. These adapter resources are not included.

These are concrete omissions, not a complete inventory of adapter requirements.
Other extensionless dataset CLIs likewise remain outside this demo-install scope.
No external adapter execution was attempted, and no general fail-closed behavior
for missing assets was proved.

The sentinel exclusion guarantee applies **only to non-Python package data**.
Namespace discovery still includes ignored/untracked Python modules beneath the
included package roots. The regression separately demonstrates this with a benign
ignored `data/local_ignored_module/__init__.py` in its disposable copy, without
importing it. Build release wheels only from a clean reviewed/exported source tree;
the explicit data allowlist is not a general dirty-checkout security boundary.

After the allowlist refinement, the integrated tree was installed again offline into a
fresh isolated venv with a separate empty HOME and no dataset/model configuration. The
sandboxed `demo-smoke` mock loop exited 0, with four of four train tasks executed and
`COMPLETE`; the expected score was 0.000. Bubblewrap was active, but the clean environment
had no user systemd bus, so the resource-envelope warning remained. The temporary venv and
run files were removed after the check; the command output was retained separately in
`/tmp/hg-quality-final-clean-install.log`. This remains demo-only evidence, not support for
external adapters or a production authorization.

## Exact environment and commands

The host's available Python 3.12.4 interpreter created fresh venvs with no system
site packages; Python's interpreter/stdlib still originate from that host. This is
not a claim of portable interpreter provisioning. Build-only setuptools 84.0.0 was
available as a cached wheel (818 kB), copied into the isolated venv by pip; the
installed runtime does not import a personal checkout or user site packages.
No package downloads, paid endpoints, model calls, or external dataset data were
needed. `HG_DATA_ROOT` and all model credentials are absent through `env -i`.

```bash
REPO=/tmp/hg-clean-install-20261104
VENV=/mnt/20t/xubuqiang/hg-clean-install-venv-20261104
HOME_TMP=/tmp/hg-clean-install-runtime-20261104/home
BUILD_WHEEL=/home/xubuqiang/.cache/pip/wheels/09/c4/7e/3f12bd4f3b911f91b5e4cb2611342c6851559d353876d0251c/setuptools-84.0.0-py3-none-any.whl
python3 -m venv "$VENV"
env -i HOME="$HOME_TMP" PATH="$VENV/bin:/usr/bin:/bin" \
  PIP_CONFIG_FILE=/dev/null PIP_NO_INDEX=1 \
  python -m pip install --no-index "$BUILD_WHEEL"
# Run from /tmp, NOT from the source checkout.
env -i HOME="$HOME_TMP" PATH="$VENV/bin:/usr/bin:/bin" \
  PIP_CONFIG_FILE=/dev/null PIP_NO_INDEX=1 PYTHONNOUSERSITE=1 \
  HG_AGENT_BACKEND=mock TMPDIR=/tmp /bin/bash -xc \
  'python -m pip install --no-index --no-build-isolation --no-deps --force-reinstall /tmp/hg-clean-install-20261104 && harnessgrad run --scenario demo-smoke --harness loop --improver none --run-id clean-verified --runs-root /tmp/hg-clean-install-runtime-20261104/runs --work-root /tmp/hg-clean-install-runtime-20261104/work --fail-on-incomplete'
```

Choose a **fresh run ID** on reproduction. Provision your own Python >=3.12 and
local setuptools wheel; the cached path above is evidence, not a dependency or a
recommended public installation path. Offline `--no-build-isolation` requires the
build backend to have been installed first. A plain fresh Python 3.12 venv contains
pip but need not contain setuptools. Online build isolation was not tested.

Installed `harnessgrad --help`, `--version`, `run --help`, the dataset catalog
(`data.scaffolds.registry.published()`), and embedded demo task loading all passed.
Catalog listing is not a claim that any external dataset is provisioned. The demo
fixture is embedded in `data/demo.py`; scenario defaults are shipped in
`scenarios/demo-smoke.json`. Explicit `--improver none` and rounds=0 avoid an editor
model even though the solver uses mock.

## Unsupported layouts and limitations

The first venv under `/tmp/hg-clean-install-runtime-20261104/venv` correctly refused
the sandboxed loop with exit 2: `Python environment ... is under a hidden sandbox
tree; create the venv outside /tmp and outside the platform/work directory`.
That is an unsupported installation layout, **not a successful sandbox smoke**.
Moving the isolated venv to the visible external path above resolved it. No
`--no-sandbox` fallback was used. The final host supports Bubblewrap user namespaces.
Other hosts may legitimately refuse: retain their exact diagnostic, do not weaken
the sandbox or claim success.

The clean environment has no user systemd bus (`DBUS_SESSION_BUS_ADDRESS` and
`XDG_RUNTIME_DIR` absent). The driver warned that the sandbox has **no resource
envelope**. Namespace isolation passed; cgroup/resource containment was not proved.
The loop declares `requirements.txt`, so the host-interpreter dependency warning
also appears; mock execution needs no third-party runtime packages. No real-model
requirements, dataset provisioning, other harness execution, sdist installation,
or full suite were validated.

## Regression and evidence

`tests/quality/test_clean_install_contract.py` copies tracked packaging inputs,
builds a wheel offline, installs into a fresh venv, removes the source copy, and
checks CLI help/version/scenario, catalog/demo loading and required resource paths
from an unrelated working directory. It never runs a model. The build/test
interpreter needs pip, setuptools >=64 and pytest already provisioned. Its `/tmp`
venv is deliberately not used for sandbox execution; that check is the explicit
host audit above, not a skipped test represented as a pass.

```bash
python3 -m pytest tests/quality/test_clean_install_contract.py \
  tests/quality/test_cli_entry.py tests/quality/test_install_sh.py \
  --basetemp=/tmp/hg-clean-install-pytest-20261104 -q
```

Machine-local exact stdout/stderr, commands and failed intermediate receipts are
preserved in `/tmp/hg-clean-install-evidence-20261104/`; the handoff report is
`/tmp/hg-clean-install-report.txt`. Preserve those receipts before removing the
two venvs, temporary HOME/cache, mock workspaces and pytest basetemp. Retain the
registered worktree for parent review, rather than removing its unmerged commit.
