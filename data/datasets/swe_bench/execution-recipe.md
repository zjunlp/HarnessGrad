# SWE official worker execution recipe v1 (2026-11-07)

The input requirements are the main dependency list of SWE-bench's pyproject at
`02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`. The lock is a NEW Python 3.12
resolution, not a historical upstream lock. Resolution used public index
https://pypi.tuna.tsinghua.edu.cn/simple because default PyPI TLS failed.
No inference, training, or development extras are needed for the grading helper.

Explicitly call `harnessgrad.dataset_runtime.prepare_execution('swe_bench')` or
`python tools/prepare_dataset.py --dataset swe_bench --runtime execution`. Optional
`HG_DATASET_RUNTIME_ROOT` selects an external owned store. Read-only checks and
scoring never install. `HG_SWE_PYTHON` no longer selects an interpreter.

Separately acquire https://github.com/SWE-bench/SWE-bench at the revision above,
retain its MIT license, and set `HG_SWE_SOURCE` to an absolute directory containing
`swebench/`. Every package source file and the complete file set must match
`data/scaffolds/native_research/SWE_EVALUATOR_SOURCE.json`. The worker snapshots
those exact authenticated bytes before import; installed registry or VCS packages
are not an implicit fallback. The official algorithms/helpers remain unchanged.

Validation: new managed artifact prepared on Linux x86_64 Python 3.12.4; actual
`swe_official.py check` imported grading, utils and run_evaluation successfully,
exit 0. Artifact ID:
`315076ab52324c57526478f43bb8c87a49787e7d9fbfb05782a0271b0669dcce`.
A task-owned copy of an existing local source tree was authenticated against the
fixed full package inventory; this is not evidence of a fresh upstream download.
No Docker daemon operation, benchmark execution or real model call was performed.
Import readiness does not establish native image readiness or benchmark results.

Follow-up validation: two consecutive `official('check')` worker launches using
`-I -B -X utf8`, each followed by exact `admit_binding`, passed against the existing
managed artifact. No bytecode writes or dependency reinstall was required. Public
verdict evidence includes runtime/module identity commitments without the internal
binding's absolute host paths or a duplicate private-case digest.
