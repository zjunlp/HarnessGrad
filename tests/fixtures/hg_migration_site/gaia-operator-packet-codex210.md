# GAIA operator packet — Task 210

**Disposition: BLOCKED; documentation handoff only.** No source correction,
dependency lock, selected manifest, runtime observation or lifecycle receipt is
approved by this packet. Task-202 evidence is unavailable in the reviewed checkout
and visible Git history; its findings remain pending reconciliation.

## Evidence basis

- Reviewed checkout: commit `65b0f2a5bf95fdafd218ca54198843c27b9fffbe`,
  tree `818805c9124c0eb3f2e289ac400677a7218690ae`.
- Task 200: commit `3cc2deed72d83b3a873f98f6cc2ec2111cd281a8`, Git objects
  `docs/gaia-public-source-research-codex200-report.md` and
  `docs/gaia-public-source-research-codex200-metadata.json`. That commit is **not
  an ancestor of this checkout**; its documentation was reviewed directly from
  Git. All six local source/document SHA-256s in its metadata match this checkout.
  This checks those files, not complete source/runtime equivalence.
- Local context: [dry-preflight report](gaia-preflight-codex175-report.md),
  [source/snapshot gate](../reports/GAIA_GATE.md), and
  [quota checklist](filesystem-quota-backends.md). Historical receipts cited by
  these documents were not opened or revalidated.
- Task 210 read repository source/docs and Git metadata only, within
  `/tmp/hg-codex-210`. No benchmark task, answer, attachment, annotator or heldout
  payload was read; no network, Docker, model, H0, control or scorer ran.

## Resolve the source identity before pinning

| Identity | Recorded value | Evidence status |
| --- | --- | --- |
| Dataset repository / revision | `gaia-benchmark/GAIA` / `682dd723ee1e1697e00360edccf2366dc8418dd9` | Local declaration; task-200 mirror metadata agrees; independent custody pending |
| Declared leaderboard revision | `9f133d71362ecb0a1c7ff6b4dcfc2a6b9f8ffd1e` | Still declared locally; task-200 mirror requests returned 404 |
| Candidate leaderboard revision | `9f133d71362e77b3539f1514f31b9c101a545fec` | Task-200 mirror observation only; not an approved correction |
| Candidate `scorer.py` | 3,212 bytes; Git blob `ede7ece61d26cc93bf440a86a527163d617e7c12` | Task-200 tree/header/blob consistency within the same mirror transport |
| Scorer SHA-256 | `0d44c07f3046eec521697c22e3eaca8719cc81e422a8eaf32695c5f22bdac6e2` | Task-200 retrieved bytes match the local expected checksum |

The source custodian must independently authenticate the accepted **full** revision
and code-only snapshot, record the discrepancy decision and reviewer, and retain
both identities and failed requests. A common prefix or matching scorer checksum
does not resolve commit provenance; a mirror 404 does not establish upstream
absence. Direct upstream access failed in task 200. The current
`resolve/main/scorer.py` URL remains mutable. Any later approved repair must bind
revision-specific acquisition to reviewed bytes and license/notices, then freeze
the final clean source before adapter import through receipt sealing. Task 200
observed an Apache-2.0 Space card but no root LICENSE; dataset gating/license
custody is separate. This task changes no pins or admission rules.

## Required operator deliveries, in dependency order

Every row is **pending**. Deliver authenticated metadata and protected receipt
references; keep task/gold/attachment contents out of this packet.

