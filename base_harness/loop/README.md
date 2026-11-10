# Loop

## Select a source version

The default `--harness loop` uses **0.3.0**. To select the historical 0.2.0
source with the existing version installer:

```bash
LOOP_DIR=$(harnessgrad harness install loop --version 0.2.0)
harnessgrad run --harness-path "$LOOP_DIR" --dataset demo --improver none --rounds 0
```

`harnessgrad harness list` lists both 0.2.0 and 0.3.0. Omit `--version` to export
0.3.0. `--install-root` selects the external source store. This command exports
source only: no Node, Git checkout, dependency installation or downloads are needed.
Prepare dependencies separately when required by the selected version.

0.2.0 is the exact three-file source tree `7a811b3ab930a3f902eb50e18c504288f5eea0dc`,
from platform revision `b87be212e901d26262a1d3cc1b6356847899d19e`, immediately before
Loop's 0.3.0 runtime migration. Its manifest and `openai>=1.0` requirement are retained
unchanged. The archive digest and source inventory are checked; installation
provenance stays beside the Harness, not inside its source tree. Edited exports
are preserved and refused on reuse rather than overwritten.

Historical runs can have different source despite sharing the 0.2.0 label. Exact
score reuse still requires matching source, model, task, scorer and runtime
identities. Use a retained run checkpoint when its source differs from this release;
selecting a version never bypasses those checks or guarantees identical dependencies.

## Current runtime

The reference ReAct Harness runs in an immutable, explicitly prepared Python
environment. Its agent logic and budgets remain candidate-owned. Methods can edit
the source and locks; each measured checkpoint binds its exact source and artifacts.

```bash
# Native files tasks, including the default Quick start. Python 3.12 must exist.
python tools/configure_harness.py --harness base_harness/loop --fetch
python tools/configure_harness.py --harness base_harness/loop --list --json

# Prepare the locally imported task images used by a container dataset.
python tools/configure_harness.py --harness base_harness/loop --dataset terminal_bench --fetch
# Or select individual local images; this command never pulls an image.
python tools/configure_harness.py --harness base_harness/loop --image YOUR_LOCAL_IMAGE --fetch
```

Omit `--fetch` for offline preparation from cached wheels. Ordinary runs only use
cached wheels when preparing a new candidate; admission and `--list` install
nothing. The platform does not need the OpenAI SDK or pip for this Harness.
Use `--runtime-root` / `HG_HARNESS_RUNTIME_ROOT` for a dedicated external store.

Native `files` uses CPython 3.12 and `requirements.lock`. An `exec` image with
CPython uses a separate venv with a read-only copy of that image's executable;
standard and system libraries remain the image's. The manifest selects complete
wheel locks for Python 3.9, 3.10, and 3.11–3.13; unsupported
versions fail preparation explicitly. Images without Python use the same bound
native CPython artifact, checked inside the selected image before execution.
An existing but broken image interpreter is an error and does not trigger that
fallback. Both bind-mounted task directories and container-state snapshots are
supported. Existing backend policy admission and resource checks still apply.
Actor execution preserves the image's startup behavior. Preparation bypasses its
entrypoint to run only the trusted interpreter and installer.

The container actor starts with its absolute bound interpreter and `-I -B`, without
activating its venv in the task environment. Task shell commands retain the image's
`PATH`, Python configuration and existing venv, so its tools and task dependencies
remain available. The actor ignores ambient Python import configuration; an actor
helper that needs its SDK should use `sys.executable` with isolated startup. An
image without Python still has no task-shell Python merely because the actor has
a prepared interpreter. Native files tasks retain their managed interpreter path.

Image content addresses, actual interpreter identity, locks, dependency contents
and candidate source are recorded. Missing, corrupt, changed or incompatible
artifacts fail admission before cache reuse; historical bindings never select a
newer publication. The actor uses `-I -B`, does not import platform packages, and
shares only read-only dependencies. HOME, temporary files and state belong to
each task. Actor dependencies are not mounted into scoring containers or baked
into container-state snapshots. Preparation executes trusted installers and image
interpreters, without mounting candidate source or importing installed packages.

OpenAI 2.21.0 is locked for this migration; the lock variants include their full
transitive closure. `requirements.txt` remains the high-level dependency input.
This isolates Python dependencies, without claiming a sealed operating system.
Managed Python still refuses unsandboxed execution, cooperative/observed workers,
external runtime paths and adapter `agent_runtime` projections.
