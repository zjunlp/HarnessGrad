# w-sel: operator-authorized first five in adapter order

LIMIT: bounded first-N, NOT random and NOT representative. This binds only the
40 selected tasks' selection -> export -> scorer-input bytes, not scorer execution,
eligibility, pristine/offline/runtime admission or the rest of the pools.

The eight JSON manifests contain IDs, source/code hashes, per-task row/gold/prompt
hashes and complete external selected-only export inventories. Payloads are never
committed. `run_id`/`selection_id` reserve a fresh prospective scope; runtime_run_id
is null and no trial was launched. Split mode is declared; seven selections span
train and eval, and TriviaQA is all eval. No side-based replacement is allowed.

Recompute from actual installed bytes and reject any changed/extra export file:

    python3 tests/fixtures/hg_wsel/packet.py
    sha256sum -c tests/fixtures/hg_wsel/SHA256SUMS
    python3 tests/fixtures/hg_wsel/check_scope.py
    python3 tools/check_dataset_blockers.py

The recomputation imports only the eight requested adapters to obtain their real
record order and verifier input serialization. It never calls a score function,
executes a verifier, reads an actor prediction or invokes a model/network/server.
Adapter construction reads installed tables, including TriviaQA's declared study
sample, but copies only the first five records to the external export. No seed
regeneration, resampling or shuffle occurs. Historical inventory comparison checks
only the five selected rows against preserved ignored main-checkout receipts;
those receipts are read-only and their full file digests are recorded.

To create a NEW packet in a separate empty packet directory/worktree, use:

    python3 tests/fixtures/hg_wsel/packet.py --create --export-root /absolute/fresh/external/path

Existing manifests/exports refuse overwrite. Keep the original exports; do not
resume or repurpose their reserved IDs as evidence of an executed diagnostic.
For the complete digest byte-domain definitions, ordering file:line citations,
selected sides, actual external paths and exact remaining next_action clauses,
see registry/w-sel-manifests.md and registry/w-sel-validation.txt.

The baseline checker needed 11 already-existing ignored registry-cited text files
restored from the main checkout. `restored-observations.json` records their current
recomputed size/hash; no missing observation or dataset payload was invented.