| Delivery / owner | Required contents and acceptance evidence |
| --- | --- |
| **Scorer bytes and dependency pin — source/runtime custodians** | Accepted revision/tree/blob, exact scorer size/SHA-256, wrapper and license/notices; sealed interpreter build, Python/NumPy versions, platform/architecture, distribution hashes, transitive and native loader/library inventory, and bundle digest. Unchanged scorer bytes import NumPy even though scoring functions do not use it; built-in generic annotations require Python >=3.9 as written. No exact dependency versions or lock are approved here. The hosted leaderboard imports remote/private data and is not the offline scorer. Require a later pristine, network-isolated import/invocation receipt; static feasibility is insufficient. |
| **Selected validation/attachment manifest — data custodian + reader implementer** | Bind dataset revision, `split=validation`, pool/source/split digests, authenticated eligible ID-only inventory/count, preselection rule/time, approved selected IDs/count and manifest digest. Per selected item: row identity/digest and associated attachment relative identity, byte size and SHA-256; explicitly record no-attachment cases. Include approved aggregate file/byte bounds and protected selected-gold projection digest/custody, excluding annotator solutions from materialization and gold from actor setup. Declared validation size 165 is not observed eligibility. Require selected-only reader evidence before nonselected reads; current full-pool loading/preparation/control paths cannot supply it. No test/heldout materialization. |
| **Actor/verifier image IDs — runtime custodian** | Independently observed exact actor `image_id` and pristine `verifier_image_id`, each `sha256:<64 lowercase hex>`, plus immutable base/build and dependency provenance, no-model harness, effective command/environment/mount plan, export/quota/web policy digests and finite resource limits. Both IDs are currently **unobserved**. Mutable tags and the actor snapshot/toolchain cannot establish a trusted verifier. Require read-only trusted inputs and protected gold/evidence storage. |
| **Services network observation — backend observer** | Bind run, actor/gateway/verifier container identities, actual network IDs/memberships, actor `services`/web topology, gateway image/config/policy digest and effective DNS/proxy/egress behavior. Independently observe pristine verifier `network=none`. `services` is a source declaration, not an observation. Shared runtime admission currently accepts only network-none; a reviewed actor-topology integration and trusted observer are needed while retaining the refusal boundary. No network receipt exists in this packet. |
| **Quota receipt — host operator + backend observer** | Actual kernel, filesystem/device/mount/namespace and Docker driver/snapshotter identity; requested/effective paired hard allocated-byte **and inode** limits with readback, ownership ledger and enforcement before first staging/write. Cover actor layer, scratch/HOME/tmpfs/binds, preparation, verifier, daemon/content, snapshot destination/temporary writes, export, logs and evidence. Also bind observed CPU/memory/swap/PID/deadline limits for each writer. Free space, reservations and byte-only snapshot checks are not quotas. Use the linked quota checklist; its sidecar schema is proposed documentation, not a connected enforcement API. |
| **Artifact-only export receipt — platform exporter + verifier reviewer** | Reviewed answer-only, data-only allowlist; per-file/count/aggregate bounds, types, sizes and digests; platform-owned sealed projection identity, provenance and fresh storage. Record pristine verifier command, read-only mounts, trusted scorer/dependency/selected-gold bindings and official invocation. Exclude executable actor state and keep gold/submission-bearing verifier evidence protected. Current artifact contracts are not integrated into GAIA scoring; copying verifier inputs into an actor snapshot does not close this gate. |
| **Snapshot receipts — lifecycle observer** | Separate preflight/completion records: exact owned actor and storage identities; all-writer quiescence and stop/exit/OOM facts; writable/free/reserved bytes **and inodes**, effective maxima/reserves, device identity, bounded destination/temporary growth, daemon/worker resources and finite deadline; operation outcome and successful immutable snapshot image ID. Bind snapshot to export/verifier receipts without treating it as the pristine verifier. Unknown size, timeout or failed snapshot retains an invalid/no-score result. |
| **Cleanup receipt — ownership/lifecycle observer** | Fresh run-scoped intent and complete owned actor/verifier/gateway, container, image/tag/snapshot, network, volume, mount, workspace and quota-scope inventory. Record removal attempts separately from independently observed absence in every relevant namespace, with observation times/deadlines. Verify no remaining writers/mounts and released storage accounting before atomic lease/reservation release. On ambiguity, retain/quarantine ownership, including commit timeouts with no returned image ID. Best-effort teardown or exit 0 is insufficient; preserve all old/incomplete receipts. |

## Seal and review before any execution expansion

