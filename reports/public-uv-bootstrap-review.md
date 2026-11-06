# Public uv bootstrap review

Base: `ae73fbf`. Scope: public README bootstrap and installer diagnostics; no sandbox policy changes.

## Findings and changes

- `uv venv` does not seed pip by default; `uv pip install -e .` uses uv's installer and does not itself put pip into that environment. Host overlay configuration checks the selected Python with `-m pip`. The README now uses `uv venv --seed --python 3.12 ~/.venvs/harnessgrad` and explains seed-cache/network and build-tool requirements.
- The sandbox explicitly rejects Python environments under hidden trees such as `/tmp`. With a synthetic `/tmp` venv, `install.sh --check` reproduced this specific failure, but previously added an unconditional kernel/LSM diagnosis. It now preserves the actual detail and gives conditional, non-prescriptive guidance. Probe status is tracked separately from earlier prerequisite failures.

## Offline validation

- `python3 -m pytest -q -p no:cacheprovider tests/quality/test_public_uv_bootstrap.py --basetemp=/tmp/hg-uv-bootstrap-test` — 2 passed. The source command is parsed and passed to a fake uv; the installer test uses a real synthetic pip-free venv under `/tmp` and `/bin/true` as the bwrap executable, exercising the runtime-boundary check before a namespace is created.
- `python3 -m pytest -q -p no:cacheprovider tests/quality/test_install_sh.py tests/quality/test_public_uv_bootstrap.py --basetemp=/tmp/hg-uv-bootstrap-extended` — 12 passed, including the existing installer suite and its updated broken-bwrap diagnostic assertion; the ready-machine tests exercise the real sandbox self-check.
- `bash -n install.sh` — passed.

No network, dependency installation, live model, or benchmark was used. The fake uv test checks the documented arguments, not uv's real download/cache behavior; the full online quickstart and a seeded environment using a pre-existing cache remain unverified.
