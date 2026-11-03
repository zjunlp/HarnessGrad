# Independent integration review

**Verdict.** The three material integration findings are closed. Review covered
the frozen working-tree resolution on `codex/integrate-onboarding-latest-20261103`
against `origin/main` at `1365a826` and the prior fix at `20b701b9`.

| Finding | Resolution independently checked |
| --- | --- |
| Reference methods A and B bypassed editor readiness after the merge. | Their manifests now declare an editor requirement separately from the endpoint-only flag. With an empty environment, WSL preflight rejected both and kept the no-op method independent of any editor. |
| A configured endpoint editor passed readiness without its Python client. | On WSL with no OpenAI package installed, a direct DGM editor failed early with the package named. A DGM CLI override passed. An RRSI CLI with a structured endpoint role still failed for the missing package. Each probe used local configuration checks. |
| An archived protocol with no recorded kind acquired a custom-heldout label during integration. | A temporary run with only `official_score_comparable=false` now yields a null matrix partition, an unknown protocol label, and the comparison warning. Rendering the same receipt through the run summary reports an unknown split and retains the official-comparison restriction. |

The upstream driver still calls the local configuration check, optional endpoint
checks and selected-task model checks before H0. Both eval and train completion
paths retain the upstream `--fail-on-incomplete` exit. The matrix preserves its
incomplete-run exclusion and early-stop status while keeping coverage and the
rejection reasons for individual rounds. The summary retains its completion
banner. The external template is the upstream implementation. The PLAN6 sandbox
module and completion module have no working-tree diff against `1365a826`.
Git reported no unresolved index entries at final inspection.

Verification here consisted of source comparisons, targeted WSL configuration
probes and a temporary matrix/summary receipt. The parent owns the integration
suite and driver smoke results. Two upstream fresh-venv cases encountered
fixture setup errors because WSL lacks ensurepip, so those cases remain
unverified in this environment.
