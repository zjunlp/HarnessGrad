# E2 historical metadata receipts and comparison

This fixture retains a historical public-HTTPS metadata review. At collection,
both source-provenance blockers were external, with no issued custody record or
independent approval. Current blocker status is maintained in
[`registry/dataset-blockers.json`](../../../registry/dataset-blockers.json).

`starting-blockers.json` retains the registry next actions from that review.
`comparison.json` records every response/hop, failure, inherited-file digest and
metadata comparison. `SHA256SUMS` inventories the files currently retained here,
except itself. Fifteen response bodies named in the original inventory were
already absent before the acquisition helper was retired; their recorded digests
remain in the receipts and comparison. Byte checks do not recover those bodies
or reproduce the complete original review. Retained receipts and response data
are unchanged; `.headers` files are Python HTTPMessage serializations of
parsed response headers, not original wire-format headers. Receipts retain method,
URL, UTC acquisition time, status, byte count and SHA-256; refusals without a
response have no invented body digest.

The historical collection used a B6 local HTTPS egress proxy with normal
certificate validation.
HTTPS origin observations are distinct from independent publisher signature or
custodian authentication. Public metadata responses are evidence of what the
endpoint advertised at retrieval, not signed artifact or platform custody.
The download-followup HEAD uses the exact redirect from the retained earlier
refusal. Its empty response digest hashes zero bytes, never the advertised ZIP.
The Dropbox ETag is retained as an opaque token, never relabeled SHA-256.

The review recorded no shard, pool, task/answer/alias data, heldout material,
reference solution or archive-body access. The inherited statement digest came
from metadata. The comparison used historical original/recovery selection and
split metadata without recomputing scores; no adapter/acquired code was executed.

The inherited Apache-2.0 evaluator license digest is recorded in `comparison.json`;
its original local source is no longer retained. No dataset/alias redistribution
license is inferred from it.
The acquired LiveCodeBench card says `license: cc`, without specifying a variant.

Check the bytes of the retained files (no network):

```sh
cd tests/fixtures/upstream_provenance_e2
sha256sum --check SHA256SUMS
```

The original inherited inputs, response bodies and registry snapshot are no
longer complete in this checkout. `comparison.json` remains the historical derived
record; it has not been regenerated. Retained-file checks do not establish missing
source custody, authenticate the comparison or authorize production admission.

The one-off acquisition and comparison helpers have been retired. Retained
receipts are historical data, not instructions to repeat the requests.
