# Adapter integration and evidence

For current adapter requirements and validation steps, see [dataset onboarding](dataset-onboarding.md), [adapter specification](adapter-spec.md), and the dataset-specific documentation under `data/datasets/`. Current known blockers and their evidence are maintained in [`registry/dataset-blockers.json`](../registry/dataset-blockers.json).

Historical experiment notes and one-off developer work reports are not a public runtime contract. They were preserved outside the source release before this document was shortened; historical line-number citations refer to the archived original, not this page. A reference to an unavailable old receipt must remain explicitly unverified rather than being promoted into a new measurement.

A new adapter should document its source version and licensing, split, scorer identity, trusted/private data boundaries, runtime requirements, resource and network assumptions, and validation coverage. Synthetic or mock controls are useful interface tests; they do not establish benchmark scores or authorize production runs. Any reported measured result must retain a verifiable run identifier, tested harness tree identity, and its underlying receipt.
