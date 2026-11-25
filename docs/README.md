# Documentation map

Start with the path that matches your task. The large reference files at the repository root retain their established paths so existing links, generators and experiment tooling continue to work.

## Get started

- [First use](getting-started.md) — installation, offline control and Console setup.
- [RSI experiments](rsi-experiments.md) — prepare and run a research recipe.
- [PTC-Bench](../data/datasets/ptc_bench/README.md) and its [train, validation, and test partition](../experiments/splits/ptc-bench/README.md).
- [Harness authoring](writing_a_harness.md) and [bring your own harness](bring-your-own-harness.md).
- [Customization proposals](customization.md) — generate a reviewable candidate from selected feedback.
- [Bring your own method](bring-your-own-method.md) and [dataset onboarding](dataset-onboarding.md).

## Architecture and interfaces

- [Architecture map](architecture.md) — current source ownership and compatibility imports.
- [Shared foundation](shared-foundation.md) — common harness and method primitives, workflow boundaries and an offline composition example.
- [Import direction](import-direction.md) — enforced and historical dependency edges.
- [Import migration](import-migration.md) — removed import paths and their replacements.
- [Interface contract](../INTERFACE.md) — the experiment protocol.
- [Dataset reference](../DATASETS.md) and [downloads](../DOWNLOADS.md).

## Measurement and operations

- [Measurement policy](refusal-policy.md), [splits](dataset-splits.md) and [noise](experiment_noise.md).
- [Evidence export](evidence-export.md) — reviewable reports from existing run receipts.
- [Harness evolution protocol](harness-evolution-protocol.md) — interpreting improvement evidence.
- [Result analysis tools](../tools/analysis/) — curves, run summaries, results matrix, component attribution, benchmark-specific reports and evidence export. Use the commands in this directory; their former top-level script paths were removed.
- [Figure renderers](../tools/visualization/) — optional renderers for recorded project figures. Use the commands in this directory; their former top-level script paths were removed.
- [Harness setup tools](../tools/harness_setup/) — general Python, Codex, DeepSeek, OpenCode, OpenClaw, Bun and Responses preparation; existing script paths remain supported.
- [Test suite](test-suite.md), [test runner tools for a source checkout](../tools/testing/), [test lanes](test-lanes.md), [refactor review map](validation/refactor-review.md), [refactor offline control](validation/shared-foundation-offline.md) and [release validation](release-validation.md).
- [Runtime interfaces](dataset-runtime-interfaces.md), [runtime store](native-runtime-store.md) and [network egress](egress.md).

## Reviews and archives

- [Dataset maintenance](../tools/dataset_maintenance/) — scaffold creation, taxonomy, lint, downloads, runtime preparation and generated inventories. Original top-level commands remain available.
- [Dataset registry maintenance](../tools/registry_ops/) — validation, reviewed proposals and semantic merge.
- [Dataset reviews](dataset-reviews/) and the [review reports](../reports/) preserve existing evidence paths.
- [Historical site redirects](archive/site-redirect/) are retained but no longer deployed.

This map is a navigation aid; it does not change the status of historical evidence or certify that an integration is ready to run.
