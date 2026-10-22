# Decision: hard filesystem quotas are out of scope

**Decision (operator, 2026-10-15):** hard byte/inode enforcement is **not** a precondition
for running a dataset, and the shared blocker `paired-filesystem-quotas` has been withdrawn
from `docs/dataset-blockers.json` accordingly.

This document exists so the withdrawal is a recorded decision rather than a deletion. The
registry's own rule is that a blocker must not be removed to make a count look better; this
removal is made because the requirement itself was reviewed and rejected, and the review is
below.

## What the blocker asserted

> No complete connected hard byte/inode enforcement backend is established for native binds,
> container roots or host workspaces. The source-only reviews did not inspect host state;
> current host quota capability and enforcement remain unknown.

and its next_action asked for the OS/backend provisioning requirements to be reviewed and
enforcement established before changing admission.

## The operator's reasoning

1. **The threat it defends against does not occur in practice.** The scenario is a harness
   that writes without bound until it fills the filesystem. Across years of empirical work
   the operator has not seen this happen, and does not consider it a realistic failure mode
   for the harnesses measured on this platform.
2. **The cost is a host-level change on a shared machine.** Enforcement cannot be added from
   inside this repository: both `/` and `/mnt/20t` are ext4 mounted **without**
   `prjquota`/`usrquota`, the quota tool-chain is absent, and enabling it means
   `tune2fs -O project,quota`, an `/etc/fstab` change and a remount — as root, on a
   filesystem that also carries Docker's store and other users' work.
3. **The benefit does not justify that.** What the platform is buying is protection against
   a failure mode the operator judges unrealistic, at the price of a disruption risk shared
   with every other user of the host.

## What is still true, and must not be misread

- **Enforcement does not exist.** Nothing here establishes, or claims, that any byte or
  inode limit is in force. Removing the blocker removes the *requirement*, not the *gap*.
- **The supporting reviews remain checked in and unedited**:
  `docs/filesystem-quota-backends.md`, `reports/filesystem-quota-codex168-report.md`,
  `docs/task-233-quota-readback-authentication.md`. The last of these is still a proposal
  with no host inspection behind it.
- **The historical audit documents that describe the blocker as open are deliberately left
  as written** — `docs/task-240-blocker-registry-audit.md` and
  `docs/task-236-diagnostic-readiness.md` state the position as of their date. They are
  records of a review, not current guidance, and rewriting them to match today's decision
  would destroy the record of why the requirement was ever raised.
- **If the threat model changes**, the blocker can be re-raised. The evidence it needs is
  already in the repository; what is missing is host provisioning, which needs root.

## What changed in the registry

- the `paired-filesystem-quotas` entry was removed from `shared_blockers`;
- it was removed from every dataset's `shared_blockers` list, because the checker requires
  that list to be a subset of the shared set;
- `tests/quality/test_dataset_blockers.py` pinned the exact shared-blocker set and was
  updated in the same change, so the removal is visible in the diff rather than achieved by
  loosening an assertion.

The `filesystem-quota` **family** name stays in the checker's vocabulary: the family is a
category, and the other quota-related reviews still belong to it.
