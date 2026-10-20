# Dataset Adapter Review

Review base: 7cfc63ec. Integration branch: fix/intern-adapter-review, registered worktree /tmp/hg-intern-review. Main checkout is deliberately unchanged while ALFWorld measurements are running. No model calls or GPU experiments were launched for this review.

## Findings

| Report | Finding | Resolution |
| --- | --- | --- |
| GSM8K leading-dot decimals | Confirmed: .5 and -.5 were extracted as 5 | Preserve leading-dot decimal tokens; regression tests include sign and currency |
| GSM8K leakage assertion | Confirmed: blanket #### prohibition rejected legitimate format instructions | Test solution/gold independence rather than prohibiting a format marker |
| BFCL-v3 dotted call text | Confirmed: wrong_tool(); namespace.expected_tool matched expected_tool | Strip namespaces only on valid identifiers; preserve malformed predictions as failures |
| LiveCodeBench numpy.bool_ | Confirmed: positive predicate worked, but evidence serialization failed | Normalize numpy scalars before transporting verdicts; preserve negative sentinels |
| SpreadsheetBench leading equals text | Confirmed: string-prefix fallback misclassified literal OOXML text | Use formula cell type; test literal strings, real scalar/shared/array formulas and unchanged comparison |
| GAIA level | Already implemented at base in scorer evidence, progress records and per-level aggregation | Document existing interface; retain current scoring and tests |
| AppWorld action/API granularity | Direct claim that scoring counts APIs as wrong is stale: evaluator scores final state. A real issue remained: replaying one execute per trusted API could hit upstream's default 1000 interaction cap | Set verifier-only `max_interactions` to trusted log length; actor action policy is unchanged; 1002-call capped stand-in regression passes |
| AgentDojo concurrent transcript updates | Confirmed unlocked read/call/write can lose updates and reorder replay | Hold a cooperative sidecar `flock` across read, relay call, and write, including submit; deterministic multiprocess regression passes |

## Parent Verification

Integrated scorer/parser suite: 222 passed, 12 skipped, 3 deselected in 57.25s. The command included GSM8K dataset/scorer, BFCL adaptfid1/dataset/containment differential, LiveCodeBench scorer, SpreadsheetBench scorer/classification, GAIA level reporting and AppWorld replay granularity. Three expensive LiveCodeBench shard/test hashing and full oracle tests were explicitly deselected; no full dataset evaluation is claimed. Eleven BFCL lazy-cache data-backed tests and one SpreadsheetBench optional test skipped.

Runtime repair suite after integration: 37 passed in 78.52s, covering AppWorld replay granularity, the actor protocol, AgentDojo official scorer, and relay behavior. The runtime agent's focused suite was 21 passed with `--basetemp=/tmp/hg-intern-runtime-pytest`; its worktree and diff were clean. The AppWorld fix is validated with a stand-in mirroring the inspected upstream cap; no real Docker transcript with over 1000 APIs was run.

The BFCL differential current-source inventory was updated separately after review: only the changed adapter and an already-stale evidence_scorers.py byte/hash row, not acquired upstream sources or historical observations. The unrelated broad BFCLguard source graph remains stale at base (42 extra sources); baseline test confirms it already failed before these changes. An unrelated AppWorld reporting test also fails at base because its isolated AST namespace omits _RUN_IDS. Neither issue was hidden or treated as a new scorer defect.

The final integrated code is in commits `132b95eb`, `e0cd45da`, `0c24b37a`, and `524547f4` on the isolated review branch. `git diff --check` passes. The review branch also contains this report as an uncommitted file until the final parent review.

## Merge Condition

Do not merge into main while the ALFWorld fill is active: changing data/, tests/, INTERFACE.md or other protected platform content invalidates measurements. Recheck the operator's current HEAD and working tree, review all integration commits, then merge on top only after measurement is finished. These fixes are implemented in isolated worktrees, not deployed to the running study. Existing scores are not silently rewritten.
