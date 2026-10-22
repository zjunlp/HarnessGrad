# E2 metadata acquisition and comparison

Both owned source-provenance blockers remain **external**. This fixture is public
HTTPS acquisition plus offline comparison, with no issued custody record or
independent approval. See [the cited review](../../../reports/e2-upstream-provenance-review.md).

`starting-blockers.json` retains the current registry next actions verbatim.
`comparison.json` records every response/hop, failure, inherited-file digest and
metadata comparison. `SHA256SUMS` hashes every file in this fixture except itself.
No earlier fixture or response is overwritten. Original HTTP response entity
bytes are unchanged; `.headers` files are Python HTTPMessage serializations of
parsed response headers, not original wire-format headers. Receipts retain method,
URL, UTC acquisition time, status, byte count and SHA-256; refusals without a
response have no invented body digest.

The preexisting B6 local HTTPS egress proxy uses normal certificate validation.
HTTPS origin observations are distinct from independent publisher signature or
custodian authentication. Public metadata responses are evidence of what the
endpoint advertised at retrieval, not signed artifact or platform custody.
The download-followup HEAD uses the exact redirect from the retained earlier
refusal. Its empty response digest hashes zero bytes, never the advertised ZIP.
The Dropbox ETag is retained as an opaque token, never relabeled SHA-256.

No shard, pool, task/answer/alias data, heldout material, examples, tests, reference
solution or archive body is fetched/read. The inherited statement digest is read
from metadata; the statement itself is not opened. Acquired README and API
metadata contain no benchmark examples. Existing original/recovery curve files
are parsed only to compare their selection/split metadata; no score is reviewed,
recomputed or assigned. No adapter/acquired code is imported or executed. Only
this fixture's acquisition/verification helpers execute.

The inherited Apache-2.0 evaluator license remains at
`tests/fixtures/twowikimultihopqa_upstream_codex197/LICENSE`; its SHA-256 is recorded
in `comparison.json`. No dataset/alias redistribution license is inferred from it.
The acquired LiveCodeBench card says `license: cc`, without specifying a variant.

Offline verification (no network):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/upstream_provenance_e2/verify.py --check
```

`--write` deliberately regenerates derived comparisons and the hash inventory;
use only after reviewing changes. `--check` checks all received entity/header
hashes, exact file coverage, inherited pins, card Git-blob linkage, ID/declaration
comparisons, historical split/selection hashes and the two-blocker registry scope.
Neither mode creates a human approval or calls production admission.

Acquisition helper requests use four concurrent public metadata operations,
18-second per-hop timeouts, a 2-MiB body limit and a six-hop bound. These are
research network limits, not benchmark budgets. Retained receipts are immutable
inputs, not execution instructions; acquisition refuses existing output paths.
