# MiniSWE native adapter

The adapter already invokes mini-SWE-agent 2.4.6. `requirements.txt` pins that
version and compatible LiteLLM 1.75.9; no native prompt, context policy or strategy
is changed. Dependencies now come from an overlay built for the executing Python
interpreter instead of an operator's Python 3.12 user-site mount.

Provision dependencies separately before a run:

```bash
python3 tools/configure_harness.py --harness base_harness/miniswe --python 3.12
```

Exec also requires an overlay for the image's interpreter (Python 3.11 for the
pinned actor below). Overlay provenance records transitive distributions. The
container configure verifier reads a structured import result so mini-SWE-agent's
startup banner is accepted while actual import errors or incomplete/failed probes
refuse. This verifier correction is supplied as a separate dependency-fix commit;
it does not change the native agent.

The manifest declares files and ordinary exec. The focused exec regression uses
actual `run_one` in actor
`sha256:d3a04ec8e295710347e978d8e58c663161d829e7dfa544da4d82d0d67abbd8eb`,
a genuine Docker/kernel backend observation, network none and the ABI-matched
read-only Python 3.11 overlay. Native mock drives upstream shell execution and
artifact delivery; source read-only/cwd/private-input/trace/usage/tree identity
checks accompany a fresh synthetic post-exit verifier. Native trajectory and
runtime provenance must both identify mini-SWE-agent 2.4.6.

This proves the ordinary interface on this image/ABI. It does not measure official
AppWorld services or scoring, every task image, observed workers, complete
lifecycle isolation or production H0 authorization. No real model or benchmark
is run. The terminating upstream submission sentinel remains represented as an
unexecuted trajectory action. Changing the manifest changes submission identity;
future experiments need a newly frozen identity.

Each task keeps its own HOME, mini configuration and XDG configuration/cache/state
directories. The adapter clears daily mini/LiteLLM settings and other model roles'
credentials, then supplies the task's selected model endpoint and key. It preserves
the platform-provided `PYTHONPATH`, so the child uses the same ABI-matched overlay
and `sys.executable` as its parent, including inside an exec image. Legacy manifests
may explicitly declare package directories in `runtime_paths`; the adapter does not
guess another user's site-packages directory when dependencies are unavailable.

The shared overlay tool is the preparation entry point. There is no separate
miniSWE Python-selection manifest or task-side installation discovery.

The synthetic exec regression can select another locally provisioned Python 3.11
image with `HG_TEST_MINISWE_EXEC_IMAGE`; the test records that image's actual
backend observation and overlay provenance. This option changes only the test
fixture, and does not establish AppWorld support for the selected image.

The native integration test discovers the executing Python's overlay before
deciding whether to skip; pytest itself need not have mini-swe-agent installed.
It uses the production surfaced-path mounts so overlays under `/tmp` remain
visible after sandbox hiding. A small overlay-only bubblewrap regression covers
that layout and readonly dependencies without imitating mini's CLI.
