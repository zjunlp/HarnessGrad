# Pinned Harbor LoCoMo reference

The four reference source/template files, parity_experiment.json and LICENSE are copied byte for byte from
Harbor commit `3e9f14a7c1033896cc600d27de69d6948bcb3410` (Apache-2.0).
File hashes and upstream paths are recorded in
`data/datasets/locomo/specs/source.json` and checked by the differential tests.
These fixtures contain no released benchmark questions or answers. Tests import
only these pinned local Python files, never the live upstream checkout.

The production implementation transcribes Harbor's normalization, Porter stemming,
category-specific scoring, JSON coercion, deterministic options, conversation
rendering, and question-weighted aggregation. It does not claim equivalence to every
execution path of the original LoCoMo evaluation. Missing/invalid answer files score
zero as Harbor does; a failed execution with no score is a separate unmeasured trial
and prevents publication of a complete aggregate.
