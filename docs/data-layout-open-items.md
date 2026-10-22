# Data layout: Stage D ownership findings

Audited in `/tmp/hg-ds3` at `fdef36a`, using this worktree and the repository's
locally reachable Git history. No other checkout, external dataset directory,
download, adapter execution, or historical run was used. Evidence references
below are `path:line` at this revision unless a historical commit is specified.
Negative searches are identified as searches; absence has no source line.

**PinchBench is a candidate-dataset feasibility inventory, not an empty
directory.** Its history does not establish a removed adapter or an approved
integration plan. Preserve it outside Stage D's automatic adapter-asset move
until its disposition is explicitly decided. **Tau-bench's inventory is
sufficient to distinguish adapter assets, pinned upstream source, and generated
derivatives.** Independent checks below confirm its classes; the final location
and third-party notice packaging still need a migration decision.

## PinchBench: what was actually added

`data/pinchbench/` held one tracked file, `inventory.json` (1,251 lines); it was deleted on 2026-10-27 with the repo slim, because no adapter was ever published for it and the inventory classified nothing a run reads.
The “empty” description in `docs/data-split-plan.md:23` and
`docs/data-split-plan.md:216` is incorrect for this revision.

The earliest tracked addition on the current ancestry is
`ceb4a551c1f250e0fb63c80013646565150055f6`, titled
“data: inventory PinchBench automated-only feasibility.” Its author date is
2026-10-09 11:01:37 +0800 and its committer date is 2026-10-09 16:44:34 +0800.
It adds only `data/pinchbench/inventory.json`; the parent tree has no tracked
`data/pinchbench/` path. Git does not record empty directories, so this establishes
the first tracked content, not the date someone ran `mkdir`.

`git log --all` also finds `a735b8071ca16c0c63c4b29fb66259af863fb842`, with the
same title and author date (committer date 11:01:37 +0800). Its inventory blob is
byte-for-byte identical to `ceb4a55` and the current file. This is another
reachable history record of the same artifact, not evidence of a second dataset.
The merge `26f15dc` appears in a full-history path log but has no PinchBench
addition/removal relative to its first parent; it retains the inventory.

The file's own evidence explains the directory's origin:

| Evidence | What it establishes |
| --- | --- |
| `data/pinchbench/inventory.json:3` | Upstream is `https://github.com/pinchbench/skill`. |
| `data/pinchbench/inventory.json:4` | Source revision is `819384ae830492365b8363fc26bc2602e73f216d`. |
| `data/pinchbench/inventory.json:5` | Recorded codeload, canonical task archive, and ordered-ID SHA-256 values identify source/selection artifacts; their presence is not a run receipt. |
| `data/pinchbench/inventory.json:8` | Selection is `TaskLoader(manifest order), grading_type == automated`; `is_official_split` is false, with 122 non-automated tasks excluded from a loader count of 147. |
| `data/pinchbench/inventory.json:13` | Inventory count is 25; independently parsing the file confirms 25 records, all marked automated. |
| `data/pinchbench/inventory.json:21` and `data/pinchbench/inventory.json:32` | Task source paths, task digests, and grading-code digests are recorded. |
| `data/pinchbench/inventory.json:41` | The first task's tool and transcript contracts are null; setup is described as loader materialization. These are feasibility observations, not a HarnessGrad adapter declaration. |

At `ceb4a55`, a case-insensitive tree-wide search for `pinchbench` / `pinch.bench`
finds only this new inventory: its repository URL at line 3, a PDF asset name at
line 738, and a private grading staging path at line 955. No registry, schema,
documentation, download entry, or receipt references it at that commit.

At `fdef36a`, a case-insensitive search of all tracked files for
`pinch[ _-]?bench` finds only the inventory and the three mentions in the split
plan (`docs/data-split-plan.md:23`, `:189`, `:216`). In particular:

* No `data/pinchbench.py` exists. Static AST enumeration finds 28 top-level
  `KIND = "dataset"` declarations, none named PinchBench. Discovery considers
  top-level Python modules and their declarations (`data/registry.py:43`);
  the blocker inventory uses the same top-level literal-declaration boundary
  (`tools/check_dataset_blockers.py:72`). An inventory directory is not an adapter.
