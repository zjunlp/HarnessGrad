# w-sel: retained first-five selection records

LIMIT: bounded first-N, NOT random and NOT representative. This binds only the
40 selected tasks' selection -> export -> scorer-input bytes, not scorer execution,
eligibility, pristine/offline/runtime admission or the rest of the pools.

The eight JSON manifests contain IDs, source/code hashes, per-task row/gold/prompt
hashes and complete external selected-only export inventories. Payloads are never
committed. `run_id`/`selection_id` reserve a fresh prospective scope; runtime_run_id
is null and no trial was launched. Split mode is declared; seven selections span
train and eval, and TriviaQA is all eval. No side-based replacement is allowed.

Check the retained files from the repository root:

    sha256sum -c tests/fixtures/hg_wsel/SHA256SUMS

This checks the retained README and ten JSON files. It does not revalidate the
original installed datasets or external exports, or establish present-day scoring
eligibility. The one-off generator and revision-specific validation scripts have
been removed.

The reserved IDs remain records of selection only and must not be repurposed as
evidence of an executed diagnostic.

The baseline checker needed 11 already-existing ignored registry-cited text files
restored from the main checkout. `restored-observations.json` records their current
recomputed size/hash; no missing observation or dataset payload was invented.
