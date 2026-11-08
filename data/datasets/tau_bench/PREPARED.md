# External prepared data

The installed adapter requires an explicitly prepared external root. It retains the historical source commit, per-file checksums, task identifiers, pool definitions, exclusions and scoring code. Private cases and database/policy data belong outside the wheel.

Import a historical asset directory using the installed entry point. The source must contain the exact complete assets declared by the shipped `PROVENANCE.json`.

```sh
python -m tools.prepare_tau_bench --source /path/to/historical/tau_bench --destination /data/tau-bench/prepared
export HG_TAU_BENCH_PREPARED=/data/tau-bench/prepared
```

Without this explicit override the location is the existing `HG_DATA_TAU_BENCH` dataset root followed by `prepared`, or `HG_DATA_ROOT/tau-bench/prepared`. Historical source-tree `gt/` is accessible through this explicit import operation. Loading no longer discovers it automatically. The original source-only regeneration script remains a historical regeneration tool and writes its original location. Import that output separately.

Import validates original provenance and every pinned file, copies verified bytes to a temporary sibling, validates again and publishes under a destination-specific Linux file lock. A matching destination can be reused. A different or damaged destination is refused and retained. Failed imports remove their temporary directory. Original source assets and historical receipts are retained.

The prepared root contains the original manifest closure including clean databases, policies and tool-library identity. The verifier still uses the trusted tool code shipped beside the adapter. Actor policy/tool/database execution remains supplied by the original pinned external image. Preparation does not build, fetch or validate that image. Keep prepared storage under trusted read-only custody during execution because the platform stages verifier inputs from these paths later.

The historical license audit in `docs/tau-bench-vendoring.md` records absent upstream license material. This migration preserves original source headers and provenance. It does not resolve that pre-existing licensing gap.

The ordinary offline checks use only manifest metadata and authored synthetic bytes:

```sh
python -m pytest tests/quality/test_tau_bench_prepared.py tests/quality/test_tau_bench_split.py
```

The historical split-integrity checks require both prepared storage and the explicit
`HG_TEST_TAU_BENCH_REAL_DATA=1` opt-in. Default skips are unmeasured checks, not
successful real-data validation. These integrity checks do not execute episodes.

On 2026-11-07, retrieval of `LICENSE`, `LICENSE.md`, `LICENSE.txt`, and `NOTICE`
from the public raw GitHub URL at the exact recorded commit failed with TLS EOF;
the alternate GitHub raw URL also failed during TLS negotiation. No upstream
license bytes were acquired and no license was inferred from the root license.
The historical license-material gap therefore remains unresolved.
