# Contributing a result

This directory is the in-repo half of the contribution process. The public, prose version is
[`website/src/content/docs/contributing.md`](../website/src/content/docs/contributing.md); this
file is the **runbook** — what a submission is, the exact command at each step, and what a
maintainer checks before a number reaches a benchmark page. The two must not disagree: the page
is for contributors who have never seen this repository, this file is for the person merging.

Nothing here is a patch to the platform. A submission describes **a measurement**; the page is
regenerated from the merged file.

## The 60-second version

```bash
# 1. write your result next to the per-task verdicts it came from
cp contrib/example-submission.json contrib/results/alfworld/your-method/greedy-local-9b.json
#    ... edit it, and put the run's per_task object beside it as per_task.json

# 2. check the shape (schema only; the numbers are checked in review)
python3 contrib/validate.py contrib/results/alfworld/your-method/greedy-local-9b.json

# 3. open a pull request. Trajectories stay where you host them; only the link is in git.
```

A PR is the normal path. An issue with the same JSON attached is also fine — a maintainer will
open the PR for you, with you credited as the author of the data.

## What lives where

| path | what it is | in git? |
| --- | --- | --- |
| `contrib/results/<dataset>/<method>/<variant>.json` | one measured row | yes |
| `contrib/results/<dataset>/<method>/<variant>/per_task.json` | the verdicts the score was computed from | yes (small) |
| `contrib/submission.schema.json` | the authoritative schema | yes |
| `contrib/validate.py` | schema check, the command above | yes |
| trajectories | one JSONL per task, one record per step | **no** — link only |

`<variant>` is what distinguishes two rows of the same method (for example `greedy-local-9b` vs
`temp07`). Two rows that differ in the agent model, the base harness, or the improver are
different measurements and must be different variants; they are never averaged into one number.

## What a maintainer verifies, in this order

The last three are the ones that catch a real mistake; the first two catch typos. Run them
against the *files in the PR*, not against what the contributor says in the description.

1. **Shape.** `python3 contrib/validate.py <file>` → `valid · …`. A file that fails is not
   reviewed; say which field failed and let the contributor fix it.
2. **The row is where the site will look.** `dataset` names a benchmark that already has a page
   (`website/src/content/docs/benchmarks/<slug>.mdx`), and `method.name` matches the merged
   value. A dataset with no page is a *new benchmark* contribution, which is a different PR
   (page + results export + reader), not a row.
3. **Task set.** The ids in `per_task.json` are exactly the pool's declared set:
   `valid_unseen` 134, `valid_seen` 140, `valid_train` 200 for ALFWorld (the frozen references
   under `hg-work/rsi10/reference-alfworld-<pool>.json` are the authority; compare the key sets,
   not the counts). A point that measured 139 of 140 is **not** published as 139/140 — it is
   excluded, because the paired comparison would silently shrink to the intersection.
4. **Validity.** `n_invalid == 0` and `n_harness_failed == 0`. Invalid, unmeasured, timeout and
   scorer failures are not zeros; a run that hit one is measured again, not rounded.
5. **The score recomputes.** Recompute the mean from `per_task` values and require it to equal
   the reported `score` to within `1e-9`. This is the check that catches a transcribed number.
   `tools/` has the platform's own version of it (`report.py`); for a contribution a five-line
   script is enough, and it must be run rather than eyeballed.
6. **The state exists.** `runs[].harness_sha` is the content address of the measured harness.
   For the platform's own runs it is a git tree hash and resolves with
   `git cat-file -t <sha>`; for an external contributor it is the hash their run recorded, and
   what we require is that the *report* names how to reproduce it (repository + commit, or a
   tarball hash). A number whose harness cannot be produced is not published as verified.
7. **The stack is declared.** `task_agent.base_harness` + `task_agent.model` and
   `improver.method` + `improver.harness` are all present. Rows are grouped by stack and never
   ranked across stacks, so a missing field is not cosmetic — it decides which table the row
   belongs to.

### The two accepted verdicts

- **`verified`** — all seven steps pass. The site says *verified by the platform*, and the
  contributor may be acknowledged in the technical report if
  `credit.acknowledge_in_report` is `true`.
