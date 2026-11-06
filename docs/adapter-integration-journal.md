# Adapter integration and evidence

For current adapter requirements and validation steps, see [dataset onboarding](dataset-onboarding.md), [adapter specification](adapter-spec.md), and the dataset-specific documentation under `data/datasets/`. Current known blockers and their evidence are maintained in [`registry/dataset-blockers.json`](../registry/dataset-blockers.json).

The old full journal and lifecycle-review snapshots have been retired from this checkout. Historical line-number quotations in retained records describe those retired sources, not this page. Unavailable experiment receipts remain explicitly unverified; a historical quotation does not establish a new measurement.

A new adapter should document its source version and licensing, split, scorer identity, trusted/private data boundaries, runtime requirements, resource and network assumptions, and validation coverage. Synthetic or mock controls are useful interface tests; they do not establish benchmark scores or authorize production runs. Any reported measured result must retain a verifiable run identifier, tested harness tree identity, and its underlying receipt.