* No match occurs in `docs/dataset-blockers.json`, `docs/dataset-blockers.md`,
  `docs/adapter-integration-journal.md`, `data/registry.py`, or
  `data/dataset.schema.json`. The schema specifies a generic module contract,
  including `KIND`, `load`, and `downloads` (`data/dataset.schema.json:14`);
  its silence is not a list of rejected datasets.
* No match occurs in `DOWNLOADS.md` or the checked-in receipts/evidence under
  `reports/`, `docs/evidence/`, and `tests/fixtures/`. The downloads manifest is
  rendered from adapter `downloads()` functions (`DOWNLOADS.md:5`), so its absence
  is consistent with the absence of an adapter. The inventory's own archive
  digests are the only located download-related PinchBench evidence.

History searches across all local refs find no `data/pinchbench.py` addition or
deletion, no deletion of the inventory, and no historical PinchBench text in the
download manifest, receipt/evidence directories, registry, schema, blocker index,
or integration journal. This is bounded repository evidence, not a claim about
unavailable archives or frozen checkouts outside this worktree.

**Verdict:** the recorded origin is feasibility work for a candidate dataset,
which supports the planned-work interpretation rather than a removed-dataset
claim. However, “approved planned dataset” versus “abandoned feasibility work”
remains undecidable. Neither a decision to integrate nor one to abandon it is
recorded. A retained implementation/removal commit or explicit retirement record
would settle a removed-dataset claim; an owner-approved integration/disposition
record naming this inventory would settle its current intended ownership and
destination. None was found. Do not invent an adapter owner or delete the file.

## Tau-bench: independent verification of the inventory

Read `docs/tau-bench-vendoring.md`, introduced by
`17fba78ed1a33d4b7a40cc1d7463af102c45c513`. Its table distinguishes 4 adapter
assets, 33 vendored upstream files, and 173 generated files
(`docs/tau-bench-vendoring.md:18`). Its content has not changed since that commit;
the Tau-bench asset tree and builder also have no diff from that revision.

An independent read-only check enumerated filesystem files (including hidden and
empty files) and `git ls-files`: both sets have 210 files and exactly match the
inventory rows, sizes, and line counts. Reconstructing classes from the builder's
copy/write boundaries gives the same 4/33/173 partition. All 205 present payloads
listed in `PROVENANCE.json.files` match their recorded SHA-256 values; the other
five present files are the four handwritten assets and the provenance receipt.
The two extra manifest entries, `db/airline.json` and `db/retail.json`, are absent.

The following samples were read directly, rather than accepted from table labels.
Paths in this table are relative to `data/tau_bench/` unless prefixed otherwise.

