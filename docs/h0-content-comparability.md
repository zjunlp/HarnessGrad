# Content-attested H0 reuse

`--h0-from RUN` uses strict matching by default. For an original round-zero
measurement published with reviewed evidence, select portable admission explicitly.

```bash
harnessgrad run --harness-path "$BASE_HARNESS" --dataset alfworld \
  --sampling all --tasks "$TASK_IDS" --runs-root "$RUNS_ROOT" \
  --h0-from "$DONOR_RUN" --h0-comparability content \
  --h0-agent-content "$SERVING_RECEIPT" \
  --h0-attestation-sha256 "$TRUSTED_DONOR_SHA256"
```

Obtain the attestation SHA-256 from the original measurement authority through a
trusted release or another reviewed channel. A checksum calculated from an
unreviewed download only identifies the downloaded bytes.

The `harnessgrad.h0-agent-content.v1` serving receipt is an operator-reviewed
JSON file for the actual configured route. Keep credentials in the existing
configuration and use only the following fields in the receipt.

| Field | Value |
| --- | --- |
| `schema` | `harnessgrad.h0-agent-content.v1` |
| `model` | The model job currently selected by `HG_AGENT_MODEL` |
| `endpoint_sha256` | SHA-256 of the exact configured `HG_AGENT_BASE_URL` string in UTF-8 |
| `content.weights_sha256` | A reviewed digest of the actual served model artifacts, including the tokenizer if it is part of the serving release |
| `content.backend` | The current configured backend |
| `content.decoding` | Effective decoding settings, including `temperature`, `max_tokens`, server defaults and wire |
| `content.harness_config` | Effective Harness measurement configuration as recorded by the original run |

A model job string or a model-card name alone cannot establish this weight
digest. The platform matches the serving receipt to the configured route and
requires the donor and recipient content to agree. Review weights, tokenizer,
server defaults and Harness configuration against deployment evidence before
using the receipt. For the pinned Loop study source, admission also checks the
live budget overrides, fixed temperature, chat wire, output token cap, and
the request timeout and retries. Supply those last two as `request_timeout_s`
and `request_retries` inside `decoding`.

On a fresh train run, pass `--h0-agent-content FILE` to capture an attestation at
measurement time. After H0, the driver writes `h0-content-attestation.json` and
prints its SHA-256. A portable export can contain that file and the committed
`round0_traces/` evidence. The original round-zero point lives inside the
attestation. The exporter can keep the original curve, raw H0 and run metadata
alongside it. A present curve point must agree with the attestation. Publish the
pin through a reviewed channel and preserve the original run ID.

Content admission checks the exact source tree and cohort, verifier/setup/task
commitments, recorded aggregation and scoring implementation, admitted
interpreter and package content, and Harness configuration. It compares the
runtime inventories within their declared scope, which covers the runtime
manager's artifacts and base executable. The local system is outside that scope.
It records deployment identifiers separately. Verifier options, unknown adapter
fields and pinned execution policies participate in comparison.

A verified inherited point preserves the original measurement's attribution and
recorded platform flag. `shared_from` records the original run, proof digest,
measurement origin and deployment differences. Recipient-bound context fields
represent the equivalent local execution context so normal continuation and
resume can still validate it. H0 reuse uses the exact attested cohort. The
existing `--h0-subset-view` continues to use the strict full-evidence path.

## Auditing a historical publication

```bash
python -m tools.prepare_h0_content "$STAGED_H0_DIRECTORY"
```

For a historical publication with complete original evidence, the publisher can
prepare a reviewed `harnessgrad.h0-publication-evidence.v1` JSON with `run_id`,
`content`, `agent` and `deployment` as in a contemporaneous attestation.
It must also include the immutable publication reference in `source`, the
reviewed canonical round-zero point SHA-256 in `point_sha256`, and the original
relative trace file names and their SHA-256 values in `traces`. Stage trace
files as `round0_traces/<task_id>.jsonl`. The tool checks the scores, cohort,
point digest and trace bytes against the reviewed evidence before packaging.

```bash
python -m tools.prepare_h0_content "$STAGED_H0_DIRECTORY" \
  --reviewed-evidence "$REVIEWED_EVIDENCE" \
  --reviewed-evidence-sha256 "$TRUSTED_REVIEWED_EVIDENCE_SHA256"
```

For the published ALFWorld 53/100 H0, the pinned Loop tree and the 100 task scores
can be checked. The public mirror lacks the original measurement-time content
attestation. Current local fingerprints cannot prove what the original endpoint,
verifier and runtime executed. Obtain the original evidence or measure one fresh
H0 on `0.2.0-study` with the fixed cohort and share that run between the arms.
