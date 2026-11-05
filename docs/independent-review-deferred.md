# Deferred: independent review and custodian approval

**Operator decision (2026-10-15):** every gate whose content is *"someone independent must
approve this"* is **revoked as a blocker** and **deferred to the operator**, who will review
them personally, one at a time, after the rest of the platform is wired up.

This is a deferral, not a judgement that the review is unnecessary. The distinction matters
and is why this file exists: the registry must no longer *gate* on a signature, and the
review must not be quietly forgotten because its blocker disappeared.

Independent review cannot be performed by the party producing the work. Deferral
removes an approval gate; it does not constitute independent review or production
authorization. Consult the current registry for each dataset's actual blockers.

## Withdrawn outright — the requirement was the approval

| Dataset | Blocker | What it demanded |
| --- | --- | --- |
| `bfcl_v3` | `release-index-custody` | human custodian approval of historical tag/archive digest and member bindings, plus independent review of the code-only evaluator candidate |
| `gsm8k` | `matched-scorer-protocol-receipts` | an *independently reviewed* pair of measured receipts matching the documented scorer/protocol identities |
| `musique` | `matched-joint-policy-receipts` | the same, for the joint answer/support policy |

`gsm8k` and `musique` now carry no open blocker. The withdrawn requirement is the demand that a second party sign the receipts.
Historical receipts are archived outside the public checkout; their existence and
current applicability must be verified independently.

## Kept, with the approval clause removed — these contain real work

Four blockers mixed acquisition with approval. The acquisition is work; only the signature is
deferred, so these stay in the registry with their requirement narrowed:

| Dataset | Blocker | Still required |
| --- | --- | --- |
| `bfcl_v3` | `simple-ast-contract` | the missing original import/initializer bodies and a reviewed Simple invocation graph |
| `triviaqa` | `hub-export-scorer-linkage` | the public exact-revision Hub commit/tree/config/split metadata and immutable conversion provenance |
| `twowikimultihopqa` | `hub-data-provenance` | the metadata-only Hub/release revision, split/selected-manifest and alias-artifact custody, and the dataset licence |
| `gaia` | `frozen-lifecycle-receipt` | the metadata-only source/runtime/lifecycle review; GAIA itself stays policy-refused and is not provisioned or run |

## What the operator is taking on

Each row above, plus anything a later review re-raises under the same heading, is now the
operator's to accept or reject. Review remains pending; acquisition requirements may also remain open. Neither
verification nor readiness follows from this deferral.

Nothing here authorizes production. `production_authorized` remains `false`, and a dataset
whose only remaining obstacle was a signature is still not a dataset that has been reviewed.