| Sample and independently confirmed class | Content, header, and provenance evidence |
| --- | --- |
| `image/tau_cli.py` — **adapter-asset** | Its docstring defines the platform's `tau call/say/done` CLI and database/log interface (`data/tau_bench/image/tau_cli.py:2`, `:17`, `:30`). The builder explicitly leaves handwritten `image/` and `verify/` alone (`tools/build_tau_bench_gt.py:252`); the platform Dockerfile installs this CLI (`data/tau_bench/Dockerfile:38`). It is absent from the generated-payload hash map, consistently with that boundary. |
| `.dockerignore`, `Dockerfile`, `verify/check.py` — **adapter-asset**, additional checks | The ignore header explains verification-only inputs (`data/tau_bench/.dockerignore:1`); the Dockerfile names the HarnessGrad image and upstream provenance (`data/tau_bench/Dockerfile:24`); the verifier header describes platform-staged snapshot verification (`data/tau_bench/verify/check.py:5`). These confirm the other three handwritten assets. |
| `lib/tau_bench/envs/retail/tools/calculate.py` — **vendored-upstream** | Begins `# Copyright Sierra`, imports upstream `Tool`, and implements `Calculate` (`data/tau_bench/lib/tau_bench/envs/retail/tools/calculate.py:1`). The builder copies each domain's `tools/*.py` with `shutil.copy2` (`tools/build_tau_bench_gt.py:216`). Upstream path is `tau_bench/envs/retail/tools/calculate.py`; pin is recorded at `data/tau_bench/PROVENANCE.json:4`, URL at `DOWNLOADS.md:591`, matching output digest at `data/tau_bench/PROVENANCE.json:194`. |
| `lib/tau_bench/envs/tool.py` — **vendored-upstream**, headerless case | Its entire 12-line body is a `Tool` ABC, with no copyright/URL/revision header (`data/tau_bench/lib/tau_bench/envs/tool.py:1`). Origin is nevertheless explicit: builder copies upstream `tau_bench/envs/tool.py` (`tools/build_tau_bench_gt.py:212`), manifest pins the checkout and matching output digest (`data/tau_bench/PROVENANCE.json:4`, `:210`). Missing inline attribution does not make this file unclassified. |
| `gt/airline_000.json` — **generated**, upstream task derivative | JSON has `domain`, `index`, `instruction`, and `task_id` (`data/tau_bench/gt/airline_000.json:44`), with no source header. The builder serializes exactly these fields (`tools/build_tau_bench_gt.py:264`); its domain mapping selects airline `tasks_test.TASKS` (`tools/build_tau_bench_gt.py:78`, `:167`). Manifest records that source file (`data/tau_bench/PROVENANCE.json:220`), upstream pin (`:4`), and matching output digest (`:8`). |
| `lib/tau_bench/envs/hash.py` — **generated**, upstream code extraction | The header explicitly says extracted rather than rewritten, names upstream `tau_bench/envs/base.py`, and gives its SHA-256 (`data/tau_bench/lib/tau_bench/envs/hash.py:1`). Builder extracts the region from `ToHashable =` to `class Env` and adds the header/imports (`tools/build_tau_bench_gt.py:141`). Original-source digest matches `data/tau_bench/PROVENANCE.json:2`; generated-output digest matches `:191`. Its generated classification does not erase third-party ownership. |
| `lib/tau_bench/__init__.py` — **generated**, packaging stub | Independently confirmed zero bytes: it has no content/header or line 1 to cite. Builder writes empty package initializers (`tools/build_tau_bench_gt.py:208`); its recorded digest is the SHA-256 of empty bytes (`data/tau_bench/PROVENANCE.json:173`). The analogous domain `tools/__init__.py` files are copied registries, not these stubs. |
| `policy/retail.txt` — **generated**, upstream policy derivative | Trailer explicitly names upstream `rules.py` and `wiki.md` (`data/tau_bench/policy/retail.txt:93`). Builder assembles those sources (`tools/build_tau_bench_gt.py:187`); matching output digest is at `data/tau_bench/PROVENANCE.json:212`, with revision at `:4`. |
| `PROVENANCE.json` — **generated**, platform metadata | `generated_by` names the builder (`data/tau_bench/PROVENANCE.json:214`); its write operation is explicit (`tools/build_tau_bench_gt.py:289`). It is not copied upstream code and does not hash itself. |

The freshly computed sample digests are:

```text
lib/tau_bench/envs/retail/tools/calculate.py  2c3380802673493843ef6d7f3e569e2aed52265ca0b9f7ac248346828d0c75f7
lib/tau_bench/envs/tool.py                    0a158be298092fc64862ae8843f35f57cfba139aa02c2ee8fbe87b1e4a1de0fc
gt/airline_000.json                          873354c140011b25f1a029f5da0b4b1e8057109f02ef7834bf6fed95a389f575
lib/tau_bench/envs/hash.py                    6ac9372d00aa9cb7fed52b8444c93a7c3e43b611fc445c439ae77c2e6771d60a
```

**Stage D verdict:** sufficient for ownership classification, not a blanket
permission to treat the directory as first-party assets. Move the four handwritten
assets with the adapter. The 33 copied files remain pinned third-party source at
`sierra-research/tau-bench@59a200c6d575d595120f1cb70fea53cef0632f6b`, whether
co-located or placed in a dependency directory. Preserve their headers, source
mappings, and provenance. Of 173 generated files, 168 carry upstream material
(165 tasks, two policies, one hash extraction); the other five are four empty
packaging stubs and the provenance receipt. Keep those derivatives and packaging
coherent with the dependency and adapter; `generated` is not an ownership waiver.

