# Adapter integration and evidence

For current requirements, see [dataset onboarding](dataset-onboarding.md), the
[adapter specification](adapter-spec.md), [dataset runtime interfaces](dataset-runtime-interfaces.md),
and the dataset-specific documentation.

The [blocker registry](../registry/dataset-blockers.json) tracks maintainer reviews,
evidence gaps, and historical run records.

The historical integration journal at local Git commit
`68e1a0b1c5324593977ffa282552b552a8b48c3c`, path
`docs/adapter-integration-journal.md`, records the 2026-11-05 and 2026-11-06
runtime migrations. Its original SHA256 is
`dbd2fddea4e535b52b4185de06e1d83c1754b578f193cfc7c97aa68625c3bee6`.
This identifies a local source, not a claim of publication. Earlier retired journal
line references in other records refer to their historical source, not this page.
The private validation summaries cited by the original journal are not provided in
this checkout and have not been independently reviewed. Historical reports do not
establish current provisioning, benchmark results, or production readiness.

For new integrations, record source and license, split, scorer identity, trusted
and private data boundaries, runtime and resource requirements, and the scope and
receipts of validation. Synthetic controls test interfaces; they are not benchmark
scores.

## Historical entry index

The following anchors identify entries in the original journal; consult the source
commit for their original text and the limitations above for its receipts.

<a id="dataset-loading-gets-an-independent-provider-environment-2026-11-05"></a>

- Dataset loading gets an independent provider environment (2026-11-05)
<a id="costbench-services-and-scorers-bind-managed-python-artifacts-2026-11-05"></a>

- CostBench services and scorers bind managed Python artifacts (2026-11-05)
<a id="bound-services-receive-only-invocation-owned-recording-handles-2026-11-05"></a>

- Bound services receive only invocation-owned recording handles (2026-11-05)
<a id="managed-dataset-artifacts-bind-h0-cache-and-resume-2026-11-05"></a>

- Managed dataset artifacts bind H0, cache and resume (2026-11-05)
<a id="candidate-owned-python-preparation-and-lifecycle-2026-11-06"></a>

- Candidate-owned Python preparation and lifecycle (2026-11-06)
<a id="correct-candidate-source-identity-and-checkpoint-eval-provenance-2026-11-06"></a>

- Correct candidate source identity and checkpoint eval provenance (2026-11-06)
<a id="enforce-source-directories-that-survive-file-only-snapshots-2026-11-06"></a>

- Enforce source directories that survive file-only snapshots (2026-11-06)
<a id="migrate-miniagent-to-a-managed-python-environment-2026-11-06"></a>

- Migrate MiniAgent to a managed Python environment (2026-11-06)
<a id="extend-qa-provider-isolation-to-triviaqa-and-hotpotqa-2026-11-06"></a>

- Extend QA provider isolation to TriviaQA and HotpotQA (2026-11-06)
<a id="route-qa-controls-and-host-inventories-through-providers-2026-11-06"></a>

- Route QA controls and host inventories through providers (2026-11-06)
<a id="give-the-default-loop-harness-managed-files-and-exec-environments-2026-11-06"></a>

- Give the default Loop Harness managed files and exec environments (2026-11-06)
<a id="preserve-task-image-python-and-tool-configuration-2026-11-06"></a>

- Preserve task-image Python and tool configuration (2026-11-06)
<a id="unified-cold-start-acceptance-of-migrated-environments-2026-11-06"></a>

- Unified cold-start acceptance of migrated environments (2026-11-06)
<a id="miniswe-managed-python-migration-2026-11-06"></a>

- MiniSWE managed Python migration (2026-11-06)
<a id="miniswe-configuration-paths-containing-equals-2026-11-06"></a>

- MiniSWE configuration paths containing equals (2026-11-06)
