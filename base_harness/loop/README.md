# Loop

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