**Files whose class could not be confirmed: none among the 210 present files.**
This combines direct content samples with an exhaustive mapping/hash check, not
an independent reread of every implementation or upstream regeneration. No
upstream archive comparison was repeated here; the earlier inventory records its
own archive check and limits (`docs/tau-bench-vendoring.md:69`).

## Explicitly undecidable items

* **PinchBench disposition:** active integration plan versus abandoned feasibility
  work, responsible adapter owner, and final destination. The recorded inventory
  establishes candidate investigation only. An explicit owner decision is absent;
  no removed implementation was found. Empty-directory creation time is also
  unrecorded by Git. Preserve the current path pending that decision.
* **Tau-bench final placement and upstream license/notice packaging:** the inventory
  selects no final layout (`docs/tau-bench-vendoring.md:31`) and records no bundled
  upstream license/notice (`docs/tau-bench-vendoring.md:361`). Stage D needs an
  explicit layout/packaging choice; classification itself is settled.
* **Clean historical source and complete regeneration:** builder records only
  `git rev-parse HEAD` (`tools/build_tau_bench_gt.py:233`), not source dirty state
  or full task/wiki/rules input digests. Matching output hashes establish recorded
  output integrity, not full reproducibility from pristine upstream inputs. A
  clean-source attestation and controlled rebuild/comparison would settle this.
* **Absent database payloads:** `data/tau_bench/db/airline.json` and
  `data/tau_bench/db/retail.json` have recorded hashes
  (`data/tau_bench/PROVENANCE.json:6`) and a generation recipe
  (`tools/build_tau_bench_gt.py:280`), but no local bytes to verify. They are ignored
  (`.gitignore:329` at the audited base) and still required by
  `data/tau_bench/Dockerfile:37`. Their generation class is known; actual bytes and
  present reproducibility remain unverified until a controlled regeneration.
* **Previously built image contents:** the Dockerfile provenance label
  (`data/tau_bench/Dockerfile:24`) is not an image-content receipt. A bound image
  digest and content/build receipt would be needed. This document establishes
  neither runtime readiness nor production authorization.

## What the existing documents should say

Neither `docs/data-split-plan.md` nor `docs/tau-bench-vendoring.md` is edited here.

For the split plan's §1/§7 PinchBench description, replace “empty” with:

> `data/pinchbench/` contains a tracked 25-task automated-only feasibility
> inventory introduced by `ceb4a55` on 2026-10-09, pinned to
> `pinchbench/skill@819384ae830492365b8363fc26bc2602e73f216d`. No adapter,
> registry declaration, blocker entry, download manifest entry, or run receipt
> was located. Its origin is candidate-dataset investigation; an approved
> integration or abandonment decision is absent. Preserve it and exclude it from
> the automatic adapter-asset move until ownership/disposition is recorded.

For §1/§6/§7 Tau-bench, replace “40 files” and “review in flight” with:

> The 210-file inventory in `docs/tau-bench-vendoring.md` is independently
> corroborated in `docs/data-layout-open-items.md`: 4 handwritten adapter assets,
> 33 pinned upstream copies, 173 generated files, no unconfirmed present-file
> class. Stage D moves the handwritten assets with the adapter while preserving
> the upstream revision, headers, provenance, and derivative source mappings.
> Final dependency placement and upstream notice packaging require an explicit
> choice. Absent databases and reproduction/image questions remain separate
> from file ownership. PinchBench is not part of this resolved classification.

The vendoring inventory needs no class correction. Its verdict and generated
lineage caveat (`docs/tau-bench-vendoring.md:26`) should stand. A follow-up note
could cite this independent sample/hash verification, retain its existing
reproducibility/packaging limits, and distinguish a completed classification
review from the still-pending migration layout decision.

## Validation and scope

Read-only checks used `git log --all`, path history, historical tree searches,
blob comparisons, current tracked-file searches, static AST declaration reads,
inventory/file-set and size/line comparisons, and SHA-256 recomputation. The
repository is not shallow; conclusions still cover only locally reachable
history. Search results exclude the audit's own transient log and this new
document. No files were moved or regenerated. No tests were added or run for
this documentation-only finding; directory presence is an audited observation,
not a host-state assertion added to the test suite. The only supporting change
is a `.gitignore` exception making this requested document normally trackable.
