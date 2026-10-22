# Decision: enforcement-proof requirements are out of scope

**Decision (operator, 2026-10-15), following the `paired-filesystem-quotas` withdrawal of the
same day:** requirements whose *only* content is "prove that some enforcement mechanism is
really in force" are **not** preconditions for running a dataset. Three were reviewed under
that heading and withdrawn:

| Blocker | Where | What it demanded |
| --- | --- | --- |
| `official-scorer-identity` | shared | that source call sites *prove* fresh official evaluator execution and release/runtime identity |
| `snapshot-owned-cleanup` | shared | proof of bounded snapshots, exact owned absence and atomic lease closure |
| `prevent-platform-writes` | `scienceworld` | independent proof that mount enforcement is effective and platform provenance immutable |

## The reasoning, which is one argument applied three times

The platform already **performs** each of these; what was demanded was evidence that the
mechanism *cannot fail*. That is a different and much stronger claim, and it was raised in
each case without a measured failure behind it. The operator's position is that this is
over-defensive: the failure modes are not ones they have encountered in empirical work, and
the cost is that every dataset carries a gate that no amount of ordinary correct operation
can ever close.

The quota withdrawal is the worked example and the reason these three fell with it: the
requirement was not "make cleanup work", it was "prove cleanup cannot leak", and the price of
proving it was a root remount of two shared filesystems.

## What is still done, and must not be read as withdrawn

Withdrawing the *proof* requirement is not withdrawing the *behaviour*:

- scrapers and verifiers still rematerialize inputs, still snapshot and still clean up.
  `eval/admission/runner_admission.py::admit_snapshot` and `admit_cleanup` check available
  observations **best-effort, with uncertainty retained**. Missing or null facts, missing
  owned-object observations and incomplete namespace coverage do not by themselves refuse
  admission or quarantine a lease. Passing receipts explicitly record `best_effort=true`
  and `uncertainties`; they do not certify bounded peak snapshot usage, exact owned absence
  or atomic lease closure. A known failure or mismatch still refuses admission and is
  recorded in a blocked receipt; cleanup refusals still request `lease_action=quarantine`.
  Cleanup can request `lease_action=release` with recorded uncertainty. These functions
  inspect facts; they do not replace snapshot/cleanup execution or ledger persistence;
- each dataset review still records **metric identity** — what the number is and where it
  comes from — because that is what the reviews are for, and `official-scorer-identity`'s own
  `next_action` asked for documentation, not enforcement. The documentation continues; the
  blocker that gated the datasets on *proof* does not;
- the realtime refusal contract is untouched: a declared policy that needs a backend
  observation this checkout cannot supply still refuses before staging.

The snapshot/cleanup change preserves binding and input integrity: stale run/trial/manifest
bindings, malformed values, unexpected identities and invalid supplied hashes still refuse.
Omitted/null lifecycle facts, owned-object identity components and evidence hashes record
uncertainty; they are not invented successful observations. Known identity components must
still match the owned policy. An observed nonzero exit, OOM, running worker, deadline mismatch,
size/headroom violation, surviving owned object or nonempty owned namespace is still bad
even when other observations are missing. A passing admission receipt remains unmeasured
and grants no production authorization. Snapshot/cleanup uncertainty alone is not a new
dataset blocker, and it never repairs missing scores or failed historical runs.

Two adjacent checks in `eval/admission/pilot_admission.py` remain unchanged:
`scorer egress is not authorized` validates a pinned cache manifest (image identity/digest
and scorer network `none`) and catches mutation; `ownership label mismatch; refusing removal`
protects other leases' and users' resources. These are integrity and ownership properties,
not requirements to prove enforcement infallible. The independent pilot control and its
ledger retain their existing behavior; this change targets only the two runner admission APIs.

## What a reader should conclude

These mechanisms are **used, unproven, and accepted**. That is a materially different
statement from "verified", and it must not be upgraded in later summaries. If a mechanism is
ever observed to fail, the failure is the evidence that was missing, and the requirement can
be re-raised with something behind it.

The supporting reviews stay checked in and unedited, including
`docs/filesystem-quota-backends.md`, `reports/scorer-closure-codex170-report.md` and the
lifecycle material in `docs/task-233-quota-readback-authentication.md`.