Bind every delivered receipt by digest to one fresh run ID, one frozen source and
external-input manifest, the selected manifest, actor/verifier identities and
policy/resource generation. The enclosing record needs metric identity, actual
eligible/selected/task/trial coverage, invalid/unmeasured counts, source/split/
manifest/harness/dependency hashes, finite budgets, observer/reviewer provenance,
cleanup status and comparability notes. Model hash and endpoint binding are
explicitly N/A for no-model controls. Unobserved counts remain unknown, not zero.

### Current contract clarification — Task 219

This delivery checklist is not an accepted preflight input or a complete receipt
schema. `eval.policy.gaia_preflight` accepts full source commit identities and optional
paired inline `harnessgrad.gaia-runtime.v1` declarations only, never receipt files
or task/attachment paths. Every review returns exit 2 with execution blocked,
`measured=false`, `production_authorized=false`, `lifecycle_authorized=false`,
`evidence_reusable=false` and `receipt_validation=not-performed`. Matching
declarations authenticate no observations and hold no source lock.

For a later separately authorized staged diagnostic/pilot, the enclosing record
must use `harnessgrad.diagnostic-receipt.v2`, including `eligible_task_ids_sha256`,
`selected_task_ids`, `context`, every selected outcome, recomputed coverage,
cleanup/snapshot summaries and explicit `lifecycle` (or null while unavailable).
Each summary and `context.gates` needs `status`, `receipt_sha256` and `detail`.
Unknown eligible counts cannot satisfy that schema's positive integer requirement;
retain this blocked handoff rather than inventing counts or IDs. For no-model
records, encode N/A as JSON null in `context.hashes.model` and `context.endpoint`,
with an explanatory comparability note, never the string `"N/A"`. Such records
cannot satisfy the model diagnostic expansion review's required model hash and
local endpoint.

The lifecycle binds `adapter_id`, `run_id`, `trial_id`, `manifest_sha256` and a
pre-execution `scope_sha256`. Its successful sequence is
`scope -> admit -> execute -> quiesce -> score -> seal -> cleanup`; the seal
contains the completed snapshot attestation, its preflight and worker-runtime
evidence, and the same outcomes digest as scoring. Before any aggregate or
individual measured score is published, require passing gates/snapshot/cleanup
and replay via `eval.policy.diagnostic_policy.validate_receipt`, which checks scope,
coverage, outcomes and exact cleanup/snapshot digests. JSON Schema shape validation
alone does not perform those checks. Failed/unknown lifecycle work remains
nonpublishable after cleanup; cleanup may still be attempted. `publication_ready`
is structural only: `production_authorized` remains false even on success. The
library neither authenticates observers nor removes resources or releases leases.
GAIA export/pristine-verifier and actor-network integration remain pending.

These documentation changes are not in the compatibility tool's two-file
`REVIEW_ONLY` exception set. Compare the final committed candidate afresh; this
clarification grants no historical receipt reuse or gate closure. The task-212
readiness matrix is unavailable in the task-219 checkout/visible Git history, so
its reconciliation remains pending; no readiness or execution approval follows.
See the [task-219 review](gaia-contract-review-codex219.md) for scope and validation.

Later separately authorized selected controls must independently measure official
oracle=1/noop=0 and preserve scorer/provider/snapshot/cleanup failures as
invalid/unmeasured rather than ordinary zeros. The metric is upstream
`question_scorer` Boolean correctness; any selected-validation mean must name its
measured denominator and cannot claim full-split/test-leaderboard comparability.

Task-202 reconciliation and independent receipt review remain prerequisites. This
packet supplies no execution authorization; the current refusal stays in force.
After all gates pass, the staged policy permits consideration of a separately
authorized **one-task/one-trial, jobs=1 diagnostic** with a fresh run ID. Expansion
requires another review of its valid measured receipt; temporary reruns select
`min(30, ceil(10% * eligible))`, trials=1, jobs=1, with total H0 concurrency <=3.
No old run ID may be resumed or overwritten.

Task-210 verification is limited to documentation consistency, exact task-200
metadata/source-hash comparison and diff review; it is not a runtime receipt.