- **`unverified`** — the contributor asked for it to be published anyway. It goes in a separate
  table with the reason recorded verbatim, never mixed into a verified table, and never used for
  a "best" claim.

There is no third verdict: we do not silently drop a submission, and we do not silently accept
one.

## Common rejection reasons, and what fixes each

Every one of these has happened on our own runs, which is why the checks are mechanical.

| what we saw | why it is not publishable | the fix |
| --- | --- | --- |
| `139/140` on `valid_seen` | the paired test would run on 139 tasks and report a p-value about the survivors | re-measure the missing task, or drop the point |
| a score with no `per_task` | nothing can be recomputed; a typo is indistinguishable from a result | re-run with the record kept |
| the mean of `per_task` ≠ `score` | the number in the file is not the number the run produced | recompute from the record, do not re-type |
| `n_invalid: 3` "excluded from the average" | an excluded task is still a task nobody measured | re-measure the invalid tasks |
| two `runs` entries averaged into one row | one row is one measurement, not a mean of runs | one row per measurement, or publish the replicate as its own variant |
| H0 missing on the contributor's stack | an absolute score cannot be shown as an improvement over ours | publish it as an absolute score marked *no reference* |
| the same row submitted twice under one method name | the page would weight one measurement twice | one `<variant>.json` per measurement |
| trajectories "available on request" | nobody can check the number, now or later | upload them before the row is published, or mark the row unverified |

The `jobs` field matters more than it looks: a point measured at `jobs=13` and one at `jobs=40`
are not the same measurement, because contention does not show up in a score. When the two
differ, the comparison is published with `baseline.problems` quoting it (the platform records
this on its own points since the `baseline` block was added to the eval side). Contributors are
asked for it in `config.jobs` and `config.slots`; if it is unknown, say so, and the row is
published with the mismatch recorded rather than hidden.

## Trajectories: review first, mirror second

Trajectories are large and they never enter this repository. The flow, with the review step in
the middle:

1. **The contributor uploads** the archive anywhere durable and puts the link, format, byte size
   and `sha256` in `trajectories`.
2. **A maintainer downloads it and checks, before anything is mirrored:**
   - the archive's sha256 equals the declared one (`sha256sum archive.tar.zst`);
   - it opens, and the files are one JSONL per task, one record per step — not a screenshot set
     or a notebook export;
   - the task ids inside are the pool's ids, and each file's final verdict matches that task's
     value in `per_task.json`. A sample of 10 tasks is enough for this check; **zero** matches
     out of 10 is the answer to "is this the same run";
   - nothing in it is a credential, an absolute host path, or another user's data. This is the
     step that matters legally, and it is why the mirror is a deliberate act and not a `cp`.
3. **After that, mirror it** into the project's HuggingFace dataset, update the merged JSON to
   `verified: true` and `mirrored: true`, and **keep the original link** in the file. Both links
   stay readable, so provenance survives a re-upload of the mirror.
4. If the contributor's link dies before a mirror exists, the row is republished as
   `mirrored: false` with the dead link removed — a score nobody can check is labelled as such
   rather than quietly kept.

`trajectories.verified` and `trajectories.mirrored` are set by the maintainer only. They are not
fields a submission sets for itself.

## Credit

A published row carries, from `credit`:

- **name (affiliation) + link**, exactly as written;
- **what was submitted**, as `dataset × method` (a contributor with rows on three benchmarks is
  listed on each);
- the verdict — *verified by the platform* or *unverified*.

Verified contributions **may be acknowledged in the technical report** under the same name and
affiliation; `credit.acknowledge_in_report: false` keeps the row on the site and the person out
of the report. `credit.contact` is optional, never published, and can be an email or a WeChat id
— the maintainer's is **Blue18067792208**, and a message that names the dataset is answered
faster than one that does not.

## What is refused outright

- a total score with no per-task record behind it;
- a number measured on a different task set, presented as this pool's;
- somebody else's method reported under the submitter's name without their agreement;
- a row whose trajectories cannot be produced on request;
- a "best of N runs" presented as a single run.

A benchmark page is only worth reading if every row on it can be re-run by someone who does not
trust us; each of these five breaks that, and none of them is fixable by a note in the table.
